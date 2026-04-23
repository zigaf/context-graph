---
name: context-graph-classify
description: Use when the user asks to classify, tag, normalize, or add structure to a raw note, bug report, decision, incident, or unstructured paragraph. Trigger phrases include "classify this note", "tag this", "normalize markers", "add structure to", "what markers apply to", "extract type/domain/status", "hierarchy path for this note". Calls the Context Graph MCP to produce normalized markers and a hierarchy path.
---

# Context Graph — Classify Record

Use this skill when the user has a single unstructured note (or a short cluster) and wants Context Graph to assign normalized markers, infer missing required fields, and compute a hierarchy path. Do NOT use it for bulk folder imports (see `context-graph-ingest`) or for searching existing notes (see `context-graph-search`).

## When to call

- User pastes a raw note, bug report, incident log, or decision text and asks for structure.
- User asks which `type`, `domain`, `goal`, or `status` applies.
- User wants a hierarchy path (e.g. `payments > deposit > webhook`) for a note.

## Tool to call

`mcp__context-graph__classify_record`

## Input shape

Pass a single `record` object. At minimum provide free-form text in `title` and/or `content`. If the user supplied any markers, forward them under `markers`; do not invent values the user did not provide.

```json
{
  "record": {
    "title": "<short note title>",
    "content": "<full note text>",
    "markers": {
      "type": "bug",
      "domain": "payments"
    }
  }
}
```

Allowed marker fields (from `docs/schema.json`): `type`, `domain`, `goal`, `status` (required), plus optional `project`, `room`, `flow`, `artifact`, `severity`, `scope`, `owner`. Valid values for each are listed in the schema. Do not invent fields outside that set.

## Interpreting the output

The tool returns:
- `markers` — the normalized marker set (aliases resolved, e.g. `issue` -> `bug`).
- `missingRequiredMarkers` — any of `type`, `domain`, `goal`, `status` that could not be inferred. Ask the user to fill these if important.
- `hierarchy` — the computed path using the schema order (`project > domain > flow > artifact`).
- `id`, `title` — normalized identifiers.

Summarize the classification back to the user, highlight any `missingRequiredMarkers`, and offer to index the record (`index_records`) or link it against candidates (`link_record`) as a natural next step.
