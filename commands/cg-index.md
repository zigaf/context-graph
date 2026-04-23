---
description: Ingest a markdown folder into the Context Graph
argument-hint: <path>
---

The user wants to ingest a folder of markdown notes at `$ARGUMENTS` into the Context Graph.

Steps:

1. Treat `$ARGUMENTS` as the `rootPath` for the ingest. If it is empty, ask the user to provide a path and stop.
2. Call the MCP tool `mcp__context-graph__ingest_markdown` with:
   - `rootPath`: the path provided in `$ARGUMENTS`
   - `graphPath`: `./data/graph.json` (default)
3. Summarize the result for the user. Report:
   - Number of files scanned (`fileCount`)
   - Record IDs that were added or updated (`recordIds`)
   - Total edge count in the resulting graph
   - Any files that were skipped or failed, if the tool reports them
4. If the tool returns an error, surface the error message and do not invent a success summary.

Keep the summary compact: a one-line headline followed by the counts and, if the list is short (<= 10), the record IDs inline. For longer lists, show the first few and note the total.
