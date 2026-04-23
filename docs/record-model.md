# Record model

A "record" is the unit every adapter produces and every part of
`scripts/context_graph_core.py` consumes. This page is the contract: the shape
of the dict, which fields are required, which are filled in by `classify_record`
rather than the adapter, and the rules that make two different adapters (Notion
export, live Notion, local markdown, a future Jira adapter) merge into the same
graph entry instead of duplicating.

For the adapter-author perspective, see [adapter-guide.md](adapter-guide.md).
For on-disk shape of the graph file, see [data-retention.md](data-retention.md).
For the adaptive classifier pipeline that fills in `markers`, see
[`docs/superpowers/specs/2026-04-23-workspace-and-adaptive-classifier-design.md`](superpowers/specs/2026-04-23-workspace-and-adaptive-classifier-design.md).

## The dict, at a glance

A record is a JSON-serializable dict. Adapter-produced records only have to
carry a few fields; `classify_record` fills the rest. After classification and
indexing, the stored form looks like this:

```json
{
  "id": "notion:1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b",
  "title": "Webhook retries on 5xx",
  "content": "We retry 3 times with jittered backoff...",
  "markers": {
    "type": "rule",
    "domain": "integration",
    "flow": "webhook",
    "goal": "prevent-regression",
    "status": "done"
  },
  "missingRequiredMarkers": [],
  "hierarchy": {
    "segments": ["integration", "webhook"],
    "path": "integration > webhook"
  },
  "relations": {
    "explicit": [{"type": "related_to", "target": "notion:..."}],
    "inferred": []
  },
  "source": {
    "system": "notion",
    "url": "https://www.notion.so/...",
    "metadata": {
      "notionPageId": "1e2f3a4b...",
      "last_edited_time": "2026-04-20T10:30:00.000Z",
      "created_time": "2026-04-01T08:12:00.000Z",
      "parent": {"type": "database_id", "id": "abcd..."},
      "classifierVersion": "2",
      "classifierNotes": {
        "classifierVersion": "2",
        "arbiter": "deterministic",
        "regionsUsed": ["titleText", "body"],
        "scores": { "...": "..." },
        "reasoning": null
      }
    }
  },
  "revision": {"version": 3, "updatedAt": "2026-04-24T11:00:00+00:00"},
  "tokens": ["webhook", "retry", "backoff", "..."],
  "classifiedAt": "2026-04-24T11:00:00+00:00",
  "arbitrationRequest": null,
  "archived": false
}
```

## Who fills what

Fields divide into three tiers.

### Tier 1 — produced by the adapter

The adapter is the source of truth for these:

| Field | Type | Required by `classify_record` | Notes |
|---|---|---|---|
| `id` | string | Strongly recommended | See "ID stability rules" below. If omitted, `stable_record_id` synthesizes one from `source.path` / `source.url`, then `title`. Adapters should provide one deterministically. |
| `title` | string | Optional but expected | Free text. Used as a classifier region (`titleText`) and shown in retrieval. |
| `content` | string | Optional | Raw markdown or plain text body. `classify_record` runs it through region extraction (front matter, `## Metadata` block, body). |
| `markers` | dict[str, str\|list] | Optional | Any markers the adapter already knows. Remaining markers are inferred by the classifier. Values are normalized via the schema's alias index. |
| `relations.explicit` | list of `{type, target, confidence?}` | Optional | Explicit edges keyed on target `id`. See `scripts/context_graph_core.py::rebuild_edges` for how they become graph edges. |
| `source` | dict | Required in practice | See "Source namespace" below. Minimum is `{"system": "..."}`; downstream stores whatever else you put here under `source.metadata`. |
| `revision.updatedAt` | ISO-8601 string | Optional | If set, used as the record's recency signal until `classify_record` stamps its own `revision`. |

Adapter dicts that skip `missingRequiredMarkers`, `hierarchy`, `tokens`,
`classifiedAt`, `classifierNotes`, and `arbitrationRequest` are fine —
`classify_record` computes all of those.

### Tier 2 — filled by `classify_record`

`classify_record` in `scripts/context_graph_core.py` (around line 671) is the
choke point every record passes through. It:

- Normalizes `markers` against the merged schema (shipped + learned + overlay).
- Runs the adaptive scorer to fill missing markers, splitting a record into
  regions (`frontmatter`, `metadataBlock`, `titleText`, `breadcrumb`, `body`)
  and scoring each candidate value with IDF-weighted region weights.
