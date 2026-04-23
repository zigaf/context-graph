---
description: Pull Notion pages into the Context Graph via the official Notion MCP
argument-hint: <scope> [--full]  (search query, page title, or database name; optional --full forces a complete refetch)
---

The user wants to sync Notion content into the workspace's Context Graph. The Notion MCP is expected to be connected through the live session's OAuth connection, so no API key or Notion token is needed. The plugin stores a per-page cursor (`page_id -> last_edited_time`) so repeat syncs skip pages Notion has not edited since the last run.

Steps:

1. Confirm a Context Graph workspace exists.
   - If the session is in a directory without `.context-graph/workspace.json`, tell the user to run `/cg-init` first and stop.

2. Parse arguments.
   - Split `$ARGUMENTS` into `scope` (the search query) and an optional `--full` flag.
   - `--full` forces a clean resync: ignore the stored cursor, fetch every page in scope, and overwrite the cursor at the end. Use this when the cursor file looks corrupted or the user explicitly asks for a full re-sync.
   - If `scope` is empty, ask the user for a search scope such as a keyword, page title, or database name, then stop.

3. Load the stored cursor.
   - Call `mcp__context-graph__load_notion_cursor` with no arguments. It returns `{"cursor": {"<page-id>": "<iso-last_edited_time>", ...}}`. An empty `{}` means no prior sync.
   - If `--full` was passed, treat the cursor as `{}` for filtering — but keep the real loaded dict around so the final save merges with any entries for pages that were not in this scope.

4. Search via the available Notion MCP search tool.
   - Use whichever Notion search tool is registered in the session, such as `mcp__notion__notion-search`.
   - Pass `query: <scope>`, `query_type: "internal"`, `page_size: 10`, and `filters: {}`.
   - If no Notion MCP tool is connected, tell the user to connect the official Notion MCP/OAuth integration for this session and stop.
   - If more than about 50 pages are returned, ask the user to confirm before pulling all of them.
   - Each search hit should carry a `timestamp` field. Build a list of page stubs of the form `{"id": "<page-id>", "last_edited_time": "<timestamp>"}` — these go into the cursor filter.

5. Filter by cursor **before fetching any bodies**.
   - Call `mcp__context-graph__filter_pages_by_cursor` with `{"pages": <stubs>, "cursor": <loaded-cursor or {} if --full>}`.
   - It returns `{"fresh": [...], "stale": [...], "newCursorHint": "<iso or null>"}`.
   - `stale` pages are skipped — do NOT call the Notion fetch tool for them. That is the whole point of this step: `notion-fetch` is the expensive call, so we skip it for pages Notion has not edited since the cursor was last saved.
   - If `fresh` is empty, report "no changes since last sync" and stop (nothing to fetch, nothing to save).

6. For each page in `fresh`, in order:
   - Fetch it through the available Notion MCP fetch tool.
   - Record the search result `timestamp` as `last_edited_time`.
   - Build a draft record:
     - `id`: `notion:<32-hex page id>` after stripping UUID hyphens and lowercasing.
     - `title`: page title.
     - `content`: markdown body between `<content>` and `</content>` inside `text`.
     - `source.system`: `notion`.
     - `source.url`: full Notion URL.
     - `source.metadata`: `notionPageId`, `last_edited_time`, and `parent` as reversed ancestor-path titles joined with ` > `.

7. Classify each draft record.
   - Call `mcp__context-graph__classify_record` with the draft.
   - If `source.metadata.classifierNotes.arbiter == "pending-arbitration"`, resolve it in this live session using the current agent, not an external API.
   - Read `arbitrationRequest`: use `record`, `candidates`, `allowedValues`, and `requiredFields`.
   - For each pending field, pick one value from that field's `allowedValues`. Return null only when nothing fits and the field is not required.
   - Override `record.markers.<field>` with the chosen values.
   - Set `record.source.metadata.classifierNotes.arbiter` to `llm-session` and fill `reasoning` with one sentence.
   - If the classifier was deterministic or fallback, keep the returned record unchanged.

8. Index once.
   - Call `mcp__context-graph__index_records` once with the finalized batch.

9. Advance the cursor.
   - Start from the cursor you loaded in step 3 (the real one, not the zeroed-out filter cursor). When `--full` was used, the stored cursor is still the baseline for any pages out of scope.
   - For each page in `fresh` that was successfully merged, set `cursor[<raw page id>] = <page last_edited_time>`. (Use the raw page id as returned by `notion-search`, not the stripped 32-hex id — the cursor indexes by whatever id the search tool used so a later `filter_pages_by_cursor` call lines up.)
   - Call `mcp__context-graph__save_notion_cursor` with `{"cursor": <advanced-cursor>}`.

10. Report:
    - Pages pulled (from `fresh`) and pages skipped (from `stale`).
    - Records upserted from `indexResult.upsertedIds`.
    - Count of records resolved by `llm-session` arbitration.
    - The `newCursorHint` for quick verification.
    - If new proposals were produced, mention `/cg-schema-review`.

Do not invent marker values beyond `allowedValues`. Do not ask for or use API keys. If validation rejects a marker, fall back to the classifier's deterministic top value.
