# Roadmap

Scope: plugin for Claude Code and Codex that classifies notes, syncs with Notion, and retrieves compact context packs on demand.

## Phase 1 - Core runtime

Status: done

- [x] Define initial marker and relation schema
- [x] Implement local CLI for `classify-record`
- [x] Implement local CLI for `link-record`
- [x] Implement local CLI for `build-context-pack`

Relation inference scoring tests are tracked in the cross-cutting testing section, not here.

## Phase 2 - Graph persistence

Status: done

- [x] Introduce a stable record format with source metadata and revision info
- [x] Add local graph storage for normalized records and edges
- [x] Support incremental re-index for record upserts
- [x] Track explicit vs inferred edges separately
- [x] Add fixture-based regression tests for graph persistence

Delete, TTL, and partial edge rebuilds are promoted to Phase 6 (Lifecycle and safety).

## Phase 3 - MCP surface

Status: done

- [x] Wrap the CLI runtime with an MCP server
- [x] Expose tools for classify, link, retrieve, and index
- [x] Add structured tool descriptions
- [x] Validate server startup from `.mcp.json`
- [x] Add `promote_pattern` to the MCP surface
- [x] Add `ingest_markdown` to the MCP surface
- [x] Add `ingest_notion_export` to the MCP surface

Tool examples and cross-client compatibility tests live in the cross-cutting testing section.

## Phase 3.5 - Claude Code integration layer

Status: in progress

Goal: make the plugin first-class in Claude Code, not only as an MCP server.

- [x] Add a `skills/` directory with skill manifests for classify, ingest, search, and promote
- [x] Add slash commands: `/cg-index`, `/cg-search`, `/cg-classify`, `/cg-sync-notion` (sync is a stub pointing at Phase 4a)
- [x] Populate `hooks.json` with a SessionStart hook that primes a small context pack
- [x] Populate `hooks.json` with a PostToolUse hook that re-indexes markdown files after Write or Edit
- [x] Add a Claude Code manifest at `.claude-plugin/plugin.json` so the harness discovers skills, commands, hooks, and the MCP server
- [x] Switch `.mcp.json` server path to `${CLAUDE_PLUGIN_ROOT}/scripts/context_graph_mcp.py` so the MCP server resolves when the plugin is installed outside this repo
- [x] Scope the PostToolUse reindex hook via `scripts/post_edit_reindex.py` — reindexes only when the edited file's directory is at or below a directory already represented in the graph (markdown or notion-export sources)
- [ ] Validate in a live session that the same plugin tree loads in both Claude Code and Codex without duplication (Codex-side `${CLAUDE_PLUGIN_ROOT}` expansion is untested)

Note on `.app.json`: it is a Codex-only concept (consumed by `.codex-plugin/plugin.json`). Claude Code discoverability comes entirely from `.claude-plugin/plugin.json`, so there is nothing to populate in `.app.json` for this phase.

Acceptance: a fresh Claude Code session auto-loads a context pack relevant to the working directory without any manual command.

## Phase 4 - Static source adapters

Status: in progress

- [x] Add a Notion-export adapter
- [x] Add local markdown folder ingestion
- [ ] Define a unified record model spec: required fields, source-specific metadata namespace, id stability rules
- [ ] Document how adapters should populate `source.metadata` so downstream code never branches on `source.system`

Acceptance: a new adapter can be added without changing `context_graph_core` beyond registering a system name.

## Phase 4a - Live Notion sync

Status: in progress

Goal: move beyond one-shot export ingestion to a live, bidirectional link with a Notion workspace.

- [x] Add a Notion API client with token auth read from env (`NOTION_TOKEN`) — `scripts/notion_client.py`
- [x] Pull pages by database id or parent page with pagination
- [x] Delta sync using `last_edited_time` and a cursor stored in `data/notion_cursor.json`
- [x] Map `notionPageId` to a single record id so the export adapter and the live adapter never create duplicates (canonical: `notion:<32-hex>`)
- [x] Define conflict policy when local edits and Notion edits diverge — documented in `docs/notion-sync.md`; current behavior is remote-wins on content
- [x] Add a `/cg-sync-notion` slash command and an MCP tool `sync_notion`
- [x] Make `merge_record` order-aware by comparing `last_edited_time` so a stale replay (backfill, rewound cursor) cannot overwrite a newer record
- [x] Rework `/cg-sync-notion` to orchestrate the **official Notion MCP** (`notion-search` + `notion-fetch`) so users do not have to create an internal integration or manage `NOTION_TOKEN` — the Python client remains as the headless fallback (see `docs/notion-sync.md`)
- [ ] Delta/cursor support over the MCP path (`notion-search` does not expose a `last_edited_time` filter; for now `merge_record` handles idempotency but every sync refetches the full scope)
- [ ] Extend Notion block-type coverage in the Python client fallback: `table`, `toggle`, `callout`, `column_list`, `link_to_page`, `image` (stubbed in `notion_markdown.py`)
- [ ] Run live smoke-test and record fixtures for integration tests
- [ ] Optional push: write promoted rules and decisions back to Notion (either via the Notion MCP `notion-create-pages` / `notion-update-page` or the Python client)

