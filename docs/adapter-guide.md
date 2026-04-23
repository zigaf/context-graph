# Adapter guide

An adapter is any piece of code that turns an upstream source (a folder of
markdown, a Notion export, a live Notion workspace, a Jira API, a Slack
channel) into Context Graph records and hands them to the core. This page is
the contract for adapter authors.

Companion docs: [record-model.md](record-model.md) defines the record shape an
adapter must produce; [notion-sync.md](notion-sync.md) is a worked example of a
live adapter; [lifecycle.md](lifecycle.md) covers what happens to records once
they are in the graph.

## The core's contract

`scripts/context_graph_core.py` exposes exactly three entry points an adapter
needs to know about:

```python
classify_record(payload: dict, schema: dict | None = None) -> dict
merge_record(previous: dict | None, current: dict) -> dict
index_records(payload: dict, schema: dict | None = None) -> dict
```

- **`classify_record({"record": <record>})`** runs the four-stage classifier:
  region extraction, IDF-weighted scoring, threshold arbiter, and
  (optionally) an arbitration request for in-session LLM pickup. It is
  idempotent and safe to call repeatedly. Returns the classified record with
  `markers`, `hierarchy`, `tokens`, `classifiedAt`, and
  `source.metadata.classifierNotes` filled in. If the input is already
  classified, the result is equivalent.

- **`merge_record(previous, current)`** decides how two versions of the same
  id combine. It is order-aware: if both sides carry
  `source.metadata.last_edited_time` and `current` is strictly older, it
  returns `previous` unchanged. Otherwise it returns `current` with a bumped
  `revision.version`. Adapters rarely call this directly — `index_records`
  does — but the behavior is the reason adapters must populate
  `last_edited_time` whenever the upstream knows it.

- **`index_records({"records": [...], "graphPath": ..., "workspaceRoot":
  ...})`** is the single write path into `graph.json`. It re-classifies each
  record, merges against the current stored copy, rebuilds edges
  (explicit + inferred), and persists.

An adapter's job, in one sentence: produce a list of record dicts and hand
them to `index_records`. Everything else — classification, merging,
edge rebuilding, IDF stats, schema learning — is driven by core.

## The single rule: do not branch on `source.system`

Core should read one well-known shape regardless of which adapter produced a
record. If you find yourself writing
`if record["source"]["system"] == "my-adapter"` in
`scripts/context_graph_core.py` or `scripts/classifier_*.py`, stop: that
branch belongs in the adapter, not in core. The adapter encodes whatever it
knows in `source.metadata` under a portable key.

Concretely:

- Parent / breadcrumb info → `source.metadata.parent` (string or `{type, id}`).
  Used by `classifier_regions.extract_regions` as the `breadcrumb` region and
  by `classifier_learning.mine_hierarchy` as a hierarchy signal, regardless of
  system.
- Upstream modification time → `source.metadata.last_edited_time`. Used by
  `merge_record` for order-aware merges.
- A canonical upstream id → either encoded directly into `record["id"]`
  (preferred — see ID stability below) or stashed in
  `source.metadata.<adapter>PageId`.
- Adapter-private fields → anywhere in `source.metadata` under a namespaced
  key that will not collide.

The goal stated in the roadmap: *"a new adapter can be added without changing
`context_graph_core` beyond registering a system name."* There is no
registration step today — `system` is a free-form string — so in practice a
new adapter needs **zero** core changes.

