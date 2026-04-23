---
description: Run the schema learner over the current workspace graph
---

The user wants to run the adaptive classifier's learning pass over their workspace graph.

Steps:

1. Call `mcp__context-graph__learn_schema` with `{}`. The workspace should be inferred from the current working directory.
2. Render a compact summary:
   - `corpusSize`
   - Counts per proposal strategy: `hierarchy`, `ngram`, `codePath`
   - Total `pendingCount`
   - Top 5 marker importance entries sorted descending
3. If `pendingCount > 0`, mention that the user can triage proposals via `/cg-schema-review`.
4. If the tool raises `No Context Graph workspace found`, tell the user to run `/cg-init` first.

Keep the output short. Do not invent proposal decisions; review happens only in `/cg-schema-review`.
