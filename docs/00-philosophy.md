# 00. Philosophy: the owner does only the final action

A one-person company run with an AI executive team tends to fail in one predictable way.
The agents produce analysis, and the human becomes the bottleneck: approving every step,
relaying messages between sessions, remembering what waits on whom.

This system inverts that. The CEO agent carries every piece of work up to the exact step
that needs a human hand, then puts one ready-to-execute item in one queue.

## Two categories, nothing else

| Category | What belongs here | How it is handled |
|---|---|---|
| **Owner action** | Sending or posting outside the company (email, DM, forms, surveys, public posts). Payments, accounts, authentication (OAuth consent, logins, transfers). Signatures, contracts, hiring counsel. Irreversible actions on real money or live services. The critical zone. Facts only the owner knows. | One queue item (`owner: human`, `ready: true`), added **only when the draft, attachments and click path are prepared**. Shown at the top of every report. |
| **CEO decides and executes** | Everything else: research direction, internal reviews and verdicts, scheduling, instructions to agents, drafting (including corrections to public pages), internal documents, retrying with a new hypothesis. | Decide, execute, report with a one-line rationale. If the owner objects, correct. |

## The one question

> **"If the owner does not do this personally, is it physically impossible?"**

If the answer is no, the CEO does it.

| Work | Category | Why |
|---|---|---|
| Writing the reply to a partner | CEO | anyone can type it |
| Pressing send on that reply | Owner | it leaves the company under the owner's name |
| Finding out why an API token expired, preparing the re-auth command | CEO | |
| Clicking "Allow" on the consent screen | Owner (`local`) | only the account holder can |
| Re-running a failed analysis with the next hypothesis | CEO | internal, reversible |
| Changing a risk limit on real money | Owner | irreversible, real capital |
| "Did we already promise this customer a date?" | Owner (`facts`) | only the owner knows |

## Approval travels as a quote

When a downstream agent needs owner approval (critical zone, external publication, irreversible action),
the CEO puts the owner's own words and timestamp, exactly as typed in the CEO session, into the instruction.
A decision relayed from one agent to another is never approval for anything external or irreversible.

## What does not get faster

Autonomy removes waiting, not checks. These stay in place:
- critical-zone approval, regardless of urgency
- pre-registered criteria, gates, independent recomputation, negative tests
- agents never hold payment credentials or custody of funds

## Failure modes this answers (generalized)

- Project sessions left open in terminals, idle for days, re-reading growing transcripts on every turn.
- Messages between sessions delivered only when the receiver is awake, so the CEO waited on peers.
- The owner turned into a session manager.
- Timers inside an interactive session silently stopped when the session went inactive.
- Dated items slipped while no session was active.

Each of these has a structural fix in this template: headless calls (04), the owner action queue (03),
and a reporting daemon that owns the clock (05).
