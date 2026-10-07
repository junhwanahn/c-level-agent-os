# 05. Reporting daemon: design notes

No code here on purpose: the daemon is where your notification channel, scheduler and secrets live,
and those differ per setup. These are the design decisions that held up in daily use.

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
