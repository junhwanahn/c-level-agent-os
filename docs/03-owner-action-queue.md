# 03. Owner action queue

One YAML file holds every dated item, for the owner and for the CEO. The reporting layer renders
the top of every periodic report from it **without an LLM**: no judgment, no softening, zero cost.

Template: [`templates/owner_action_queue.yaml`](../templates/owner_action_queue.yaml) -> copy to `config/owner_action_queue.yaml`.

## Schema

| Field | Required | Values | Notes |
|---|---|---|---|
| `id` | yes | unique string | stable; changing it creates a new item |
| `due` | yes | `"YYYY-MM-DD"` | for an approval request, the date it must be ready, not the event date |
| `owner` | yes | `human` / `ceo` / agent name | who has to act |
| `title` | yes | one line | what, plus where the prepared material lives |
| `status` | yes | `open` / `done` / `dropped` | |
| `done_at` | when done | `"YYYY-MM-DD"` | drives the "executed in the last 24h" block |
| `action` | queue | `external` / `local` / `facts` | external = leaves the company; local = needs the owner's hands but stays inside (terminal, OAuth consent); facts = only the owner knows |
| `ready` | queue | `true` | only when draft, attachments and click path are prepared |
| `prep` | recommended | list of paths/steps | what the owner opens to act |
| `trigger_at` | optional | ISO time **with timezone** | scheduled run by the daemon ([05](05-reporting-daemon.md)) |
| `trigger_prompt` | with trigger_at | text | instruction to run |
| `trigger_agent` | optional | registry name | run as that headless agent ([04](04-logic-agents-headless.md)) |
| `trigger_tools` | optional | `true` | tools are off by default for scheduled runs |

## Render rules (top block of every report)

1. **Owner actions (n)**: items with `owner: human`, `status: open`, `ready: true`, sorted by `due`
   (malformed dates last). `external` and `local` items first; `facts` items listed separately as
   "Pending fact checks". Each line: title, due date with D-n / D+n, action type.
2. **Decided and executed by CEO (last 24h)**: items whose `done_at` is today or yesterday, newest first,
   capped (for example 8, then "and n more"). Items owned by the human are tagged "owner done" so they
   are not mixed up with CEO execution.
3. **Due soon or overdue (D-2)**: every other `open` item with `due <= today + 2 days`, oldest first.
   Overdue items stay visible as D+n until closed. Never filter them out silently.

Failure policy: if the file is missing or cannot be parsed, render an empty block and log a warning;
one bad file must not kill the whole report. Check the warning log.

Reference implementation of the queue selection (about 10 lines):

```python
from datetime import date

def _d(raw):
    try:
        return date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None

def action_queue(items):
    """Owner action queue: owner human, status open, ready true; sorted by due (malformed last)."""
    q = [i for i in items
         if i.get("owner") == "human" and i.get("status") == "open" and i.get("ready") is True]
    return sorted(q, key=lambda i: _d(i.get("due")) or date.max)
```

## Example output

```
> Owner actions 3 (actions 2, fact checks 1)
- Send reply to partner X (draft final, 6 lines) - 2026-01-15 (D-0) - external
- Approve OAuth consent for the video upload API (command prepared) - 2026-01-16 (D-1) - local
> Pending fact checks 1
- Did we already promise customer Y a delivery date? - 2026-01-17 (D-2) - facts

> Decided and executed by CEO (24h) 1
- Re-run analysis B with hypothesis 2 (hypothesis 1 rejected) (01/14)

> Due soon or overdue (D-2) 1
- [D-1] ceo - Weekly review: block budget per open topic, close or extend (2026-01-16)
```

## Discipline

- `ready: true` means executable in minutes by someone who has not read the conversation.
- The queue is the only place the owner looks for "what do I need to do". If it is not in the queue,
  the owner does not need to do it.
- Mark `done` with `done_at` the moment it is done; the next report then shows it as executed.
