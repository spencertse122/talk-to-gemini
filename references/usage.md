# Helper usage

Set `SKILL_DIR` to the absolute directory containing this skill's `SKILL.md`.
Run from the project where you want to keep the session registry. The registry
location and executor working directory are independent.

```bash
# Assignment file already prepared by Codex; prompts never become shell code.
python3 "$SKILL_DIR/scripts/agy_delegate.py" start researcher --prompt-file task.txt
python3 "$SKILL_DIR/scripts/agy_delegate.py" wait researcher --turn 1
python3 "$SKILL_DIR/scripts/agy_delegate.py" result researcher --turn 1

# A correction goes to exactly the same conversation.
python3 "$SKILL_DIR/scripts/agy_delegate.py" send researcher --prompt-file correction.txt
python3 "$SKILL_DIR/scripts/agy_delegate.py" wait researcher --turn 2
python3 "$SKILL_DIR/scripts/agy_delegate.py" result researcher --turn 2
```

Use the turn IDs actually returned by start/send. Every turn's result remains
retrievable after subsequent turns. `result` writes the exact UTF-8 response to
stdout, including its original trailing newline behavior.

```bash
# From another directory, keep using the original state root from the receipt.
python3 "$SKILL_DIR/scripts/agy_delegate.py" --state-dir /absolute/project/.agy-delegate status
python3 "$SKILL_DIR/scripts/agy_delegate.py" --state-dir /absolute/project/.agy-delegate peek researcher --lines 30
```

## Commands

Global option: `--state-dir PATH` before the command, default `.agy-delegate`.

| Command | Additional arguments |
|---|---|
| `start NAME` | `--prompt-file PATH` (otherwise stdin), `--cwd PATH`, `--model MODEL`, `--agy EXECUTABLE`, `--layout auto\|session` |
| `send NAME` | `--prompt-file PATH` (otherwise stdin) |
| `wait NAME` | Required `--turn N`; optional `--timeout SECONDS` (default 60, zero for an immediate check) |
| `result NAME` | Required `--turn N` |
| `status [NAME]` | Omit the name to list all records in this state root |
| `peek NAME` | `--lines N` (default 40) |
| `stop NAME` | Preserves all records |
| `resume NAME` | `--layout auto\|session`; never sends or replays a prompt |

The default executable is `agy`; `--agy` selects another installation, not a shell
command string. Model defaults to `gemini-3.8-flash-high`. To see available models,
run `agy models`. Model and working directory are fixed for a saved executor.

## Receipts and exit codes

Control commands print JSON. A submission receipt identifies `name`, `session_id`,
`conversation_id` (initially null until agy identifies it), `turn_id`, `state_dir`,
`model`, `cwd`, and status. Wait returns the selected turn's status and the saved
response path, never the response content or progress events. Use the state root
and session name to address a worker; session_id identifies it in your task ledger.

- Exit 0: command succeeded; for wait/result, the turn returned `SUCCESS`.
- Exit 1: command failure or a non-success turn. Inspect JSON diagnostics.
- Exit 2: command-line usage error.
- Exit 124 from wait: still running when the wait budget expired.

`ERROR`, `CANCELED`, `INTERRUPTED`, `INVALID`, and `WAITING` are not treated as
successful work. An agy `WAITING` result requires inspection and an explicit
follow-up. For a failed turn with response text, result emits that text to stdout,
diagnostics to stderr, and exits 1. Records are never silently summarized.

## tmux and runtime records

Inside tmux, auto layout creates a horizontal side pane without moving focus.
Outside tmux, or with `--layout session`, it creates a detached session. Existing
tmux configuration/server selection is respected. Each worker records its exact
pane ID, server socket, and ownership token in `NAME/tmux.json`; stop only closes
that owned pane. Use `tmux -S SOCKET list-sessions` and then
`tmux -S SOCKET attach-session -t SESSION` to view a detached worker.

The pane is a readable transcript, not the native agy TUI. Send messages through
the helper. Terminal control characters are escaped for display; saved prompts
and final responses retain their exact text.

Under the state root, each named session has:

- `config.json`: model, workspace, executable, stable session ID.
- `state.json`: current worker state, turn ID, agy conversation ID.
- `turns/000001.prompt.txt`: exact input.
- `turns/000001.response.txt`: exact final answer, when available.
- `turns/000001.json`: durable completion/status record, written after the answer.
- `events.log`, `stderr.log`, `transcript.log`: raw protocol, diagnostics, display.
- Runtime locks and `launch.json`/`tmux.json` for addressing the owned worker.

The root contains a local `.gitignore`; files are private to the current OS user.
Sockets use a short private directory under `/tmp/agy-delegate-UID/`. Records and
logs have no automatic retention cutoff. Keep them while resume/history is needed.
tmux separates processes and conversations; it does not isolate shared files.

## Troubleshooting and recovery

Check status, then peek or stderr.log. A late wait still works because completion
is a durable record. If the worker disappears mid-turn, status/wait records
`INTERRUPTED`. The prompt may already have caused actions; inspect the record and
artifacts before continuing. Resume uses the exact saved conversation ID and
rejects a different ID reported by agy. Provider context restoration is confirmed
when agy emits its ID; resume itself does not spend a model turn verifying history.

If no conversation ID was saved, recovery cannot preserve context: choose a new
session deliberately. If agy's stored history is missing, restore it or start a
new named session with an explicit context handoff. No fallback creates a fresh
conversation under the old name.

Startup validates flags from installed `agy --help`; it does not prove every
protocol behavior. Compatibility checks and live tests cover the installed
version. Protocol records/control requests larger than 16 MiB fail explicitly.
Tools already dispatched by agy may have external effects that stopping the local
process cannot reverse.

## Validation and source references

From this repository: `python3 -m unittest discover -s tests -v` runs deterministic
tests through real tmux with a fake agy executable. The opt-in live smoke script
uses the existing agy login and consumes model usage; see its `--help`.

- [Reference skill and helper](https://github.com/AI-Builder-Club/skills/tree/main/skills/open-agent-teams)
- [agy headless protocol](https://antigravity.google/docs/cli/headless)
- [Gemini CLI reference](https://geminicli.com/docs/cli/cli-reference/) — a different executable with different flags
- [tmux manual](https://man.openbsd.org/tmux.1)
- [tmux documentation supplied during design](https://tmux.app/doc/)
