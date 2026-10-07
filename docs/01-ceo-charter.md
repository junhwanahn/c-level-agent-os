# 01. CEO charter

The CEO agent's `CLAUDE.md` is its charter. Template: [`templates/CLAUDE.md.ceo`](../templates/CLAUDE.md.ceo).

## Sections and why each exists

| Section | Purpose |
|---|---|
| Company overview | One line per business unit. Enough for routing, not a strategy document. |
| CEO role | Combine specialist reports into one recommendation with data; carry work up to the owner's final action. |
| C-level agents | Who covers what, and the exact command to call each one. |
| Projects and access | Every directory the CEO may read or write. Other projects change only through their own agent. |
| Critical zone | Paths and actions with real-world consequences. Owner approval at any urgency. |
| Data sources | Where each metric comes from, so numbers are traceable. |
| Decision framework | The two categories from [00](00-philosophy.md). |
| Working principles | Data over opinion; derive numbers from data; ask the owner only owner-only questions. |
| Session procedure | Recap at start, adversarial review for irreversible or external work, handoff update. |

## C-level agents

Role prompts: [`templates/agents/`](../templates/agents/). Each one is a Claude Code subagent file
(copy to `.claude/agents/`). Keep them short; the mandate lives in the responsibility matrix below.

C-level agents are the **observe, analyse, recommend, coordinate** layer. Code in a business unit
changes in that unit's own project (its own agent), and critical-zone changes need owner approval.

## Responsibility matrix (skeleton)

Keep one matrix as the single source of truth. Every agent prompt and report derives from it.
Rule: if the cell for a unit under your column is not blank, it is your responsibility.

```markdown
## 1. Units
| Unit | What it does | Lifecycle stage | Resource type | Host |
|------|--------------|-----------------|---------------|------|
| unit_a | ... | prototype / pilot / live (own resources) / live (customers) | own / customer | ... |

## 2. What each agent cares about, by lifecycle stage
| Stage | CRO | CFO | CMO |
|-------|-----|-----|-----|
| prototype / pilot | integrity of tests, zero incidents | resource estimate | wait |
| live (own resources) | our losses and exposure | company P&L | build track record |
| live (customers) | customer risk + service commitments | fees and revenue | launch, onboarding, funnel |

## 3. Data source per unit
(paths, APIs, log files, state files; how each agent reads them)

## 4. Matrix: one table per agent
### 4.x <AGENT>
| Unit | Mandate | Watch metrics | Escalation trigger |
|------|---------|---------------|--------------------|

## 5. CEO company-wide mandate
lifecycle gates, resource allocation, single points of failure, governance, decision support

## 6. Critical zones (per unit)
## 7. Open items
## 8. Change log (one line per change; cell changes need owner approval)
```

Escalation path: agent trigger fires -> report to CEO -> CEO combines under its company-wide mandate ->
owner sees it only if it is an owner action or a result worth knowing.
