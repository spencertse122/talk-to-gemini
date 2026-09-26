# talk-to-gemini v1 design

Approved in conversation; implementation requested on 2026-09-26.

Codex (Astra or Sol) orchestrates persistent Gemini executors in tmux. Coding
assignments can run concurrently in distinct sessions, with revisions sent to
the original conversation. In simulations, Codex plays the human and Gemini is
the agent under test. The transport never injects executor instructions, summary
requirements, or simulation evaluation notes into prompts.

## Interface and behavior

A Python 3.10+ standard-library helper provides start, send, wait, result, status,
peek, stop, and resume. A worker in an owned tmux pane holds an agy subprocess
open using stream-json input/output. A private Unix socket carries helper requests.
Start/send return session and turn IDs; only one turn may run in a session.
Different sessions run independently. Prompts arrive from files or stdin and are
preserved literally. Revisions use the same agy conversation ID.

The CLI result event finishes a turn, not the assignment. Save the full response
before atomically marking the turn complete. Wait returns compact status only;
result returns the complete final response. Progress and diagnostics stay on disk
and in the pane; peek exposes recent activity on demand. Wait timeout never stops
work. Codex judges completion and continues the loop until satisfied or blocked.

State lives under .agy-delegate/ in the invoking workspace, with a configurable
state root. State includes session/conversation IDs, configuration, prompts, raw
events, diagnostics, responses, and per-turn status. Stop preserves all records.
Resume reopens the saved conversation explicitly by ID. A lost worker's active
turn becomes interrupted; no uncertain prompt is automatically replayed. A
conversation mismatch on resume is an error. An active turn may finish after Codex
disconnects, but further turns await Codex.

## Defaults

- Skill name talk-to-gemini; explicit delegation only.
- Gemini model gemini-3.8-flash-high, overridable at start.
- Automatic agy tool approval within host execution permissions.
- Existing agy authentication; working directory defaults to invoking directory.
- Detached horizontal split inside tmux, preserving focus; detached session outside.
- Linux first, Python/tmux/agy installed; validate capabilities without installing.
- No live redirection, automatic retries, unattended orchestrator, or simulation engine.

## Acceptance

Multiple revision rounds preserve history; concurrent sessions route independently;
simulated human messages contain no helper-added instructions; literal multiline
prompts and full responses survive; late waits work; failures/timeouts are explicit;
stop/resume preserves identity and avoids replay; logs do not enter normal command
results. Test deterministic behavior with a fake agy executable through real tmux,
then verify the installed Gemini protocol with small live conversations.

## References

- https://github.com/AI-Builder-Club/skills/tree/main/skills/open-agent-teams
- https://antigravity.google/docs/cli/headless
- https://geminicli.com/docs/cli/cli-reference/ (different CLI; do not copy its flags)
- https://man.openbsd.org/tmux.1
- https://tmux.app/doc/

Keep the reference's small command interface and per-turn records. Implement new
agy-specific code; do not inherit model-written sentinels or terminal scraping.
