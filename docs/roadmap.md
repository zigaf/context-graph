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
- [ ] Switch `.mcp.json` server path to `${CLAUDE_PLUGIN_ROOT}/scripts/context_graph_mcp.py` so the MCP server resolves when the plugin is installed outside this repo — verify it does not break Codex
- [ ] Scope the PostToolUse reindex hook: it currently fires for any `.md` file Claude edits anywhere on disk, which can pollute `data/graph.json`. Limit to files under a known notes root or only if the containing dir already has records in the graph
- [ ] Validate that the same plugin tree loads in both Claude Code and Codex without duplication

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
- [ ] Make `merge_record` order-aware by comparing `last_edited_time` so a stale replay (backfill, rewound cursor) cannot overwrite a newer record — see Phase 6
- [ ] Extend Notion block-type coverage: `table`, `toggle`, `callout`, `column_list`, `link_to_page`, `image` (currently stubbed in `notion_markdown.py`)
- [ ] Run live smoke-test against a real Notion workspace and record fixtures for integration tests
- [ ] Optional push: write promoted rules and decisions back to a Notion database

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

Status: not started

Goal: keep the graph small, correct, and safe over time.

- [ ] Add record delete with partial edge rebuild (only recompute edges for neighbors)
- [ ] Add TTL or decay for inferred edges so stale probable links drop out
- [ ] Add an archive mode that keeps records out of retrieval but not out of storage
- [ ] Make `merge_record` in `context_graph_core.py` order-aware: when the incoming record carries a `last_edited_time` / `revision.updatedAt` older than the stored one, keep the stored copy (flagged by Phase 4a — remote-wins currently breaks on out-of-order replays)
- [ ] Define Notion token storage rules and document them in the README
- [ ] Define data retention policy for `data/graph.json` and any exported bundles
- [ ] Add an optional redaction hook that runs before a record enters a context pack (e.g., strip emails, tokens)

Acceptance: deleting a record does not leave dangling edges, and an inferred edge older than its TTL is not returned by `search_graph`.

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
