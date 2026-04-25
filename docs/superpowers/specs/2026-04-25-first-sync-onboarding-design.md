# First Sync Onboarding Design

Date: 2026-04-25

## Problem

Context Graph has useful primitives, but the first successful setup requires
too many user decisions. A new user needs to know when to run `/cg-init`,
whether Notion is connected, what `/cg-sync-notion` expects as a scope, how
bootstrap relates to sync, and when local markdown ingestion is the right path.

The first-run experience should hide those internals. The user should be able
to run one command, pick a source, and finish with indexed records in the graph.

## Goal

Add a hybrid onboarding command, `/cg-start`, that guides the user through the
first workspace setup and first sync.

The command supports two source paths:

- Notion-first, using the live Notion MCP/OAuth connection.
- Local markdown, using existing markdown ingestion.

Existing commands remain available as advanced/manual commands.

## Non-goals

- Do not replace `/cg-init`, `/cg-sync-notion`, `/cg-bootstrap`, or `/cg-index`.
- Do not introduce a new Notion API-token flow.
- Do not auto-push promoted records back to Notion during onboarding.
- Do not build a visual UI; this is a slash-command wizard.

## User Flow

The happy path is one command:

```text
/cg-start
```

The command proceeds one step at a time:

1. Detect whether a Context Graph workspace exists.
2. If no workspace exists, ask the user to confirm the current directory as the
   workspace root, then initialize it.
3. Ask which source to sync:
   - Notion
   - Local markdown
   - Skip for now
4. For Notion, ask for a search scope such as a page title, database name, or
   keyword. Then search, cursor-filter, fetch fresh pages, classify, index, and
   save the cursor.
5. For local markdown, ask for the notes directory and run markdown ingestion
   with indexing enabled.
6. Report a compact completion summary and show one concrete next command, for
   example `/cg-search <topic>`.

The user-facing text should avoid implementation terms such as `graphPath`,
`cursor`, `index_records`, and `MCP` unless an error requires them.

## State Handling

The command must be idempotent:

- If the workspace already exists, skip initialization.
- If Notion has already synced pages, use the stored cursor and report "no
  changes since last sync" when nothing is fresh.
- If the local markdown path has been indexed before, re-ingestion updates the
  graph rather than creating duplicates.
- If the user chooses skip, the workspace still remains initialized.

Failure handling:

- If Notion tools are unavailable, explain that the official Notion OAuth
  connection must be enabled and tell the user to rerun `/cg-start`.
- If Notion search finds too many pages, ask for confirmation before fetching
  bodies.
- If classification returns pending arbitration, resolve it in-session using
  the same rules already documented in `/cg-sync-notion`.
- If markdown ingestion finds zero files, report the path and suggest checking
  the folder or pattern.

## Architecture

Implement the first version mostly as command orchestration:

- Add `commands/cg-start.md`.
- Reuse existing MCP tools:
  - `init_workspace`
  - `load_notion_cursor`
  - `filter_pages_by_cursor`
  - `classify_record`
  - `index_records`
  - `save_notion_cursor`
  - `ingest_markdown`
- Reuse the Notion MCP search/fetch/create tools already referenced by
  `/cg-sync-notion` where available.

Add a small core helper only if command orchestration needs a single status
payload:

- `workspace_status`
  - `initialized`
  - `workspaceRoot`
  - `workspaceId`
  - `notionRootPageId`
  - `bootstrapDeclined`
  - `recordCount`
  - `edgeCount`

The helper is optional for the first implementation. If existing tools and
manifest reads are enough, defer it.

## Completion Summary

At the end, `/cg-start` should return a concise summary:

- Workspace path.
- Source type.
- For Notion: pages pulled, pages skipped, records indexed, arbitration count.
- For markdown: files processed and records indexed.
- Next command to try.

Example:

```text
Context Graph is ready.
Workspace: /path/to/project
Source: Notion
Pulled 8 pages, skipped 12 unchanged pages, indexed 8 records.
Try: /cg-search webhook retry
```

## Testing

Add tests at the command/tool level where possible:

- Existing `init_workspace` idempotence is already covered by workspace tests.
- Existing cursor filtering and Notion sync pieces are covered by their current
  tests.
- Add a command documentation smoke test if the project already has command
  validation conventions.
- If `workspace_status` is added, test initialized and uninitialized cases.

Manual verification:

1. Fresh repo with no `.context-graph`: run `/cg-start`, choose markdown, and
   verify records are indexed.
2. Fresh repo with Notion OAuth available: run `/cg-start`, choose Notion, and
   verify the first sync indexes records.
3. Rerun `/cg-start` for the same Notion scope and verify unchanged pages are
   skipped.

## Success Criteria

- First-time markdown setup requires `/cg-start` plus one path.
- First-time Notion setup requires `/cg-start` plus one search scope, assuming
  Notion OAuth is already connected.
- Users no longer need to know `/cg-init` or `/cg-sync-notion` to get their
  first indexed graph.
- Re-running `/cg-start` is safe and does not duplicate records.
