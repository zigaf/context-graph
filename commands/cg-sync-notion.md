---
description: Pull Notion pages into the Context Graph via the official Notion MCP
argument-hint: <scope>  (search query, page title, or database name)
---

The user wants to sync Notion content into the Context Graph using the official Notion MCP that is already connected to this session (no API key in the plugin, no manual "Add connection" per page — auth goes through whatever OAuth login the user did when they connected the Notion MCP).

Scope: `$ARGUMENTS`. If empty, ask the user to narrow it (a search term, page title, or database name) and stop. A completely unscoped sync is almost never what they want.

Steps:

1. **Search.** Call the Notion MCP search tool (it will be registered under a name like `mcp__notion__notion-search` or similar — use whatever Notion search tool is in your available tool list). Pass the scope as `query`, set `query_type: "internal"`, a small `page_size` (e.g., 10), and `filters: {}`. If no Notion MCP tool is connected in this session, tell the user and point them at the headless fallback: `python3 scripts/smoke_notion.py --database <id>` with `NOTION_TOKEN` set, or the `mcp__context-graph__sync_notion` MCP tool directly.

2. **Cap.** If the search returns more than ~50 pages, ask the user to confirm before pulling everything. Otherwise proceed.

3. **Fetch each page.** For every result, call the Notion MCP fetch tool (e.g., `mcp__notion__notion-fetch`) with the result's `id` or `url`. The response shape is:
   - `title` — the page title
   - `url` — full `https://www.notion.so/<32-hex>` URL
   - `text` — the page rendered as Notion-flavored markdown wrapped in `<page>…</page>`. The actual body lives between `<content>` and `</content>` tags inside `text`. Parent breadcrumb lives inside `<ancestor-path>` with `<parent-page title="…" />` entries (outermost first, so reverse them for a human-readable path).

4. **Build records.** One Context Graph record per page, exactly this shape:

   ```json
   {
     "id": "notion:<32-hex-page-id>",
     "title": "<page title>",
     "content": "<markdown body — the substring between <content> and </content>>",
     "source": {
       "system": "notion",
       "url": "<full notion.so url>",
       "metadata": {
         "notionPageId": "<32-hex>",
         "last_edited_time": "<iso — from the search result's `timestamp` field>",
         "parent": "<reversed ancestor-path titles joined with ' > '>"
       }
     }
   }
   ```

   The 32-hex id: Notion's `notion-search` result gives `url` as a bare 32-hex string already (no hyphens); use that directly. If you only have a hyphenated UUID, strip hyphens and lowercase. The `notion:<32-hex>` id is the canonical scheme — it matches what the offline export adapter and the Python sync engine produce, so records dedupe instead of duplicating on re-runs.

5. **Index.** Call `mcp__context-graph__index_records` once with the full batch and the default `graphPath`. `merge_record` inside the indexer rejects stale replays by comparing `last_edited_time`, so a redundant call is safe.

6. **Report** to the user:
   - pages pulled
   - records upserted (from `indexResult.upsertedIds` count)
   - any pages skipped, with a one-line reason each

Do NOT invent markers during the sync. Classification is a separate step — suggest `/cg-classify` or the `context-graph-classify` skill as a follow-up on interesting records.
