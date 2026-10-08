# C-Level Agent OS

An operating structure for a one-person company run with an AI executive team on Claude Code:
a CEO agent, C-level role agents (four templates included: CTO, CRO, CFO, CMO — add a strategy or other role as you need), project agents called headless, and one queue for the
actions only the human owner can take.

Built and used by a one-person systematic trading firm. Everything domain-specific has been removed.

[한국어 README](README.ko.md)

## What this is

- **A rule set.** The owner does only the final action: anything that physically needs the owner's
  hands (sending, paying, signing, authorizing, owner-only facts) goes into one queue, fully prepared.
  The CEO agent decides and executes everything else, then reports. See [docs/00](docs/00-philosophy.md).
- **Templates** for the CEO charter (`CLAUDE.md`), CTO/CRO/CFO/CMO subagents, an autonomy policy,
  the owner action queue, and a registry of project agents.
- **A small runner** (`scripts/logic_agent.py`, Python + PyYAML) that calls a project's agent headless
  from that project's directory, keeps its permission boundary, resumes and rotates sessions, and logs every call.
- **Design notes** for a two-layer periodic report and a scheduled-trigger daemon (no daemon code).

## What it is not

- **Not investment, legal or financial advice.** Nothing here tells you what to trade or how.
- **Not a way to give agents money.** The design keeps payments, transfers, credentials and irreversible
  actions on real capital with the human. Do not give agents custody of funds or payment credentials.
- Not a hosted product, not a framework to install, and not a guarantee that agents behave.
  Permission rules reduce risk; they do not remove it. Review what your agents commit.

## Quick start

Requirements: Claude Code CLI, Python 3.9+, `pip install pyyaml`.

1. **Charter and roles.** Copy `templates/CLAUDE.md.ceo` to your CEO project root as `CLAUDE.md` and fill the
   `{{...}}` placeholders. Copy `templates/agents/*.md` to `.claude/agents/`.
2. **Queue and registry.** Copy `templates/owner_action_queue.yaml` and `templates/logic_agents.yaml` into `config/`,
   and `scripts/logic_agent.py` into `scripts/`. Point each agent's `path` at a real project directory and set its `mode`.
3. **Check before the first call.**
   ```bash
   python3 scripts/logic_agent.py --status
   python3 scripts/logic_agent.py strategy_a -p "Read your handoff and report status in 5 lines" --dry-run
   python3 -m pytest tests -q
   ```
   Then drop `--dry-run`. Keep `permissions_status: proposed` until you, the owner, confirm the allowlist.

## Layout

```
docs/00-philosophy.md             owner does only the final action; the one question
docs/01-ceo-charter.md            charter sections, responsibility matrix skeleton
docs/02-autonomy-policy.md        improvement loop, block budget, persistence
docs/03-owner-action-queue.md     queue schema, render rules, example
docs/04-logic-agents-headless.md  registry, permissions, session rotation, no live+headless together
docs/05-reporting-daemon.md       two-layer report, scheduled triggers (design notes)
templates/                        CLAUDE.md.ceo, agents/, owner_action_queue.yaml, logic_agents.yaml
scripts/logic_agent.py            headless runner
tests/test_logic_agent.py         runner tests (stub CLI, no real calls)
```

## Paid: install and 30-day coaching

The template is free (MIT). If you want it set up on your own projects and tuned over the first
30 days of use, contact **contact@crynomad.ai** · [crynomad.ai](https://crynomad.ai)

## License

MIT. See [LICENSE](LICENSE).
