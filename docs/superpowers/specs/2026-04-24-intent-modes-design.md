# Query Intent Modes — Design Spec

**Status:** design approved, pending implementation plan.
**Phase:** Phase 5 (Smarter retrieval) — closes the last open item.
**Related:** builds on the scoring refactor in `_score_record_detailed` (observability tranche) and the freshness / hop-penalty / conflict-aware scoring added in the Phase 5 mechanical tranche.

---

## 1. Problem

`build_context_pack` and `search_graph` score every query the same way regardless of what the caller is trying to accomplish. A stack-trace-driven "why is this webhook failing" query and a "what is our overall payments architecture" query both run through identical marker weights, the same one-hop traversal cap, and the same relation-type treatment. The roadmap has called for query intent modes since Phase 5 kicked off:

> Add query intent modes: `debug`, `implementation`, `architecture`, `product`.
> Spec: each mode changes which markers dominate ranking, which relation types are followed, and how many hops are allowed.
> Acceptance: `build_context_pack` returns visibly different results for the same query when intent mode is switched.

The goal of this spec is to specify those four modes and the API that exposes them.

## 2. Design decisions (brainstormed, locked)

1. **Model B+C** — intent modes are **weight-tuning + relation-filter + hop-cap**. Modes do NOT filter records by type. Records of any type are eligible; modes only change how they rank and how the traversal frontier expands.
2. **Invocation: explicit only** — intent is passed as an `intentMode` payload field on `build_context_pack` / `search_graph`. No auto-detection from query tokens. No workspace default. Missing `intentMode` → current behavior (backward-compatible).
3. **Presets + per-query override** — four built-in presets cover the 80% case. Callers can pass `intentOverride` with any subset of the per-mode fields to tune live (primary use case: eval-harness experiments without editing source).
4. **Per-mode parameters: 8 knobs** — `markerWeights`, `typeBoost`, `statusBias`, `freshnessMultiplier`, `hopPenalty`, `hopCap`, `allowedRelations`, `includeArchived`. All optional; missing fields fall back to neutral defaults.

## 3. API surface

### 3.1 Payload shape

Both `build_context_pack` and `search_graph` accept two new fields:

```python
{
  "query": "...",
  "graphPath": "...",
  # existing fields...

  "intentMode": "debug" | "implementation" | "architecture" | "product" | None,
  "intentOverride": {                 # all fields optional
    "markerWeights": {"severity": 3.0, "artifact": 2.0, ...},
    "typeBoost":     {"bug": 1.5, ...},
    "statusBias":    {"in-progress": 1.5, ...},
    "freshnessMultiplier": 1.5,
    "hopPenalty": 0.7,
    "hopCap": 2,
    "allowedRelations": ["might_affect", "same_pattern_as"],
    "includeArchived": false,
  } | None
}
```

Semantics:
- `intentMode` absent AND `intentOverride` absent → current behavior (neutral weights, hopCap=1, all relations allowed, current freshness/hop policy).
- `intentMode` set → load preset.
- `intentOverride` set → partial merge over preset; any field in override replaces the preset's field (not merged recursively on dict values — a dict override fully replaces).
- `intentOverride` without `intentMode` → raw config from neutral baseline (power-user / eval-harness path).
- Unknown `intentMode` → `ValueError` with the list of valid names.

### 3.2 MCP schemas

`build_context_pack` and `search_graph` ToolSpec input schemas add:
- `intentMode`: `{"type": ["string", "null"], "enum": ["debug", "implementation", "architecture", "product", null]}`
- `intentOverride`: object with the 8 optional properties above.

`inspect_record` gains the same two fields so `--mode debug` can explain how scoring would rank under that mode.

### 3.3 CLI

No new flags on the base CLI (`build-context-pack`, `search-graph` still read JSON from stdin; payload gains the new fields).

`inspect-record` gains two CLI flags:
- `--mode <name>` → sets `intentMode`.
- `--override <path>` → reads a JSON file containing an `intentOverride` object.

### 3.4 Slash command

`/cg-search` parses an optional `--mode <name>` token. Example:

```
/cg-search --mode debug payment retry loop
/cg-search --mode architecture domain payments
```

The rest of the line after the mode is the query. No `--override` in the slash command (JSON-on-stdin is the escape hatch).

## 4. Preset definitions

All values are the canonical defaults. Tune via eval harness after implementation; commit a revised baseline when changing them.

### 4.1 `debug` — active cascade

