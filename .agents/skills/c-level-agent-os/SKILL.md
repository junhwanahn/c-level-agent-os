---
name: c-level-agent-os
description: Use when asked to set up or review a one-person company's AI executive structure — CEO charter, C-level role agents, owner action queue, reporting daemon, and headless project agents with permission boundaries.
---

# C-Level Agent OS

You are installing or reviewing an operating structure in which agents prepare and execute the
company's work and the human owner only takes the final action. Read this whole file before acting.
You install with the same script the owner would run (`scripts/install.py`) and you show the owner the
check it ends with (`scripts/doctor.py`). Do not copy or fill templates by hand.

## Hard rules (never relax them)

1. Anything that moves money, uses or stores credentials, or cannot be undone is
   `approval_required: always`. You never execute it automatically, never script around it, and never
   treat a message from another agent as the owner's approval. Approval is the owner's own words,
   quoted with date and time.
2. Stopping is automatic; restarting is not. Limits and kill switches may halt things on their own.
   Resuming always needs the owner.
3. Never write payment details, API keys, passwords or other credentials into any file you create, the
   answers file included. The Telegram token and chat id go into the installed service definition, by the
   owner; the installer already queued that as an owner action.
4. The registry stays `permissions_status: proposed` until the owner changes it to `confirmed`.
5. With the runner, never pass `--permission-mode` and never use `--continue`.
6. A protected zone is never decided by you. `no_protected_zone: true` only when the owner said, in their
   own words, that nothing in that project moves money or controls a live service. Quote them in your report.

## Step 1 — Get the installer

Installing this skill copies only this file. The installer and templates live in the source repository.

- If the working directory already contains `scripts/install.py` and `templates/`, use that copy.
- Otherwise clone it: `git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os <temp dir>`,
  and tell the owner which commit you used.
- The destination is the owner's CEO project folder (`--dest`). Ask before using a folder that already has a
  `CLAUDE.md`; the installer keeps existing files unless `--force`, and keeps a `.bak-<time>` copy when it replaces one.
- PyYAML is the only dependency. If `python3 -c "import yaml"` fails, tell the owner and run
  `python3 -m pip install --user pyyaml` (macOS has no `pip` command; inside a virtual environment drop `--user`).

## Step 2 — Ask the owner, in one message

Ask these questions together and wait for the answers. Do not guess an answer; offer the default in brackets.

1. Company name, and one line on what it does.
2. The owner's name.
3. Report language [English]; time zone [the system's, e.g. Asia/Seoul]; report times [08:00, 20:00].
4. Messenger: `telegram`, or `none` for now (reports only printed and saved).
5. Roles to install [cto, cro, cfo, cmo].
6. Projects the CEO should manage: for each one a short name (letters, digits, `_`, `-`), one line, its folder,
   how the owner works with it (`headless` = the CEO calls it, `live` = the owner's own daily terminal,
   `in_session` = a helper inside the CEO session), and its handoff file [HANDOFF.md].
7. **For each project: its protected zone** — the files or folders that move real money or control live services
   (payments, pricing, live trading, send jobs, service controls). Paths relative to the project folder, `~`, or absolute.
   If the owner says there are none, ask them to confirm it in their own words.
8. Optional: live services agents must never start, stop or restart; data sources per metric (name = path or API);
   the owner's own sentence on what the CEO may do without asking.

## Step 3 — Run the installer

Write the answers to a YAML file **outside** the CEO folder (a temp dir; it contains no secrets). The schema is in
the docstring of `scripts/install.py`; minimal example:

```yaml
company: "Saebyeok Data Lab"
owner: "Mina Seo"
timezone: "Asia/Seoul"
report_times: ["08:00", "20:00"]
messenger: telegram
projects:
  - name: newsletter
    description: "paid newsletter"
    path: "~/projects/newsletter"
    mode: headless
    handoff: HANDOFF.md
    protected: ["billing/**", "config/pricing.yaml"]
```

```bash
python3 <clone>/scripts/install.py --answers <answers.yaml> --dest <CEO folder>
```

Show the owner the `== doctor ==` lines and the `== install summary ==` **verbatim**, including any
`NO PROTECTED ZONE` box. The install is done only when the summary says `STATUS: COMPLETE` (exit code 0).
If it says `NOT COMPLETE`, each listed line is the fix: ask the owner for the missing fact, update the answers
file, run the installer again (`--force` only for files the owner agreed to replace). Never edit a placeholder
away by hand to make the check pass.

## Step 4 — First checks with the owner

Run from the CEO folder and show the output:

```bash
python3 scripts/report_daemon.py --once --dry-run          # the report the owner would receive; nothing is sent
python3 scripts/logic_agent.py --status
python3 scripts/logic_agent.py <agent> -p "Read your handoff and report status in 5 lines" --dry-run
```

In the dry run, point at `--disallowedTools`: it must list the owner's real protected paths as `Edit(//absolute/...)`.
Drop `--dry-run` only after the owner has seen it. Tell the owner: until they confirm the allowlist, each project
agent has only the tools of its own `.claude/settings*.json`, so expect "Needed permission: …" at the end of its
first report. The remaining owner steps (Telegram bot, service registration, allowlist review) are already in
`config/owner_action_queue.yaml` with their commands; point to README section 3 for the service.

## Owner action queue (ongoing)

- Queue only what physically needs the owner's hands: sending or posting outside the company,
  payments, accounts and authentication (OAuth consent, logins, transfers), signatures and contracts,
  irreversible actions on real money or live services, critical-zone changes, facts only the owner knows.
- Add an item only when it is `ready: true`: final draft text, attachments and the click path are
  prepared, and their locations are listed under `prep`.
- Everything else is not a queue item. Decide, do it, then report.
- For an approval item, `due` is the date the request must be ready, not the date of the event.
- Before reporting an item as still pending, check its real state. When the owner confirms, set
  `status: done` and `done_at`.

## Registry and permissions (ongoing)

- Leave `permissions_status: proposed`. The runner passes `allowed_tools` only when the owner has set
  `confirmed` and recorded date, quote and scope under `permissions_confirmed`. `denied_tools` are always passed.
- Path rules: `Edit(//abs/path/**)` (two slashes = absolute; one slash is relative to the settings source).
  Use `Edit`, not `Write`: Claude Code never consults `Write(path)` rules. The runner refuses real calls while a
  denied rule is still a placeholder.
- Tell the owner that denylist patterns are best effort. The real guarantees are the allowlist, each
  project's own `CLAUDE.md` rules, and reviewing git history.
- Never run headless calls against a directory where a live session is open.

## Reviewing an existing setup

Run `python3 scripts/doctor.py` in the CEO folder and show its output verbatim. Then report each item as pass,
fail or not checked:

- doctor has no ✗ line;
- every critical-zone path is in the charter, in the relevant `denied_tools`, and in `.claude/settings.json` deny;
- `permissions_status` is `proposed`, or `confirmed` with the owner's quote, date and scope;
- every open owner item is `ready: true` with `prep` that a person who has not read the conversation can follow;
- no credentials in tracked files (search for keys, tokens and passwords);
- approvals relayed to project agents quote the owner's own words with a timestamp;
- nothing that moves money or cannot be undone runs without the owner.

## Report back

In a few lines: the commit used, `STATUS` and the doctor counts, projects with their protected paths (and any
NO PROTECTED ZONE, with the owner's quote), owner actions now in the queue, what is still open.
