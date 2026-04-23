# Notion Live Sync

There are two ways to pull a live Notion workspace into the Context Graph. Both
produce records with the same canonical id (`notion:<32-hex>`), so they dedupe
against each other and against the offline `ingest_notion_export` adapter.

## Path A — via the official Notion MCP (default, no API key)

The `/cg-sync-notion <scope>` slash command orchestrates the official Notion
MCP. Claude calls `notion-search` to discover pages in scope, then
`mcp__context-graph__filter_pages_by_cursor` to drop pages whose
`last_edited_time` is already covered by the stored cursor, then
`notion-fetch` for only the fresh remainder, builds Context Graph records,
and hands them to `mcp__context-graph__index_records` in one batch. Auth
flows through whatever OAuth login the user did when they connected the
Notion MCP — the plugin never sees a token and Notion pages do not need a
per-integration "Add connection" step.

The cursor is read via `mcp__context-graph__load_notion_cursor` at the start
of the run and advanced via `mcp__context-graph__save_notion_cursor` at the
end — so the slash command is cursor-aware without needing to touch the
filesystem directly. Pass `--full` to `/cg-sync-notion` to force a clean
refetch that ignores the stored cursor for filtering (but still saves the
advanced cursor on success).

This path runs **only during a live Claude session** (the LLM is the glue).
For crons, CI, or headless scripts, use Path B.

## Path B — headless Python client (for crons / CI)

The `scripts/notion_sync.py` module pulls pages directly from the Notion API
using `NOTION_TOKEN`. Use it when no live session is available, or when you
want to run the sync from a scheduled job.

Entry point:

```python
from notion_sync import sync_notion

result = sync_notion({
    "token": "env",                # or a literal string; "env" reads NOTION_TOKEN
    "databaseId": "<database-uuid>",  # XOR with parentPageId
    # "parentPageId": "<page-uuid>",
    "graphPath": "data/graph.json",  # optional
    "cursorPath": "data/notion_cursor.json",  # optional
    "since": "2026-04-01T00:00:00Z",  # optional; overrides cursorPath
    "index": True,
})
```

Path B also requires the user to create an **internal integration** at
<https://www.notion.so/my-integrations> and add it to each target database or
page via the **Connections** menu. Path A avoids both of those steps.

## Canonical record id scheme

Both the live sync and the offline export use the **same** record id so the two
adapters merge in the graph rather than duplicating entries:

```
notion:<32-hex-page-id>
```

- Lowercase.
- No hyphens.
- The page id comes straight from the Notion page object (`page["id"]`); hyphens
  are stripped via `str(raw_id).replace("-", "").lower()`.

Cross-reference with the offline path:

- `scripts/context_graph_core.py` `detect_notion_page_id` (around line 202) uses
  the regex `NOTION_PAGE_ID_RE` and lowercases the match.
- `scripts/context_graph_core.py` `explicit_id_for_markdown_file` (around line
  207) then prefixes the result with `notion:` for `system == "notion-export"`.

The live sync mirrors this exactly in `_normalize_notion_id` and `_build_record`
(see `scripts/notion_sync.py`). This is covered by the dedup test in
`tests/test_notion_sync.py::test_dedup_against_preseeded_notion_record_updates_content`,
which pre-seeds the graph with a `notion:<32-hex>` record mimicking the export
adapter and verifies that the live sync merges into the same entry.

## Cursor mechanics

- Storage location: `.context-graph/notion_cursor.json` under the workspace
  root by default (override via `cursorPath`).
- Format: a flat mapping of `page_id -> last_edited_time` (ISO-8601), e.g.
  `{"aaaaaaaa-aaaa-...": "2026-04-22T10:00:00.000Z", ...}`. An absent key
  means "never seen" and the page is treated as fresh.
- Parent directory is created on first write.
- The same schema is consumed by both the Path A (MCP) and Path B (Python)
  adapters via the core helpers `load_notion_cursor`, `save_notion_cursor`,
  `cursor_is_fresh`, and `update_cursor` (see `scripts/context_graph_core.py`).

Filtering rule for each page on a run:

1. If `payload.since` is set, treat the cursor as if every page had
   last-seen = `since` and then apply rule 2.
2. A page is **fresh** (fetched and indexed) when its `last_edited_time` is
   strictly greater than `cursor.get(page.id, "")`. Stale pages are skipped
   before any block fetch.

On a successful sync, the cursor is advanced in-place: each fresh page's
entry is set to its new `last_edited_time`. Entries for pages outside the
current scope are left untouched, so per-scope syncs compose.

