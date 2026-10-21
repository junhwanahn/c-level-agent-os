# 05. Reporting daemon: design notes

These are the design decisions that held up in daily use. [`scripts/report_daemon.py`](../scripts/report_daemon.py)
implements them as a daemon-lite: one Python file (PyYAML only), Telegram delivery, launchd or systemd.
Your notification channel, schedule and credentials still differ per setup, so they live in the config,
the service definition and environment variables, not in code. Setup: [Install](#install-daemon-lite).

## Two-layer periodic report

| Layer | Reader | Content |
|---|---|---|
| 1 | owner | **Top block rendered from the queue without an LLM** ([03](03-owner-action-queue.md)): owner actions, CEO executions in the last 24h, items due or overdue. Then notable items and the CEO's cross-unit view. Hard length cap. |
| 2 | CEO | each agent's findings (status line + evidence), used as context for layer 1 |

- Run it a fixed number of times per day from a service manager (launchd, systemd), not from an interactive session.
- Ask the model for structured output for layer 2 so the CEO step parses fields, not prose.
- Every input carries its age. The report says "stale" explicitly instead of quietly using old data.
- Do not evaluate state invariants on stored snapshots without checking their age; stale snapshots produce false alarms.
- Messaging APIs have size limits. Split long reports into chunks; never truncate silently.
- If the model call fails (outage, billing, timeout), still send the LLM-free top block with a one-line failure note.

## Scheduled triggers (`trigger_at` in the queue)

Timers inside an interactive session stop when the session goes inactive. Anything whose time matters
moves to the daemon.

| Rule | Design |
|---|---|
| Eligibility | `status: open` items with a valid `trigger_at` and `trigger_prompt` |
| Timezone | required (`Z` or `+hh:mm`); a naive timestamp is invalid and reported, not guessed |
| Polling | short loop (for example 60 s) instead of long sleeps; laptop sleep freezes long timers |
| On-time window | from a few minutes before to a few minutes after `trigger_at` |
| Catch-up | later than that but within a catch-up window (for example 90 min): run and label the delay |
| Missed | beyond the catch-up window: notify once that it did not run; never drop it silently |
| Report collision | if a periodic report is about to start, defer the trigger until after it; a deferred trigger runs even past the catch-up window |
| Dedupe | key = (`id`, `trigger_at`) in an append-only log. Changing `trigger_at` re-schedules |
| Crash safety | write "fired" **before** running. After a restart, an unfinished fire becomes an "interrupted" notice, not a silent re-run |
| Retry | one retry after a short delay, then report failure |
| Tools | off by default (same as the periodic report); `trigger_tools: true` opts in |
| Agent target | `trigger_agent: <name>` runs the prompt as that headless agent in its own directory with its own permissions ([04](04-logic-agents-headless.md)); CEO context is not injected |

## Service manager lessons

- The service's `PATH` is not your shell's. Set the environment (node, python, CLI paths) in the service definition,
  or hooks and the CLI fail with confusing errors.
- On launchd, a changed plist needs unload/load (bootout/bootstrap); a restart (kickstart) keeps the old definition.
- Keep a restart command and the quiet hours (away from report times) in the CEO's notes.

## What the owner sees

One message per report, with owner actions at the top. Scheduled runs post their own short message
(on time / delayed n min / missed / interrupted / failed). Nothing else needs the owner's attention.

## Install (daemon-lite)

How the notes above map to the daemon:

| Note | In `scripts/report_daemon.py` |
|---|---|
| Layer 1, top block | rendered from the queue with the [03](03-owner-action-queue.md) render rules, no model, plus the scheduled triggers of the next 24 h |
| Layer 1, notable items and CEO view | one `claude -p` call in `project_dir` (tools off), JSON reply, prompt in `config/report_prompt.md` |
| Layer 2 | the files under `inputs:` (each agent's status file), passed to that call with their age |
| Input age | header line `Inputs: queue 2h · cto 31h STALE · cfo MISSING`; the prompt says the same |
| Model failure | top block + `Model block unavailable (timeout after 600s); ...` |
| Scheduled triggers | every row of the table above; state in `state/report_daemon/triggers.jsonl` |
| Size limits | split into chunks of at most `max_chunk` UTF-16 units, labelled `(1/3)`; nothing is cut |

Requirements: Python 3.9+ with PyYAML, the Claude Code CLI logged in for the user the service runs as,
a Telegram bot (or `delivery: none` to start with printed and saved reports only). Run every command from the
CEO project root.

`scripts/install.py` does step 1 from your answers (time zone, report times, the absolute `claude_cmd`, delivery),
renders the service file of step 4 into `service/` with your paths, PATH and python filled in, and queues the
Telegram and registration steps as owner actions. Steps 2-5 below are then the check and the registration.

1. **Configure.**
   ```bash
   # TEMPLATE = your clone of this repository
   cp "$TEMPLATE"/templates/report_daemon.yaml "$TEMPLATE"/templates/report_prompt.md config/
   cp "$TEMPLATE"/scripts/report_daemon.py scripts/      # next to logic_agent.py
   mkdir -p state/report_daemon                          # launchd will not create the log directory
   ```
   In `config/report_daemon.yaml` set `timezone` (required, IANA name) and `report_times` (quoted, `"08:00"`),
   set `claude_cmd` to the absolute path printed by `which claude`, and list the files your agents keep current
   under `inputs:`. Create a bot with @BotFather, send it one message, and read your chat id from
   `https://api.telegram.org/bot<token>/getUpdates`. Token and chat id go in environment variables only:
   ```bash
   export REPORT_TELEGRAM_TOKEN='<token>' REPORT_TELEGRAM_CHAT_ID='<chat id>'
   ```
2. **Check the config.** Fix every `error`, read every `warn`:
   ```bash
   python3 scripts/report_daemon.py --check-config
   ```
3. **Dry run.** Prints the report; sends nothing and records nothing:
   ```bash
   python3 scripts/report_daemon.py --dry-run --once --no-model   # top block only, no model call
   python3 scripts/report_daemon.py --dry-run --once              # one model call
   ```
4. **Register the service.** macOS: [`templates/service/com.example.report-daemon.plist`](../templates/service/com.example.report-daemon.plist)
   (launchd user agent). Linux: [`templates/service/report-daemon.service`](../templates/service/report-daemon.service)
   (systemd user unit). Each file starts with the exact install, restart and reload commands. Put `PATH`
   (the directories of `claude` and `node`) and the two Telegram variables in the service definition.
5. **Confirm the first report.**
   ```bash
   python3 scripts/report_daemon.py --once      # sends one report now, outside the schedule
   python3 scripts/report_daemon.py --status    # daemon running?, last and next report, trigger states
   tail state/report_daemon/daemon.log
   ```
   Then wait for the next slot and check that it arrives labelled `on time`.

| Command | What it does |
|---|---|
| `report_daemon.py` | the loop; this is what the service manager runs |
| `--check-config` | validates config, queue, triggers, prompt, CLI path, log directory, environment; never prints a secret |
| `--dry-run --once [--no-model]` | one report now, printed to stdout; no message, no state written, triggers listed but not run |
| `--once` | one report now, sent; recorded as `manual`, does not use up a scheduled slot |
| `--status` | JSON lines: lock held, last and next report, each trigger's state |

Operating notes:

- The queue is re-read on every poll. Config changes take effect after a restart (away from report times).
- `reports.jsonl` and `triggers.jsonl` are append-only; do not edit them. To run a trigger again, change its `trigger_at`.
- On first start, earlier report slots are not reported as missed; scheduled items already past their catch-up
  window are (once). Template examples (`example: true`) are ignored; delete them anyway (`scripts/doctor.py` fails
  while one is left).
- `delivery: none` prints each report and keeps the full text under `state/report_daemon/reports/`; nothing is sent.
- One process, no threads: a long trigger run blocks the loop. A report or trigger that comes due meanwhile is
  run afterwards with the delay labelled (`daemon busy with trigger x`), not marked missed.
- `trigger_agent` runs `python3 scripts/logic_agent.py <name> --json` (plus `--no-tools` unless `trigger_tools: true`)
  with the prompt on stdin; the registry decides model, timeout and permissions.
- Telegram only. For another messenger, replace `telegram_send()`; keep the split-never-truncate rule.
