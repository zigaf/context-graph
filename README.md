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

## Workspace layout

Run `init-workspace` or `/cg-init` once per project. This creates a local
workspace directory:

```text
.context-graph/
  workspace.json
  graph.json
  schema.learned.json
  schema.feedback.json
  idf_stats.json
  notion_cursor.json
```

`workspace.json` is the opt-in marker. Runtime state files are added to
`.gitignore` by default. The shipped schema still lives in `docs/schema.json`;
workspace-specific learned values and marker importance live in
`.context-graph/schema.learned.json`.

## First setup

For a new project, start with:

```bash
/cg-start
```

The command creates the local workspace if needed, asks whether your first
source is Notion or a local markdown folder, runs the first sync, and finishes
with a suggested `/cg-search` query. The lower-level commands (`/cg-init`,
`/cg-sync-notion`, `/cg-index`) remain available for manual workflows.

## Implemented MVP commands

The current scaffold includes a local CLI runtime in `scripts/context_graph_cli.py`.

Available commands:

1. `classify-record` - normalize markers, infer missing markers, and build hierarchy paths
2. `init-workspace` - create `.context-graph/workspace.json` and local state ignores
3. `link-record` - create inferred relations between one source record and candidate records
4. `build-context-pack` - rank records for a user request and return a compact retrieval payload
5. `index-records` - classify records, upsert them into local graph storage, rebuild edges, refresh IDF stats, and run the light learner
6. `search-graph` - build a context pack from the persisted graph index, using learned marker importance when available
7. `promote-pattern` - derive a reusable rule or decision record from related source records
8. `ingest-markdown` - scan markdown files, extract records from front matter plus headings, and optionally index them
9. `ingest-notion-export` - scan Notion markdown exports, preserve page ids from filenames, and resolve local links between exported pages
10. `sync-notion` - headless Notion API fallback for cron/CI; live sessions should prefer `/cg-sync-notion` through the official Notion MCP OAuth connection
11. `learn-schema` - run a full workspace learning pass and write proposal queues plus marker importance
12. `list-proposals` - list pending, accepted, and rejected schema proposals
13. `apply-proposal-decision` - accept, reject, or skip one schema proposal
14. `delete-record` - remove a record from the graph and rebuild affected edges
15. `archive-record` - hide a record from context packs and graph search without touching its edges
16. `unarchive-record` - clear the archived flag so a record becomes visible again

All commands read JSON from `stdin` and write JSON to `stdout`.

By default, commands resolve the nearest `.context-graph/workspace.json` by
walking up from the current directory. Set `CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1`
to use the plugin-local `data/` directory for legacy tests or scripts. You can
also override the graph with `graphPath` in the input payload.

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
- `init_workspace`
- `link_record`
- `build_context_pack`
- `index_records`
- `search_graph`
- `promote_pattern`
- `learn_schema`
- `list_proposals`
- `apply_proposal_decision`
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
echo '{}' | python3 scripts/context_graph_cli.py init-workspace
```

```bash
echo '{"records":[{"id":"r1","title":"Webhook race in deposit flow","content":"Duplicate payment creation after callback retry","markers":{"type":"bug","domain":"payments","goal":"fix-bug","status":"in-progress"}}],"workspaceRoot":"."}' \
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
echo '{"workspaceRoot":"."}' | python3 scripts/context_graph_cli.py learn-schema
```

In live Claude Code/Codex sessions, `/cg-sync-notion` uses the official Notion
MCP OAuth connection and does not ask for API keys. The Python `sync-notion`
command remains a headless fallback for cron/CI environments only.

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

## Slash commands

The plugin includes slash-command instructions for:

- `/cg-init`
- `/cg-index`
- `/cg-search`
- `/cg-classify`
- `/cg-sync-notion`
- `/cg-schema-learn`
- `/cg-schema-review`

Schema review is intentionally user-driven: proposals are never auto-accepted
without an explicit accept/reject/skip decision.

## Security and data

See [docs/security.md](docs/security.md) for headless Notion token handling.
Live session sync should use the official Notion MCP OAuth connection instead.

See [docs/data-retention.md](docs/data-retention.md) for what lives under `data/` (`graph.json` carries full record bodies; `notion_cursor.json` is just a timestamp) and the recommended hygiene for keeping the graph out of version control.

See [docs/lifecycle.md](docs/lifecycle.md) for the user-facing view of record create, update, archive, delete, the TTL on inferred edges, and the optional redaction hook applied before a context pack is returned.

## Publishing note

The manifest uses `example.com` placeholders for external URLs. Replace them before distribution.
