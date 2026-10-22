# C-Level Agent OS

### One operating structure for a one-person company run by an AI executive team.

**Agents prepare everything up to the last click. You take only that click — and nothing that moves money is theirs to touch.**

[![MIT license](https://img.shields.io/badge/license-MIT-f4c430?style=flat-square)](LICENSE)
[![skills.sh](https://skills.sh/b/junhwanahn/c-level-agent-os)](https://skills.sh/junhwanahn/c-level-agent-os)

[한국어 README](README.ko.md)

A CEO agent, C-level role agents (four templates included: CTO, CRO, CFO, CMO — add a strategy or other
role as you need), project agents called headless, and one queue for the actions only the human owner can take.
Built for Claude Code. Built and used by a one-person systematic trading firm; everything domain-specific has been removed.

## 1. What changes after you install

Twice a day (08:00 and 20:00 by default) one message arrives, by Telegram or in a file. The next morning it looks like this:

```
[Report 2026-01-15 08:00 KST] on time
Inputs: queue 2h · cto 5h · cfo 31h STALE

> Owner actions 3 (actions 2, fact checks 1)
- Send reply to partner X (draft final, 6 lines) - 2026-01-15 (D-0) - external
- Approve OAuth consent for the video upload API (command prepared) - 2026-01-16 (D-1) - local
> Pending fact checks 1
- Did we already promise customer Y a delivery date? (yes/no + date) - 2026-01-17 (D-2) - facts

> Decided and executed by CEO (24h) 1
- Re-run analysis B with hypothesis 2 (hypothesis 1 rejected) (01/14)

> Due soon or overdue (D-2) 1
- [D-1] ceo - Weekly review: block budget per open topic, close or extend (2026-01-16)

> Summary: Two owner actions are due; the partner reply is final and waiting for your send
> Notable 2
- Analysis B re-run with hypothesis 2 is done; the result is in its handoff - (cto, 5h)
- The cfo input is older than its limit, so no cost statement is made - (cfo, STALE)
> CEO view
Nothing outside the queue needs you. Next CEO step: the weekly review tomorrow morning.
> Treated as stale: cfo
```

- The top block comes straight from one file, `config/owner_action_queue.yaml`, **without a model**: nothing is
  softened or re-ranked. It is your whole to-do list. If something is not there, you do not need to do it.
- Each owner action is prepared up to the last click (draft, attachments, click path). You send, pay, sign or answer.
- Everything else the CEO agent decides, executes and reports in the second block, with the age of every input.
- On the first day you see only the setup steps the installer put in the queue. Input lines such as `cto 5h` appear
  once you list your agents' status files under `inputs:` in `config/report_daemon.yaml`. Until then (having no number
  file at the start is normal) the second block runs in plan mode: from your company line, the handoff notes, the
  open queue items and the first plan it writes today's most urgent step for you, each role's state and next action,
  and its questions.
- Every report is also saved as a file under `state/report_daemon/reports/`, whatever the delivery. With delivery
  `none` (the default) nothing is sent and that file is the report; with `telegram` it is the copy you can reread.
- Your computer makes the report, so it must be on and awake at the report time (see the box in section 2).

## 2. Quick start (no coding)

You need a Mac or Linux computer, [Claude Code](https://code.claude.com/docs/en/setup) installed and signed in
(type `claude` in Terminal; if it asks, type `/login` and follow the browser steps), and Python 3 (on a Mac, the first
`python3` or `git` offers to install Apple's command line tools: accept). Open Terminal and type these commands:

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os my-company
cd my-company
python3 scripts/install.py
python3 scripts/first_plan.py
python3 scripts/report_daemon.py --once --dry-run
```

These five lines do not yet bring tomorrow's report by themselves: a service makes the reports, so finish with
section 3 (register the service).

Time and usage: the first plan takes 5-15 minutes, each of the 2 daily reports 1-3 minutes; all of it uses your own
Claude subscription.

1. **Get it.** Downloads the template into a new folder `my-company`; that folder becomes your company's CEO folder.
   The clone creates the folder: do not create it with mkdir first (an empty one does no harm).
   No git? Download the ZIP from GitHub, unzip it, then open Terminal in that folder (see the box below).
2. **Install.** 12 questions (7 more for each work folder you add), about 3 minutes. Every question shows its
   default in [brackets] or says what Enter does, so pressing Enter all the way through works: you get an
   installation without work folders and can add them later by running the installer again. Ctrl+C stops at any
   time; no installation file is written before the last question. If PyYAML (the only dependency) is missing, it
   offers to install it. Pressing Enter at *How should reports reach you?* keeps the default `none`: reports are
   saved as files and no bot token is needed. Answer `telegram` there (or later:
   `python3 scripts/install.py --reconfigure delivery`) to receive them by Telegram. It ends with a check and one
   line: `STATUS: COMPLETE`, or `NOT COMPLETE` with the exact things to fix. The summary is read back from the files
   it wrote, and it always prints the service commands for section 3 with your paths filled in, also when it says
   `NOT COMPLETE` (fix the listed items first; registering the service can be done right away).
3. **First plan.** The CEO agent reads your files (company line, handoff notes, queue) and writes a two-week plan to
   `advisory_drafts/<date>-first-plan.md`: where the company stands, three lines per role, what only you can do, and
   its questions for you. Your first 3 to 5 owner items go straight into the queue, `ready: false` until the CEO has
   prepared them (a question for you is ready at once). It may write only that file and the queue, never the
   protected zone. About 5-15 minutes; almost no output while it runs is normal. It ends with the file path, the
   number of queue items added and the next command.
4. **See tomorrow's report now.** Prints the report you would receive (one model call; nothing is sent or recorded).
   Add `--no-model` to see only the top block. If it says `Not logged in`, type `claude`, then `/login`, and try again.

Running the installer again in the same folder asks first: 1 keep everything (only check it and show what the files
say), 2 answer again and overwrite (each replaced file keeps a `.bak` copy; `HANDOFF.md` and the queue are never
replaced), 3 stop. To change only how reports reach you: `python3 scripts/install.py --reconfigure delivery`.
To change the time zone or report times: `python3 scripts/install.py --reconfigure schedule`.

**Protected zone.** For each project the installer asks which files or folders move real money or control live
services (payments, pricing, live trading, send jobs). Each one becomes an absolute deny rule for that project's agent
and for the CEO session. A path that does not exist is refused unless you confirm it (a typo would leave the real
folder unprotected), and the doctor fails on a missing protected path you did not confirm. If you leave a project's
zone empty, you must type `none` to confirm, and the summary shows **NO PROTECTED ZONE** in a box. Without any
project there is nothing to protect yet: the installer skips this step with one line and asks when you add a project.
Each live service name you give becomes a deny rule for the `launchctl` and `systemctl` commands that name it.
A template placeholder left anywhere means the install is not complete.

**Without questions.** `python3 scripts/install.py --answers answers.yaml` reads the answers from a file. Its format
is the one of `config/install_answers.yaml`, which every install writes (`python3 scripts/install.py --help` shows a
full example). `--answers` counts only the paths listed under `allow_missing` as checked: if any other protected path
does not exist, the installer stops before it writes any file.

Then open Claude Code in this folder: type `claude`, press Enter, and type `recap` (for example: "recap, please").
From now on your to-do list is `config/owner_action_queue.yaml` (open it with `open -e config/owner_action_queue.yaml`);
the installer already put your remaining setup steps there. When you finish one, tell the CEO in Claude Code (for
example "done: register the report service"); it sets `status: done` and `done_at`.
Run `python3 scripts/doctor.py` any time to re-check the installation (✓ ok, ℹ for your information, ⚠ read it,
✗ fix it). It checks that
Claude Code is signed in without calling a model; `python3 scripts/doctor.py --check-login` also sends Claude one short
test message, after asking you.

- **Project agents start with few tools.** Until you confirm the proposed allowlist, each one has only the tools of its
  own folder's `.claude/settings*.json`. The queue item says what to do: check which tools the project agents may use
  by reading the list the installer proposed (`allowed_tools` in `config/logic_agents.yaml`); if it is fine, change
  `permissions_status` to `confirmed` (and fill `date`, `by`, `quote`, `scope` under `permissions_confirmed`: the
  doctor fails while one is empty), or tell the CEO agent "confirm the allowlist" and it records your words, the
  date and the scope (example in the box below).
- **Usage.** Project agents run with `model: opus` and `effort: high` by default (`defaults:` in
  `config/logic_agents.yaml`); lower them if your plan is small.
- **Your own history.** The company folder starts as a copy of the template, so git still points at the template
  repository. To keep your records from ever being pushed there: `git remote rename origin template`.

**Or let your agent do it.** This needs Node.js (check with `npx --version`; if it is not found, install Node.js from
nodejs.org first). In the company folder run `npx skills add junhwanahn/c-level-agent-os`. If it asks, choose Claude
Code and the **Project** scope (Project writes only into this folder: the skill file, a `.claude/skills` link and
`skills-lock.json`; Global writes to your home folder). Then ask your agent: *"Set up the C-Level Agent OS for my
company."* It asks you the same questions, runs the same installer, and shows you the check.

> **Words used here**
>
> - **Absolute path**: the full address of a folder, starting with `/` (such as `/Users/you/shop`), or with `~` for
>   your home folder (`~/shop`). To type one, drag the folder from Finder into the Terminal window.
> - **Open Terminal in a folder**: type `cd` and a space, drag the folder from Finder into the window, press Enter.
> - **Ctrl+C**: hold Control and press C. It stops the command that is running.
> - **Node.js**: needed only for `npx skills add`, not for the installer.
> - **Telegram token and chat id**: (1) in Telegram, open @BotFather, send `/newbot`, copy the token it gives you;
>   (2) send your new bot any message; (3) in a browser open `https://api.telegram.org/bot`, your token, then
>   `/getUpdates` (one address, no spaces) and copy the number after `"chat":{"id":`. If the page shows
>   `{"ok":true,"result":[]}`, send the bot another message and reload.
> - **Confirming the allowlist**: in Claude Code, say for example *"I confirm the allowlist for shop: it may edit
>   files in its own folder."* The CEO then writes this into `config/logic_agents.yaml`:
>
>   ```yaml
>   permissions_status: confirmed
>   permissions_confirmed:
>     date: "2026-01-15"
>     by: "Minji Kim"
>     quote: "I confirm the allowlist for shop: it may edit files in its own folder."
>     scope: "shop: allowed_tools as proposed"
>   ```
> - **Awake at report time**: the report is made on your computer, so it must be on and not asleep at the report time.
>   If it was asleep, the report goes out when it wakes, as long as that is within 90 minutes (labelled `delayed`);
>   later than that you get one `missed` notice instead.

## 3. Register the service (reports arrive by themselves)

The installer wrote the service file with your paths already filled in (`service/` folder) and printed the exact
commands for this computer at the end of its summary, under *3. register the report service* (whatever the STATUS
line says). **Paste those lines into Terminal as they are.** They are also in your queue item `register_report_service_…`. To print them again:
`python3 scripts/install.py --render-service`.

What you paste depends on how reports reach you:

- **`none` (files only):** reports are saved only as files under `state/report_daemon/reports/`, and there is no
  Telegram value to fill in. To switch to Telegram later, run `python3 scripts/install.py --reconfigure delivery`,
  answer `telegram`, and paste the commands it prints. It updates `config/report_daemon.yaml`, the service file and
  your queue together.
- **`telegram`:** the first lines copy the service file and open the copy in TextEdit. Replace `REPLACE_WITH_BOT_TOKEN`
  and `REPLACE_WITH_CHAT_ID` (how to get them: the box in section 2), save, close TextEdit, then paste the remaining
  lines. On Linux, the line that writes `~/.config/report-daemon/env` holds `PASTE_TOKEN` and `PASTE_CHAT_ID`:
  replace both before pressing Enter. `python3 scripts/doctor.py` checks the values in that registered copy (or env
  file): while a placeholder is left it shows ✗, and the daemon does not send but records why. Each report is still
  saved under `state/report_daemon/reports/`.

Success on macOS looks like this (`plutil` says OK, `launchctl bootstrap` prints nothing, the status says running):

```text
/Users/you/Library/LaunchAgents/com.my-company.report-daemon.plist: OK
{"daemon": "running", "config": "...", "timezone": "Asia/Seoul", "now": "...", "report_times": ["08:00", "20:00"]}
```

A common error:

```text
Bootstrap failed: 5: Input/output error
```

It usually means the service is already registered (check: `python3 scripts/report_daemon.py --status` says
`"daemon": "running"`), or that the copy in `~/Library/LaunchAgents/` was broken while editing. Run
`python3 scripts/install.py --render-service` and paste the lines it prints: they unregister the old copy first.

Four pitfalls:

1. **The service has its own PATH and environment.** launchd and systemd do not read `~/.zshrc` or `~/.bashrc`
   (no nvm, pyenv or brew setup). The installer writes absolute paths for `python3` and `claude` and a PATH with their
   folders. The Telegram values live only in the service definition (or the systemd env file), never in the YAML or
   in git. If you move the folder or reinstall Claude Code, run the installer again.
2. **YAML quotes and the time zone.** Times are quoted text: `"08:00"` (unquoted `20:00` is read as the number 1200).
   The time zone is an IANA name such as `Asia/Seoul`, not `KST`. A scheduled `trigger_at` needs its offset
   (`Z` or `+09:00`). `python3 scripts/report_daemon.py --check-config` and the doctor catch all three.
3. **Reloading.** macOS: after editing the installed plist, `launchctl kickstart -k` keeps the *old* definition;
   unregister and register it again (the lines `--render-service` prints). Linux: `systemctl --user daemon-reload`
   after every edit, and `loginctl enable-linger` or the service stops when you log out.
4. **No comments when pasting.** macOS Terminal (zsh) does not treat `#` as a comment: anything you add after `#` on a
   command line becomes part of the command. Paste the printed lines unchanged.

Restart away from report times. Details and the scheduled-trigger rules: [docs/05](docs/05-reporting-daemon.md#install-daemon-lite).

## 4. Manual install

For developers, or to see every step. `TEMPLATE` is your clone; run the rest from your CEO project folder.

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os ~/c-level-agent-os
TEMPLATE=~/c-level-agent-os
mkdir -p ~/my-company && cd ~/my-company && git init
mkdir -p config scripts tests .claude/agents state/report_daemon advisory_drafts/reviews
python3 -m pip install --user --disable-pip-version-check -r "$TEMPLATE/requirements.txt"
cp "$TEMPLATE"/templates/CLAUDE.md.ceo CLAUDE.md && cp "$TEMPLATE"/templates/HANDOFF.md HANDOFF.md
cp "$TEMPLATE"/templates/agents/*.md .claude/agents/
cp "$TEMPLATE"/templates/{owner_action_queue,logic_agents,report_daemon,access_control}.yaml config/
cp "$TEMPLATE"/templates/{report_prompt,autonomy_policy}.md config/
cp "$TEMPLATE"/scripts/{install,logic_agent,report_daemon,doctor,first_plan}.py scripts/
cp -R "$TEMPLATE"/templates .
cp "$TEMPLATE"/tests/{test_logic_agent,test_report_daemon,test_first_plan}.py tests/
cp "$TEMPLATE"/.gitignore .
```

PyYAML is the only dependency (macOS has no `pip` command; `python3 -m pip` always works). Delete the role agents you
do not want from `.claude/agents/`. The `templates/` copy is what `--render-service` and `--reconfigure` read, and
`.gitignore` keeps `state/` and `service/` out of git.

Then edit (every `{{...}}`; numbered ones such as `UNIT_1`, `UNIT_2` mean one line per item; delete the comment at
the top of `CLAUDE.md`):

- `config/logic_agents.yaml`: one entry per project agent. **The key is the name you choose** and call
  (for example `python3 scripts/logic_agent.py shop`); the template's `strategy_a`, `ops_bot` are examples. Set a real
  `path`, the `mode`, and a `handoff` file that exists there. Protected zone: every critical-zone path in
  `denied_tools` as `Edit(//absolute/path/**)` (two slashes = absolute; one slash is relative to the settings source;
  use `Edit`, Claude Code ignores `Write(path)` rules), or `protected_zone: none` only if the owner confirms there is
  none. A path that does not exist yet also goes under that agent's `allow_missing:` (the doctor fails otherwise).
  Live services: `Bash(launchctl *name*)` and `Bash(systemctl *name*)` under `defaults.denied_tools`. Put the same
  rules in `.claude/settings.json` as `{"permissions": {"deny": [...]}}` for the CEO session.
- `config/owner_action_queue.yaml`: delete the example items (`example: true`).
- `config/report_daemon.yaml`: `timezone`, quoted `report_times`, `claude_cmd` = the output of `which claude`,
  `delivery` (`none` by default; `telegram` once you have a bot).
- `config/autonomy_policy.md`, `config/access_control.yaml`, `HANDOFF.md`: the date, your own words, your paths.

Write the service file, check, then make the first calls:

```bash
python3 scripts/install.py --render-service
python3 scripts/doctor.py
python3 scripts/logic_agent.py --status
python3 scripts/report_daemon.py --once --dry-run
```

- `--render-service` writes `service/…` from `config/report_daemon.yaml` and prints the lines that register it
  (section 3). In the doctor output every ✗ must be gone. `--status` lists the names you chose; then try one with
  `python3 scripts/logic_agent.py shop -p "Read your handoff and report status in 5 lines" --dry-run` (`shop` = your name).
- In the dry-run output, `--disallowedTools` must list your real critical-zone paths. The runner refuses real calls
  while a denied rule is still a placeholder.
- Tests are optional: `python3 -m pip install --user -r requirements-dev.txt && python3 -m pytest tests -q`, or
  without pytest `python3 -m unittest discover tests`.
- Dropping `--dry-run` calls Claude. Until you confirm the allowlist (`permissions_status: confirmed` plus
  `permissions_confirmed`: date, by, your quote, scope), an agent has only the tools of its own project's
  `.claude/settings*.json`; with none, expect a read-only report ending in "Needed permission: …".
  The CEO agent calls project agents itself; you do not need to run the runner by hand.
- Prefer a virtual environment? `python3 -m venv .venv && .venv/bin/python -m pip install pyyaml`, then use
  `.venv/bin/python` everywhere, including for `--render-service` (the service runs the python that wrote it).

## 5. What this is, and what it is not

- **A rule set.** The owner does only the final action: anything that physically needs the owner's
  hands (sending, paying, signing, authorizing, owner-only facts) goes into one queue, fully prepared.
  The CEO agent decides and executes everything else, then reports. See [docs/00](docs/00-philosophy.md).
- **Templates** for the CEO charter (`CLAUDE.md`), CTO/CRO/CFO/CMO subagents, an autonomy policy,
  the owner action queue, a registry of project agents, access control and the handoff log.
- **An installer and a check** (`scripts/install.py`, `scripts/doctor.py`) that fill the templates from plain answers
  and refuse to call an install complete while a placeholder is left.
- **A small runner** (`scripts/logic_agent.py`, Python + PyYAML) that calls a project's agent headless
  from that project's directory, keeps its permission boundary, resumes and rotates sessions, and logs every call.
- **A reporting daemon-lite** (`scripts/report_daemon.py`, same dependencies) that implements the design notes
  in [docs/05](docs/05-reporting-daemon.md): a two-layer report twice a day (top block from the queue without a model,
  then one model call), scheduled triggers from the queue, delivery to Telegram, run by launchd or systemd.

It is not:

- **Not investment, legal or financial advice.** Nothing here tells you what to trade or how.
- **Not a way to give agents money.** The design keeps payments, transfers, credentials and irreversible
  actions on real capital with the human. Do not give agents custody of funds or payment credentials.
- Not a hosted product and not a runtime framework: the only long-running piece is the reporting daemon-lite,
  one script you register with your own service manager. Not a guarantee that agents behave. Permission rules
  reduce risk; they do not remove it (a script an agent runs can still write files). Review what your agents commit.

<p align="center"><img src="docs/assets/approval-required-always.png" alt="Critical zone: files that move real money are denied to every agent; owner approval always" width="1200" /></p>

### Showcase: stopping is automatic, restarting is not

The firm that built this ran a live strategy, VOLCANO, under a pre-registered kill-switch. On 2026-09-23
the kill-switch tripped and was executed as designed. The stop needed no one's decision.

What the agents did next: froze the record and drafted the public write-up. What they did not do:
touch capital or restart anything. Both stayed with the owner, as `approval_required: always`.

Public record: [crynomad.ai/track-record.html#volcano](https://crynomad.ai/track-record.html#volcano)

### Layout

```
.agents/skills/c-level-agent-os/  skill for `npx skills add`: your agent runs the installer and shows the check
docs/00-philosophy.md             owner does only the final action; the one question
docs/01-ceo-charter.md            charter sections, responsibility matrix skeleton
docs/02-autonomy-policy.md        improvement loop, block budget, persistence
docs/03-owner-action-queue.md     queue schema, render rules, example
docs/04-logic-agents-headless.md  registry, permissions and path rules, session rotation, no live+headless together
docs/05-reporting-daemon.md       two-layer report, scheduled triggers (design notes, install)
templates/                        CLAUDE.md.ceo, agents/, owner_action_queue.yaml, logic_agents.yaml, report_daemon.yaml,
                                  report_prompt.md, first_plan_prompt.md, autonomy_policy.md, access_control.yaml,
                                  HANDOFF.md, project_HANDOFF.md
templates/service/                launchd plist and systemd user unit for the reporting daemon
scripts/install.py                one-step installer (questions or --answers file), --reconfigure,
                                  --render-service; ends with the check
scripts/doctor.py                 installation check: ✓ / ℹ / ⚠ / ✗ per line, exit 1 on any ✗, --check-login
scripts/first_plan.py             the first two-week plan and the owner's first queue items (one model run)
scripts/logic_agent.py            headless runner
scripts/report_daemon.py          reporting daemon-lite: two-layer report, scheduled triggers, Telegram
tests/                            installer, runner and daemon tests (temp dirs, stub CLI, no real calls)
requirements.txt                  PyYAML (required); requirements-dev.txt adds pytest (optional)
```

## 6. Not a developer? Paid install and 30-day coaching

The template is free (MIT). If you want it set up on your own projects and tuned over the first
30 days of use, contact **contact@crynomad.ai** · [crynomad.ai](https://crynomad.ai)

## License

MIT. See [LICENSE](LICENSE).
