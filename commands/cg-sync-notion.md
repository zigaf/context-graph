---
description: Pull Notion pages into the Context Graph via the official Notion MCP, and optionally push promoted rules/decisions back.
argument-hint: <scope> [--full | push]  (search query, page title, or database name; --full forces a complete refetch; "push" pushes promoted records back)
---

The user wants to sync Notion content into the workspace's Context Graph. The Notion MCP is expected to be connected through the live session's OAuth connection, so no API key or Notion token is needed. The plugin stores a per-page cursor (`page_id -> last_edited_time`) so repeat syncs skip pages Notion has not edited since the last run.

## Pull (default)

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

## Push (opt-in)

Push promotes the reverse direction: local `rule` and `decision` records (the output of `promote_pattern`) are written back to Notion so the workspace becomes a real second memory.

**Do not push without explicit consent.** Trigger the push flow only when the user's message includes a clear intent like "push", "push to Notion", "send promoted rules back", or when `$ARGUMENTS` contains the literal token `push`. If the user uses the unambiguous phrasing `push-auto` (or `--push-auto`), treat that as pre-confirmed and skip the confirmation prompt below. In every other case, confirm first: summarize which records would be affected (from `plan_notion_push`) and ask "push these N records to Notion?" — proceed only on a yes.

Steps:

1. Confirm a Context Graph workspace exists with a Notion root page.
   - Call `mcp__context-graph__plan_notion_push` with `{graphPath?, workspaceRoot?, recordIds?}`.
   - Read `plan.creates` and `plan.updates`. If both are empty, tell the user there is nothing to push and stop.
   - If any `create` exists and the workspace has no `notion.rootPageId` (the user skipped the Notion root during `/cg-init`), tell the user to re-run `/cg-init` with a Notion root page and stop. You can detect this by checking `workspace.json` or by noting the first `create` path failing with a clear error.

2. Show the plan.
   - Summarize: "Will create N pages and update M pages. Creates land under Notion root page `<rootPageId>`. Proceed?"
   - Wait for explicit confirmation (unless `push-auto`).

3. For each entry in `plan.creates`:
   - Call `mcp__context-graph__record_to_notion_payload` with `{recordId, graphPath?, workspaceRoot?}` to get `{title, blocks, content, parentPageId}`.
   - Call `mcp__notion__notion-create-pages` with:
     - `parent: {type: "page_id", page_id: parentPageId}`
     - `pages: [{properties: {title: title}, content: content}]`
   - On success, extract the new page id from the create-pages response.
   - Call `mcp__context-graph__apply_notion_push_result` with `{recordId, notionPageId, workspaceRoot?}`.

4. For each entry in `plan.updates`:
   - Call `mcp__context-graph__record_to_notion_payload` with `{recordId, graphPath?, workspaceRoot?}`.
   - Call `mcp__notion__notion-update-page` with:
     - `page_id: plan.updates[i].notionPageId`
     - `command: "replace_content"`
     - `new_str: <the content field from record_to_notion_payload>`
     - `allow_deleting_content: true` (body-only replacement of a rule page; no child pages are ever created here)
     - `properties: {}`, `content_updates: []` (required-field placeholders)
   - Call `mcp__context-graph__apply_notion_push_result` with `{recordId, notionPageId, workspaceRoot?}` (idempotent: preserves the existing mapping).

5. Summarize what was pushed.
   - List created page ids, updated page ids, and any records that were skipped.

### Error handling

- **No `notionRootPageId`:** the push stops before any network call. Tell the user to re-init the workspace with a Notion root page (`/cg-init`), or to use `recordIds` scoped to records that are already in the push state (i.e., already exist in Notion).
- **Notion returns an error on create:** skip that record, surface the error, and continue with the rest. The push state file is only updated after a successful response, so the record stays in `creates` on the next run and the push is automatically retry-safe.
- **Notion returns an error on update:** same pattern — skip, report, continue. The mapping is preserved, so the next run retries the update.
- **Half-finished push:** everything written to `.context-graph/notion_push.json` is final and committed after each success. Re-running the slash command picks up at the next unpushed record; successful creates never duplicate because `plan_notion_push` classifies them as updates on the second pass.

### Headless / CI fallback

For scripted runs without a live session, use the Python CLI:

```
python3 scripts/context_graph_cli.py push-notion --dry-run
python3 scripts/context_graph_cli.py push-notion --apply
```

The CLI defaults to `--dry-run` so accidental invocation cannot duplicate content.

## Auto (driven by triggers)

Use this mode when invoked from `scripts/trigger_detect.py`. Do NOT
prompt for confirmation in this mode — the user has already opted in
during onboarding by accepting the auto-push hooks.

Steps:

1. Read `.context-graph/auto_push_plan.json`. If the file does not
   exist or `blocked` is true, exit silently with the appropriate
   one-line message:
   - `blocked == true` and `reason == "no-notion-root"`: `Auto-push paused: run /cg-bootstrap first.`
   - File missing: nothing — the trigger script already exited cleanly.
2. For each entry in `creates`:
   a. Call `mcp__context-graph__record_to_notion_payload` with `{recordId}`.
   b. Call `mcp__notion__notion-create-pages` with `parent: {type: "page_id", page_id: parentPageId}` and the returned title/body.
   c. Capture the new page id and append `{recordId, notionPageId, revision, pushedAt}` to a results list, where `pushedAt` is the current ISO-8601 UTC timestamp.
3. For each entry in `updates`:
   a. Call `mcp__context-graph__record_to_notion_payload`.
   b. Call `mcp__notion__notion-update-page` with `page_id: notionPageId`, `command: "replace_content"`, `allow_deleting_content: true`, and the returned content.
   c. Append `{recordId, notionPageId, revision, pushedAt}` to results.
4. Call the CLI subcommand `apply-auto-push-result` with `{"results": <list>}`. This dequeues successful records and writes their `lastPushedRevision`/`lastPushedAt` into `notion_push.json`.
5. Print a summary block:

```text
Auto-pushed to Notion
  + Rule: <title> → <dir>
  + Decision: <title> → <dir>
```

Skipped records (from `plan.skipped`) are mentioned as a single line at
the end if non-empty: `Skipped N records: <reason summary>`.

Failure handling: on per-record API error, do NOT include that record in
the results list. The CLI subcommand only dequeues successes, so the
record will be retried on the next trigger.

Note: the trigger script that invokes this mode also calls
`prepare-auto-push` to refresh `.context-graph/auto_push_plan.json`
before this slash command runs.
