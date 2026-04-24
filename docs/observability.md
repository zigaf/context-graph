# Observability tooling

Three features help a user explain a surprising context pack without reading
source code: `dry-run`, `graph-diff`, and `inspect-record`.

## dry-run

Every mutator (`classify_record`, `index_records`, `ingest_markdown`,
`ingest_notion_export`, `promote_pattern`, `delete_record`, `archive_record`,
`unarchive_record`) accepts a `dryRun: true` payload field. When set:

- the function computes everything it would normally compute
- nothing is written to disk (no `save_graph`, no state-file writes)
- the response carries `dryRun: true`
- summary fields (e.g. `index_records.recordCount`) reflect what WOULD happen

Example via the MCP surface:

```json
{
  "records": [{"id": "r1", "title": "New record", "markers": {}}],
  "graphPath": "data/graph.json",
  "dryRun": true
}
```

Use `dry-run` when you want to:

- preview a large ingest before committing it
- confirm a delete drops the edges you expect
- plan a `promote_pattern` run without polluting the graph

The guarantee tested in `tests/test_dry_run.py` is that `graph.json` bytes are
identical before and after a dry-run call. If you ever see the file change
after a dry-run, that is a bug — the response remains the source of truth.

## graph-diff

Compare two `graph.json` snapshots and get a structured list of changes:

```
python3 scripts/context_graph_cli.py graph-diff --left a/graph.json --right b/graph.json
python3 scripts/context_graph_cli.py graph-diff --left a/graph.json --right b/graph.json --json
```

Also available as MCP tool `graph_diff` (`{leftPath, rightPath}` or inline
`{left, right}` dicts).

Output categories:

- `recordsAdded` — present in right, absent in left (id, title, markers).
- `recordsRemoved` — the reverse.
- `recordsModified` — shared ids with differing fingerprints. The `changes`
  map lists only fields that differ: `title`, `contentHash` (short SHA-256
  prefix; raw bodies never leak into diffs), `markers`, `lastEditedTime`,
  `revision`.
- `edgesAdded` / `edgesRemoved` — keyed by `(source, target, type)`.
- `summary` — scalar counts for each category.

Text mode ends with a single summary line:

```
Summary: 1 records added, 1 removed, 1 modified; 1 edges added, 1 removed.
```

## inspect-record

Explain a record's rank for a query:

```
python3 scripts/context_graph_cli.py inspect-record \
  --graph data/graph.json --record r-123 --query "webhook retry"
```

Add `--json` for the structured payload. Also exposed as MCP tool
`inspect_record`.

The output includes:

- `id`, `title`, `markers` — identifies the record.
- `queryTokens` — the query after `tokenize` normalization.
- `queryMarkers` — markers the ranker inferred from the query.
- `matchedMarkers` — record markers that equal query markers.
- `matchedTokens` — tokens present in both the query and the record index.
- `factors` — per-factor weight + contribution:
  - `markerMatch` (weight 0.45): weighted by learned marker importance; the
    key driver when a query names a specific marker value.
  - `tokenMatch` (weight 0.20): fraction of query tokens present in the
    record's token set.
  - `severity` (weight 0.15): higher for critical/high records.
  - `status` (weight 0.10): in-progress beats done beats archived.
  - `freshness` (weight 0.10): exponential decay over 30 days.
- `score` — final total (matches `search_graph`'s reported score bit-for-bit
  — the ranker and the explainer share a helper, so they cannot drift).
- `rank`, `inTopK`, `limit` — where this record lands when the same query is
  run across the full graph.
- `outgoingEdges`, `incomingEdges` — raw edges that touch the record.

Use `inspect-record` when a user says "why is this record in my pack?" or
"why isn't it in my pack?". The per-factor breakdown points at the single
number that dominated the decision, so the answer is usually one line
(e.g. `tokenMatch.contribution=0.2, all other factors=0` => "it's a
keyword hit only").

## Relationship to eval harness

The eval harness in `scripts/eval_cli.py` also consumes `build_context_pack`.
Because `inspect-record` shares the same scoring helper, an eval-baseline
regression and a surprising `inspect-record` output will always point at the
same underlying change. If `python3 scripts/context_graph_cli.py eval`
passes but `inspect-record` now reports different numbers for a specific
record, the scoring math has not changed — only the surrounding graph has.

## Inspecting under an intent mode

`inspect-record` accepts `--mode <preset>` (and `--override <path>` for
a JSON override file). The report shows the four intent factors that
were applied to the score:

```
$ python3 scripts/context_graph_cli.py inspect-record \
    --graph .context-graph/graph.json \
    --record r-webhook-crash \
    --query "webhook retry" \
    --mode debug
...
  intentMarkerMultiplier:
    severity: 2.5
    type: 2.0
  intentTypeBoost: bug -> 1.5
  intentStatusBias: in-progress -> 1.5
  intentFreshnessMultiplier: 1.5
```

Use this when a record ranks unexpectedly under a given mode — the
factor breakdown tells you which preset knob did it.