- Stamps `missingRequiredMarkers`, `hierarchy`, `tokens`, `classifiedAt`.
- Adds `source.metadata.classifierVersion` and `source.metadata.classifierNotes`
  (arbiter, regions used, per-field scores, reasoning).
- Produces `arbitrationRequest` iff one or more markers finished
  `pending-arbitration` — i.e., deterministic scoring was ambiguous and an
  in-session LLM needs to pick from a candidate set. The structure is:

  ```json
  {
    "recordId": "...",
    "record": {"title": "...", "breadcrumb": "...", "frontmatter": "...",
               "metadataBlock": "...", "bodyPreview": "..."},
    "candidates": {"<field>": [{"value": "...", "score": 0.0, "..."}]},
    "allowedValues": {"<field>": ["..."]},
    "requiredFields": ["type", "domain", "goal", "status"],
    "instructions": "..."
  }
  ```

  When the headless path (`sync_notion`, ingest CLIs without a live session)
  produces `pending-arbitration`, it overwrites `classifierNotes.arbiter` to
  `"fallback"` and counts it in the response's `fallbackCount`. The record is
  still indexed.

### Tier 3 — stamped by `merge_record` / `index_records`

`index_records` in `scripts/context_graph_core.py` (around line 1419) is the
single write path into `graph.json`. For each incoming record it:

- Runs `classify_record` again (idempotent) to pick up schema changes.
- Calls `merge_record` (around line 1122) against the previous stored copy.
  `merge_record` is order-aware — if both sides have
  `source.metadata.last_edited_time` and the incoming one is older, the
  stored copy wins. Otherwise the incoming record replaces the stored one,
  carrying forward a bumped `revision.version`.
- Stamps a fresh `revision.updatedAt` on the merged dict.
- Calls `rebuild_edges` over the whole record set to re-materialize explicit
  and inferred edges.

`archived` is set by `archive_record` / `unarchive_record`. Adapters should
not set it.

## Source namespace

`source` is the only place adapters are allowed to leak adapter-specific shape
into the record. It has two fields of known meaning plus an open `metadata`
bag:

```json
{
  "source": {
    "system": "notion-export",
    "space": "my-notion-dump",
    "path": "subfolder/Some Page 1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b.md",
    "url": "/absolute/or/canonical/path",
    "metadata": {
      "notionPageId": "1e2f3a4b...",
      "parent": "my-notion-dump > subfolder",
      "last_edited_time": "2026-04-20T10:30:00.000Z",
      "arbitraryAdapterField": "whatever"
    }
  }
}
```

**Top-level fields of `source`:**

- `system` (string, required) — the registered adapter name.
  Current values: `markdown`, `notion-export`, `notion`, `context-graph`
  (promoted records). A future adapter chooses a new slug (`jira`, `linear`,
  `slack-export`) and **does not touch** `context_graph_core`.
- `space` (string, optional) — a top-level container name. For the markdown
  adapters it is the root directory's basename; for Notion it is the workspace
  or database name if the adapter knows it.
- `path` (string, optional) — relative path or stable locator inside the space.
- `url` (string, optional) — absolute URL or filesystem path. For markdown
  adapters this is the absolute filesystem path; for Notion it is the page URL.

**`source.metadata` is the portable bag.**

Anything an adapter wants to keep about the record goes here. Downstream code
must not branch on `source.system`; instead it reads well-known keys from
`source.metadata`. The current well-known keys are:

| Key | Type | Meaning | Used by |
|---|---|---|---|
| `last_edited_time` | ISO-8601 string | When the upstream record was last modified. | `merge_record` for order-aware merges (`scripts/context_graph_core.py` ~line 1122). |
| `parent` | string or `{type, id}` | Human-readable breadcrumb (e.g. `"root > subfolder"`) or a structured parent reference. | `classifier_regions.extract_regions` reads it as the `breadcrumb` region; `classifier_learning.mine_hierarchy` splits it on `>` to propose hierarchy markers. A string is the portable form; Notion's raw `{type, id}` is also accepted and ignored for breadcrumb purposes. |
| `notionPageId` / `notion_page_id` | 32-hex string | Canonical Notion page id. Used to correlate the same page across the export adapter and the live adapter. | Indirectly via the `notion:<32-hex>` id rule. |
| `created_time` | ISO-8601 string | Upstream creation time. | Advisory; not consumed by core today. |
| `classifierVersion` | string | Version tag for the classifier that produced `classifierNotes`. | Stamped by `classify_record`; adapters do not set this. |
| `classifierNotes` | dict | Arbiter, regions used, scores, reasoning. See Tier 2. | Read by `classifier_learning.compute_marker_importance` to weight explicit metadata over body text. |

