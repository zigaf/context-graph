---
name: context-graph-promote
description: Use when the user wants to derive a reusable rule, invariant, guardrail, or decision from a cluster of repeated incidents, bugs, or notes. Trigger phrases include "promote this pattern", "turn these incidents into a rule", "extract a guardrail from", "derive a decision from these bugs", "what's the common pattern across", "make this reusable", "consolidate these into an ADR".
---

# Context Graph — Promote Pattern

Use this skill when the user has a set of related records (typically bugs, incidents, or debug notes that repeat) and wants Context Graph to consolidate them into a single reusable `rule` or `decision` record linked back to its sources.

## When to call

- User points at several similar bugs/incidents and asks for a shared rule.
- User wants to turn a recurring failure mode into a guardrail or invariant.
- User asks to synthesize an ADR / decision from prior discussions.

## Tool to call

`mcp__context-graph__promote_pattern`

## Input shape

Pass either `recordIds` (when the graph is persisted) or inline `records` (when the user supplied them directly). Prefer `recordIds` if the records already live in the graph.

```json
{
  "recordIds": ["rec_123", "rec_456"],
  "graphPath": "/absolute/path/to/graph.json",
  "title": "Always verify webhook idempotency",
  "outputType": "rule",
  "writeToGraph": true
}
```

Or with inline records:

```json
{
  "records": [ /* array of existing records */ ],
  "title": "Optional override for the promoted record title",
  "outputType": "decision",
  "writeToGraph": false
}
```

Guidance:
- `outputType` should be `rule` or `decision` (both are valid `type` values in `docs/schema.json`).
- Only pass `graphPath` if the user specified one.
- Set `writeToGraph: true` to persist the promoted record and back-links; use `false` for a preview.
- Do not invent fields outside the schema.

## Interpreting the output

The tool returns:
- `promotedRecord` — the synthesized rule or decision, already classified.
- `sourceRecords` — the originals that fed it (linked via `derived_from` / `same_pattern_as`).
- `sharedKeywords`, `commonMarkers` — what the sources had in common.
- `quality` — a confidence/coverage signal for the synthesis.
- `splitSuggestions` — if the cluster was too heterogeneous, hints to split it.

Summarize the promoted record first, call out shared markers, and surface any `splitSuggestions` so the user can decide whether to re-run on a tighter subset. If `writeToGraph` was false, offer to persist it as a follow-up.
