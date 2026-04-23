# Workspace binding + adaptive classifier — design spec

**Status**: draft, pending user review
**Phase**: 1 of the long-term "Notion as second memory" vision
**Date**: 2026-04-23

## TL;DR

Phase 1 lands two tightly coupled features that unblock every later phase:

1. **Workspace binding** — each project directory maps to a Notion root page. All classification, indexing, and learning state lives per-workspace in `<project>/.context-graph/`.
2. **Adaptive classifier** — the deterministic classifier is replaced by a four-stage pipeline (region extraction → IDF-weighted scoring → threshold arbiter → optional in-session LLM) with a learning loop that mines project-specific taxonomy from the corpus itself.

Together these two give the plugin a sense of *which* project it is in and a vocabulary that grows to fit that project without the user editing YAML.

## Goals

- Each directory the user opens a CC session in can become a named workspace with its own Notion root and its own graph.
- Deterministic classification stops losing to term-frequency bias (`trading` beating `payments` only because it appears in API names).
- Explicit metadata users write in Notion (frontmatter, `## Metadata` blocks, titles, breadcrumbs) is given more weight than incidental body mentions.
- The taxonomy grows automatically from the corpus via hierarchy mining, n-gram mining, and code-path mining; proposals surface in a review queue, and the plugin learns the user's acceptance pattern over time.
- Ambiguous classifications are upgraded by the session's own Claude (subscription only — no API key), not a second-tier rules layer.
- Headless / cron callers keep working without any LLM; they accept deterministic fallbacks and can be reclassified later in a live session (deferred to Phase 2).

## Non-goals for Phase 1

- Session-start bootstrap that seeds a fresh Notion folder from the codebase (Phase 2 — S2 + S3).
- Automatic note creation on every user query — the "write-back to Notion" feature (Phase 3 — S5).
- Proactive auto-loading of related notes based on detected keywords in user prompts (Phase 3 — S7).
- `ANTHROPIC_API_KEY`-driven classification. The plugin stays inside the user's CC subscription.
- Cross-workspace operations, schema sharing between teams at runtime, dashboard views.
- Reclassification of records produced by the legacy classifier (handled by `/cg-reclassify` in Phase 2).

## Vision context

Long-term the plugin is an agent that keeps Notion as a second memory, synced per-project. The user described seven subsystems:

| # | Subsystem | Phase |
|---|---|---|
| S1 | Workspace binding (directory ↔ Notion folder) | 1 |
| S2 | Session bootstrap (find folder, prompt if empty) | 2 |
| S3 | Codebase introspection (seed initial notes) | 2 |
| S4 | Query-driven retrieval | 1 + 2 + 3 incremental |
| S5 | Memory enrichment (write back to Notion) | 3 |
| S6 | Adaptive taxonomy (self-growing tags) | 1 |
| S7 | Auto-loaded context on keyword | 3 |

Phase 1 delivers S1 + S6 as the backbone. Without S1 all projects collide in one graph; without S6 the graph grows as a dump. Everything else composes on top.

---

## 1. Architecture overview

Classifier becomes a four-stage pipeline; a fifth stage runs post-ingest:

```
Raw record
    │
    ▼
[1. Structure Extractor]     → regions: frontmatter, metadataBlock,
    │                          titleText, breadcrumb, body
    ▼
[2. Scorer]                  → for each marker field, per-candidate score
    │                          = Σ (region_weight × IDF × match_signal)
    │                          schema = shipped ⊕ learned.accepted ⊕ overlay
    ▼
[3. Arbiter]                 → top ≥ HIGH_CONFIDENCE and gap ≥ MIN_GAP
    │                          → deterministic; else → pending-arbitration
    ▼
[4. LLM Arbiter] (optional)  → in-session Claude picks from candidates
    │                          headless → fallback to deterministic top
    ▼
Classified record with classifierNotes (scores, arbiter, reasoning)

[5. Corpus Learner]          → runs after index_records
                               light-pass on new records;
                               full-pass on explicit /cg-schema-learn
                               → updates schema.learned.json
```

