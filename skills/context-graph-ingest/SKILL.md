---
name: context-graph-ingest
description: Use when the user wants to import, ingest, or bulk-load a folder of markdown notes or a Notion export into the Context Graph. Trigger phrases include "ingest my notes", "import this folder", "scan the markdown in", "load my Notion export", "bulk import", "index everything under this directory", "build the graph from my notes folder".
---

# Context Graph — Ingest Markdown or Notion Export

Use this skill when the user points at a directory of notes and wants Context Graph to classify each file and persist them to a local graph. Do NOT use it for single-note classification (see `context-graph-classify`) or for querying an existing graph (see `context-graph-search`).

## When to call

- User asks to import or scan a markdown folder.
- User provides a path to a Notion export (the flavor where filenames encode page ids).
- User wants to seed or rebuild the graph from notes on disk.

## Which tool

- `mcp__context-graph__ingest_markdown` — generic markdown tree. Use for a plain notes folder, Obsidian vault, or any `.md` directory.
- `mcp__context-graph__ingest_notion_export` — use only when the source is a Notion markdown export; it preserves Notion page ids from filenames and resolves local page links.
- For a **live Notion workspace** (no export, no API key), use the `/cg-sync-notion <scope>` slash command instead — it orchestrates the official Notion MCP (`notion-search` + `notion-fetch`) and pipes results into `index_records`. Offline export ingestion and live MCP sync share the `notion:<32-hex>` id scheme, so they dedupe.

## Input shape

```json
{
  "rootPath": "/absolute/path/to/notes",
  "pattern": "**/*.md",
  "recursive": true,
  "index": true,
  "graphPath": "/absolute/path/to/graph.json"
}
```

Guidance:
- `rootPath` is required; use the absolute path the user provided.
- Leave `pattern` and `recursive` at their defaults unless the user narrows scope.
- Set `index: true` to persist results; set `false` for a dry-run preview.
- Only pass `graphPath` if the user specified a non-default location.

Both tools accept the same fields. Do not invent additional fields.

## Interpreting the output

The tool returns:
- `rootPath` — echoed for confirmation.
- `fileCount` — number of markdown files processed.
- `recordIds` — ids of the records created or updated.

Report the counts succinctly. If `index` was true, mention that the graph at `graphPath` has been updated and suggest `search_graph` as the next step. If the user's folder contains non-markdown content or the counts look off, ask whether to adjust `pattern` or `recursive`.