See [record-model.md](record-model.md#known-deviations-to-clean-up) for the
two current places core still does branch on system. New adapters should not
rely on (or extend) those branches.

## Adding a new adapter, step by step

1. **Pick a system name.** A short slug: `jira`, `linear`, `slack-export`,
   `gdocs`. Use it as the value of `source.system` on every record the
   adapter produces. There is no central registry to update.
2. **Pick an id prefix that matches the system.** Usually `<system>:<stable-
   upstream-id>`. See "ID stability" below.
3. **Decide what goes in `source.metadata`.** At minimum, anything the
   upstream exposes that lets you answer "when was this last changed?" and
   "where does it sit in the upstream's hierarchy?" — put the first under
   `last_edited_time`, the second under `parent`.
4. **Write a function that yields record dicts.** No subclassing, no
   registration. The function takes whatever config it needs (an API token,
   a path, a workspace id) and returns (or yields) records in the shape
   defined in [record-model.md](record-model.md).
5. **Call `index_records`.** Pass `{"records": [...], "graphPath": ...,
   "workspaceRoot": ...}`. That's the whole integration.
6. **Optional: expose a CLI or MCP tool.** Wire into `scripts/context_graph_cli.py`
   and `scripts/context_graph_mcp.py` if end users should invoke the adapter
   directly. This is layering on top; the core contract is already satisfied
   by step 5.

## Minimal record examples

The point of the unified model is that the three examples below are
indistinguishable from core's point of view. Only `source.system`, `id`, and
what goes into `source.metadata` differ.

### A synthetic Jira adapter

```python
from context_graph_core import index_records


def jira_issue_to_record(issue: dict) -> dict:
    # issue is whatever jira's REST client returned
    issue_key = issue["key"]  # e.g. "PAY-1234"
    return {
        "id": f"jira:{issue_key.lower()}",
        "title": issue["fields"]["summary"],
        "content": issue["fields"].get("description") or "",
        "markers": {
            # adapter fills in whatever it reliably knows;
            # the rest is inferred by classify_record
            "type": "bug" if issue["fields"]["issuetype"]["name"] == "Bug" else "task",
            "status": _map_jira_status(issue["fields"]["status"]["name"]),
        },
        "relations": {
            "explicit": [
                {"type": "depends_on", "target": f"jira:{link.lower()}"}
                for link in _jira_depends_on(issue)
            ],
            "inferred": [],
        },
        "source": {
            "system": "jira",
            "space": issue["fields"]["project"]["key"],
            "url": f"https://example.atlassian.net/browse/{issue_key}",
            "metadata": {
                "last_edited_time": issue["fields"]["updated"],
                "created_time": issue["fields"]["created"],
                "parent": issue["fields"]["project"]["name"],
                "jiraIssueKey": issue_key,
                "jiraIssueType": issue["fields"]["issuetype"]["name"],
            },
        },
        "revision": {"updatedAt": issue["fields"]["updated"]},
    }


def sync_jira(issues: list[dict], *, graph_path: str, workspace_root: str) -> dict:
    records = [jira_issue_to_record(issue) for issue in issues]
    return index_records({
        "records": records,
        "graphPath": graph_path,
        "workspaceRoot": workspace_root,
    })
```

Nothing in `context_graph_core.py` needs to change. The classifier will read
`title`, `content`, the `parent` breadcrumb, and the `markers` dict exactly
the way it reads records from the Notion and markdown adapters.

### The markdown adapter (abbreviated)

`markdown_record_from_file` in `scripts/context_graph_core.py` (around line
1334) produces records that look like this before `classify_record` fills in
the rest:

```python
{
    "id": "src:markdown-my-notes-subfolder-some-page-md",
    "title": "Some Page",
    "content": "...body without front matter or leading heading...",
    "markers": {"type": "task"},  # from YAML front matter if present
    "relations": {
        "explicit": [{"type": "related_to", "target": "src:..."}],  # from [] links
        "inferred": [],
    },
    "source": {
        "system": "markdown",
        "space": "my-notes",                       # root directory basename
        "path": "subfolder/Some Page.md",          # relative to the root
        "url": "/absolute/path/to/subfolder/Some Page.md",
        # metadata omitted; any leftover front-matter keys go here
    },
}
```

The same function also covers the `notion-export` case — the only difference
is `system="notion-export"`, and `explicit_id_for_markdown_file` rewrites the
id to `notion:<32-hex>` when the filename embeds a Notion page id (see
[notion-sync.md](notion-sync.md) for why).

### The live Notion adapter

`_build_record` in `scripts/notion_sync.py` produces:

```python
{
    "id": f"notion:{normalized_page_id}",         # hex, lowercase, no hyphens
    "title": "Webhook retries on 5xx",
    "content": "...markdown from page_to_markdown...",
    "source": {
        "system": "notion",
        "url": page["url"],
        "metadata": {
            "notionPageId": normalized_page_id,
            "last_edited_time": page["last_edited_time"],
            "created_time": page["created_time"],
            "parent": page["parent"],             # {"type": "database_id", "id": "..."}
        },
    },
    "revision": {"updatedAt": page["last_edited_time"]},
}
```

The `notion:<32-hex>` id is the same form the export adapter produces, so a
single real-world Notion page round-trips through either adapter to the same
record.

## ID stability for adapters

Records in the graph are keyed on `id`. Re-running an adapter against the same
upstream must produce the same ids, otherwise each rerun duplicates the corpus.

Rules (restated from [record-model.md](record-model.md#id-stability-rules)
from the adapter's perspective):

- **Derive the id from something the upstream controls.** An issue key, a page
  id, a stable path — not `uuid4()`, not a timestamp, not a hash of the
  classified body.
- **Use `<system>:<upstream-id>` as the prefix.** `jira:pay-1234`,
  `linear:lin-2039`, `notion:1e2f3a4b...`, `gdocs:1a2b3c...`. This keeps
  related adapters dedupe-friendly and makes ids self-describing.
- **Lowercase, slugify if the upstream id has spaces.** `stable_record_id`'s
  fallback uses `slugify(...)`; adapter-produced ids should do the same if
  necessary.
- **If two adapters can see the same upstream record, they must agree on the
  id.** The paradigm case is `notion:<32-hex>`: the export adapter and the
  live adapter both emit the same string because they both normalize the
  Notion page id the same way (strip hyphens, lowercase). If you add a
  "slack-export" adapter today and a "slack-live" adapter tomorrow, plan for
  their id rule up front.
- **Do not rely on the classifier to shape your id.** `classify_record` calls
  `stable_record_id` as a last resort, which composes
  `source.system + source.space + source.path` into a slug. That fallback is
  stable enough for the built-in markdown adapters but is a weak guarantee
  for anything with a real upstream id. Set `record["id"]` yourself.

Example of the failure mode: suppose a "linear" adapter keys by issue title
instead of issue id. An author renames an issue; next sync, the adapter emits
a different id; the graph now has two records, the old one stale, and no
explicit edges point to the new one. Use `linear:<issue-id>` and the rename
collapses into a single updated record via `merge_record`.

## What to fill, what to skip

Quick reference of what an adapter should populate vs. leave empty (see
[record-model.md](record-model.md#who-fills-what) for the full breakdown):

| Field | Adapter fills? |
|---|---|
| `id` | Yes — derive from upstream id. |
| `title`, `content` | Yes — from upstream. |
| `markers` | Partial — anything the upstream knows unambiguously (e.g. Jira issue type → `markers.type`). Leave the rest; the classifier fills them. |
| `relations.explicit` | Yes if the upstream has structured relations (Jira "depends on", Notion mentions, `[]`-links). Targets must be other record ids in the same graph. |
| `relations.inferred` | No — always `[]`. Inferred edges are rebuilt by `rebuild_edges`. |
| `source.system` | Yes. |
| `source.space`, `source.url`, `source.path` | Yes when meaningful. |
| `source.metadata.last_edited_time` | Yes when the upstream exposes it. Feeds order-aware merges. |
| `source.metadata.parent` | Yes — feeds the breadcrumb region and hierarchy mining. |
| `source.metadata.<adapter-private>` | Yes — namespace under anything that won't collide with the well-known keys. |
| `revision.updatedAt` | Optional — `classify_record` will set one. |
| `missingRequiredMarkers`, `hierarchy`, `tokens`, `classifiedAt` | No — `classify_record` stamps these. |
| `classifierNotes`, `classifierVersion` | No — `classify_record` stamps these into `source.metadata`. |
| `arbitrationRequest` | No — emitted by `classify_record` when scoring is ambiguous. |
| `archived` | No — toggled by `archive_record` / `unarchive_record`. |

## Calling `index_records` — the full payload

```python
from context_graph_core import index_records

result = index_records({
    "records": [record_dict_1, record_dict_2, ...],
    # Optional. Defaults to default_graph_path() for the current workspace.
    "graphPath": "/path/to/.context-graph/graph.json",
    # Optional. Defaults to walking up from the current working directory to
    # find .context-graph/workspace.json. Pass explicitly for headless callers.
    "workspaceRoot": "/path/to/project-root",
})
# result = {
#     "graphPath": "...",
#     "upsertedIds": [...],
#     "recordCount": N,
#     "edgeCount": M,
#     "updatedAt": "2026-04-24T11:00:00+00:00",
# }
```

A few things happen as side effects when `workspaceRoot` resolves:

- IDF stats are recomputed and written to `.context-graph/idf_stats.json`.
- The schema learner runs a full pass and updates
  `.context-graph/schema.learned.json` with any new proposals.

Both are useful for the classifier next time but are not required for the
adapter to succeed. If there is no workspace (e.g. you set
`CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1` and call from a plain CLI), those steps
are skipped silently.

## Can a headless adapter handle arbitration?

No. If `classify_record` produces `arbitrationRequest` for any of an adapter's
records, the record is still indexed — but with `classifierNotes.arbiter` set
to `"fallback"` and `reasoning` set to
`"Headless sync cannot use in-session arbitration."` (see `notion_sync.py` for
the pattern). The count shows up in the adapter's response as
`fallbackCount`. Nothing is lost — the arbitration payload is still on the
record, and a later in-session run can pick up where the headless run left
off. Adapters do not need to special-case this; just propagate
`fallbackCount` so the caller can decide whether to rerun in a live session.

## Where the existing adapters live

- `scripts/context_graph_core.py::ingest_markdown` — local markdown trees.
- `scripts/context_graph_core.py::ingest_notion_export` — offline Notion
  export bundles. Differs from `ingest_markdown` only by `system` string and
  the notion-id filename trick.
- `scripts/notion_sync.py::sync_notion` — live Notion API via `NOTION_TOKEN`.
- `commands/cg-sync-notion.md` — orchestrates the official Notion MCP
  (`notion-search` + `notion-fetch`) in a live Claude session and hands
  records to `index_records` via `mcp__context-graph__index_records`. This is
  not a Python module — it's a skill — but it's still an adapter in the same
  sense: it produces records of the agreed shape.

Read those four to see the pattern end-to-end; a new adapter should look like
any of them minus whatever parts are upstream-specific.
