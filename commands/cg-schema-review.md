---
description: Triage pending schema proposals
---

The user wants to review the pending marker proposals the learner has queued.

Steps:

1. Call `mcp__context-graph__list_proposals` with `{}`.
2. If `pending` is empty, say so and stop.
3. Walk through proposals one at a time. For each proposal, show:
   - Value
   - Source (`hierarchy`, `ngram`, `codePath`)
   - Confidence
   - Sample support records, limited to the first 5 ids
   - Detail such as n-gram tokens, code-path occurrences, or average hierarchy depth
4. Ask the user whether to `accept`, `reject`, `skip`, or `quit`.
5. If the user accepts, ask which field to attach it to: `domain`, `flow`, `artifact`, `type`, `severity`, `status`, `project`, `room`, `scope`, or `owner`.
6. Call `mcp__context-graph__apply_proposal_decision` with:
   - `value`: proposal value
   - `decision`: `accept`, `reject`, or `skip`
   - `field`: only when accepting
7. Repeat until the user quits or the queue is empty.
8. On exit, report how many proposals were accepted, rejected, skipped, and remaining.

Do not auto-accept proposals. The user must explicitly choose a decision.