If zero pages pass the cursor filter, `sync_notion` returns
`noChangesSince=True`, does not call `index_records`, and leaves the cursor
file untouched (`newCursor` in the response is `None`).

Pagination is handled internally: `list_database_pages` / `list_child_pages` and
`get_blocks` loop until `has_more` is false or a falsy `next_cursor` is
returned.

## Conflict policy — remote-wins on content

**Policy:** a page with a later `last_edited_time` overwrites the local record's
content. The live sync is authoritative for pages it reuses.

**What we observed with the current `merge_record` in
`scripts/context_graph_core.py`:**

```python
def merge_record(previous, current):
    if not previous:
        return current
    revision_version = int(previous.get("revision", {}).get("version", 1)) + 1
    merged = dict(current)
    merged["revision"] = {
        "version": revision_version,
        "updatedAt": now_iso(),
    }
    return merged
```

In practice this already behaves as remote-wins: `merged = dict(current)` takes
the classified form of the incoming record and carries forward only `revision`
from the previous record (incrementing `version`). Title, content, markers,
source, and relations are all replaced with the incoming values. The dedup test
exercises this: after a second sync with new content, `record["content"]`
updates to the new value and `record["revision"]["version"]` is at least `2`.

**Caveat / known gap — flagged, not fixed:**

`merge_record` does not consult `last_edited_time` at all. If a sync somehow
runs with an **older** page (e.g., a backfill replays stale exports out of
order, or `since` is manually rewound), the older content still wins because
the merge is unconditional. If stricter remote-wins semantics are required —
for example, "only overwrite when incoming `source.metadata.last_edited_time`
is newer than the persisted value" — `merge_record` would need a small change
to compare timestamps and return `previous` when the incoming record is
strictly older.

Per task constraints, `scripts/context_graph_core.py` was **not** modified.
This is logged here as a follow-up for a future core patch.

## Smoke test against a real workspace

`scripts/smoke_notion.py` is a one-shot check that exercises the real Notion
API against a throwaway graph path. It does not touch `data/graph.json`.

Prerequisites:

- An internal Notion integration (create one at
  <https://www.notion.so/my-integrations>).
- The integration added to the target database or parent page via the
  **Connections** menu in Notion.
- `NOTION_TOKEN` exported in the shell.

Run it with either a database id or a parent page id:

```bash
export NOTION_TOKEN=secret_xxx
python3 scripts/smoke_notion.py --database <database-id>
# or
python3 scripts/smoke_notion.py --parent <page-id>
```

The script prints three stages:

1. **Raw Notion API reach** — lists pages with `NotionClient` directly.
2. **First sync_notion call** — pulls pages and indexes into a temp graph.
3. **Second sync_notion call** — expects `noChangesSince=True` (delta cursor
   is doing its job).

Exit code 0 means all three passed. Non-zero prints an actionable message
(invalid token, integration not added to the resource, stale cursor, etc.).

## Known limitations

- **Block-type fidelity** is the responsibility of the parallel
  `scripts/notion_markdown.py` track (`page_to_markdown`). Unsupported block
  types (e.g., embeds, synced blocks, columns, child_database previews,
  equations, breadcrumbs) are expected to be dropped or stringified there;
  `sync_notion` does not inspect block contents.
- **Rate-limit handling** is not implemented. `NotionClient` is expected to
  surface 429 or 5xx errors as exceptions, and the sync does not retry. For
  production use, wrap the client or add retry/backoff inside
  `scripts/notion_client.py`.
- **Server-side filtering by `last_edited_time`** is not used. The sync pulls
  the full page listing and filters client-side against the cursor. For large
  databases this is wasteful; a future optimization can pass a Notion filter
  via `list_database_pages(filter_=...)`.
- **Archived / trashed pages** are passed through as-is. If the API returns
  them in listings, they are upserted. Filtering them out is a future concern.
- **No deletion propagation.** Pages removed in Notion are not removed from
  the graph. Use a separate reconcile pass if this matters.
- **Relations inferred from page content** (e.g., mentions, outbound links)
  are not materialized as explicit graph edges here; the edge pass runs inside
  `index_records` against markers and tokens only.
- **Lazy imports:** `notion_client` and `notion_markdown` are imported inside
  `sync_notion` so tests can inject `clientFactory` / `markdownConverter`
  without those modules being present. If you run the sync against the real
  API without overrides, both modules must be importable on `sys.path`.