Core properties:

- **Pure-functional stages**: each stage is `(record, context) → record'`. Every stage is independently testable and swappable.
- **Progressive enhancement**: without overlay, without IDF stats, without LLM — the pipeline collapses to today's behavior. Nothing regresses.
- **Reasoning as first-class output**: `classifierNotes.reasoning` and `.scores` are persisted on every record; this is the foundation for later feedback loops and for debugging surprising rankings.
- **LLM is a boost, not a critical path**: 80% of records classify deterministically; LLM only arbitrates the ambiguous tail.

## 2. Data model

### 2.1 `<workspace>/.context-graph/schema.overlay.json` (optional, user-curated, git-tracked)

Same shape as shipped `docs/schema.json`. Lives per-workspace so different projects can keep different overlays. Merge rules at load time:

- `markers.<field>`: union of lists.
- `aliases.<field>.<canonical>`: concat.
- `hierarchy.preferredOrder`: overlay replaces if present.
- `relations.explicit` / `relations.inferred`: union.

### 2.2 `<workspace>/.context-graph/workspace.json` (git-tracked)

```json
{
  "version": "1",
  "id": "ws-a1b2c3d4",
  "rootPath": "/Users/maks/projects/myapp",
  "notion": {
    "rootPageId": "34a37bbb09ff81839b2ae100879d1089",
    "rootPageUrl": "https://www.notion.so/...",
    "createdAt": "2026-04-23T12:00:00Z"
  },
  "createdAt": "2026-04-23T12:00:00Z",
  "updatedAt": "2026-04-23T12:00:00Z"
}
```

### 2.3 `<workspace>/.context-graph/schema.learned.json` (gitignored, auto-mined)

```json
{
  "version": "1",
  "corpusSize": 10,
  "updatedAt": "2026-04-23T...",
  "proposals": {
    "pending": [
      {
        "value": "challenge-payment",
        "source": "ngram",
        "confidence": 0.78,
        "supportRecords": ["notion:33337...", "notion:33537..."],
        "detail": { "ngram": ["challenge", "payment"], "docFreq": 6, "pmi": 2.3 }
      }
    ],
    "rejected": [
      { "value": "bl-api", "field": null, "rejectedAt": "2026-04-23T..." }
    ]
  },
  "accepted": {
    "domain": ["challenge", "promo", "ib-commission"],
    "artifact": ["ninjacharge", "room-override"]
  },
  "markerImportance": {
    "domain": 0.87,
    "artifact": 0.75,
    "type": 0.90,
    "flow": 0.42,
    "status": 0.85,
    "severity": 0.60
  }
}
```

### 2.4 `<workspace>/.context-graph/schema.feedback.json` (gitignored, personal)

```json
{
  "version": "1",
  "totalDecisions": 7,
  "bootstrapComplete": false,
  "acceptedBySource": {
    "fromHierarchy": { "accepted": 3, "rejected": 0 },
    "fromNgrams":    { "accepted": 1, "rejected": 3 },
    "fromCode":      { "accepted": 0, "rejected": 0 }
  },
  "autoAcceptPolicy": {
    "minConfidence": 0.80,
    "sourcesAutoAccept": ["fromHierarchy"],
    "sourcesAlwaysReview": ["fromNgrams", "fromCode"]
  }
}
```

### 2.5 `<workspace>/.context-graph/idf_stats.json` (gitignored)

```json
{
  "version": "1",
  "corpusSize": 10,
  "updatedAt": "2026-04-23T...",
  "tokenDocumentFrequency": {
    "trading": 10,
    "challenge": 9,
    "upsale": 3,
    "ninjacharge": 2
  }
}
```

### 2.6 Record additions

New `source.metadata` fields on every classified record:

```json
{
  "classifierVersion": "2",
  "classifierNotes": {
    "arbiter": "deterministic" | "pending-arbitration" | "llm-session" | "fallback",
    "regionsUsed": ["metadataBlock", "titleText", "body"],
    "scores": {
      "domain": [
        { "value": "payments", "score": 0.82 },
        { "value": "trading",  "score": 0.41 }
      ]
    },
    "reasoning": null
  }
}
```

