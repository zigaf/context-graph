---
name: context-graph-search
description: Use when the user wants to find related notes, prior decisions, past bugs, or build a compact context pack for a task or query. Trigger phrases include "find related notes", "build a context pack", "what do we know about X", "search the graph", "prior decisions on", "similar incidents to", "context for this task", "pull relevant notes". Prefer `search_graph` when a persisted graph exists; use `build_context_pack` when records are passed inline.
---

# Context Graph — Search & Build Context Pack

Use this skill when the user wants Context Graph to surface the smallest useful slice of known records for a task, question, or new incident — instead of dumping a whole knowledge base into the prompt.

## When to call

- User asks for related notes, prior decisions, duplicate bugs, or known risks around a topic.
- User wants a compact "context pack" before starting work on a task.
- User hands you a list of records inline and asks which ones matter for a query.

## Which tool

- `mcp__context-graph__search_graph` — preferred when a graph has been persisted (e.g. after `index_records` or an ingest). Reads from `graphPath` (default is the plugin's standard location; pass explicitly if the user named one).
- `mcp__context-graph__build_context_pack` — use when the user supplies `records` inline and there is no persisted graph to read.

## Input shape

For `search_graph`:

```json
{
  "query": "<natural language task or question>",
  "markers": { "domain": "payments", "flow": "webhook" },
  "graphPath": "<optional path if user specified one>",
  "limit": 10
}
```

For `build_context_pack`:

```json
{
  "query": "<natural language task or question>",
  "markers": { "domain": "payments" },
  "records": [ /* array of records from the user */ ],
  "limit": 10
}
```

Only use marker keys and values that exist in `docs/schema.json` (`type`, `domain`, `goal`, `status`, `severity`, `flow`, `artifact`, `project`, `room`, `scope`, `owner`). Do not fabricate markers the user did not imply.

## Interpreting the output

Both tools return:
- `directMatches` — top-ranked records for the query.
- `supportingRelations` — one-hop neighbors via explicit or inferred edges.
- `promotedRules` (context pack) / `graphStats` (search_graph).
- `unresolvedRisks` (context pack) — known-risk records still open.

Summarize direct matches first, then supporting relations, then any active rules or open risks. Keep the reply compact; do not paste full record content unless asked.
