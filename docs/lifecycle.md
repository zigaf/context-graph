# Record lifecycle

This page is the user-facing summary of how records enter, change, and leave
the graph. For on-disk storage details see
[data-retention.md](data-retention.md). For token handling see
[security.md](security.md).

## Create and update

Records are created or updated by the same code path — an upsert keyed on
record id. Any of these will drive it:

- `ingest-markdown` for a local markdown folder
- `ingest-notion-export` for an offline Notion export bundle
- `sync-notion` for a live pull from a Notion database or parent page

The live sync and the offline export share a canonical Notion id, so the two
adapters merge into the same record rather than duplicating it.

## Order-aware merges

Updates compare the incoming `last_edited_time` against the stored copy. A
stale replay — a backfill, a manually rewound cursor, an out-of-order queue —
will not overwrite a newer record. The older payload is dropped and the stored
copy wins.

## Archive

Archive hides a record from retrieval (`build_context_pack`, `search_graph`)
while keeping it in the graph. Edges that touch archived records are still
stored but are not traversed on the way out. Use this when you want to
preserve lineage or historical context without polluting future packs.

An archived record can be un-archived or fully deleted later.

## Delete

Delete removes the record and every edge that referenced it. Neighbors of the
deleted record are recomputed so no dangling edges remain. This is the only
way to fully evict a record's content from `data/graph.json`.

## TTL for inferred edges

Explicit edges (`fixes`, `affects`, `depends_on`, ...) persist until the
source record is deleted. Inferred edges — probable links produced by the
relation-inference pass — carry a `createdAt` timestamp. Once an inferred
edge is older than the TTL window it stops appearing in `search_graph`
results even if the record is still indexed. Re-indexing preserves the
original `createdAt` for edges that still hold, so the age is real, not
reset on every rebuild.

The TTL is `INFERRED_EDGE_TTL_DAYS` in `scripts/context_graph_core.py`,
defaulting to 30 days. Override per query via the `inferredEdgeTtlDays`
field on `search_graph`'s payload (not persisted). Tune the module constant
if the default is too aggressive or too lax for your corpus.

## Redaction hook

`build_context_pack` supports an optional transformer that runs over each
record before the pack is returned. Typical use is stripping emails, API
tokens, or internal URLs from bodies that would otherwise end up in a
downstream prompt. Register one via `register_redactor` in
`scripts/context_graph_core.py`.

The hook only touches outbound packs; the stored graph is unchanged. See
[data-retention.md](data-retention.md) for how redaction fits alongside
delete and archive.
