# HANDOFF

Session handoff log, newest block on top. The CEO updates the top block right after every big decision
and at session end. A recap starts here but refreshes the real state first (charter: Session procedure).
Copy to: HANDOFF.md in the CEO project root.

## {{INSTALL_DATE}} - C-Level Agent OS installed

- Owner's instruction, verbatim: "{{OWNER_INSTRUCTION}}"
- State: installed. Project agents: {{AGENT_SUMMARY}}. Registry `permissions_status: proposed`.
- Waiting on the owner: the items with `owner: human` in `config/owner_action_queue.yaml`.
- Next: [Owner] `python3 scripts/first_plan.py` (the first two-week plan, 5-15 min), then the queue items above.
  [CEO] first recap, then read `config/owner_action_queue.yaml`, the latest `advisory_drafts/*-first-plan.md` and
  each project's handoff file.
