#!/usr/bin/env python3
"""Opt-in live agy smoke test; consumes Gemini usage and retains records in /tmp."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HELPER = Path(__file__).resolve().parents[1] / 'scripts/agy_delegate.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='gemini-3.8-flash-high')
    parser.add_argument('--turn-timeout', type=float, default=180)
    args = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix='talk-to-gemini-live-'))
    root = workspace / 'state'
    env = dict(os.environ)
    env.pop('TMUX', None)
    env.pop('TMUX_PANE', None)
    names = ['coding-a', 'human-simulation']
    print(f'Records: {workspace}', flush=True)

    def call(*command, prompt=None, check=True):
        result = subprocess.run([sys.executable, str(HELPER), '--state-dir', str(root),
                                 *map(str, command)], input=prompt, text=True,
                                capture_output=True, env=env, timeout=65)
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result

    def await_turn(name, turn_id):
        deadline = time.monotonic() + args.turn_timeout
        while time.monotonic() < deadline:
            result = call('wait', name, '--turn', turn_id, '--timeout', 30, check=False)
            status = json.loads(result.stdout)
            if result.returncode == 124:
                print(f'{name} turn {turn_id}: still running', flush=True)
                continue
            if result.returncode:
                raise RuntimeError(json.dumps(status))
            response = call('result', name, '--turn', turn_id).stdout
            print(json.dumps({'name': name, 'turn': turn_id,
                              'conversation_id': status['conversation_id'],
                              'response': response}), flush=True)
            return status['conversation_id'], response
        raise RuntimeError(f'{name}: live test time budget exceeded')

    def send(name, prompt):
        receipt = json.loads(call('send', name, prompt=prompt).stdout)
        return await_turn(name, receipt['turn_id'])

    try:
        prompts = [
            'We are planning a tiny coding task. Remember the project codename AMBER. '
            'Do not use tools or change files. Reply with exactly AMBER.',
            'Hi! I am organizing a reading club for beginners. Could you ask me one '
            'question to help me plan it? Please answer conversationally and do not use tools.',
        ]
        def start(name, prompt):
            cwd = workspace / name
            cwd.mkdir()
            return json.loads(call('start', name, '--cwd', cwd, '--model', args.model,
                                   '--layout', 'session', prompt=prompt).stdout)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            receipts = list(pool.map(start, names, prompts))
        cid_a, first = await_turn(names[0], receipts[0]['turn_id'])
        cid_b, _ = await_turn(names[1], receipts[1]['turn_id'])
        assert cid_a != cid_b
        assert first.strip() == 'AMBER'
        cid, response = send(names[0], 'Correction: replace our codename with COBALT. '
                             'Do not use tools. Reply with exactly COBALT.')
        assert cid == cid_a and response.strip() == 'COBALT'
        cid, _ = send(names[1], 'We can meet weekly for one hour. Based on what I said '
                     'about the group earlier, suggest a simple first meeting in two sentences.')
        assert cid == cid_b
        call('stop', names[0])
        call('resume', names[0], '--layout', 'session')
        cid, response = send(names[0], 'What is our corrected project codename? '
                             'Do not use tools. Reply with just the codename.')
        assert cid == cid_a and response.strip() == 'COBALT'
        print('PASS: concurrent conversations, correction loop, human simulation, stop/resume', flush=True)
    finally:
        for name in names:
            result = call('stop', name, check=False)
            if result.returncode:
                print(f'Cleanup for {name}: {result.stderr}', file=sys.stderr)


if __name__ == '__main__':
    main()