```python
markerWeights:       {"severity": 2.5, "artifact": 2.5, "flow": 1.5, "type": 2.0}
typeBoost:           {"bug": 1.5, "incident": 1.5, "debug": 1.5}
statusBias:          {"in-progress": 1.5, "known-risk": 1.3, "new": 1.2,
                      "fixed": 0.6, "done": 0.6}
freshnessMultiplier: 1.5
hopPenalty:          0.7
hopCap:              2
allowedRelations:    ["might_affect", "same_pattern_as"]
includeArchived:     false
```

Rationale: severity and artifact are the primary axes when tracing failures; 2 hops capture cascade neighbors; weak hop penalty (0.7) keeps those neighbors visible; freshness bias (1.5) demotes stale tickets; fixed/done records damped to 0.6; follow `might_affect` and `same_pattern_as` to find similar failures and their impact set.

### 4.2 `implementation` — working examples

```python
markerWeights:       {"flow": 2.0, "artifact": 2.0, "goal": 1.5, "type": 2.0}
typeBoost:           {"rule": 1.5, "spec": 1.5, "pattern": 1.5, "decision": 1.3}
statusBias:          {"done": 1.3, "fixed": 1.1}
freshnessMultiplier: 1.0
hopPenalty:          0.3
hopCap:              1
allowedRelations:    ["same_pattern_as", "derived_from"]
includeArchived:     false
```

Rationale: precision-focused (hopCap=1, aggressive hopPenalty=0.3); favors records that ship (done/fixed ×1.3/1.1); `same_pattern_as` surfaces prior art; `derived_from` brings in the rationale behind a pattern.

### 4.3 `architecture` — decision chains

```python
markerWeights:       {"domain": 2.5, "scope": 2.5, "project": 1.5, "type": 2.0}
typeBoost:           {"architecture": 1.8, "decision": 1.5, "rule": 1.3}
statusBias:          {}                     # neutral
freshnessMultiplier: 0.3
hopPenalty:          0.6
hopCap:              3
allowedRelations:    ["derived_from", "related_pattern"]
includeArchived:     false
```

Rationale: 3-hop traversal follows decision chains (decision → base decision → rule); `freshnessMultiplier=0.3` so old decisions retain weight (year-old architecture is still architecture); `domain`/`scope` dominate; status is irrelevant for "why is it this way"; `derived_from` and `related_pattern` carry the chain.

### 4.4 `product` — forward-looking

```python
markerWeights:       {"goal": 2.5, "project": 2.0, "room": 2.0, "type": 2.0}
typeBoost:           {"spec": 1.5, "research": 1.5, "decision": 1.3}
statusBias:          {"new": 1.3, "in-progress": 1.2, "known-risk": 1.2}
freshnessMultiplier: 1.2
hopPenalty:          0.5
hopCap:              2
allowedRelations:    ["related_pattern", "derived_from"]
includeArchived:     false
```

Rationale: goals / projects / rooms are the dominant dimensions for product thinking; new/in-progress boosted (forward-looking), done is NOT boosted here (unlike implementation); moderate traversal to pick up supporting research/decisions.

### 4.5 Neutral baseline (no mode)

```python
markerWeights:       {}          # all axes 1.0
typeBoost:           {}
statusBias:          {}
freshnessMultiplier: 1.0
hopPenalty:          None        # falls back to global HOP_PENALTY = 0.5
hopCap:              1           # current default
allowedRelations:    None        # all types allowed
includeArchived:     None        # as in payload
```

## 5. Implementation shape

### 5.1 New module: `scripts/intent_modes.py`

Pure, stdlib-only. No I/O. Immutable. Exposes:

