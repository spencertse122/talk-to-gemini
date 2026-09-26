#!/usr/bin/env python3
"""Control persistent agy executors in tmux. See --help and references/usage.md."""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid

from agy_runtime import (DelegateError, atomic_text, lock, private_dir, read, receipt,
                         reconcile, rpc, run_worker, save, session_path, turn_path)

DEFAULT_MODEL = 'gemini-3.8-flash-high'


def tmux(*args, socket_path=None, check=True):
    command = ['tmux'] + (['-S', socket_path] if socket_path else []) + list(args)
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if check and result.returncode:
        raise DelegateError('tmux: ' + result.stderr.strip())
    return result


def close_pane(directory):
    path = directory / 'tmux.json'
    if not path.exists():
        return
    pane = read(path)
    owner = tmux('show-options', '-p', '-v', '-t', pane['pane_id'], '@agy_delegate',
                 socket_path=pane['socket_path'], check=False)
    if owner.returncode == 0 and owner.stdout.strip() == pane['token']:
        tmux('kill-pane', '-t', pane['pane_id'], socket_path=pane['socket_path'], check=False)


def check_agy(binary):
    resolved = shutil.which(binary)
    if not resolved:
        raise DelegateError(f'agy executable not found: {binary}')
    result = subprocess.run([resolved, '--help'], capture_output=True, text=True, timeout=10)
    required = ['--input-format', '--output-format', '--conversation', '--model',
                '--dangerously-skip-permissions', '--disable-slash-commands', '--print-timeout']
    missing = [flag for flag in required if flag not in result.stdout + result.stderr]
    if result.returncode or missing:
        raise DelegateError('Unsupported agy CLI; required flags missing: ' + ', '.join(missing))
    return str(Path(resolved).resolve())


def launch(directory, layout):
    state = read(directory / 'state.json')
    state.update(state='starting', started_at=time.time())
    state.pop('error', None)
    save(directory / 'state.json', state)
    token = uuid.uuid4().hex
    socket_dir = private_dir(Path('/tmp') / f'agy-delegate-{os.getuid()}')
    socket_path = socket_dir / (token + '.sock')
    save(directory / 'launch.json', {'token': token, 'socket': str(socket_path)})
    command = 'exec ' + shlex.join([sys.executable, str(Path(__file__).resolve()),
                                   '_worker', str(directory)])
    fmt = '#{pane_id}\t#{socket_path}'
    if layout == 'auto' and os.environ.get('TMUX') and os.environ.get('TMUX_PANE'):
        result = tmux('split-window', '-h', '-d', '-P', '-F', fmt, '-t',
                      os.environ['TMUX_PANE'], '-c', str(directory), command)
    else:
        result = tmux('new-session', '-d', '-P', '-F', fmt, '-s', 'agy-' + token[:12],
                      '-c', str(directory), command)
    pane_id, server = result.stdout.strip().split('\t', 1)
    save(directory / 'tmux.json', {'pane_id': pane_id, 'socket_path': server, 'token': token})
    tmux('set-option', '-p', '-t', pane_id, '@agy_delegate', token, socket_path=server, check=False)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            answer = rpc(directory, {'command': 'status'})
            if answer.get('generation') == token:
                return answer
        except (OSError, DelegateError, ValueError):
            pass
        state = read(directory / 'state.json')
        if state['state'] == 'dead':
            raise DelegateError(state.get('error', 'Worker failed at startup'))
        time.sleep(0.05)
    close_pane(directory)
    reconcile(directory)
    raise DelegateError('Worker did not become ready; inspect status and logs before retrying')


def prompt_text(args):
    if args.prompt_file:
        with open(args.prompt_file, encoding='utf-8', newline='') as stream:
            prompt = stream.read()
    else:
        if sys.stdin.isatty():
            raise DelegateError('Provide --prompt-file PATH or pipe the prompt on stdin')
        prompt = sys.stdin.read()
    if not prompt.strip():
        raise DelegateError('Prompt must contain text')
    return prompt


def start(root, args):
    prompt = prompt_text(args)
    binary = check_agy(args.agy)
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        raise DelegateError(f'Working directory does not exist: {cwd}')
    if not args.model.strip():
        raise DelegateError('Model must not be empty')
    private_dir(root)
    # Local ignore file keeps runtime records untracked without editing the project.
    if not (root / '.gitignore').exists():
        atomic_text(root / '.gitignore', '*\n')
    directory = session_path(root, args.name)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError as error:
        raise DelegateError('Session name already exists; resume it or choose a new name') from error
    private_dir(directory / 'turns')
    config = {'name': args.name, 'session_id': uuid.uuid4().hex, 'model': args.model,
              'cwd': str(cwd), 'agy': binary}
    save(directory / 'config.json', config)
    save(directory / 'state.json', {'state': 'starting', 'turn_id': 0,
                                   'conversation_id': None, 'started_at': time.time()})
    with lock(directory / 'lifecycle.lock'):
        launch(directory, args.layout)
        return rpc(directory, {'command': 'send', 'prompt': prompt})


