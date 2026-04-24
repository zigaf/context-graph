# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Query intent modes: `debug`, `implementation`, `architecture`, `product`
  — explicit `intentMode` payload field on `build_context_pack`,
  `search_graph`, and `inspect_record`; optional `intentOverride`
  escape hatch for tuning at query time; slash command
  `/cg-search --mode <name>`. Eval harness now routes each query
  through its declared intent; baseline updated accordingly.
  See `docs/retrieval.md` and `docs/superpowers/specs/2026-04-24-intent-modes-design.md`.

### Pending for v0.1.0 release

- Live validation that the plugin loads in both Claude Code and Codex without duplication (Phase 3.5)
- Live Notion smoke-test against a real workspace (Phase 4a)

## [0.1.0] - 2026-04-24

Initial release.

### Added

- **Core runtime** — classify, link, retrieve, and index MCP tools over a local graph store (`scripts/context_graph_core.py`, `scripts/context_graph_cli.py`, `scripts/context_graph_mcp.py`).
- **Workspace binding** — per-project `.context-graph/workspace.json` resolved by walk-up; `/cg-init` slash command; all graph state, learned schema, IDF stats, Notion cursor, and push state scoped per workspace.
- **Adaptive classifier** — region extractor, IDF-weighted scorer, threshold arbiter, and self-mining learning loop (hierarchy, n-gram, code-path). Proposal accept/reject/skip lifecycle via `learn_schema`, `list_proposals`, `apply_proposal_decision`.
- **Live Notion sync** — `/cg-sync-notion` orchestrates the official Notion MCP with per-page delta/cursor (`.context-graph/notion_cursor.json`) so repeat syncs skip unchanged pages; Python fallback via `scripts/notion_sync.py`. Notion block-type coverage for `table`, `toggle`, `callout`, `column_list`, `link_to_page`, `image`.
- **Optional push-back** — opt-in `push-notion` CLI + MCP tools (`plan_notion_push`, `apply_notion_push_result`, `record_to_notion_payload`); idempotent via `.context-graph/notion_push.json` mapping.
- **Lifecycle** — record delete with per-neighbor partial edge rebuild (equivalent to full rebuild, verified), TTL for inferred edges (30d default), archive/unarchive, order-aware `merge_record`, redactor registry.
- **Retrieval evaluation** — precision@k / recall@k harness with committed baseline; `context_graph_cli.py eval` exits non-zero on regression; MCP tool `eval_retrieval`.
- **Unified record model** — adapter-contract documented in `docs/record-model.md` and `docs/adapter-guide.md`; new adapters require registering a system name without editing core (two known deviations flagged for cleanup).
- **Claude Code + Codex plugin manifests** — SessionStart hook primes a context pack; PostToolUse reindexes markdown edits in scope.

### Known limitations

- Two code paths still switch on `source.system` (`scripts/post_edit_reindex.py`, `scripts/context_graph_core.py::explicit_id_for_markdown_file`) — documented in `docs/adapter-guide.md`, tracked for cleanup.
- Notion push update semantics = full body replace; concurrent edits in Notion between syncs are overwritten.
- Eval harness recall saturates at 1.0 on the 15-record seed fixture; larger corpora will expose variance.
- No CI wiring yet; eval regression check runs locally only.

[Unreleased]: https://github.com/zigaf/context-graph/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zigaf/context-graph/releases/tag/v0.1.0