Adapter-private keys are welcome as long as they do not collide with the keys
above. The persisted graph will faithfully round-trip them.

## ID stability rules

Every record has an `id`. The id is the merge key: a second sync, a re-ingest,
or a live pull against a record that was already imported must produce the
**same** id or the graph will duplicate.

Rules:

1. **Deterministic.** `id` is derived from the source, not from a timestamp or
   a uuid generated at ingest time.
2. **Stable across reruns.** Re-running the same adapter against the same source
   must produce identical ids. `stable_record_id`
   (`scripts/context_graph_core.py` ~line 271) is the fallback; adapters
   should provide their own explicit id rather than rely on it.
3. **Prefixed by system.** The current convention is `<prefix>:<slug>`:
   - `notion:<32-hex>` — a Notion page (see below).
   - `src:<slug>` — the generic markdown/export form.
     `slugify("<system>::<space>::<path>")`.
   - `record:<slug>` — the last-resort form when nothing better is available.
     `slugify(title)`.
   - `promoted:<slug>` — records generated by `promote_pattern`.
   - A new adapter should pick a new prefix that matches its `source.system`
     (e.g., `jira:<issue-key>`, `linear:<issue-id>`).
4. **Cross-adapter convergence on the same identity.** When two adapters can
   observe the same real-world record, they **must** produce the same id so
   the graph merges them into one entry rather than duplicating. The Notion
   case is the canonical example:

   ```
   notion:<32-hex-page-id>
   ```

   - Lowercase.
   - No hyphens (the raw Notion id has hyphens; strip with
     `str(raw_id).replace("-", "").lower()`).
   - The Notion-export adapter extracts the 32-hex suffix from the filename
     (`NOTION_PAGE_ID_RE` in `context_graph_core.py` ~line 91) and emits
     `notion:<hex>`. The live sync emits the same form from `page["id"]`.
     Both merge into the same record. See [notion-sync.md](notion-sync.md) for
     the full story.

5. **Opaque to everything else.** Ids are strings. Nothing in the pipeline
   parses them for semantics beyond the `notion:<32-hex>` rule. A new adapter
   does not need to invent a format that core knows about; it just needs
   intra-adapter stability plus cross-adapter agreement on shared identities.

## Validation rules — what is required, what is optional

The classifier enforces very little at input time; `classify_record` is
permissive. What matters at indexing time:

- `source.system` should be set. Several consumers look it up — only
  `scripts/post_edit_reindex.py` branches on it today, and that is a
  known deviation (see below).
- An `id` (explicit or derivable) is required — the graph is keyed on it.
- `title` and `content` are optional but at least one should be non-empty,
  otherwise classification has nothing to score.
- `markers` that are not in the schema will be kept verbatim (slugified) but
  will not be enforced. The "required markers" list lives in the merged
  schema under `record.requiredMarkers` and drives
  `classified["missingRequiredMarkers"]`; a record with missing required
  markers is still indexed — the gap surfaces later in retrieval scoring and
  in proposal mining.

No top-level field is *schema*-validated at ingest. Callers that want hard
validation should do it before calling `index_records`.

## Known deviations to clean up

These are places where current code either leaks adapter-specific logic into
core or uses `source.system` as a branch. Treat them as the short list for
future core patches; adapter authors should *not* add new branches like these:

1. **`scripts/post_edit_reindex.py` branches on `source.system in {"markdown",
   "notion-export"}`** to decide whether a record represents an on-disk
   directory to reindex. This is the only remaining `source.system` switch
   outside `notion_sync.py` itself. The cleaner shape is to let an adapter
   opt in via a `source.metadata.ingestRoot` (or similar) so the hook can
   discover reindexable roots without a hard-coded allowlist.

2. **`scripts/context_graph_core.py::explicit_id_for_markdown_file`** hard-codes
   the `notion-export` case: if `system == "notion-export"` and the filename
   contains a 32-hex id, the function emits `notion:<hex>`. This is the only
   place in core that knows the name of a specific adapter. It is load-bearing
   today because it is the glue that merges export-mode and live-mode into the
   same record; a future refactor could move the id-derivation strategy into
   the adapter itself, with core exposing only a `register_id_strategy` hook.

3. **`stable_record_id`** composes
   `source.system + source.space + source.path` into a fallback `src:<slug>`
   id. This is intentionally generic and treats `system` as opaque — no
   semantic branching — so it is not a deviation. Listed here for
   contrast.

Neither (1) nor (2) blocks a new adapter from being added; they just mean that
the goal of "core never switches on `source.system`" is 95% achieved, not
100%.
