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
5. If the `init_workspace` response has `alreadyExists: true`, continue with
   that existing workspace and set `workspaceRoot` to its `rootPath`. Treat
   this as a normal success, not an error.
6. Use the `rootPath` returned by `init_workspace` (which is the canonical,
   resolved path — for example, `/tmp` may resolve to `/private/tmp` on
   macOS) as `workspaceRoot` for all later Context Graph tool calls. Do not
   reconstruct it from the user's input.

## Step 2: Source Selection

Parse `$ARGUMENTS` for an optional first token:

- `notion`: choose Notion without asking.
- `markdown`: choose Local markdown without asking.
- `skip`: initialize only and skip first sync.
- A bare path that resolves to an existing directory and contains at least
  one `.md` file: treat as if the user typed `markdown <path>`. Skip the
  source-selection prompt and use the path in Step 3B.

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
3. Search Notion with the available official Notion search tool to collect
   page stubs for this first onboarding sync:
   - Query: the user's scope.
   - Query type: internal.
   - Page size: 25 (the current Notion MCP per-call maximum).
   - Filters: `{}`.
   - The current Notion MCP search does not return a continuation cursor or a
     total-result count, so a single call is the entire result set this
     onboarding has access to. The hard cap for the first batch is 25 pages.
4. If no Notion search tool is available, tell the user:
   `Notion is not connected in this session. Connect the official Notion OAuth integration, then rerun /cg-start notion <scope>.`
   Stop without changing the graph.
5. If search returns no pages, report that no matching Notion pages were found
   and ask the user to rerun with a broader or different scope.
6. If exactly 25 results were returned, treat the result set as possibly
   truncated and ask before continuing:
   `Notion returned 25 matching pages — that is the per-call cap, so there may be more. Continue with these 25, or narrow the scope to be sure? [continue/narrow]`
   Stop unless the user chooses `continue`; if they choose `narrow`, ask for a
   narrower scope and rerun the search from step 3. If fewer than 25 pages
   came back, skip this prompt — the result set is complete.
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
      - `id`: `notion:<32-hex page id without dashes>`
      - `title`: the `title` field of the fetch response (strip a leading
        emoji and surrounding whitespace if present)
      - `content`: the markdown text inside the `<content>...</content>` block
        of the fetch response. Drop the `Here is the result of "view"...`
        preamble and the surrounding `<page>`, `<ancestor-path>`, and
        `<properties>` tags. Keep inline `<page url="...">child</page>`
        references as plain text.
      - `source.system`: `notion`
      - `source.url`: Notion URL from the fetch response
      - `source.metadata.notionPageId`: raw page id without dashes
      - `source.metadata.last_edited_time`: the `timestamp` from the search
        result for this page (the fetch response does not carry it)
      - `source.metadata.parent`: ancestor titles joined by ` > ` in
        top-down order (root first, immediate parent last). The fetch
        response lists ancestors immediate-first as `<parent-page>`,
        `<ancestor-2-page>`, `<ancestor-3-page>`, ...; reverse that order
        before joining. Omit the field when no ancestors are present.
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
13. Report the actual pulled, skipped, and indexed counts. The summary always
    starts with `Context Graph is ready. Source: Notion.` and then includes:
    - `<N> pages pulled, <M> pages skipped, <R> records indexed.`
    - One of the following follow-on sentences only when relevant:
      - If 25 pages came back from search and the user accepted the truncated
        result in step 6: `Only the first 25 matching pages were considered.`
      - If the user narrowed the scope in step 6: `Scope was narrowed to <new scope>.`
      - Otherwise omit any extra sentence — do not emit a literal placeholder.
    - `Try: /cg-search <scope>`

## Step 3B: Local Markdown First Sync

Use this path when the user chose Local markdown.

1. Determine the notes path:
   - If `$ARGUMENTS` has text after `markdown`, use that as the path.
   - Otherwise ask:
     `Which folder of markdown notes should I index?`
2. Resolve the path to an absolute path.
3. Load the per-file freshness state by calling
   `mcp__context-graph__load_markdown_cursor` with
   `{"workspaceRoot": "<workspaceRoot>"}`.
4. Call `mcp__context-graph__ingest_markdown` with the loaded cursor and
   `index: false` to get classified records for files that have changed
   since the cursor was last saved:
   ```json
   {
     "rootPath": "<absolute notes path>",
     "recursive": true,
     "index": false,
     "cursor": <loaded cursor>
   }
   ```
5. If `fileCount` is zero and `skippedFileCount` is zero, report:
   `No markdown files found under <path>. Check the folder and rerun /cg-start markdown <path>.`
   Stop.
6. If `fileCount` is zero but `skippedFileCount` is greater than zero, report:
   `Context Graph is ready. No markdown changes found under <path>. Try: /cg-search <folder name or topic>`
   Stop.
7. For each returned record, run the same arbitration step used in Step 3A.10:
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
8. Call `mcp__context-graph__index_records` once with all finalized records:
   `{"records": <finalized records>, "graphPath": "<workspaceRoot>/.context-graph/graph.json"}`.
9. Persist the advanced cursor returned by `ingest_markdown` by calling
   `mcp__context-graph__save_markdown_cursor` with
   `{"cursor": <returned cursor>, "workspaceRoot": "<workspaceRoot>"}`.
10. Report:
    `Context Graph is ready. Source: Local markdown. <N> files processed, <M> files skipped, <R> records indexed. Try: /cg-search <folder name or topic>`

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
