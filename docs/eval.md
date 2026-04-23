# Retrieval evaluation

Phase 7 of the roadmap: score how well `build_context_pack` ranks the right
records against a hand-curated query set. Every merge should include a run
that shows no precision regression.

## Files

- `data/eval/queries.json` — the query set (schema version `"1"`, list of
  queries with `id`, `query`, `intent`, `expectedDirectMatches`,
  `expectedSupporting`, `k`).
- `data/eval/fixtures/graph.json` — a small hand-built record set the queries
  target. Deterministic — regenerating the baseline over it must give the
  same numbers.
- `data/eval/baseline.json` — the committed baseline. Holds
  `meanPrecisionAtK`, `meanRecallAtK`, and supporting fields. The CI gate
  fails a run when mean precision falls below this (subject to `--tolerance`).

## CLI

```
python3 scripts/context_graph_cli.py eval                   # run + regression check
python3 scripts/context_graph_cli.py eval --save-baseline   # rebaseline
python3 scripts/context_graph_cli.py eval --tolerance 0.05  # allow 5% drop
```

Exit codes: `0` no regression, `1` regression, `2` CLI error.

## Adding a query

1. Pick or author the records you want the ranker to surface, in
   `data/eval/fixtures/graph.json` (the fixture is intentionally small so
   expected matches stay obvious).
2. Append a query object to `data/eval/queries.json`:
   ```json
   {
     "id": "q9",
     "query": "...",
     "intent": "debug",
     "expectedDirectMatches": ["r:foo"],
     "expectedSupporting": ["r:bar"],
     "k": 5
   }
   ```
3. Run `context_graph_cli.py eval`. If the result looks right, run once more
   with `--save-baseline` and commit the updated `baseline.json` alongside
   the query change.

## Rebaselining

Only rebaseline when you intentionally change retrieval behaviour. Commit the
updated `baseline.json` with the change that caused the shift, so reviewers
can tie the two together. Never rebaseline to silence a regression discovered
in CI — investigate first.

## Reading the report

Each row is `query_id  p@k  r@k  pack_chars  full_chars  ratio`. `ratio`
below ~0.5 means the context pack is materially smaller than loading every
record. A query that returns `missed direct` or `missed supporting` lines
below its row is failing to surface one of its hand-picked expectations.

## MCP tool

`eval_retrieval` exposes the harness as an MCP tool (`queriesPath`,
`graphPath`, optional `baselinePath` and `tolerance`). Primary use is CLI /
CI; the MCP surface is there for ad-hoc scoring during chat.
