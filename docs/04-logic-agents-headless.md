# 04. Project agents, called headless

Each business unit keeps its own project directory with its own `CLAUDE.md`, `.claude/settings*.json`,
memory, MCP servers and critical-zone boundary. What changes is the delivery channel:
instead of a live terminal per project plus messages between sessions, the CEO agent runs
`claude -p` **in that directory** and reads the final answer as the report.

- Registry (single source of truth): [`templates/logic_agents.yaml`](../templates/logic_agents.yaml) -> `config/logic_agents.yaml`
- Runner: [`scripts/logic_agent.py`](../scripts/logic_agent.py) (Python 3.9+, PyYAML)
- Tests: [`tests/test_logic_agent.py`](../tests/test_logic_agent.py)

## Registry schema

| Key | Meaning |
|---|---|
| `permissions_status` | `proposed` or `confirmed`. Only `confirmed` passes `allowed_tools`. |
| `permissions_confirmed` | `date`, `by`, `quote` (owner's words, verbatim), `scope` |
| `defaults.model / effort / timeout_sec / rotate_mb` | per-call defaults; agents may override |
| `defaults.allowed_tools / denied_tools` | common rules merged in front of each agent's own |
| `defaults.preamble_extra` | extra lines appended to every agent's preamble |
| `agents.<name>.path` | working directory (absolute, `~`, or relative to the CEO project root) |
| `agents.<name>.role` | one line; goes into the preamble |
| `agents.<name>.handoff` | file the agent reads first on a fresh or rotated session |
| `agents.<name>.mode` | `headless` / `live` / `in_session` |
| `agents.<name>.notes` | what the CEO must remember when calling it |

## Three modes

| Mode | Who drives it | Headless call |
|---|---|---|
| `headless` | the CEO agent, via the runner | allowed |
| `live` | the owner, in a terminal, daily | refused (two processes on one transcript) |
| `in_session` | the CEO session itself, as a subagent | refused |

`--force-mode` overrides the refusal. Do not make it a habit.

## Permission principles

1. **The allowlist is the guarantee.** A headless run cannot answer a permission prompt, so any tool
   outside the directory's settings allowlist is denied automatically. That is what protects the critical zone.
   The runner never passes `--permission-mode` or `--dangerously-skip-permissions` (a test pins this).
2. **Widening is the owner's call.** `allowed_tools` in the registry is passed only after the owner confirms
   (`permissions_status: confirmed`, with date and quote).
3. **Restricting is the CEO's call.** `denied_tools` is always passed.
4. **The denylist is best effort.** Rules are string patterns and can be bypassed (`git -C`, `find -exec`,
   writing through `python3`). Real guarantees: the allowlist, the agent's own `CLAUDE.md` rules, and git history review.
5. **Per-call extras are logged.** `--allowed-tools` adds a rule for one call and is recorded in the call log.

## Session continuity and rotation

- The runner stores each agent's last headless session id in `state/logic_agent_sessions.json` and resumes it.
- If the transcript grows beyond `rotate_mb`, the next call starts a fresh session and the preamble tells the agent
  to read its handoff block and `git log -5` first. Long-lived transcripts make every turn expensive.
- If a resumed session no longer exists, the runner retries once with a fresh session.
- `--continue` is never used: it takes the most recent conversation in the directory, which may be the owner's live one.

## Never live and headless in the same directory

Two processes writing one transcript corrupt the context for both. The runner holds a per-agent file lock,
but it cannot see a terminal the owner opened by hand. Cutover per agent:

1. the live session writes and commits a handoff block
2. the owner closes that terminal
3. headless calls only from then on (set `mode: headless`)

## How approval reaches a headless agent

A headless agent cannot receive the owner's typing. The owner types the approval into the CEO session;
the CEO quotes the words and the time in the call prompt. General instructions via the CEO need no quote.
External publication, critical-zone edits and irreversible actions always do.

## Operational details the runner handles

- Strips `CLAUDECODE` / `CLAUDE_CODE_*` from the child environment so a call from inside a session is not blocked as nested.
- Starts the child in its own process group; on timeout it kills the whole group (ssh, node children) and returns rc 124.
- Appends one JSON line per call to `state/logic_agent_calls.jsonl`: agent, model, session reason, cost, duration, errors.
- `--dry-run` prints the exact command and preamble. `--status` lists sessions and transcript sizes.

Check flag names against `claude --help` for your CLI version before the first real call.
