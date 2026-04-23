---
description: Build a context pack for a query from the persisted graph
argument-hint: <query>
---

The user wants a compact context pack for the query `$ARGUMENTS`, built from the persisted Context Graph.

Steps:

1. If `$ARGUMENTS` is empty, ask the user for a query and stop.
2. Call the MCP tool `mcp__context-graph__search_graph` with:
   - `query`: `$ARGUMENTS`
   - `graphPath`: `./data/graph.json` (default)
   - `limit`: omit unless the user specified one
3. Render the resulting context pack in four clearly-labeled sections. Omit any section that has no items rather than printing "none".

Sections, in order:

- Direct matches: records whose markers or content matched the query most strongly. Show record ID, title or first line, and top markers.
- Supporting relations: linked records that add context (decisions, related notes, parents in the hierarchy). Show how each relates to a direct match.
- Promoted rules: any records classified as rules or decisions that apply. Show the rule statement.
- Unresolved risks: records tagged as risks, open questions, or blockers that the pack surfaces. Flag them clearly.

If the tool returns an error or an empty pack, say so plainly and suggest running `/cg-index <path>` if the graph looks empty.