Old records without `classifierVersion` remain valid; they are reclassified only via an explicit `/cg-reclassify` operation (Phase 2).

## 3. Workspace architecture

### 3.1 Root detection

Walk up from the current working directory looking for `.context-graph/workspace.json`. First match wins. Analogous to how `git` finds `.git`.

### 3.2 Per-workspace data layout

```
<workspace>/
  .context-graph/
    workspace.json        ✓ git
    graph.json            ✗ gitignore
    schema.learned.json   ✗ gitignore
    schema.feedback.json  ✗ gitignore
    idf_stats.json        ✗ gitignore
    notion_cursor.json    ✗ gitignore
    schema.overlay.json   ✓ git (optional)
```

### 3.3 `/cg-init` flow

1. Walk up from CWD for existing `.context-graph/`; if found, report "already initialized" and exit.
2. Ask: "Use `<cwd>` as the workspace root? `[y / N / <other path>]`". Accept alternative path if the user wants the root at a parent or sibling.
3. Create `<root>/.context-graph/workspace.json` with a generated id and the confirmed absolute `rootPath`.
4. Ask about Notion: create a root page automatically under a plugin-managed parent, accept a user-supplied parent URL / id, or skip for now.
5. If creating: call `mcp__notion__notion-create-pages` with the chosen parent and a title derived from the directory name; store the returned page id back into `workspace.json`.
6. Append the five gitignored filenames to the repo's `.gitignore`.
7. Print summary including the Notion URL.

### 3.4 Session start behavior

Silent. Walks up from CWD; if a workspace is found, warm up its graph; if not, no-op. Initialization never happens implicitly from a hook — only via `/cg-init`.

### 3.5 Path resolution changes in `context_graph_core.py`

- `find_workspace_root(start=None) -> Path | None`
- `require_workspace(start=None) -> Path` — raises `WorkspaceNotInitializedError`
- `default_graph_path()` resolves via `require_workspace`
- Similar resolvers for `schema_learned_path`, `schema_overlay_path`, `schema_feedback_path`, `idf_stats_path`, `notion_cursor_path`.

