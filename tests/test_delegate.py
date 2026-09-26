"""Black-box helper tests against real tmux and a fake external agy process."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/agy_delegate.py'
FAKE = ROOT / 'tests/fake_agy.py'


@unittest.skipUnless(shutil.which('tmux'), 'tmux is required')
class DelegateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agy-test-')
        self.cwd = Path(self.temp.name)
        self.state = self.cwd / 'state'
        self.env = dict(os.environ, TMUX_TMPDIR=str(self.cwd))
        self.env.pop('TMUX', None)
        self.env.pop('TMUX_PANE', None)
        self.sessions = []

    def tearDown(self):
        for name in self.sessions:
            self.call('stop', name, check=False)
        # This is a private tmux server created by the test, never the user's.
        subprocess.run(['tmux', 'kill-server'], env=self.env, capture_output=True)
        self.temp.cleanup()

    def call(self, *args, prompt=None, check=True):
        result = subprocess.run([sys.executable, str(HELPER), '--state-dir', str(self.state),
                                 *map(str, args)], input=prompt, text=True,
                                cwd=self.cwd, env=self.env, capture_output=True, timeout=20)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def obj(self, *args, **kwargs):
        return json.loads(self.call(*args, **kwargs).stdout)

    def start(self, name='worker', prompt='first', **kwargs):
        self.sessions.append(name)
        return self.obj('start', name, '--agy', FAKE, '--layout', 'session',
                        prompt=prompt, **kwargs)

    def wait(self, name, turn, **kwargs):
        return self.obj('wait', name, '--turn', turn, '--timeout', '5', **kwargs)

    def result(self, name, turn):
        return self.call('result', name, '--turn', turn).stdout

    def test_revisions_keep_context_and_results_are_not_progress(self):
        receipt = self.start(prompt='Original task')
        done = self.wait('worker', receipt['turn_id'])
        cid = done['conversation_id']
        self.assertTrue(cid)
        self.assertNotIn('Original task', json.dumps(done))
        self.assertEqual(self.result('worker', 1), 'Original task')
        for prompt in ['Fix the first answer', 'Revise again', 'HISTORY']:
            sent = self.obj('send', 'worker', prompt=prompt)
            done = self.wait('worker', sent['turn_id'])
            self.assertEqual(done['conversation_id'], cid)
        self.assertEqual(json.loads(self.result('worker', 4)),
                         ['Original task', 'Fix the first answer', 'Revise again', 'HISTORY'])
        self.assertEqual(self.result('worker', 1), 'Original task')
        self.assertEqual(self.obj('status', 'worker')['state'], 'idle')

    def test_multiline_simulated_human_prompt_is_literal(self):
        prompt = '/help\nI lost access.\n$HOME `touch NOPE` $(touch NOPE) "你好"\n\n'
        path = self.cwd / 'human prompt.txt'
        path.write_text(prompt)
        self.sessions.append('human')
        receipt = self.obj('start', 'human', '--agy', FAKE, '--layout', 'session',
                           '--prompt-file', path)
        self.wait('human', receipt['turn_id'])
        self.assertEqual(self.result('human', 1), prompt)
        self.assertFalse((self.cwd / 'NOPE').exists())

    def test_concurrent_executors_route_independently(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            a = pool.submit(self.start, 'alice', 'SLEEP 0.3')
            b = pool.submit(self.start, 'bob', 'B task')
            a.result(); b.result()
        self.wait('alice', 1); self.wait('bob', 1)
        self.obj('send', 'bob', prompt='B correction'); self.wait('bob', 2)
        self.obj('send', 'alice', prompt='HISTORY'); self.wait('alice', 2)
        self.assertEqual(json.loads(self.result('alice', 2)), ['SLEEP 0.3', 'HISTORY'])
        self.assertEqual(self.result('bob', 2), 'B correction')
        self.assertNotEqual(self.obj('status', 'alice')['conversation_id'],
                            self.obj('status', 'bob')['conversation_id'])

    def test_timeout_does_not_stop_work_and_busy_send_is_rejected(self):
        self.start(prompt='SLEEP 0.8')
        timed = self.obj('wait', 'worker', '--turn', 1, '--timeout', 0, check=False)
        self.assertEqual(timed['status'], 'RUNNING')
        rejected = self.call('send', 'worker', prompt='must not be delivered', check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.wait('worker', 1)
        self.obj('send', 'worker', prompt='HISTORY'); self.wait('worker', 2)
        self.assertEqual(json.loads(self.result('worker', 2)), ['SLEEP 0.8', 'HISTORY'])

    def test_completed_turn_can_be_waited_repeatedly(self):
        self.start()
        first = self.wait('worker', 1)
        again = self.wait('worker', 1)
        self.assertEqual(first, again)
        self.assertEqual(first['status'], 'SUCCESS')

    def test_stop_and_resume_preserve_identity_without_replay(self):
        self.start(prompt='Remember this')
        cid = self.wait('worker', 1)['conversation_id']
        self.obj('stop', 'worker')
        self.assertEqual(self.result('worker', 1), 'Remember this')
        self.obj('resume', 'worker', '--layout', 'session')
        self.obj('send', 'worker', prompt='HISTORY')
        self.assertEqual(self.wait('worker', 2)['conversation_id'], cid)
        self.assertEqual(json.loads(self.result('worker', 2)), ['Remember this', 'HISTORY'])

    def test_errors_are_never_success(self):
        for prompt in ['FAIL', 'CRASH', 'BAD_JSON', 'MISSING_RESPONSE', 'WRONG_ID']:
            with self.subTest(prompt=prompt):
                name = prompt.lower().replace('_', '-')
                self.start(name, prompt)
                done = self.wait(name, 1, check=False)
                self.assertIn(done['status'], ['ERROR', 'INTERRUPTED'])
                self.assertNotEqual(self.call('result', name, '--turn', 1,
                                              check=False).returncode, 0)

    def test_large_response_is_complete(self):
        self.start(prompt='LARGE'); self.wait('worker', 1)
        self.assertEqual(self.result('worker', 1), 'αβ\n' * 100000)

    def test_stdin_preserves_crlf(self):
        self.start(prompt='first\r\nsecond\r\n')
        self.wait('worker', 1)
        path = self.state / 'worker/turns/000001.response.txt'
        self.assertEqual(path.read_bytes(), b'first\r\nsecond\r\n')

    def test_large_prompt_does_not_block_control_when_agy_is_not_reading(self):
        self.sessions.append('slow-reader')
        began = time.monotonic()
        launched = self.call('start', 'slow-reader', '--agy', FAKE, '--layout', 'session',
                             '--model', 'never-read', prompt='x' * 200000, check=False)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(self.obj('status', 'slow-reader')['state'], 'running')
        self.obj('stop', 'slow-reader')
        self.assertEqual(self.wait('slow-reader', 1, check=False)['status'], 'INTERRUPTED')

    def test_auto_layout_splits_without_stealing_focus_or_killing_host(self):
        host = subprocess.run(['tmux', 'new-session', '-d', '-P', '-F', '#{pane_id}',
                               '-s', 'host', 'sleep 60'], env=self.env, text=True,
                              capture_output=True, check=True).stdout.strip()
        connection = subprocess.run(['tmux', 'display-message', '-p', '-t', host,
                                     '#{socket_path},#{pid},0'], env=self.env,
                                    text=True, capture_output=True, check=True).stdout.strip()
        self.env.update(TMUX=connection, TMUX_PANE=host)
        self.sessions.append('beside')
        self.obj('start', 'beside', '--agy', FAKE, prompt='hello')
        self.wait('beside', 1)
        panes = subprocess.run(['tmux', 'list-panes', '-t', 'host', '-F',
                                '#{pane_id} #{pane_active}'], env=self.env, text=True,
                               capture_output=True, check=True).stdout.splitlines()
        self.assertEqual(len(panes), 2)
        self.assertIn(host + ' 1', panes)
        self.obj('stop', 'beside')
        panes = subprocess.run(['tmux', 'list-panes', '-t', 'host', '-F', '#{pane_id}'],
                               env=self.env, text=True, capture_output=True,
                               check=True).stdout.splitlines()
        self.assertEqual(panes, [host])

    def test_worker_death_marks_interrupted_and_resume_does_not_replay(self):
        self.start(prompt='remember'); self.wait('worker', 1)
        sent = self.obj('send', 'worker', prompt='SLEEP 5')
        status = self.obj('status', 'worker')
        os.kill(status['worker_pid'], signal.SIGKILL)
        done = self.wait('worker', sent['turn_id'], check=False)
        self.assertEqual(done['status'], 'INTERRUPTED')
        self.obj('resume', 'worker', '--layout', 'session')
        self.obj('send', 'worker', prompt='HISTORY'); self.wait('worker', 3)
        self.assertEqual(json.loads(self.result('worker', 3)), ['remember', 'SLEEP 5', 'HISTORY'])

    def test_duplicate_names_and_invalid_identifiers_are_rejected(self):
        self.start(); self.wait('worker', 1)
        bad = self.call('start', 'worker', '--agy', FAKE, prompt='duplicate', check=False)
        self.assertNotEqual(bad.returncode, 0)
        for name in ['../escape', 'space name', '-bad']:
            self.assertNotEqual(self.call('status', name, check=False).returncode, 0)
        self.assertNotEqual(self.call('result', 'worker', '--turn', 99, check=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
