---
description: Pull pages from Notion into the Context Graph via the Notion API
argument-hint: <database-id> | --database <id> | --parent <id> [--since <iso>]
---

The user wants to sync a Notion database or parent page into the Context Graph using the live API.

Parse `$ARGUMENTS`:

1. If `$ARGUMENTS` is empty, ask the user for a Notion database id or `--parent <id>` and stop.
2. Otherwise tokenize on whitespace and extract:
   - `--database <id>` sets `databaseId`
   - `--parent <id>` sets `parentPageId`
   - `--since <iso>` sets `since` (ISO 8601 timestamp)
   - A bare token that is not a flag value is treated as `databaseId` (prefer this when ambiguous).
3. If neither `databaseId` nor `parentPageId` is present, ask the user for one and stop.

Call the MCP tool `mcp__context-graph__sync_notion` with the resolved fields plus:
- `graphPath`: `./data/graph.json`
- `cursorPath`: `./data/notion_cursor.json`
- `index`: true

Do not pass `token` from `$ARGUMENTS`. The tool reads `NOTION_TOKEN` from the environment.

Render the result compactly:
- Headline: `Pulled <pagesPulled> Notion pages`
- `newCursor` on its own line
- First 5 `recordIds` (note the total if there are more)
- If `noChangesSince` is true, say so plainly and skip the record list
- If the tool returns an error, surface the message verbatim and remind the user that `NOTION_TOKEN` must be exported
