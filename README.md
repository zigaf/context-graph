# Context Graph

Context Graph is a plugin scaffold for turning unstructured notes into a compact retrieval system.

It is built around four ideas:

- normalized markers instead of free-form hashtags
- explicit and inferred relations between records
- hierarchy paths that keep notes navigable
- compact context packs assembled per request

## Core model

Each record should eventually contain:

- markers: canonical fields like `type`, `domain`, `flow`, `goal`, `status`
- hierarchy: a stable path such as `project > domain > flow > artifact`
- explicit relations: confirmed links like `fixes`, `affects`, `depends_on`
- inferred relations: probable links with a confidence score

## Initial repository layout

- `.codex-plugin/plugin.json` - plugin manifest
- `.mcp.json` - MCP server registry placeholder
- `.app.json` - app registry placeholder
- `hooks.json` - hook registry placeholder
- `docs/schema.json` - initial marker and relation schema
- `docs/retrieval.md` - retrieval policy for building context packs

## Implemented MVP commands

The current scaffold includes a local CLI runtime in `scripts/context_graph_cli.py`.

Available commands:

1. `classify-record` - normalize markers, infer missing markers, and build hierarchy paths
2. `link-record` - create inferred relations between one source record and candidate records
3. `build-context-pack` - rank records for a user request and return a compact retrieval payload
4. `index-records` - classify records, upsert them into local graph storage, and rebuild edges
5. `search-graph` - build a context pack from the persisted graph index
6. `promote-pattern` - derive a reusable rule or decision record from related source records
7. `ingest-markdown` - scan markdown files, extract records from front matter plus headings, and optionally index them
8. `ingest-notion-export` - scan Notion markdown exports, preserve page ids from filenames, and resolve local links between exported pages
9. `sync-notion` - pull pages from a Notion database or parent page via the Notion API, persist a cursor for delta sync, and optionally index the result
10. `delete-record` - remove a record from the graph and rebuild affected edges
11. `archive-record` - hide a record from context packs and graph search without touching its edges
12. `unarchive-record` - clear the archived flag so a record becomes visible again

All commands read JSON from `stdin` and write JSON to `stdout`.

By default the persisted index lives at `data/graph.json`. You can override it with `graphPath` in the input payload.

## MCP server

The plugin now also includes a stdio MCP server in `scripts/context_graph_mcp.py`.

Implemented protocol surface:

- `initialize`
- `ping`
- `tools/list`
- `tools/call`
- `logging/setLevel` as a no-op acknowledgement
- `notifications/initialized`

The server is registered in [.mcp.json](/Users/maksnalyvaiko/context-graph/.mcp.json) and exposes the following tools:

- `classify_record`
- `link_record`
- `build_context_pack`
- `index_records`
- `search_graph`
- `promote_pattern`
- `ingest_markdown`
- `ingest_notion_export`
- `sync_notion`
- `delete_record`
- `archive_record`
- `unarchive_record`

Example:

```bash
echo '{"record":{"title":"Webhook race in deposit flow","content":"Duplicate payment creation after callback retry"}}' \
  | python3 scripts/context_graph_cli.py classify-record
```

```bash
echo '{"records":[{"id":"r1","title":"Webhook race in deposit flow","content":"Duplicate payment creation after callback retry","markers":{"type":"bug","domain":"payments","goal":"fix-bug","status":"in-progress"}}]}' \
  | python3 scripts/context_graph_cli.py index-records
```

```bash
echo '{"rootPath":"/tmp/context-graph-md","graphPath":"/tmp/context-graph-md/graph.json"}' \
  | python3 scripts/context_graph_cli.py ingest-markdown
```

```bash
echo '{"graphPath":"/tmp/context-graph-md/graph.json","recordIds":["src:markdown-context-graph-md-bug-md","src:markdown-context-graph-md-rule-md"],"writeToGraph":true}' \
  | python3 scripts/context_graph_cli.py promote-pattern
```

```bash
echo '{"rootPath":"/tmp/notion-export-sample","graphPath":"/tmp/notion-export-sample/graph.json"}' \
  | python3 scripts/context_graph_cli.py ingest-notion-export
```

```bash
echo '{"token":"'"$NOTION_TOKEN"'","databaseId":"<notion-database-id>","graphPath":"./data/graph.json","cursorPath":"./data/notion_cursor.json","index":true}' \
  | python3 scripts/context_graph_cli.py sync-notion
```

`sync-notion` requires the `NOTION_TOKEN` environment variable to be set (an internal integration token with access to the target database or page). Pass it into the payload as `token`.

## Promotion quality

`promote-pattern` now emits a `quality` block with:

- `score`
- `recommendation` (`safe`, `review`, `split`)
- marker conflict counts
- per-marker conflict details

This helps decide whether a promoted rule should be accepted as-is or split into narrower records.

It also emits `splitSuggestions`, which groups source records by high-signal conflicting markers such as `type`, `goal`, or `artifact`.

## Tests

The repository now includes fixture-based `unittest` coverage in [tests/test_core.py](/Users/maksnalyvaiko/context-graph/tests/test_core.py).

Run it with:

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
```

## First implementation targets

See [docs/roadmap.md](docs/roadmap.md) for the full plan. The next targets are:

1. Claude Code integration layer: skills, slash commands, SessionStart and PostToolUse hooks, non-empty `.app.json`
2. Live Notion sync: API client with delta sync, id mapping that dedupes with the export adapter, conflict policy
3. Lifecycle and safety: record delete with partial edge rebuilds, TTL for inferred edges, token storage and redaction rules
4. Evaluation harness: eval set, precision and recall metrics, CI regression gate

## Security and data

See [docs/security.md](docs/security.md) for Notion token handling: where to obtain the integration token, how to pass it via `NOTION_TOKEN` or the payload `token` field, and rotation guidance if it leaks.

See [docs/data-retention.md](docs/data-retention.md) for what lives under `data/` (`graph.json` carries full record bodies; `notion_cursor.json` is just a timestamp) and the recommended hygiene for keeping the graph out of version control.

See [docs/lifecycle.md](docs/lifecycle.md) for the user-facing view of record create, update, archive, delete, the TTL on inferred edges, and the optional redaction hook applied before a context pack is returned.

## Publishing note

The manifest uses `example.com` placeholders for external URLs. Replace them before distribution.
