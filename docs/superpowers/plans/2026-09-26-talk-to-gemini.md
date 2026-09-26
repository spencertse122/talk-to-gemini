# talk-to-gemini Implementation Plan

> For agentic workers: use superpowers:executing-plans for inline execution.

**Goal:** Persistent Gemini workers with reliable send/done/result revision loops.

**Architecture:** A CLI controls a worker in tmux through a private Unix socket.
The worker owns the agy pipes and persists responses before completion records.

**Tech Stack:** Python 3.10+ standard library, Linux, tmux, agy.

**Spec:** ../specs/2026-09-26-talk-to-gemini-design.md

## Global constraints

Use the current repository. Default model gemini-3.8-flash-high. Explicit skill
invocation. Automatic agy tool approval. Preserve prompt/response text. No retries
of uncertain turns. One active turn per executor; concurrent independent executors.

## Review focus

Check stale process state and PID reuse; concurrent lifecycle requests; large or
partial stream records; shell/terminal control characters in prompts; malformed
or mismatched resume responses. The integration suite must exercise these boundaries.

## Task 1: Runtime and deterministic tests

Files: scripts/agy_delegate.py (CLI/lifecycle), scripts/agy_runtime.py (worker and
state), tests/fake_agy.py (protocol fixture), tests/test_delegate.py (integration).

Interfaces: CLI --state-dir PATH COMMAND NAME; start/send --prompt-file PATH or
stdin; start --cwd PATH --model NAME --agy PATH --layout auto|session;
wait/result --turn INTEGER. Receipts are JSON; result/peek are text. All commands
identify the state root. Worker is an internal command, never an agy role prompt.

- [x] Write tests for revision loops, late completion, parallel routing, literal
  prompts, busy rejection, timeouts, errors, worker death, stop/resume, and bad IDs.
- [x] Run python3 -m unittest discover -s tests -v; observe missing helper failure.
- [x] Implement atomic state, worker, CLI, owned tmux lifecycle, and capability checks.
- [x] Run the full deterministic suite; resolve failures at their causes.

## Task 2: Skill and user-facing reference

Files: SKILL.md, agents/openai.yaml, references/usage.md.

- [x] Document explicit activation, commands, receipts, the same-session review
  loop, coding workspace ownership, human simulation, and the limits of wakeups.
- [x] Set allow_implicit_invocation to false. Keep detailed CLI/recovery examples
  in the usage reference; make the skill usable from arbitrary working directories.
- [x] Run the skill validator and verify help/examples against the implementation.

## Task 3: Live verification and independent review

- [x] Run small live Gemini conversations with corrections and remembered context.
- [x] Exercise concurrent workers, stop/resume, and a simulated human conversation.
- [x] Request one independent code/skill review, then add regression tests for
  substantive findings and fix them.
- [x] Run the full suite, skill validator, and git diff --check; record evidence.

## Execution record

Implementation authorized by the user's explicit request. Working on a feature
branch in the requested empty repository; a second worktree adds no isolation
benefit here. No global skill installation or configuration changes are planned.

Initial tests failed because the helper did not exist. After implementation, ten
tests passed. A slow-input regression then exposed synchronous stdin delivery
blocking control requests; it failed before moving delivery to a dedicated thread.
The full suite then passed 13 tests, including real tmux split/focus behavior.
Skill validation passed. Live verification with gemini-3.8-flash-high passed five
turns across two independent conversations, including correction memory after
stop/resume and a two-turn simulated human conversation. Live records were saved
outside the repository at /tmp/talk-to-gemini-live-phiiizyh.

Independent review identified a reconciliation race during concurrent resume and
a shutdown deadlock when detached descendants retain agy's pipes. Regression
tests reproduced both before their fixes. Start/resume now also wait for agy's
initialization event and verify the conversation ID before accepting work; a
wrong-ID regression failed before the fix. Nonfinite wait timeouts are rejected.
The final deterministic suite passed all 17 tests in 21.386 seconds. Skill
validation, Python compilation, and git diff --check passed.

A separate live coding check ran two Gemini executors in distinct directories.
They created working Python functions, then one revised its function in the same
conversation; Python assertions verified both the initial code and correction.
Records are at /tmp/talk-to-gemini-coding-6lkvxc7p. After the lifecycle fixes, the
live smoke script passed again: two concurrent conversations, a correction loop,
a two-turn simulated human conversation, and remembered context after stop/resume
(five turns, /tmp/talk-to-gemini-live-tfl7po78). All test workers were stopped.