Compat: `CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1` env var keeps plugin-local paths (only for the plugin's own tests).

### 3.6 Multi-workspace

Every public function accepts an optional `workspaceRoot` payload field that short-circuits the walk-up. This enables tests, cross-project scripting, and future multi-workspace flows.

## 4. Pipeline scoring

### 4.1 Region extraction

```python
def extract_regions(record) -> dict[str, str]:
    if record.get("structuredContent"):
        return record["structuredContent"]
    content = record.get("content", "")
    frontmatter, rest = parse_frontmatter(content)
    metadata_block, body = extract_metadata_block(rest)
    return {
        "frontmatter":   frontmatter,
        "metadataBlock": metadata_block,
        "titleText":     record.get("title", ""),
        "breadcrumb":    record.get("source", {}).get("metadata", {}).get("parent", ""),
        "body":          body,
    }
```

`extract_metadata_block` recognizes `## Metadata` or `# Metadata` headings and captures content up to the next top-level heading.

### 4.2 Region weights

```python
REGION_WEIGHTS = {
    "frontmatter":   5.0,
    "metadataBlock": 4.0,
    "titleText":     3.0,
    "breadcrumb":    2.0,
    "body":          1.0,
}
```

### 4.3 Scoring formula

For each `(marker_field, candidate_value)`:

```python
def score_candidate(field, value, regions, idf) -> float:
    canonical_forms = [value] + aliases[field].get(value, [])
    raw = 0.0
    total_weight = 0.0
    for region_name, region_text in regions.items():
        w = REGION_WEIGHTS[region_name]
        total_weight += w
        for token in tokenize(region_text):
            if matches(token, canonical_forms):
                raw += w * idf.get(token, 1.0)
    max_idf = max(idf.values(), default=1.0)
    return raw / (total_weight * max_idf)   # normalized to [0, 1]
```

When `idf_stats.json` is absent, IDF falls back to uniform 1.0 and the scoring becomes structure-only.

### 4.4 Thresholds

```python
HIGH_CONFIDENCE = 0.75
MIN_GAP         = 0.15
MIN_SCORE       = 0.20
```

```
top, runner = top two scores
if top < MIN_SCORE:              value=None;               arbiter="fallback"
elif top >= HIGH and gap>=GAP:   value=top;                arbiter="deterministic"
else:                            value=top (draft);        arbiter="pending-arbitration"
```

### 4.5 Tie-breaking when LLM unavailable

Records with `arbiter="pending-arbitration"` in headless mode degrade to `arbiter="fallback"` with the deterministic top value preserved. Within a tie, prefer the candidate with more mentions in the highest-priority region; then alphabetical for determinism.

### 4.6 Missing required markers

Required fields listed in `schema.record.requiredMarkers`. If a required field produces `value=None` after scoring, it enters `missingRequiredMarkers` and is prioritized for LLM arbitration when available.

## 5. LLM arbitration

Two modes only. No Anthropic API key path.

| Mode | Context | Who calls the LLM |
|---|---|---|
| A: In-session | slash command / skill in a live CC session | orchestrator Claude, subscription-billed |
| B: Fallback | `sync_notion` via Python CLI / cron | nothing — deterministic top kept |

### 5.1 Extended `classify_record` output

When `arbiter=="pending-arbitration"`, the result includes `arbitrationRequest`:

```json
{
  "arbitrationRequest": {
    "record": {
      "title": "...",
      "breadcrumb": "...",
      "frontmatter": {},
      "metadataBlock": "",
      "bodyPreview": "first 2000 chars..."
    },
    "candidates": {
      "domain": [{ "value": "payments", "score": 0.41 }, { "value": "trading", "score": 0.38 }]
    },
    "allowedValues": {
      "domain": ["payments", "auth", "kyc", "trading", "challenge", "promo", "ib-commission"]
    },
    "requiredFields": ["type", "domain", "goal", "status"],
    "instructions": "<embedded instructions>"
  }
}
```

### 5.2 Shared prompt builder

`scripts/classifier_prompt.py` exports `build_arbitration_prompt(request) -> str`. Used by the slash-command body to describe what the session's LLM should do. No Python code ever posts the prompt to an external API.

### 5.3 Slash command orchestration

`/cg-sync-notion` (and future `/cg-classify-live`) loops records through `classify_record`, handles `pending-arbitration` by having Claude itself read `arbitrationRequest` and pick values from `allowedValues`, writes them back to the record, then calls `index_records` with the finalized batch.

### 5.4 Validation before persisting LLM output

- Every returned value must be in the field's `allowedValues`; else fallback to deterministic top.
- Required-field null response falls back to deterministic top.
- Missing or malformed reasoning → synthesized from top-3 deterministic scores.

### 5.5 Headless degradation

`scripts/notion_sync.py` never calls out for arbitration. `pending-arbitration` records are persisted with `arbiter="fallback"` and the deterministic top value. `fallbackCount` is returned in the sync result so the user can trigger a future reclassify pass.

## 6. Learning loop

### 6.1 Triggers

| Trigger | Scope | Cost |
|---|---|---|
| After `index_records` | Light-pass on added records only | ~ms |
| Explicit `/cg-schema-learn` | Full-pass on whole corpus | ~seconds |

Light-pass is non-blocking; full-pass prints a summary.

### 6.2 Mining strategies

#### Hierarchy mining

From `source.metadata.parent` breadcrumbs:

```
support         = ancestor_count / corpus_size
distinctiveness = 1 - support
depth_penalty   = 1 - (avg_depth / 5)
confidence      = 0.4·support + 0.4·distinctiveness + 0.2·depth_penalty
```

Filter: appears in ≥2 records and <100% of the corpus; confidence ≥ 0.5.

#### N-gram mining

Bi-grams from body and title. For each:

```
idf = log(N / doc_freq)
pmi = log(joint_freq · N / (token_a_freq · token_b_freq))
confidence = min(1.0, 0.5·(idf/log N) + 0.3·(pmi/log N) + 0.2·(doc_freq/N))
```

Filter: `doc_freq ≥ 2 AND doc_freq < N AND confidence ≥ 0.5`.

#### Code-path mining

Regex over body for filesystem-like paths (`a/b/c.ext`). Extract path components that appear in ≥2 separate path mentions and aren't in a common-prefix list (`app`, `src`, `lib`, `modules`).

### 6.3 Field assignment during review

The user picks the field during `/cg-schema-review`:

```
[1/7] Proposed value: "challenge-payment"
      Source: ngram ("challenge payment" × 6 records)  Confidence: 0.78
      Sample records: Critical Business Processes, Study bl-api challenge hot paths, ...
      Which field?
      [d] domain  [f] flow  [a] artifact  [t] type  [s] skip  [r] reject entirely
```

After a handful of decisions the plugin learns patterns ("n-grams starting with `challenge-` → domain") and starts pre-selecting the field.

### 6.4 Marker importance

Per marker field:

```
presence_rate   = populated / total
discriminative  = normalized entropy of value distribution
explicit_rate   = populated-from-frontmatter-or-metadata / populated
importance      = 0.3·presence + 0.4·discriminative + 0.3·explicit
```

Stored in `schema.learned.json.markerImportance`. Consumed by `search_graph` / `build_context_pack` to weight matched markers:

```python
exactness_score = sum(importance[F] for F in matched) / sum(importance[F] for F in queried)
```

Importance does NOT influence classification — there all fields are treated equally so "popular" fields don't snowball.

### 6.5 Bootstrap → auto-accept policy

Per `schema.feedback.json`:

- First 10 decisions: everything is proposed, nothing auto-accepted.
- After 10 decisions: `autoAcceptPolicy` is derived from accept-rate per source.
- Subsequent ingests: proposals matching the policy (source ∈ `sourcesAutoAccept` AND confidence ≥ `minConfidence`) go straight into `accepted`; the rest queue for review.
- Rejected values persist forever; never re-proposed.

### 6.6 Performance budget

- Light-pass on 10 new records: ~50ms.
- Full-pass on 1000 records: 3–5 seconds.
- Memory on 1000 records: 2–5 MB.

## 7. Integration and migration

### 7.1 New Python functions

```
find_workspace_root, require_workspace, init_workspace,
learn_schema, list_proposals, apply_proposal_decision.
```

### 7.2 New MCP tools

`init_workspace`, `learn_schema`, `list_proposals`, `apply_proposal_decision`.

### 7.3 New slash commands

`/cg-init`, `/cg-schema-learn`, `/cg-schema-review`.

### 7.4 Updated `classify_record` contract

- Always returns `classifierNotes` (with `arbiter`, `regionsUsed`, `scores`, optional `reasoning`).
- When arbitration is needed, additionally returns `arbitrationRequest`.
- Old callers that ignore new fields are unaffected.

### 7.5 Updated `/cg-sync-notion` flow

1. `require_workspace` — fail fast with guidance to run `/cg-init` if missing.
2. `notion-search` + `notion-fetch` per the existing contract.
3. For each fetched page: build draft, call `classify_record`, handle `pending-arbitration` with session-LLM picks, add to batch.
4. `index_records` once with the whole batch (triggers light-learn).
5. Surface proposal count if new pending proposals exist.

### 7.6 Updated `sync_notion` (Python headless) flow

Identical except step 3: no LLM arbitration; `pending-arbitration` degrades to `fallback`. Result payload gains `fallbackCount`.

### 7.7 Backward compatibility

- `CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1` preserves plugin-local data (plugin's own tests).
- `/cg-migrate-to-workspace` (one-off): interactively moves `<plugin>/data/graph.json` into a user-specified workspace's `.context-graph/`.
- Records without `classifierVersion` remain valid; `/cg-reclassify` (Phase 2) will upgrade them.

### 7.8 Dependencies

Stdlib only. All statistics (IDF, entropy, PMI) implemented with `collections.Counter` and `math.log`.

### 7.9 Hooks

No architectural change. `SessionStart` and `PostToolUse` (`scripts/post_edit_reindex.py`) already work with walk-up workspace resolution because both already consult `CLAUDE_PLUGIN_ROOT`; they just need to additionally respect `.context-graph/` boundaries.

## 8. Testing strategy

### 8.1 New unit test modules

| File | Scope | Approx tests |
|---|---|---|
| `tests/test_workspace.py` | walk-up, init, `.gitignore` edits | 8 |
| `tests/test_regions.py` | frontmatter + metadata-block extraction | 6 |
| `tests/test_scorer.py` | score formula, IDF fallback, normalization | 6 |
| `tests/test_arbiter.py` | thresholds, required-field handling, pending-arbitration shape | 5 |
| `tests/test_learning.py` | hierarchy + n-gram + code-path mining, importance scoring | 8 |
| `tests/test_schema_merge.py` | shipped ⊕ learned ⊕ overlay, rejected blacklist | 4 |
| `tests/test_proposals.py` | accept / reject / skip, bootstrap policy | 7 |
| `tests/test_integration.py` | workspace → ingest → learn → review end-to-end | 5 |

Projected totals: 42 existing tests → ~91 tests after Phase 1.

### 8.2 LLM arbitration — no real calls

A `FakeArbiter` helper in the test suite simulates orchestrator decisions. Public functions that trigger arbitration accept an optional injectable `arbiter` callable. Tests verify:

- Shape of `arbitrationRequest`.
- Merging of LLM-provided markers back into the record (with validation).
- Fallback when `arbiter` is absent.

Quality of LLM classification itself is validated via manual smoke testing in live sessions.

### 8.3 Backward compat

All 42 existing tests must keep passing. They already use explicit `graphPath` against `tempfile.TemporaryDirectory`, so they do not rely on plugin-local defaults.

### 8.4 Fixtures

New fixture directories under `tests/fixtures/`:

```
workspaces/     bare, with-graph, with-proposals
classification/ record-with-frontmatter, record-with-metadata-block, record-combo-page, record-pure-body
learning/       corpus-10-challenge, corpus-heterogeneous
schema/         overlay-sample, learned-after-bootstrap, feedback-mid-bootstrap
```

### 8.5 Performance benchmarks (advisory, not gating)

Optional `tests/bench_classifier.py` run outside the unittest suite:

- 100 records full pipeline: <2s
- 1000 records full-pass learn: <10s
- Memory within 50 MB

### 8.6 Out of scope for Phase 1 tests

- Live Notion API (remains `scripts/smoke_notion.py`).
- Live LLM arbitration quality (manual).
- Migration path from legacy plugin-local data (manual).

## Glossary

- **Workspace**: a directory containing `.context-graph/workspace.json`, bound to a single Notion root page.
- **Shipped schema**: `docs/schema.json`, ships with the plugin, never mutated at runtime.
- **Overlay schema**: optional user-curated additions, lives per-workspace.
- **Learned schema**: auto-mined proposals + accepted values, lives per-workspace.
- **Arbiter**: the final decider for a marker value — deterministic rules, in-session LLM, or fallback.
- **Pending-arbitration**: a classifier result that is below threshold and awaits LLM resolution.
- **Light-pass**: incremental learning run on newly added records.
- **Full-pass**: global re-analysis of the corpus triggered by `/cg-schema-learn`.

## Open questions (to resolve during implementation)

1. Exact `extract_metadata_block` heuristic — how to detect "## Metadata" variants across languages (`## Метадані`, `## Метаданные`). Leaning toward a configurable list.
2. Whether `/cg-schema-review` should persist a partial session so the user can review in several sittings (probably yes; deferred to review UX work).
3. The plugin-managed `🤖 Context Graph` parent page convention in Notion — auto-create on first `/cg-init`, or wait until the user opts in. Leaning toward opt-in.