```python
@dataclass(frozen=True)
class IntentMode:
    name: str
    marker_weights: dict[str, float]
    type_boost: dict[str, float]
    status_bias: dict[str, float]
    freshness_multiplier: float
    hop_penalty: float | None
    hop_cap: int
    allowed_relations: frozenset[str] | None
    include_archived: bool | None

PRESETS: dict[str, IntentMode] = {
    "debug":          IntentMode(name="debug", ...),
    "implementation": IntentMode(name="implementation", ...),
    "architecture":   IntentMode(name="architecture", ...),
    "product":        IntentMode(name="product", ...),
}

def resolve_intent(
    mode_name: str | None,
    override: dict | None,
) -> IntentMode | None:
    """Return the IntentMode for this query, or None when both args are None."""

def apply_marker_weight(axis: str, intent: IntentMode | None) -> float:
    """Multiplier for the given marker axis. 1.0 when intent is None."""

def apply_type_boost(record_type: str, intent: IntentMode | None) -> float:
    """Multiplier keyed by markers.type. 1.0 when intent is None."""

def apply_status_bias(status: str, intent: IntentMode | None) -> float:
    """Multiplier keyed by markers.status. 1.0 when intent is None."""

def apply_freshness_multiplier(decay: float, intent: IntentMode | None) -> float:
    """Returns decay * intent.freshness_multiplier, or decay when intent is None."""

def is_relation_allowed(rel_type: str, intent: IntentMode | None) -> bool:
    """True when the relation type is allowed to be traversed (default: True)."""

def hop_penalty_for(intent: IntentMode | None) -> float | None:
    """Per-mode hop penalty, or ``None`` to let the caller fall back
    to the module-level ``HOP_PENALTY`` in ``context_graph_core``. This
    keeps ``intent_modes`` pure and free of cross-module imports."""

def hop_cap_for(intent: IntentMode | None, default: int) -> int:
    """Cap from intent or `default` when intent is None."""
```

### 5.2 Integration in `scripts/context_graph_core.py`

**`_score_record_detailed(record, query, graph, intent=None)`**: the existing helper gains an `intent` parameter. Inside the function's existing scoring steps:

1. When computing per-axis marker score, multiply by `apply_marker_weight(axis, intent)` before summing.
2. After marker-score: `score *= apply_type_boost(record["markers"].get("type", ""), intent)`
3. After type-boost: `score *= apply_status_bias(record["markers"].get("status", ""), intent)`
4. When applying freshness decay: `decay = apply_freshness_multiplier(decay, intent)`.
5. The returned `factors` dict gains four new keys: `intentMarkerMultiplier`, `intentTypeBoost`, `intentStatusBias`, `intentFreshnessMultiplier`. Each is the product actually applied (for `intentMarkerMultiplier`, the per-axis factors can be stored as a sub-dict). These are what `inspect_record` prints.

**`build_context_pack`** parses `intentMode` / `intentOverride`, calls `resolve_intent`, and passes the result through to:
- `_score_record_detailed` (via the existing ranking loop).
- The traversal loop: for each hop `h` in `range(1, hop_cap_for(intent, default=1) + 1)`, iterate edges whose type passes `is_relation_allowed(edge["type"], intent)`. The per-hop penalty is `intent.hop_penalty if intent is not None and intent.hop_penalty is not None else HOP_PENALTY` (the same fallback pattern callers already use elsewhere in `context_graph_core`).

**`search_graph`** applies the same payload parsing and passes `intent` into scoring. It does not traverse today, so no traversal change here; the hop knobs have no effect on `search_graph`.

**`inspect_record`** accepts `intentMode` / `intentOverride`, forwards to `_score_record_detailed`, and `format_inspect_record` prints the four new intent factors when present.

### 5.3 What does NOT change

- Graph file format (`graph.json`).
- `classify_record`, `index_records`, `merge_record`, `promote_pattern`, `ingest_*` — intent is query-time only and is never persisted.
- Workspace manifest (`workspace.json`) — no new fields.
- Inference engine (`infer_relations`, `rebuild_edges*`) — relation types continue to be produced the same way; intent only controls which ones are followed at query time.

### 5.4 Risks and mitigations

