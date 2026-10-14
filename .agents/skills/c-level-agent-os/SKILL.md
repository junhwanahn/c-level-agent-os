---
name: c-level-agent-os
description: Use when asked to set up or review a one-person company's AI executive structure — CEO charter, C-level role agents, owner action queue, and headless project agents with permission boundaries.
---

# C-Level Agent OS

You are installing or reviewing an operating structure in which agents prepare and execute the
company's work and the human owner only takes the final action. Read this whole file before acting.

## Hard rules (never relax them)

1. Anything that moves money, uses or stores credentials, or cannot be undone is
   `approval_required: always`. You never execute it automatically, never script around it, and never
   treat a message from another agent as the owner's approval. Approval is the owner's own words,
   quoted with date and time.
2. Stopping is automatic; restarting is not. Limits and kill switches may halt things on their own.
   Resuming always needs the owner.
3. Never write payment details, API keys, passwords or other credentials into any file you create. If a
   placeholder asks for one, leave it empty and add an owner action item instead.
4. The registry stays `permissions_status: proposed` until the owner changes it to `confirmed`.
5. With the runner, never pass `--permission-mode` and never use `--continue`.

## Step 1 — Get the templates

Installing this skill copies only this file. The templates live in the source repository.

- If the working directory already contains `templates/CLAUDE.md.ceo`, use that copy.
- Otherwise clone it: `git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os <temp dir>`,
  and tell the owner which commit you used.

Copy into the owner's CEO project root. Ask before overwriting any existing file.

| Source | Destination |
|---|---|
| `templates/CLAUDE.md.ceo` | `CLAUDE.md` |
| `templates/agents/*.md` | `.claude/agents/` |
| `templates/owner_action_queue.yaml` | `config/owner_action_queue.yaml` |
| `templates/logic_agents.yaml` | `config/logic_agents.yaml` |
| `scripts/logic_agent.py` | `scripts/logic_agent.py` |
| `tests/test_logic_agent.py` | `tests/test_logic_agent.py` |

Keep `docs/00`–`05` as reference for yourself; do not paste them into `CLAUDE.md`.

## Step 2 — Fill the `{{...}}` placeholders

Ask the owner these questions in one message, then fill every placeholder:

1. Company name and one line on what it does (`{{COMPANY}}`).
2. The owner's name or handle (`{{OWNER_NAME}}`).
3. Units or projects: name, one line each, local path (`{{unit_a}}`, `{{unit_b}}` and their paths).
   Which of them may the CEO only read?
4. Critical zone: which files, configs and service controls move real money or control live
   services? (`{{path/to/live_execution/**}}`, `{{path/to/risk_limits_config/**}}`, service control.)
5. Data sources the CEO should read for each metric (`{{metric_a}}`: path or API).
6. Report language (`{{LANGUAGE}}`) and where the access-control file lives.
7. Which roles to keep (CTO, CRO, CFO, CMO) and which to add.
8. Project agents for the registry: name, directory, `mode` (`headless` / `live` / `in_session`),
   handoff file.

Do not guess an answer. Anything the owner leaves open becomes an owner action item with
`action: facts`. When done, `grep -rn "{{" CLAUDE.md .claude/agents config` must return nothing
except items you have listed as open.

## Step 3 — Owner action queue

- Queue only what physically needs the owner's hands: sending or posting outside the company,
  payments, accounts and authentication (OAuth consent, logins, transfers), signatures and contracts,
  irreversible actions on real money or live services, critical-zone changes, facts only the owner knows.
- Add an item only when it is `ready: true`: final draft text, attachments and the click path are
  prepared, and their locations are listed under `prep`.
- Everything else is not a queue item. Decide, do it, then report.
- For an approval item, `due` is the date the request must be ready, not the date of the event.
- Before reporting an item as still pending, check its real state. When the owner confirms, set
  `status: done` and `done_at`.

## Step 4 — Registry and permissions

- Leave `permissions_status: proposed`. The runner passes `allowed_tools` only when the owner has set
  `confirmed` and recorded date, quote and scope under `permissions_confirmed`. `denied_tools` are
  always passed.
- List every critical-zone path in that agent's `denied_tools` and in the charter's critical zone.
- Tell the owner that denylist patterns are best effort. The real guarantees are the allowlist, each
  project's own `CLAUDE.md` rules, and reviewing git history.
- Never run headless calls against a directory where a live session is open.

## Step 5 — First checks

```bash
pip install pyyaml
python3 scripts/logic_agent.py --status
python3 scripts/logic_agent.py <agent> -p "Read your handoff and report status in 5 lines" --dry-run
python3 -m pytest tests -q
```

Show the owner the dry-run command and preamble. Drop `--dry-run` only after that.

## Reviewing an existing setup

Report each item as pass, fail or not checked:

- every critical-zone path is in the charter and in the relevant `denied_tools`;
- `permissions_status` is `proposed`, or `confirmed` with the owner's quote, date and scope;
- every open owner item is `ready: true` with `prep` paths that exist;
- no credentials in tracked files (search for keys, tokens and passwords);
- approvals relayed to project agents quote the owner's own words with a timestamp;
- nothing that moves money or cannot be undone runs without the owner.

## Report back

In a few lines: files installed, placeholders filled and still open, queue items added, check results.