def status(directory):
    try:
        return rpc(directory, {'command': 'status'})
    except (OSError, DelegateError, ValueError):
        return receipt(directory, reconcile(directory))


def stop(directory):
    with lock(directory / 'lifecycle.lock'):
        try:
            rpc(directory, {'command': 'stop'})
        except (OSError, DelegateError, ValueError):
            pass
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                with lock(directory / 'worker.lock', blocking=False):
                    break
            except BlockingIOError:
                time.sleep(0.05)
        else:
            raise DelegateError('Worker did not stop; inspect status and pane before further action')
        close_pane(directory)
        return receipt(directory, reconcile(directory))


def resume(directory, layout):
    with lock(directory / 'lifecycle.lock'):
        state = reconcile(directory)
        if state['state'] not in ('stopped', 'dead'):
            raise DelegateError('Executor is already running')
        if not state.get('conversation_id'):
            raise DelegateError('No saved conversation ID; cannot resume without starting fresh')
        check_agy(read(directory / 'config.json')['agy'])
        close_pane(directory)
        return launch(directory, layout)


def wait_turn(directory, turn_id, timeout):
    if timeout < 0:
        raise DelegateError('Timeout must be nonnegative')
    path = turn_path(directory, turn_id).with_suffix('.json')
    deadline = time.monotonic() + timeout
    while True:
        turn = read(path)
        state = reconcile(directory)
        turn = read(path)
        if turn['status'] != 'RUNNING' or time.monotonic() >= deadline:
            # A completed receipt is independent of later session state/turns.
            stable = dict(state, state='running' if turn['status'] == 'RUNNING' else 'completed')
            answer = receipt(directory, stable, turn)
            code = 124 if turn['status'] == 'RUNNING' else (0 if turn['status'] == 'SUCCESS' else 1)
            return answer, code
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--state-dir', default='.agy-delegate',
                        help='Persistent registry (default: .agy-delegate in invoking directory)')
    sub = result.add_subparsers(dest='command', required=True)
    for command in ('start', 'send', 'wait', 'result', 'status', 'peek', 'stop', 'resume'):
        item = sub.add_parser(command)
        item.add_argument('name', nargs='?' if command == 'status' else None)
        if command in ('start', 'send'):
            item.add_argument('--prompt-file', help='UTF-8 prompt file; otherwise read stdin')
        if command == 'start':
            item.add_argument('--cwd', default=os.getcwd())
            item.add_argument('--model', default=DEFAULT_MODEL)
            item.add_argument('--agy', default='agy', help='agy executable path/name')
        if command in ('start', 'resume'):
            item.add_argument('--layout', choices=['auto', 'session'], default='auto')
        if command in ('wait', 'result'):
            item.add_argument('--turn', required=True, type=int)
        if command == 'wait':
            item.add_argument('--timeout', type=float, default=60,
                              help='Wait budget in seconds; expiry leaves the turn running')
        if command == 'peek':
            item.add_argument('--lines', type=int, default=40)
    return result


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == '_worker':
        run_worker(sys.argv[2])
        return 0
    args = parser().parse_args()
    root = Path(args.state_dir).absolute()
    if root.is_symlink():
        raise DelegateError('State directory must not be a symlink')
    root = root.resolve()
    if args.command == 'start':
        answer = start(root, args)
    elif args.command == 'status' and args.name is None:
        answer = {'state_dir': str(root), 'sessions': [status(d) for d in sorted(root.iterdir())
                  if d.is_dir() and not d.is_symlink() and (d / 'config.json').exists()]} if root.exists() else {'state_dir': str(root), 'sessions': []}
    else:
        directory = session_path(root, args.name)
        if not (directory / 'config.json').exists():
            raise DelegateError(f'Unknown session: {args.name}')
        if args.command == 'send':
            answer = rpc(directory, {'command': 'send', 'prompt': prompt_text(args)})
        elif args.command == 'status':
            answer = status(directory)
        elif args.command == 'stop':
            answer = stop(directory)
        elif args.command == 'resume':
            answer = resume(directory, args.layout)
        elif args.command == 'wait':
            answer, code = wait_turn(directory, args.turn, args.timeout)
            print(json.dumps(answer, ensure_ascii=False))
            return code
        elif args.command == 'result':
            record = read(turn_path(directory, args.turn).with_suffix('.json'))
            if record['status'] == 'RUNNING':
                raise DelegateError('Turn is still running; use wait first')
            if record.get('response_file'):
                sys.stdout.buffer.write(Path(record['response_file']).read_bytes())
            if record['status'] != 'SUCCESS':
                print(json.dumps({'status': record['status'], 'error': record.get('error')}), file=sys.stderr)
                return 1
            return 0
        elif args.command == 'peek':
            if args.lines < 1:
                raise DelegateError('--lines must be positive')
            path = directory / 'transcript.log'
            if path.exists():
                with path.open(encoding='utf-8') as stream:
                    sys.stdout.write(''.join(deque(stream, maxlen=args.lines)))
            return 0
    print(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (DelegateError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        sys.exit(1)
