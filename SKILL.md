---
name: talk-to-gemini
description: Use when the user invokes talk-to-gemini or requests delegation to agy/Gemini executors through tmux, including coding swarms and conversations where Codex simulates a human. Supports persistent sessions and repeated review-and-revision loops. Do not initiate delegation for unrelated tasks.
---

# Talk to Gemini

Use the bundled Python helper to run persistent Gemini executors in tmux.
Codex, whether Astra or Sol, directs the work and reviews the results. A **done
signal completes one turn**; Codex decides whether the assignment is finished.

The helper is `scripts/agy_delegate.py` relative to this skill's directory. Resolve
that absolute path once; keep the working directory in the user's project.
Requires Linux, Python 3.10+, tmux, and an authenticated `agy` CLI. Authentication
is performed with the normal interactive `agy` setup. The helper does not install
dependencies or change authentication/settings.

## Delegate and iterate

1. Prepare the prompt in a UTF-8 file, or provide it through stdin. Include the
   context Gemini actually needs. The helper forwards it literally, without adding
   roles, summary requests, completion instructions, or evaluation criteria.
2. Run `start NAME --prompt-file PATH`. Save the returned `state_dir`, `session_id`,
   name, and `turn_id` with the assignment. An optional `--cwd` chooses the executor's
   workspace; `--model` overrides `gemini-3.8-flash-high`.
3. Work independently while Gemini runs, or call `wait NAME --turn ID`.
   The receipt contains status only. Retrieve the answer with `result NAME --turn ID`.
4. Review the full answer and any relevant artifacts. If changes are needed, use
   `send NAME --prompt-file FEEDBACK` with the **same state root and name**, then
   wait for the new turn ID and retrieve its result. Repeat within the assignment's
   scope until accepted or blocked. Do not replace the executor for each revision.
5. Keep the session available while more follow-ups are expected. `stop NAME`
   ends its process and owned pane, preserving records. `resume NAME` reopens its
   saved agy conversation; send the next prompt explicitly afterward.

Every helper invocation accepts `--state-dir PATH` **before** the command. It
defaults to `.agy-delegate/` in the invoking directory. Use the absolute state root
from the receipt when changing directories. Session names are unique within that
root and cannot be reused by `start`; resume or choose a new name.

## Keep orchestration context small

`start` and `send` return after acceptance without waiting for the model's answer.
Start first waits for agy to initialize. `wait` returns compact status; `result`
returns the full final response, with no summarization or added wrapper. Progress
and raw events stay in tmux and on disk. Use `status` or `peek` for diagnosis.

Use the host's yielding/background shell tools when available. Resume an existing
wait command rather than repeatedly launching waits or capturing the pane. With
only synchronous tools, use bounded waits (default 60 seconds). Exit 124 means the
wait expired and the turn is still running; it does not cancel or resend work.
These external workers are not native Codex subagents: do not use `wait_agent` for
them. A completion file cannot independently wake an idle Codex conversation.

## Coding swarms

Use distinct session names and clear task ownership. Give concurrent writers
separate working directories/worktrees or non-overlapping file assignments. The
helper does not create worktrees or merge changes. Codex coordinates dependencies,
reviews changes and validation, and sends each correction to the original executor.

## Human–agent simulations

Codex plays the human and Gemini is the agent under test. Keep scenario goals,
scoring, and evaluation notes in Codex's context; send only the intended agent
setup and simulated human messages. Choose each next human message after reviewing
Gemini's response. Keep one session per independent scenario, and reuse it for
all turns of that scenario. This tests the Gemini agent configured in agy, with
its normal tools and instructions; the helper adds no hidden role prompt.

## Recovery and limits

- One active turn per executor. A busy `send` is rejected, not queued.
- Inspect the saved turn after a send error or disconnection. Delivery may have
  occurred even without a receipt. Never automatically repeat an uncertain prompt.
- A successful CLI turn is evidence of a response, not proof the work is correct.
- `stop` can interrupt work but cannot undo tool actions already performed.
- Automatic agy tool approval is enabled as requested. It does not grant permission
  beyond the user's assignment or bypass the host's execution restrictions.
- After Codex disconnects, active turns may finish; new turns await Codex.

Read [references/usage.md](references/usage.md) for copyable commands, exit codes,
saved-state layout, tmux attachment, and troubleshooting. The command structure
is inspired by [open-agent-teams](https://github.com/AI-Builder-Club/skills/tree/main/skills/open-agent-teams).
