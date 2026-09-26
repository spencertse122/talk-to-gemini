#!/usr/bin/env python3
"""Deterministic external process fixture for the documented agy protocol."""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import uuid

FLAGS = ('--input-format', '--output-format', '--conversation', '--model',
         '--dangerously-skip-permissions', '--disable-slash-commands', '--print-timeout')
if '--help' in sys.argv:
    print(' '.join(FLAGS))
    sys.exit(0)
parser = argparse.ArgumentParser()
for flag in FLAGS:
    parser.add_argument(flag, action='store_true' if flag in FLAGS[4:6] else 'store')
args = parser.parse_args()
assert args.input_format == args.output_format == 'stream-json'
assert args.dangerously_skip_permissions and args.disable_slash_commands
cid = args.conversation or str(uuid.uuid4())
history_file = Path('.fake-' + cid + '.json')
if args.conversation and not history_file.exists():
    print('unknown conversation', file=sys.stderr)
    sys.exit(1)
history = json.loads(history_file.read_text()) if history_file.exists() else []
if args.conversation and Path('.resume-wrong').exists():
    time.sleep(0.4)
    cid = 'wrong-conversation'


def emit(event, **payload):
    print(json.dumps({'event': event, **payload}, ensure_ascii=False), flush=True)


emit('init', conversation_id=cid, init={'cwd': os.getcwd(), 'model': args.model,
     'tools': [], 'permission_mode': 'always-proceed'})
if args.model == 'never-read':
    time.sleep(30)
for line in sys.stdin:
    request = json.loads(line)
    assert request['event'] == 'user'
    prompt = request['message']['content']
    history.append(prompt)
    history_file.write_text(json.dumps(history))
    emit('step_update', step_update={'conversation_id': cid, 'step_index': len(history),
         'step_type': 'agent_response', 'state': 'ACTIVE', 'text_delta': 'progress only'})
    if prompt.startswith('SLEEP '):
        time.sleep(float(prompt.split()[1]))
    if prompt == 'CRASH':
        sys.exit(9)
    if prompt == 'DETACH':
        descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                      start_new_session=True)
        Path('.detached-pid').write_text(str(descendant.pid))
    if prompt == 'BAD_JSON':
        print('this is not JSON', flush=True)
        time.sleep(1)
        continue
    response = json.dumps(history, ensure_ascii=False) if prompt == 'HISTORY' else prompt
    if prompt == 'LARGE':
        response = 'αβ\n' * 100000
    result = {'conversation_id': 'wrong-id' if prompt == 'WRONG_ID' else cid,
              'status': 'ERROR' if prompt == 'FAIL' else 'SUCCESS',
              'response': response, 'num_turns': len(history),
              'duration_seconds': 0.1, 'usage': {'total_tokens': len(history) * 10}}
    if prompt == 'FAIL':
        result['error'] = 'intentional fixture failure'
    if prompt == 'MISSING_RESPONSE':
        del result['response']
    emit('result', result=result)
