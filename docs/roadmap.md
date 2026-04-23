# Roadmap

## Phase 1 - Core runtime

Status: in progress

- [x] Define initial marker and relation schema
- [x] Implement local CLI for `classify-record`
- [x] Implement local CLI for `link-record`
- [x] Implement local CLI for `build-context-pack`
- [ ] Add direct fixture-based tests for relation inference scoring

## Phase 2 - Graph persistence

- [x] Introduce a stable record format with source metadata and revision info
- [x] Add local graph storage for normalized records and edges
- [x] Support incremental re-index for record upserts
- [x] Track explicit vs inferred edges separately
- [ ] Add delete support and partial edge rebuilds
- [x] Add fixture-based regression tests for graph persistence

## Phase 3 - MCP surface

- [x] Wrap the CLI runtime with an MCP server
- [x] Expose tools for classify, link, retrieve, and index
- [x] Add structured tool descriptions
- [x] Validate server startup from `.mcp.json`
- [x] Add `promote_pattern` to the MCP surface
- [x] Add `ingest_markdown` to the MCP surface
- [x] Add `ingest_notion_export` to the MCP surface
- [ ] Add tool examples and compatibility tests

## Phase 4 - Source adapters

- [x] Add a Notion-export adapter
- [x] Add local markdown folder ingestion
- [ ] Normalize source-specific metadata into one record model

## Phase 5 - Smarter retrieval

- [ ] Add query intent modes: debug, implementation, architecture, product
- [ ] Add freshness decay tuning by record type
- [ ] Add relation distance penalties beyond one hop
- [x] Promote repeated bugs into reusable rules and decisions
- [x] Improve promotion quality with stronger summaries and conflict detection
- [ ] Add conflict-aware splitting or narrower promotion suggestions
