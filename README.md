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

## 2. Quick start (no coding)

You need a Mac or Linux computer, [Claude Code](https://code.claude.com/docs/en/setup) installed and signed in
(run `claude` once), and Python 3 (on a Mac, the first `python3` or `git` offers to install Apple's command line
tools: accept). Open Terminal and type three commands:

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os my-company && cd my-company
python3 scripts/install.py
python3 scripts/report_daemon.py --once --dry-run
```

1. **Get it.** Downloads the template into a new folder `my-company`; that folder becomes your company's CEO folder.
   No git? Download the ZIP from GitHub, unzip it, and open Terminal in that folder.
2. **Install.** About ten plain questions, each with a default you accept with Enter: company, your name, time zone,
   report times, messenger (`telegram`, or `none` to start with files only), which roles, and your project folders.
   If PyYAML (the only dependency) is missing, it offers to install it. It ends with a check and one line:
   `STATUS: COMPLETE`, or `NOT COMPLETE` with the exact things to fix.
3. **See tomorrow's report now.** Prints the report you would receive (one model call; nothing is sent or recorded).
   Add `--no-model` to see only the top block.

**Protected zone.** For each project the installer asks which files or folders move real money or control live
services (payments, pricing, live trading, send jobs). Each one becomes an absolute deny rule for that project's agent
and for the CEO session. If you leave it empty, you must type `none` to confirm, and the summary shows
**NO PROTECTED ZONE** in a box. A template placeholder left anywhere means the install is not complete.

Then open Claude Code in this folder (`claude`) and ask for a recap. From now on your to-do list is
`config/owner_action_queue.yaml`; the installer already put your remaining setup steps there. Run
`python3 scripts/doctor.py` any time to re-check the installation (✓ ok, ⚠ read it, ✗ fix it).

**Or let your agent do it.** In the company folder run `npx skills add junhwanahn/c-level-agent-os`. If it asks,
choose Claude Code and the **Project** scope (Project writes only into this folder: the skill file, a `.claude/skills`
link and `skills-lock.json`; Global writes to your home folder). Then ask your agent: *"Set up the C-Level Agent OS
for my company."* It asks you the same questions, runs the same installer, and shows you the check.

## 3. Register the service (reports arrive by themselves)

The installer wrote the service file with your paths already filled in: `service/<label>.plist` on macOS,
`service/report-daemon.service` on Linux. The owner action `register_report_service_…` in your queue holds the exact
commands for your machine. For Telegram: open @BotFather, send `/newbot`, copy the token; send your bot one message,
open `https://api.telegram.org/bot<token>/getUpdates` and copy `chat.id`.

macOS (launchd user agent; `<label>` is printed by the installer, e.g. `com.my-company.report-daemon`):

```bash
cp service/<label>.plist ~/Library/LaunchAgents/ && chmod 600 ~/Library/LaunchAgents/<label>.plist
open -e ~/Library/LaunchAgents/<label>.plist      # replace REPLACE_WITH_BOT_TOKEN and REPLACE_WITH_CHAT_ID, save
plutil -lint ~/Library/LaunchAgents/<label>.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist
python3 scripts/report_daemon.py --status          # "daemon": "running", next report time
```

Linux (systemd user unit):

```bash
mkdir -p ~/.config/systemd/user ~/.config/report-daemon && cp service/report-daemon.service ~/.config/systemd/user/
printf 'REPORT_TELEGRAM_TOKEN=...\nREPORT_TELEGRAM_CHAT_ID=...\n' > ~/.config/report-daemon/env && chmod 600 ~/.config/report-daemon/env
systemctl --user daemon-reload && systemctl --user enable --now report-daemon
loginctl enable-linger "$USER"
python3 scripts/report_daemon.py --status
```

Three pitfalls:

1. **The service has its own PATH and environment.** launchd and systemd do not read `~/.zshrc` or `~/.bashrc`
   (no nvm, pyenv or brew setup). The installer writes absolute paths for `python3` and `claude` and a PATH with their
   folders. The Telegram values live only in the service definition (or the systemd env file), never in the YAML or
   in git. If you move the folder or reinstall Claude Code, run the installer again.
2. **YAML quotes and the time zone.** Times are quoted text: `"08:00"` (unquoted `20:00` is read as the number 1200).
   The time zone is an IANA name such as `Asia/Seoul`, not `KST`. A scheduled `trigger_at` needs its offset
   (`Z` or `+09:00`). `python3 scripts/report_daemon.py --check-config` and the doctor catch all three.
3. **Reloading.** macOS: after editing the installed plist, `launchctl kickstart -k` keeps the *old* definition;
   run `launchctl bootout gui/$(id -u)/<label>`, then the `bootstrap` line again. Linux: `systemctl --user daemon-reload`
   after every edit, and `loginctl enable-linger` or the service stops when you log out.

Restart away from report times. Details and the scheduled-trigger rules: [docs/05](docs/05-reporting-daemon.md#install-daemon-lite).

## 4. Manual install

For developers, or to see every step. `TEMPLATE` is your clone; run the rest from your CEO project folder.

```bash
git clone --depth 1 https://github.com/junhwanahn/c-level-agent-os ~/c-level-agent-os && TEMPLATE=~/c-level-agent-os
mkdir -p ~/my-company && cd ~/my-company && git init
mkdir -p config scripts tests .claude/agents state/report_daemon advisory_drafts/reviews
python3 -m pip install --user -r "$TEMPLATE/requirements.txt"   # PyYAML. macOS has no `pip` command; this always works
cp "$TEMPLATE"/templates/CLAUDE.md.ceo CLAUDE.md && cp "$TEMPLATE"/templates/HANDOFF.md HANDOFF.md
cp "$TEMPLATE"/templates/agents/*.md .claude/agents/          # delete the roles you do not want
cp "$TEMPLATE"/templates/{owner_action_queue,logic_agents,report_daemon,access_control}.yaml config/
cp "$TEMPLATE"/templates/{report_prompt,autonomy_policy}.md config/
cp "$TEMPLATE"/scripts/{logic_agent,report_daemon,doctor}.py scripts/
cp "$TEMPLATE"/tests/test_logic_agent.py "$TEMPLATE"/tests/test_report_daemon.py tests/
cp "$TEMPLATE"/.gitignore .                                    # keeps state/ and service/ out of git
```

Then edit (every `{{...}}`; numbered ones such as `UNIT_1`, `UNIT_2` mean one line per item; delete the comment at
the top of `CLAUDE.md`):

- `config/logic_agents.yaml`: one entry per project agent. **The key is the name you choose** and call
  (`python3 scripts/logic_agent.py <your name>`); the template's `strategy_a`, `ops_bot` are examples. Set a real
  `path`, the `mode`, and a `handoff` file that exists there. Protected zone: every critical-zone path in
  `denied_tools` as `Edit(//absolute/path/**)` (two slashes = absolute; one slash is relative to the settings source;
  use `Edit`, Claude Code ignores `Write(path)` rules), or `protected_zone: none` only if the owner confirms there is
  none. Put the same rules in `.claude/settings.json` as `{"permissions": {"deny": [...]}}` for the CEO session.
- `config/owner_action_queue.yaml`: delete the example items (`example: true`).
- `config/report_daemon.yaml`: `timezone`, quoted `report_times`, `claude_cmd` = the output of `which claude`, `delivery`.
- `config/autonomy_policy.md`, `config/access_control.yaml`, `HANDOFF.md`: the date, your own words, your paths.

Check, then make the first call:

```bash
python3 scripts/doctor.py                     # every ✗ must be gone
python3 scripts/logic_agent.py --status       # the names you chose
python3 scripts/logic_agent.py <name> -p "Read your handoff and report status in 5 lines" --dry-run
python3 scripts/report_daemon.py --once --dry-run
```

- In the dry-run output, `--disallowedTools` must list your real critical-zone paths. The runner refuses real calls
  while a denied rule is still a placeholder.
- Tests are optional: `python3 -m pip install --user -r requirements-dev.txt && python3 -m pytest tests -q`, or
  without pytest `python3 -m unittest discover tests`.
- Dropping `--dry-run` calls Claude. Until you confirm the allowlist (`permissions_status: confirmed` plus
  `permissions_confirmed`: date, by, your quote, scope), an agent has only the tools of its own project's
  `.claude/settings*.json`; with none, expect a read-only report ending in "Needed permission: …".
  The CEO agent calls project agents itself; you do not need to run the runner by hand.
- Prefer a virtual environment? `python3 -m venv .venv && .venv/bin/python -m pip install pyyaml`, then use
  `.venv/bin/python` everywhere, including the service file.

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
                                  report_prompt.md, autonomy_policy.md, access_control.yaml, HANDOFF.md
templates/service/                launchd plist and systemd user unit for the reporting daemon
scripts/install.py                one-step installer (questions or --answers file), ends with the check
scripts/doctor.py                 installation check: ✓ / ⚠ / ✗ per line, exit 1 on any ✗
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
