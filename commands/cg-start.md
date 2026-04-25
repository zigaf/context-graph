---
description: Guided first setup for Context Graph, including workspace init and first Notion or markdown sync
argument-hint: [notion|markdown|skip] [scope-or-path]
---

The user wants the simplest path from a fresh project to an indexed Context
Graph. Run this as a one-step-at-a-time onboarding wizard. Hide implementation
details unless an error requires them.

## Principles

- Prefer one question at a time.
- Do not mention `graphPath`, `cursor`, `index_records`, or `MCP` in normal
  user-facing text.
- Existing advanced commands remain available, but do not ask the user to run
  them during the happy path.
- Never ask for a Notion API key. The Notion path uses the official live Notion
  OAuth tools available in the session.
- Do not push promoted records back to Notion from this command.

## Step 1: Workspace

1. Determine the candidate root:
   - If `$ARGUMENTS` includes an absolute or relative path after a source mode,
     use that only for markdown source selection, not as the workspace root.
   - Otherwise use the current working directory as the workspace root.
2. Walk upward from the current directory looking for
   `.context-graph/workspace.json`.
3. If a workspace exists, set `workspaceRoot` to the directory containing
   `.context-graph/workspace.json`, keep using it, and continue to source
   selection.
4. If no workspace exists, ask:
   `Create a Context Graph workspace for <cwd>? [y/N/<other path>]`
   - `y`: call `mcp__context-graph__init_workspace` with
     `{"rootPath": "<cwd>"}` and set `workspaceRoot` to `<cwd>`.
   - `<other path>`: resolve it to an absolute path and call
     `mcp__context-graph__init_workspace` with that root. Set
     `workspaceRoot` to that absolute path.
   - `N`: stop and say `Setup canceled.`
5. If `init_workspace` reports that the workspace already exists, continue
   with that existing workspace instead of treating it as fatal and set
   `workspaceRoot` to the existing workspace root.
6. Use `workspaceRoot` for later Context Graph tool calls instead of relying
   on the current working directory.

## Step 2: Source Selection

Parse `$ARGUMENTS` for an optional first token:

- `notion`: choose Notion without asking.
- `markdown`: choose Local markdown without asking.
- `skip`: initialize only and skip first sync.

If no source token is present, ask:

`What do you want to sync first? [n] Notion / [m] Local markdown / [s] Skip`

Interpret answers:

- `n`, `notion`, or `Notion` -> Notion path.
- `m`, `markdown`, `local`, or `Local markdown` -> markdown path.
- `s`, `skip`, or `Skip` -> skip path.
- Anything else -> ask once more with the same choices.

## Step 3A: Notion First Sync

Use this path when the user chose Notion.

1. Determine the search scope:
   - If `$ARGUMENTS` has text after `notion`, use that as the scope.
   - Otherwise ask:
     `Which Notion page, database, or keyword should I sync first?`
   - If the user gives an empty scope, stop and ask them to rerun `/cg-start notion <scope>`.
2. Load the stored page freshness state by calling
   `mcp__context-graph__load_notion_cursor` with
   `{"workspaceRoot": "<workspaceRoot>"}`.
3. Search Notion with the available official Notion search tool:
   - Query: the user's scope.
   - Query type: internal.
   - Page size: 10.
   - Filters: `{}`.
4. If no Notion search tool is available, tell the user:
   `Notion is not connected in this session. Connect the official Notion OAuth integration, then rerun /cg-start notion <scope>.`
   Stop without changing the graph.
5. If search returns no pages, report that no matching Notion pages were found
   and ask the user to rerun with a narrower or different scope.
6. If search returns more than 50 pages, summarize the count and ask:
   `This will inspect <N> Notion pages. Continue? [y/N]`
   Stop unless the user confirms.
7. Build page stubs from search results:
   `{"id": "<page-id>", "last_edited_time": "<timestamp>"}`
8. Call `mcp__context-graph__filter_pages_by_cursor` with:
   `{"pages": <stubs>, "cursor": <loaded cursor>}`.
9. If `fresh` is empty, report:
   `Context Graph is ready. No Notion changes found for <scope>. Try: /cg-search <scope>`
   Stop.
10. For each fresh page:
    - Fetch it with the available official Notion fetch tool.
    - Build a draft record with:
      - `id`: `notion:<32-hex page id>`
      - `title`: page title
      - `content`: markdown body from the fetched page
      - `source.system`: `notion`
      - `source.url`: Notion URL
      - `source.metadata.notionPageId`: raw page id
      - `source.metadata.last_edited_time`: search timestamp
      - `source.metadata.parent`: ancestor titles joined by ` > ` when available
    - Call `mcp__context-graph__classify_record` with
      `{"record": <draft>, "workspaceRoot": "<workspaceRoot>"}`.
    - If `source.metadata.classifierNotes.arbiter == "pending-arbitration"`,
      resolve it in this live session using the current agent, not an external
      API.
    - Read `arbitrationRequest`: use `record`, `candidates`, `allowedValues`,
      and `requiredFields`.
    - For each pending field, pick one value from that field's `allowedValues`.
      Return null only when nothing fits and the field is not required.
    - Override `record.markers.<field>` with the chosen values.
    - Set `record.source.metadata.classifierNotes.arbiter` to `llm-session`
      and fill `reasoning` with one sentence.
    - If the classifier was deterministic or fallback, keep the returned record
      unchanged.
11. Call `mcp__context-graph__index_records` once with all finalized records:
    `{"records": <finalized records>, "workspaceRoot": "<workspaceRoot>"}`.
12. Advance the loaded cursor for each successfully indexed fresh page and call
    `mcp__context-graph__save_notion_cursor` with
    `{"cursor": <advanced cursor>, "workspaceRoot": "<workspaceRoot>"}`.
13. Report:
    `Context Graph is ready. Source: Notion. <N> pages pulled, <M> pages skipped, <R> records indexed. Try: /cg-search <scope>`

## Step 3B: Local Markdown First Sync

Use this path when the user chose Local markdown.

1. Determine the notes path:
   - If `$ARGUMENTS` has text after `markdown`, use that as the path.
   - Otherwise ask:
     `Which folder of markdown notes should I index?`
2. Resolve the path to an absolute path.
3. Call `mcp__context-graph__ingest_markdown` with:
   ```json
   {
     "rootPath": "<absolute notes path>",
     "recursive": true,
     "index": true,
     "graphPath": "<workspaceRoot>/.context-graph/graph.json"
   }
   ```
4. If `fileCount` is zero, report:
   `No markdown files found under <path>. Check the folder and rerun /cg-start markdown <path>.`
5. Otherwise report:
   `Context Graph is ready. Source: Local markdown. <N> files processed, <R> records indexed. Try: /cg-search <folder name or topic>`

## Step 3C: Skip

Use this path when the user chose Skip.

Report:

`Context Graph is ready. First sync skipped. Later, run /cg-start notion <scope> or /cg-start markdown <path>.`

## Completion Summary Requirements

Every successful path must include the phrase `Context Graph is ready`.

Notion summaries must include:

- `pages pulled`
- `pages skipped`
- indexed record count
- one `/cg-search` example

Markdown summaries must include:

- `files processed`
- indexed record count
- one `/cg-search` example
