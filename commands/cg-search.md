---
description: Search the Context Graph for records relevant to a query, optionally under an intent preset.
argument-hint: [--mode <name>] <query>  (intent presets: debug, implementation, architecture, product)
---

The user wants a compact context pack for the query `$ARGUMENTS`, built from the persisted Context Graph.

## Intent modes (optional)

If the user prefixes the query with `--mode <name>`, extract the value
and pass it to `build_context_pack` as `intentMode`. Valid presets:
`debug`, `implementation`, `architecture`, `product`. Any other value
— surface the error from the tool (invalid preset raises ValueError
listing the allowed names).

Example:

    /cg-search --mode architecture payments idempotency

Strip `--mode <name>` from the query before passing the remainder as
the `query` string. If no `--mode` prefix is present, omit `intentMode`
from the payload.

Steps:

1. If `$ARGUMENTS` is empty, ask the user for a query and stop.
2. Call the MCP tool `mcp__context-graph__search_graph` with:
   - `query`: `$ARGUMENTS` (with any leading `--mode <name>` already stripped per the Intent modes section above)
   - `graphPath`: `./data/graph.json` (default)
   - `limit`: omit unless the user specified one
   - `intentMode`: include only when the user passed `--mode <name>`; otherwise omit entirely so the call falls back to the no-mode default
3. Render the resulting context pack in four clearly-labeled sections. Omit any section that has no items rather than printing "none".

Sections, in order:

- Direct matches: records whose markers or content matched the query most strongly. Show record ID, title or first line, and top markers.
- Supporting relations: linked records that add context (decisions, related notes, parents in the hierarchy). Show how each relates to a direct match.
- Promoted rules: any records classified as rules or decisions that apply. Show the rule statement.
- Unresolved risks: records tagged as risks, open questions, or blockers that the pack surfaces. Flag them clearly.

If the tool returns an error or an empty pack, say so plainly and suggest running `/cg-index <path>` if the graph looks empty.
