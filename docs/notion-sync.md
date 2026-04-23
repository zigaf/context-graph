# Notion Live Sync

The live Notion sync engine (`scripts/notion_sync.py`) pulls pages directly from
the Notion API and upserts them into the Context Graph. It is the online sibling
of the offline `ingest_notion_export` adapter in `scripts/context_graph_core.py`.

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

- Storage location: `data/notion_cursor.json` by default (override via
  `cursorPath`).
- Format: `{"cursor": "<ISO-8601 last_edited_time>"}`.
- Parent directory is created on first write.

Resolution order for the effective cursor on each run:

1. `payload.since` if provided.
2. Contents of `cursorPath` if the file exists and contains a valid `cursor`
   string.
3. `None` (full pull).

On a successful sync that pulls at least one record, the cursor file is
overwritten with the **max** `last_edited_time` across the pulled batch.

If zero pages pass the cursor filter, `sync_notion` returns
`noChangesSince=True`, does not call `index_records`, and leaves the cursor
file untouched (the response still echoes the existing cursor value under
`newCursor`).

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