Acceptance: running sync twice in a row is a no-op, and a Notion edit reflects in the graph on the next sync without duplicating the record. (Met in offline tests; pending live smoke-test.)

## Phase 5 - Smarter retrieval

Status: in progress

- [ ] Add query intent modes: `debug`, `implementation`, `architecture`, `product`
  - Spec: each mode changes which markers dominate ranking, which relation types are followed, and how many hops are allowed
- [ ] Add freshness decay tuning by record type (rules and decisions decay slower than tasks and incidents)
- [ ] Add relation distance penalties for hops beyond one, reconciled with the one-hop default in `docs/retrieval.md`
- [x] Promote repeated bugs into reusable rules and decisions
- [x] Improve promotion quality with stronger summaries and conflict detection
- [ ] Add conflict-aware splitting or narrower promotion suggestions

Acceptance: `build_context_pack` returns visibly different results for the same query when intent mode is switched.

## Phase 6 - Lifecycle and safety

Status: in progress

Goal: keep the graph small, correct, and safe over time.

- [x] Add record delete with partial edge rebuild — `delete_record` drops touching edges then reruns `rebuild_edges` (idempotent) over survivors, preserving `createdAt` for surviving inferred edges
- [x] Add TTL for inferred edges — `INFERRED_EDGE_TTL_DAYS = 30` default; `search_graph` filters at read time with `inferredEdgeTtlDays` payload override
- [x] Add archive mode — `archive_record` / `unarchive_record`; `build_context_pack` and `search_graph` filter by default, `includeArchived` flag disables
- [x] Make `merge_record` in `context_graph_core.py` order-aware: when the incoming record carries a `last_edited_time` older than the stored one, keep the stored copy
- [x] Add an optional redaction hook — `_REDACTORS` registry, `register_redactor` / `clear_redactors` / `strip_obvious_secrets` built-in
- [x] Define Notion token storage rules — `docs/security.md`
- [x] Define data retention policy for `data/graph.json` and any exported bundles — `docs/data-retention.md`
- [ ] True per-neighbor partial edge rebuild for delete (current impl rebuilds over all survivors; acceptable while corpora are small, revisit when graph size warrants)

Acceptance: deleting a record does not leave dangling edges (verified), and an inferred edge older than its TTL is not returned by `search_graph` (verified).

## Phase 6 follow-ups - Phase 1 adaptive plan

Status: done

- [x] Workspace binding via `.context-graph/workspace.json`
- [x] Workspace-local graph, learned schema, IDF stats, feedback, and Notion cursor paths
- [x] Adaptive classifier pipeline: regions, IDF weighting, scorer, threshold arbiter, `classifierNotes`, and `arbitrationRequest`
- [x] Learning loop: hierarchy, n-gram, code-path mining, marker importance, full-pass learner, proposal accept/reject/skip lifecycle
- [x] CLI/MCP surface for `init_workspace`, `learn_schema`, `list_proposals`, and `apply_proposal_decision`
- [x] Slash commands: `/cg-init`, `/cg-schema-learn`, `/cg-schema-review`
- [x] `/cg-sync-notion` orchestrates in-session arbitration through the live Notion MCP/OAuth path without API keys
- [x] Backward-compatible legacy plugin-data mode via `CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1`

## Phase 7 - Evaluation

Status: not started

Goal: measure that structured retrieval actually beats loading the full note set.

- [ ] Build an eval set of {query, expected direct matches, expected supporting relations}
- [ ] Compute precision@k and recall@k per query, plus context pack size vs full-dump baseline
- [ ] Track metrics over time; fail CI on regression
- [ ] Add an eval mode to the CLI that runs the full set and prints a summary

Acceptance: every merge to main includes an eval report that shows no precision regression.

## Cross-cutting - Schema versioning and migrations

Status: not started

- [ ] Stamp `schema.version` into every graph.json write
- [ ] Add a migration runner that upgrades older graph files on load
- [ ] Add tests that new aliases and new marker values do not break existing records

Acceptance: adding a new marker value in `docs/schema.json` never requires manually editing `data/graph.json`.

## Cross-cutting - Testing strategy

Status: in progress

- [x] Fixture-based tests for classify, index, search, promote, and Notion export ingestion
- [ ] Fixture-based tests for relation inference scoring (moved from Phase 1)
- [ ] MCP protocol compatibility tests: initialize, tools/list, tools/call round-trip (moved from Phase 3)
- [ ] Recorded-fixture integration tests for the live Notion adapter (Phase 4a)
- [ ] Eval-harness tests for retrieval quality (Phase 7)

## Cross-cutting - Observability

Status: not started

- [ ] `dry-run` flag on classify and ingest that prints what would change without writing
- [ ] `graph-diff` command that compares two graph snapshots
- [ ] `inspect-record` command that shows why a record was ranked at its current score, including matched markers and matched tokens

Acceptance: a user debugging a surprising context pack can explain the ranking without reading source code.

## First implementation targets

1. Phase 3.5 - Claude Code integration layer (close the harness gap first)
2. Phase 4a - Live Notion sync (close the "sync with Notion" gap)
3. Phase 6 - Delete and TTL (prevents the graph from rotting while we build more)
4. Phase 7 - Eval harness (so later changes do not silently regress retrieval)
