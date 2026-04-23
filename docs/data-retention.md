# Data retention

Context Graph persists two files under `data/`. This page describes what they
contain, how to keep them out of version control when the content is sensitive,
and how to prune or redact records.

For token handling, see [security.md](security.md). For the user-facing
delete/archive/TTL/redaction surface, see [lifecycle.md](lifecycle.md).

## What lives on disk

### `data/graph.json`

The persisted graph. For every indexed record it stores:

- the full record content as indexed — title, body text, and anything the
  adapter put into `source.metadata`
- normalized markers (`type`, `domain`, `flow`, `goal`, `status`, ...)
- hierarchy path
- explicit and inferred edges

Treat this file as carrying the same sensitivity as the source notes. If your
notes are proprietary, the graph is proprietary too.

### `data/notion_cursor.json`

A single ISO-8601 timestamp — the max `last_edited_time` seen on the last
successful sync. It enables delta pulls and contains no note content. It is
not sensitive on its own; back it up or delete it freely.

## Keeping the graph out of git

The repo's `.gitignore` already lists `data/graph.json`. If you fork the plugin
and store notes in the same tree, confirm that line is still present or widen
it to the whole `data/` directory:

```
data/
```

This matters most when collaborating — a pushed `graph.json` leaks note bodies
to anyone with repo access.

## Retention and pruning

There is no automatic retention policy. The graph grows with every indexed
record and never shrinks on its own.

Recommended workflows:

- **Remove a single record.** Use the `delete-record` operation. It removes the
  record and any edges touching it. See [lifecycle.md](lifecycle.md).
- **Hide without forgetting.** Use archive mode when you want the record to
  remain in the graph (for lineage, for historical edges) but stop appearing in
  retrieval. See [lifecycle.md](lifecycle.md).
- **Archive, then delete later.** A safe two-step retirement: archive the
  record first, observe that nothing downstream needs it, then delete.
- **Stale inferred edges.** Inferred edges carry a TTL and fall out of
  retrieval on their own. See [lifecycle.md](lifecycle.md).

## Redaction before retrieval

`build_context_pack` supports an optional redaction hook. Register a
transformer (see `register_redactor` in `scripts/context_graph_core.py`) and it
runs over each record on the way out of the pack — useful for stripping
obvious secrets like emails, API tokens, or internal URLs before a pack is
returned to a client.

The hook does not mutate the stored graph. It filters outbound packs only. If
you want to scrub the graph itself, reindex after editing the source.

Read the source for the exact signature rather than copying a snippet from
here.

## Backups

`data/graph.json` is a single JSON file. Before any bulk operation — a large
sync, a pattern promotion, a manual edit — copy it:

```
cp data/graph.json data/graph.json.bak
```

That is the entire backup story. Restore by copying the backup back.