- **Double-weighting.** `typeBoost` multiplies after `markerWeights["type"]` already applied. This is intentional — "type axis matters more AND specific types are even more relevant" — but documented so nobody is surprised. Test asserts the product is what the spec says.
- **Undefined marker axis.** `markerWeights` entry for an axis that the record lacks → helper returns 1.0. No KeyError.
- **Override without mode.** `intentOverride={"hopCap": 3}` alone must work as "neutral baseline with hopCap=3". `resolve_intent` constructs a neutral IntentMode and merges over it. Tested.
- **Backward compatibility.** `_score_record_detailed(record, query, graph)` (without `intent`) and `_score_record_detailed(record, query, graph, intent=None)` must return identical numbers. Existing test `test_record_weight_and_detailed_agree` plus a new `test_build_context_pack_without_intent_matches_pre_change` (comparing against a snapshot of today's eval-harness output) enforce this.

## 6. Testing

### 6.1 Unit (new `tests/test_intent_modes.py`, ~18–22 tests)

- `resolve_intent` — 4 presets load; unknown name raises `ValueError` listing known presets; `(None, None)` returns `None`; override alone yields a neutral-base mode; partial override only touches specified fields.
- Each preset's eight fields match the spec numbers exactly (regression guard for accidental tuning).
- Each of the seven pure helpers: values for `intent=None`, for a mode where the relevant field is set, and for a mode where it is unset (fall-back behavior).
- `_merge_override`: each field can be replaced individually; dict fields fully replace, not recursively merge.

### 6.2 Integration (extend `tests/test_core.py` or new `tests/test_intent_integration.py`, ~8–10 tests)

- **Acceptance test:** same query across four modes produces four different top-5 orderings; specific assertions per mode (e.g. under `architecture`, `r-payment-architecture-decision` ranks higher than under `debug`).
- **Traversal routing:** seed a 4-record graph with both `might_affect` and `derived_from` edges. Under `debug`, the `might_affect` neighbor appears in the pack; the `derived_from` neighbor does not. Under `architecture`, reversed.
- **HopCap enforcement:** 3-hop chain fixture; `architecture` (cap=3) reaches the far end; `debug` (cap=2) does not; baseline (cap=1) reaches only the direct neighbor.
- **Backward compat:** `build_context_pack` without `intentMode` matches pre-change baseline byte-for-byte via a snapshot test. `search_graph` similarly.
- **`inspect_record` under a mode:** returned factors include the four intent multipliers; mathematical consistency — product of factors equals the final score.

### 6.3 Eval harness integration

`data/eval/queries.json` already has `intent` per query. Today the harness ignores it. Change `scripts/eval_harness.py::run_harness` to pass `intentMode=query["intent"]` into `build_context_pack`.

Expected eval shape after landing:
- Mean precision@k currently 0.683 (declared-intent ignored).
- Expected: precision@k ≥ 0.75 with intent routing — verify per-query no regression (any query whose precision drops under its own declared intent signals a bad preset and is a TUNING WAIT before committing the new baseline).
- Recall@k stays 1.0 on the 15-record fixture (set is small).
- Pack-ratio may rise slightly for `debug`/`architecture` (larger hopCap) and drop for `implementation` (hopCap=1 + strong hop penalty).

Baseline rewrite policy: rewrite `data/eval/baseline.json` ONCE in the same commit that enables intent routing in the harness. Commit message must note the rewrite and the before/after numbers. After that commit, eval-gate prevents regressions on the new baseline.

### 6.4 Docs

- `docs/retrieval.md` — new "Intent modes" section: the preset table, an `intentOverride` example, how to extend (point at pure helpers).
- `docs/observability.md` — example of `inspect-record --mode debug` output.
- `commands/cg-search.md` — `/cg-search --mode architecture ...` example.
- `CHANGELOG.md` — Unreleased section entry.

## 7. Out of scope

- Auto-detection of intent from query tokens (rejected during brainstorm — brittle UX).
- Workspace-level default intent mode (rejected — adds config surface for 5% usage).
- User-defined modes via `.context-graph/intent_modes.json` (rejected — preset + override covers experimentation; custom modes are future work if eval proves we need them).
- New relation types in the inferencer (separate concern; modes consume existing types).
- Changes to `classify_record` / `index_records` / `promote_pattern` / learning loop (intent is query-time only).
- Tuning constants beyond the presets declared here (first-pass tuning happens in the implementation plan; structural changes go in a separate doc).

## 8. Success criteria

1. `build_context_pack` under `intentMode="debug"` and `intentMode="architecture"` return measurably different top-5 for at least one query in `data/eval/queries.json`.
2. All 269 pre-change tests pass unchanged.
3. New unit + integration tests (~25–30) pass.
4. Eval harness with intent routing yields precision@k ≥ 0.75 (see §6.3 for the tuning gate).
5. `inspect-record --mode debug` produces a report that shows at least the four intent factors and their product matches the final score.
6. Slash command `/cg-search --mode architecture payments` works end-to-end.

## 9. Open questions deferred to implementation

1. Exact eval-baseline after tuning — numbers in §6.3 are expectations, real numbers settle in the implementation plan.
2. Whether `_merge_override` should warn when an unknown field is passed (lenient vs strict). Default: lenient (ignore unknown keys, log at DEBUG). Revisit if it causes user confusion.
3. Whether `/cg-search` should accept mode as positional first arg without `--mode` prefix ("lazy mode shortcut"). Default: `--mode` only.

---

**Ready for implementation plan.** Next step: invoke the `writing-plans` skill to break this into bite-sized TDD tasks.
