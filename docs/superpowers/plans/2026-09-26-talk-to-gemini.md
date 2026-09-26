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
- [ ] Request one independent code/skill review, then add regression tests for
  substantive findings and fix them.
- [ ] Run the full suite, skill validator, and git diff --check; record evidence.

## Execution record

Implementation authorized by the user's explicit request. Working on a feature
branch in the requested empty repository; a second worktree adds no isolation
benefit here. No global skill installation or configuration changes are planned.

Initial tests failed because the helper did not exist. After implementation, ten
tests passed. A slow-input regression then exposed synchronous stdin delivery
blocking control requests; it failed before moving delivery to a dedicated thread.
The full suite then passed 13 tests, including real tmux split/focus behavior.
Skill validation passed. Live verification with gemini-3.8-flash-high passed six
turns across two independent conversations, including correction memory after
stop/resume and a two-turn simulated human conversation. Live records were saved
outside the repository at /tmp/talk-to-gemini-live-phiiizyh.
