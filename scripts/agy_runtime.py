"""Persistent agy transport and durable per-turn records (Linux, stdlib only)."""
from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time

MAX_MESSAGE = 16 * 1024 * 1024
TERMINAL = {'SUCCESS', 'ERROR', 'CANCELED', 'INTERRUPTED', 'INVALID', 'WAITING'}


class DelegateError(Exception):
    pass


def private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise DelegateError(f'Not an owned directory: {path}')
    path.chmod(0o700)
    return path


def atomic_text(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False) + '\n')


def read(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


@contextmanager
def lock(path, blocking=True):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


def session_path(root, name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
        raise DelegateError('Session name must be 1–64 letters, digits, underscores or hyphens, starting with a letter or digit')
    path = root / name
    if path.is_symlink():
        raise DelegateError('Session directories must not be symlinks')
    return path


def turn_path(directory, turn_id):
    if not isinstance(turn_id, int) or turn_id < 1:
        raise DelegateError('turn_id must be a positive integer')
    return directory / 'turns' / f'{turn_id:06d}'


def receipt(directory, state, turn=None):
    config = read(directory / 'config.json')
    result = {key: config[key] for key in ('name', 'session_id', 'model', 'cwd')}
    result.update(state_dir=str(directory.parent), state=state['state'],
                  conversation_id=state.get('conversation_id'),
                  turn_id=turn['turn_id'] if turn else state.get('turn_id', 0))
    if turn:
        result.update(status=turn['status'])
        if turn.get('error'):
            result['error'] = turn['error']
        if turn.get('response_file'):
            result['response_file'] = turn['response_file']
    else:
        for key in ('worker_pid', 'error', 'generation'):
            if key in state:
                result[key] = state[key]
    return result


def interrupt_pending(directory, state, reason):
    turn_id = state.get('turn_id', 0)
    if turn_id:
        path = turn_path(directory, turn_id)
        turn = read(path.with_suffix('.json'))
        if turn['status'] == 'RUNNING':
            turn.update(status='INTERRUPTED', error=reason, finished_at=time.time())
            save(path.with_suffix('.json'), turn)
    state.update(state='dead', error=reason)
    save(directory / 'state.json', state)


def reconcile(directory):
    """A released OS lock, not a saved PID, proves a worker is gone."""
    state = read(directory / 'state.json')
    if state['state'] in ('stopped', 'dead'):
        return state
    if state['state'] == 'starting' and time.time() - state['started_at'] < 20:
        return state
    try:
        with lock(directory / 'worker.lock', blocking=False):
            state = read(directory / 'state.json')
            if state['state'] not in ('stopped', 'dead'):
                interrupt_pending(directory, state, 'Worker exited; delivery/completion may be uncertain. No prompt was replayed.')
    except BlockingIOError:
        pass
    return read(directory / 'state.json')


def wire_read(connection):
    chunks = bytearray()
    while True:
        block = connection.recv(65536)
        if not block:
            raise DelegateError('Connection closed before receipt; inspect status before sending again')
        chunks.extend(block)
        if len(chunks) > MAX_MESSAGE:
            raise DelegateError('Control message exceeds 16 MiB')
        if b'\n' in block:
            return json.loads(chunks.split(b'\n', 1)[0])


def rpc(directory, request):
    launch = read(directory / 'launch.json')
    request = dict(request, token=launch['token'])
    payload = (json.dumps(request, ensure_ascii=False) + '\n').encode()
    if len(payload) > MAX_MESSAGE:
        raise DelegateError('Prompt exceeds 16 MiB control-message limit')
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(launch['socket'])
        client.sendall(payload)
        answer = wire_read(client)
    if 'error' in answer and not answer.get('ok'):
        raise DelegateError(answer['error'])
    return answer


def parent_death_signal(parent_pid):
    """Prevent a killed worker from leaving its local agy process running."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM) != 0:
        raise OSError(ctypes.get_errno(), 'prctl(PR_SET_PDEATHSIG) failed')
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGTERM)


class Worker:
    def __init__(self, directory):
        self.directory = directory
        self.config = read(directory / 'config.json')
        self.launch = read(directory / 'launch.json')
        self.state = read(directory / 'state.json')
        self.state.update(worker_pid=os.getpid(), generation=self.launch['token'])
        self.events = queue.Queue(maxsize=256)
        self.proc = None
        self.stopping = False
        self.active = None
        self.expected_cid = self.state.get('conversation_id')

    def persist(self):
        save(self.directory / 'state.json', self.state)

    def transcript(self, text):
        # Prevent model/terminal escape sequences from controlling the attached terminal.
        clean = ''.join(c if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) < 160)
                        else f'\\x{ord(c):02x}' for c in text)
        with open(self.directory / 'transcript.log', 'a', encoding='utf-8') as stream:
            stream.write(clean)
        print(clean, end='', flush=True)

    def pump(self, pipe, kind):
        try:
            with open(self.directory / f'{kind}.log', 'ab') as raw:
                while True:
                    line = pipe.readline(MAX_MESSAGE + 1)
                    if not line:
                        break
                    raw.write(line); raw.flush()
                    if len(line) > MAX_MESSAGE:
                        self.events.put(('fault', f'{kind} event exceeds 16 MiB'))
                        return
                    self.events.put((kind, line.decode('utf-8', errors='strict' if kind == 'events' else 'replace')))
        except Exception as error:
            self.events.put(('fault', f'{kind} stream: {error}'))
        finally:
            self.events.put((kind + '_eof', None))

    def finish(self, status, response=None, error=None):
        if self.active is None:
            raise DelegateError('Received completion without an active turn')
        path = turn_path(self.directory, self.active['turn_id'])
        if response is not None:
            atomic_text(path.with_suffix('.response.txt'), response)
            self.active['response_file'] = str(path.with_suffix('.response.txt'))
        self.active.update(status=status, finished_at=time.time())
        if error:
            self.active['error'] = str(error)
        # This atomic record is the durable done signal; response is already synced.
        save(path.with_suffix('.json'), self.active)
        self.transcript(f'\n[turn {self.active["turn_id"]}: {status}]\n')
        if response is not None:
            self.transcript(response + '\n')
        self.active = None
        self.state['state'] = 'idle'
        self.persist()

    def identify(self, cid):
        if not isinstance(cid, str) or not cid:
            raise DelegateError('agy omitted the conversation ID')
        known = self.state.get('conversation_id')
        if known and cid != known:
            raise DelegateError('agy conversation ID mismatch; refusing a different conversation')
        self.state['conversation_id'] = cid
        self.persist()

    def event(self, kind, payload):
        if kind == 'fault':
            raise DelegateError(payload)
        if kind == 'events_eof':
            raise DelegateError('agy output closed' + (' during an active turn' if self.active else ''))
        if kind == 'stderr':
            self.transcript('[agy] ' + payload)
        if kind != 'events':
            return
        try:
            event = json.loads(payload)
            if not isinstance(event, dict):
                raise ValueError('expected an event object')
            if event.get('event') == 'init':
                self.identify(event.get('conversation_id'))
            elif event.get('event') == 'step_update':
                step = event.get('step_update', {})
                if step.get('conversation_id'):
                    self.identify(step['conversation_id'])
                if isinstance(step.get('text_delta'), str):
                    self.transcript(step['text_delta'])
                elif step.get('tool_name'):
                    self.transcript(f'\n[tool: {step["tool_name"]}]\n')
            elif event.get('event') == 'result':
                result = event['result']
                self.identify(result.get('conversation_id'))
                status = result.get('status')
                if status not in TERMINAL or not isinstance(result.get('response'), str):
                    raise ValueError('invalid status or missing response in result')
                self.finish(status, result['response'], result.get('error'))
            # Unknown event types remain available in events.log for compatibility.
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise DelegateError(f'Invalid agy event: {error}') from error

    def submit(self, prompt):
        if self.active is not None or self.stopping:
            raise DelegateError('Executor is busy or stopping; wait for the current turn')
        if not isinstance(prompt, str) or not prompt.strip():
            raise DelegateError('Prompt must contain text')
        turn_id = self.state.get('turn_id', 0) + 1
        path = turn_path(self.directory, turn_id)
        atomic_text(path.with_suffix('.prompt.txt'), prompt)
        self.active = {'turn_id': turn_id, 'status': 'RUNNING', 'started_at': time.time()}
        save(path.with_suffix('.json'), self.active)
        self.state.update(state='running', turn_id=turn_id)
        self.persist()
        self.transcript(f'\n[human / assignment — turn {turn_id}]\n{prompt}\n[Gemini]\n')
        payload = (json.dumps({'event': 'user', 'message': {'content': prompt}},
                              ensure_ascii=False) + '\n').encode()
        def deliver():
            try:
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as error:
                self.events.put(('fault', f'Delivery uncertain: {error}; no automatic replay'))
        threading.Thread(target=deliver, daemon=True).start()
        return receipt(self.directory, self.state, self.active)

    def command(self, connection):
        connection.settimeout(3)
        try:
            request = wire_read(connection)
            if request.get('token') != self.launch['token']:
                raise DelegateError('Worker identity mismatch')
            command = request.get('command')
            if command == 'send':
                result = self.submit(request.get('prompt'))
            elif command == 'status':
                result = receipt(self.directory, self.state)
            elif command == 'stop':
                self.stopping = True
                result = receipt(self.directory, self.state)
            else:
                raise DelegateError('Unknown worker command')
            result['ok'] = True
        except (DelegateError, OSError, ValueError, AttributeError) as error:
            result = {'ok': False, 'error': str(error)}
        try:
            connection.sendall((json.dumps(result) + '\n').encode())
        except OSError:
            pass  # An accepted turn remains discoverable even if its client disconnects.

    def cleanup(self, reason):
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
            except ProcessLookupError:
                self.proc.wait()
            for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                pipe.close()
        if self.active:
            self.finish('INTERRUPTED', error=reason)
        self.state['state'] = 'stopped' if self.stopping else 'dead'
        if not self.stopping:
            self.state['error'] = reason
        self.persist()

    def run(self):
        os.umask(0o077)
        with lock(self.directory / 'worker.lock', blocking=False):
            def stop_signal(signum, frame):
                self.stopping = True
            for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                signal.signal(sig, stop_signal)
            self.state.pop('error', None)
            command = [self.config['agy'], '--input-format', 'stream-json', '--output-format',
                       'stream-json', '--model', self.config['model'],
                       '--dangerously-skip-permissions', '--disable-slash-commands',
                       '--print-timeout', '0']
            if self.expected_cid:
                command += ['--conversation', self.expected_cid]
            reason = 'Executor stopped by request'
            with socket.socket(socket.AF_UNIX) as server:
                try:
                    server.bind(self.launch['socket'])
                    server.listen(8)
                    server.settimeout(0.05)
                    parent_pid = os.getpid()
                    self.proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.config['cwd'],
                        start_new_session=True, preexec_fn=lambda: parent_death_signal(parent_pid),
                        env=dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE='true'))
                    for pipe, kind in ((self.proc.stdout, 'events'), (self.proc.stderr, 'stderr')):
                        threading.Thread(target=self.pump, args=(pipe, kind), daemon=True).start()
                    self.state['state'] = 'idle'
                    self.persist()
                    while not self.stopping:
                        # Process a bounded batch so a noisy model cannot starve commands.
                        for _ in range(256):
                            try:
                                event = self.events.get_nowait()
                            except queue.Empty:
                                break
                            self.event(*event)
                        try:
                            connection, _ = server.accept()
                        except socket.timeout:
                            continue
                        with connection:
                            self.command(connection)
                except Exception as error:
                    reason = str(error)
                    self.transcript(f'\n[worker error] {reason}\n')
                finally:
                    self.cleanup(reason)
                    Path(self.launch['socket']).unlink(missing_ok=True)


def run_worker(directory):
    Worker(Path(directory)).run()
