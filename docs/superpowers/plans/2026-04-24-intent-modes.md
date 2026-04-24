# Query Intent Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the final Phase 5 item — four query intent modes (`debug`, `implementation`, `architecture`, `product`) that tune scoring weights, traversal depth, and relation filtering per query, with an explicit `intentMode` payload field + optional `intentOverride` escape hatch.

**Architecture:** A new pure stdlib module (`scripts/intent_modes.py`) holds the four preset dataclasses and seven pure helpers (`resolve_intent`, `apply_marker_weight`, `apply_type_boost`, `apply_status_bias`, `apply_freshness_multiplier`, `is_relation_allowed`, `hop_penalty_for`, `hop_cap_for`). The existing `_score_record_detailed` gains an `intent=None` parameter and applies multipliers through those helpers. `build_context_pack` parses payload and passes intent through to scoring AND to the existing two-pass traversal, which consults `hop_cap_for`, `hop_penalty_for`, and `is_relation_allowed` for its frontier expansion. Search, inspect, CLI, MCP, slash-command, and eval harness get the same payload hookup. No new persisted state.

**Tech Stack:** Python 3.11 stdlib (no external deps), `unittest`, MCP JSON-RPC over stdio, markdown docs.

**Spec:** [docs/superpowers/specs/2026-04-24-intent-modes-design.md](../specs/2026-04-24-intent-modes-design.md)

---

## Milestone 1 — intent_modes module (pure, stdlib-only)

Goal: stand up the `scripts/intent_modes.py` module with the `IntentMode` dataclass, four presets, `resolve_intent`, and seven pure helpers. No integration in scoring yet — this milestone ships a self-contained module with full unit coverage.

### Task 1: `IntentMode` dataclass + neutral baseline

**Files:**
- Create: `scripts/intent_modes.py`
- Test: `tests/test_intent_modes.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intent_modes.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from intent_modes import IntentMode, NEUTRAL_INTENT  # noqa: E402


class IntentModeDataclassTests(unittest.TestCase):
    def test_intent_mode_is_frozen(self):
        mode = IntentMode(
            name="debug",
            marker_weights={"severity": 2.5},
            type_boost={"bug": 1.5},
            status_bias={"in-progress": 1.5},
            freshness_multiplier=1.5,
            hop_penalty=0.7,
            hop_cap=2,
            allowed_relations=frozenset({"might_affect"}),
            include_archived=False,
        )
        with self.assertRaises(Exception):
            mode.name = "other"  # type: ignore[misc]

    def test_neutral_intent_fields(self):
        self.assertEqual(NEUTRAL_INTENT.name, "")
        self.assertEqual(NEUTRAL_INTENT.marker_weights, {})
        self.assertEqual(NEUTRAL_INTENT.type_boost, {})
        self.assertEqual(NEUTRAL_INTENT.status_bias, {})
        self.assertEqual(NEUTRAL_INTENT.freshness_multiplier, 1.0)
        self.assertIsNone(NEUTRAL_INTENT.hop_penalty)
        self.assertEqual(NEUTRAL_INTENT.hop_cap, 1)
        self.assertIsNone(NEUTRAL_INTENT.allowed_relations)
        self.assertIsNone(NEUTRAL_INTENT.include_archived)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: `ModuleNotFoundError: No module named 'intent_modes'`.

- [ ] **Step 3: Create the module with the dataclass + neutral baseline**

```python
# scripts/intent_modes.py
"""Query-time intent modes for retrieval scoring.

Intent modes are pure, immutable presets that tune per-axis marker
weights, record-type boosts, status bias, freshness multiplier, hop
penalty, hop cap, and the allowed set of relation types during
traversal. The presets are applied in ``_score_record_detailed`` and in
the ``build_context_pack`` traversal loop; helpers return neutral values
when ``intent`` is ``None`` so the non-intent path is a strict no-op.

See ``docs/superpowers/specs/2026-04-24-intent-modes-design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntentMode:
    name: str
    marker_weights: dict[str, float] = field(default_factory=dict)
    type_boost: dict[str, float] = field(default_factory=dict)
    status_bias: dict[str, float] = field(default_factory=dict)
    freshness_multiplier: float = 1.0
    hop_penalty: float | None = None
    hop_cap: int = 1
    allowed_relations: frozenset[str] | None = None
    include_archived: bool | None = None


# The neutral baseline mirrors the non-intent defaults: unit multipliers
# everywhere, ``hop_penalty=None`` (fall back to the global
# ``HOP_PENALTY``), ``hop_cap=1``, and ``allowed_relations=None`` (all
# types permitted). Used internally by ``resolve_intent`` when the
# caller passes an ``override`` without a preset name.
NEUTRAL_INTENT = IntentMode(name="")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: 2 tests pass.

- [ ] **Step 5: Run the full suite to verify no regression**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: previous count + 2 pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/intent_modes.py tests/test_intent_modes.py
git commit -m "Add IntentMode dataclass and neutral baseline"
```

---

### Task 2: Four presets — `debug`, `implementation`, `architecture`, `product`

**Files:**
- Modify: `scripts/intent_modes.py` (append `PRESETS` dict)
- Test: `tests/test_intent_modes.py` (extend)

- [ ] **Step 1: Append failing tests for presets**

```python
# Append to tests/test_intent_modes.py
from intent_modes import PRESETS  # noqa: E402


class PresetTests(unittest.TestCase):
    def test_four_presets_registered(self):
        self.assertEqual(
            sorted(PRESETS.keys()),
            ["architecture", "debug", "implementation", "product"],
        )

    def test_debug_preset_values(self):
        mode = PRESETS["debug"]
        self.assertEqual(mode.name, "debug")
        self.assertEqual(mode.marker_weights, {
            "severity": 2.5, "artifact": 2.5, "flow": 1.5, "type": 2.0
        })
        self.assertEqual(mode.type_boost, {
            "bug": 1.5, "incident": 1.5, "debug": 1.5
        })
        self.assertEqual(mode.status_bias, {
            "in-progress": 1.5, "known-risk": 1.3, "new": 1.2,
            "fixed": 0.6, "done": 0.6,
        })
        self.assertEqual(mode.freshness_multiplier, 1.5)
        self.assertEqual(mode.hop_penalty, 0.7)
        self.assertEqual(mode.hop_cap, 2)
        self.assertEqual(mode.allowed_relations, frozenset({"might_affect", "same_pattern_as"}))
        self.assertFalse(mode.include_archived)

    def test_implementation_preset_values(self):
        mode = PRESETS["implementation"]
        self.assertEqual(mode.name, "implementation")
        self.assertEqual(mode.marker_weights, {
            "flow": 2.0, "artifact": 2.0, "goal": 1.5, "type": 2.0
        })
        self.assertEqual(mode.type_boost, {
            "rule": 1.5, "spec": 1.5, "pattern": 1.5, "decision": 1.3
        })
        self.assertEqual(mode.status_bias, {"done": 1.3, "fixed": 1.1})
        self.assertEqual(mode.freshness_multiplier, 1.0)
        self.assertEqual(mode.hop_penalty, 0.3)
        self.assertEqual(mode.hop_cap, 1)
        self.assertEqual(mode.allowed_relations, frozenset({"same_pattern_as", "derived_from"}))
        self.assertFalse(mode.include_archived)

    def test_architecture_preset_values(self):
        mode = PRESETS["architecture"]
        self.assertEqual(mode.name, "architecture")
        self.assertEqual(mode.marker_weights, {
            "domain": 2.5, "scope": 2.5, "project": 1.5, "type": 2.0
        })
        self.assertEqual(mode.type_boost, {
            "architecture": 1.8, "decision": 1.5, "rule": 1.3
        })
        self.assertEqual(mode.status_bias, {})
        self.assertEqual(mode.freshness_multiplier, 0.3)
        self.assertEqual(mode.hop_penalty, 0.6)
        self.assertEqual(mode.hop_cap, 3)
        self.assertEqual(mode.allowed_relations, frozenset({"derived_from", "related_pattern"}))
        self.assertFalse(mode.include_archived)

    def test_product_preset_values(self):
        mode = PRESETS["product"]
        self.assertEqual(mode.name, "product")
        self.assertEqual(mode.marker_weights, {
            "goal": 2.5, "project": 2.0, "room": 2.0, "type": 2.0
        })
        self.assertEqual(mode.type_boost, {
            "spec": 1.5, "research": 1.5, "decision": 1.3
        })
        self.assertEqual(mode.status_bias, {
            "new": 1.3, "in-progress": 1.2, "known-risk": 1.2
        })
        self.assertEqual(mode.freshness_multiplier, 1.2)
        self.assertEqual(mode.hop_penalty, 0.5)
        self.assertEqual(mode.hop_cap, 2)
        self.assertEqual(mode.allowed_relations, frozenset({"related_pattern", "derived_from"}))
        self.assertFalse(mode.include_archived)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: `ImportError` for `PRESETS`.

- [ ] **Step 3: Implement the four presets**

Append to `scripts/intent_modes.py`:

```python
PRESETS: dict[str, IntentMode] = {
    "debug": IntentMode(
        name="debug",
        marker_weights={"severity": 2.5, "artifact": 2.5, "flow": 1.5, "type": 2.0},
        type_boost={"bug": 1.5, "incident": 1.5, "debug": 1.5},
        status_bias={
            "in-progress": 1.5, "known-risk": 1.3, "new": 1.2,
            "fixed": 0.6, "done": 0.6,
        },
        freshness_multiplier=1.5,
        hop_penalty=0.7,
        hop_cap=2,
        allowed_relations=frozenset({"might_affect", "same_pattern_as"}),
        include_archived=False,
    ),
    "implementation": IntentMode(
        name="implementation",
        marker_weights={"flow": 2.0, "artifact": 2.0, "goal": 1.5, "type": 2.0},
        type_boost={"rule": 1.5, "spec": 1.5, "pattern": 1.5, "decision": 1.3},
        status_bias={"done": 1.3, "fixed": 1.1},
        freshness_multiplier=1.0,
        hop_penalty=0.3,
        hop_cap=1,
        allowed_relations=frozenset({"same_pattern_as", "derived_from"}),
        include_archived=False,
    ),
    "architecture": IntentMode(
        name="architecture",
        marker_weights={"domain": 2.5, "scope": 2.5, "project": 1.5, "type": 2.0},
        type_boost={"architecture": 1.8, "decision": 1.5, "rule": 1.3},
        status_bias={},
        freshness_multiplier=0.3,
        hop_penalty=0.6,
        hop_cap=3,
        allowed_relations=frozenset({"derived_from", "related_pattern"}),
        include_archived=False,
    ),
    "product": IntentMode(
        name="product",
        marker_weights={"goal": 2.5, "project": 2.0, "room": 2.0, "type": 2.0},
        type_boost={"spec": 1.5, "research": 1.5, "decision": 1.3},
        status_bias={"new": 1.3, "in-progress": 1.2, "known-risk": 1.2},
        freshness_multiplier=1.2,
        hop_penalty=0.5,
        hop_cap=2,
        allowed_relations=frozenset({"related_pattern", "derived_from"}),
        include_archived=False,
    ),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/intent_modes.py tests/test_intent_modes.py
git commit -m "Add four intent-mode presets (debug, implementation, architecture, product)"
```

---

### Task 3: `resolve_intent` with partial-override merging

**Files:**
- Modify: `scripts/intent_modes.py` (append `resolve_intent`, `_merge_override`)
- Test: `tests/test_intent_modes.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_intent_modes.py
from intent_modes import resolve_intent  # noqa: E402


class ResolveIntentTests(unittest.TestCase):
    def test_none_and_none_returns_none(self):
        self.assertIsNone(resolve_intent(None, None))

    def test_preset_name_returns_preset(self):
        mode = resolve_intent("debug", None)
        self.assertIsNotNone(mode)
        self.assertEqual(mode.name, "debug")
        self.assertEqual(mode.hop_cap, 2)

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_intent("unknown", None)
        message = str(ctx.exception)
        self.assertIn("unknown", message)
        for name in ("debug", "implementation", "architecture", "product"):
            self.assertIn(name, message)

    def test_override_without_mode_uses_neutral_base(self):
        mode = resolve_intent(None, {"hopCap": 3})
        self.assertIsNotNone(mode)
        self.assertEqual(mode.hop_cap, 3)
        self.assertEqual(mode.marker_weights, {})
        self.assertEqual(mode.freshness_multiplier, 1.0)

    def test_partial_override_on_preset(self):
        mode = resolve_intent("debug", {"hopCap": 5})
        self.assertEqual(mode.hop_cap, 5)
        # Untouched fields keep preset values:
        self.assertEqual(mode.marker_weights["severity"], 2.5)
        self.assertEqual(mode.allowed_relations, frozenset({"might_affect", "same_pattern_as"}))

    def test_override_full_dict_replaces_preset_dict(self):
        # dict fields replace, not recursively merge
        mode = resolve_intent("debug", {"typeBoost": {"pattern": 3.0}})
        self.assertEqual(mode.type_boost, {"pattern": 3.0})

    def test_override_allowedRelations_list(self):
        mode = resolve_intent("debug", {"allowedRelations": ["derived_from"]})
        self.assertEqual(mode.allowed_relations, frozenset({"derived_from"}))

    def test_override_allowedRelations_null_means_all_allowed(self):
        mode = resolve_intent("debug", {"allowedRelations": None})
        self.assertIsNone(mode.allowed_relations)

    def test_override_ignores_unknown_keys(self):
        # Lenient: unknown fields do not raise. Default per spec §9.
        mode = resolve_intent("debug", {"mysteryKey": 123, "hopCap": 4})
        self.assertEqual(mode.hop_cap, 4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: `ImportError` for `resolve_intent`.

- [ ] **Step 3: Implement `resolve_intent` and `_merge_override`**

Append to `scripts/intent_modes.py`:

```python
# Mapping from camelCase override field names (JSON payload) to
# snake_case IntentMode attributes.
_OVERRIDE_FIELD_MAP = {
    "markerWeights": "marker_weights",
    "typeBoost": "type_boost",
    "statusBias": "status_bias",
    "freshnessMultiplier": "freshness_multiplier",
    "hopPenalty": "hop_penalty",
    "hopCap": "hop_cap",
    "allowedRelations": "allowed_relations",
    "includeArchived": "include_archived",
}


def _merge_override(base: IntentMode, override: dict) -> IntentMode:
    """Return a new IntentMode with fields replaced by override values.

    Only keys in ``_OVERRIDE_FIELD_MAP`` are consulted; unknown keys are
    silently ignored (lenient per spec §9 Q2). Dict/list fields in the
    override fully replace the corresponding preset field — they do NOT
    recursively merge.
    """
    updates: dict[str, object] = {}
    for src_key, dst_attr in _OVERRIDE_FIELD_MAP.items():
        if src_key not in override:
            continue
        value = override[src_key]
        if dst_attr == "allowed_relations":
            updates[dst_attr] = frozenset(value) if value is not None else None
        else:
            updates[dst_attr] = value
    if not updates:
        return base
    from dataclasses import replace
    return replace(base, **updates)


def resolve_intent(
    mode_name: str | None,
    override: dict | None,
) -> IntentMode | None:
    """Resolve an intent mode + optional partial override to an IntentMode.

    Returns ``None`` when both inputs are ``None`` so the non-intent path
    remains a strict no-op. An override alone (no mode) yields a mode
    derived from the neutral baseline — the power-user / eval-harness
    path. An unknown mode name raises ValueError with the list of known
    preset names.
    """
    if mode_name is None and override is None:
        return None
    if mode_name is not None:
        base = PRESETS.get(mode_name)
        if base is None:
            known = sorted(PRESETS.keys())
            raise ValueError(
                f"Unknown intentMode: {mode_name!r}. Expected one of {known}."
            )
    else:
        base = NEUTRAL_INTENT
    if override:
        return _merge_override(base, override)
    return base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: all tests pass (6 + 9 = 15 in this file so far).

- [ ] **Step 5: Commit**

```bash
git add scripts/intent_modes.py tests/test_intent_modes.py
git commit -m "Add resolve_intent with partial override merging"
```

---

### Task 4: Marker weight and type boost helpers

**Files:**
- Modify: `scripts/intent_modes.py` (append two helpers)
- Test: `tests/test_intent_modes.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_intent_modes.py
from intent_modes import apply_marker_weight, apply_type_boost  # noqa: E402


class MarkerWeightHelperTests(unittest.TestCase):
    def test_none_intent_returns_one(self):
        self.assertEqual(apply_marker_weight("severity", None), 1.0)

    def test_unknown_axis_returns_one(self):
        self.assertEqual(apply_marker_weight("mystery_axis", PRESETS["debug"]), 1.0)

    def test_mapped_axis_returns_weight(self):
        self.assertEqual(apply_marker_weight("severity", PRESETS["debug"]), 2.5)
        self.assertEqual(apply_marker_weight("domain", PRESETS["architecture"]), 2.5)


class TypeBoostHelperTests(unittest.TestCase):
    def test_none_intent_returns_one(self):
        self.assertEqual(apply_type_boost("bug", None), 1.0)

    def test_unmapped_type_returns_one(self):
        self.assertEqual(apply_type_boost("task", PRESETS["debug"]), 1.0)

    def test_mapped_type_returns_boost(self):
        self.assertEqual(apply_type_boost("bug", PRESETS["debug"]), 1.5)
        self.assertEqual(apply_type_boost("architecture", PRESETS["architecture"]), 1.8)

    def test_empty_type_returns_one(self):
        self.assertEqual(apply_type_boost("", PRESETS["debug"]), 1.0)

    def test_none_type_returns_one(self):
        self.assertEqual(apply_type_boost(None, PRESETS["debug"]), 1.0)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: `ImportError` for the two helpers.

- [ ] **Step 3: Implement the helpers**

Append to `scripts/intent_modes.py`:

```python
def apply_marker_weight(axis: str, intent: IntentMode | None) -> float:
    """Per-axis marker weight multiplier. 1.0 when intent is None or the
    axis is not configured for this intent."""
    if intent is None:
        return 1.0
    return float(intent.marker_weights.get(axis, 1.0))


def apply_type_boost(record_type: str | None, intent: IntentMode | None) -> float:
    """Multiplier keyed by ``markers.type``. 1.0 when intent is None, the
    type is missing/empty, or the type is not in this intent's boost
    map."""
    if intent is None or not record_type:
        return 1.0
    return float(intent.type_boost.get(record_type, 1.0))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/intent_modes.py tests/test_intent_modes.py
git commit -m "Add apply_marker_weight and apply_type_boost helpers"
```

---

### Task 5: Status bias and freshness multiplier helpers

**Files:**
- Modify: `scripts/intent_modes.py` (append two helpers)
- Test: `tests/test_intent_modes.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_intent_modes.py
from intent_modes import apply_status_bias, apply_freshness_multiplier  # noqa: E402


class StatusBiasHelperTests(unittest.TestCase):
    def test_none_intent_returns_one(self):
        self.assertEqual(apply_status_bias("in-progress", None), 1.0)

    def test_unmapped_status_returns_one(self):
        self.assertEqual(apply_status_bias("in-progress", PRESETS["architecture"]), 1.0)

    def test_mapped_status_returns_bias(self):
        self.assertEqual(apply_status_bias("in-progress", PRESETS["debug"]), 1.5)
        self.assertEqual(apply_status_bias("fixed", PRESETS["debug"]), 0.6)
        self.assertEqual(apply_status_bias("done", PRESETS["implementation"]), 1.3)

    def test_empty_status_returns_one(self):
        self.assertEqual(apply_status_bias("", PRESETS["debug"]), 1.0)


class FreshnessMultiplierHelperTests(unittest.TestCase):
    def test_none_intent_returns_decay_unchanged(self):
        self.assertEqual(apply_freshness_multiplier(0.5, None), 0.5)

    def test_applies_mode_multiplier(self):
        self.assertAlmostEqual(apply_freshness_multiplier(0.5, PRESETS["debug"]), 0.75)
        self.assertAlmostEqual(apply_freshness_multiplier(1.0, PRESETS["architecture"]), 0.3)
        self.assertAlmostEqual(apply_freshness_multiplier(0.8, PRESETS["product"]), 0.96)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: `ImportError` for the two helpers.

- [ ] **Step 3: Implement the helpers**

Append to `scripts/intent_modes.py`:

```python
def apply_status_bias(status: str | None, intent: IntentMode | None) -> float:
    """Multiplier keyed by ``markers.status``. 1.0 when intent is None,
    status is missing/empty, or status is not in this intent's bias
    map."""
    if intent is None or not status:
        return 1.0
    return float(intent.status_bias.get(status, 1.0))


def apply_freshness_multiplier(decay: float, intent: IntentMode | None) -> float:
    """Scale an existing freshness decay factor by ``intent.freshness_multiplier``.

    ``apply_freshness_multiplier(decay, None)`` returns ``decay`` unchanged.
    """
    if intent is None:
        return float(decay)
    return float(decay) * float(intent.freshness_multiplier)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/intent_modes.py tests/test_intent_modes.py
git commit -m "Add apply_status_bias and apply_freshness_multiplier helpers"
```

---

### Task 6: Traversal helpers — relation filter, hop penalty, hop cap

**Files:**
- Modify: `scripts/intent_modes.py` (append three helpers)
- Test: `tests/test_intent_modes.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_intent_modes.py
from intent_modes import is_relation_allowed, hop_penalty_for, hop_cap_for  # noqa: E402


class RelationFilterTests(unittest.TestCase):
    def test_none_intent_allows_all(self):
        self.assertTrue(is_relation_allowed("anything", None))

    def test_mode_without_restriction_allows_all(self):
        # NEUTRAL_INTENT and override-only modes have allowed_relations=None
        from intent_modes import NEUTRAL_INTENT
        self.assertTrue(is_relation_allowed("anything", NEUTRAL_INTENT))

    def test_mode_with_restriction(self):
        debug = PRESETS["debug"]
        self.assertTrue(is_relation_allowed("might_affect", debug))
        self.assertTrue(is_relation_allowed("same_pattern_as", debug))
        self.assertFalse(is_relation_allowed("derived_from", debug))
        self.assertFalse(is_relation_allowed("related_pattern", debug))


class HopPenaltyHelperTests(unittest.TestCase):
    def test_none_intent_returns_none(self):
        self.assertIsNone(hop_penalty_for(None))

    def test_mode_without_penalty_returns_none(self):
        from intent_modes import NEUTRAL_INTENT
        self.assertIsNone(hop_penalty_for(NEUTRAL_INTENT))

    def test_mode_with_penalty(self):
        self.assertEqual(hop_penalty_for(PRESETS["debug"]), 0.7)
        self.assertEqual(hop_penalty_for(PRESETS["implementation"]), 0.3)


class HopCapHelperTests(unittest.TestCase):
    def test_none_intent_returns_default(self):
        self.assertEqual(hop_cap_for(None, default=1), 1)
        self.assertEqual(hop_cap_for(None, default=3), 3)

    def test_mode_returns_mode_cap(self):
        self.assertEqual(hop_cap_for(PRESETS["debug"], default=1), 2)
        self.assertEqual(hop_cap_for(PRESETS["architecture"], default=1), 3)
        self.assertEqual(hop_cap_for(PRESETS["implementation"], default=1), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: `ImportError` for the three helpers.

- [ ] **Step 3: Implement the helpers**

Append to `scripts/intent_modes.py`:

```python
def is_relation_allowed(rel_type: str, intent: IntentMode | None) -> bool:
    """True when ``rel_type`` may be traversed under this intent.

    ``intent`` is None, or ``intent.allowed_relations`` is None → all
    relation types are allowed (backward-compatible default).
    """
    if intent is None or intent.allowed_relations is None:
        return True
    return rel_type in intent.allowed_relations


def hop_penalty_for(intent: IntentMode | None) -> float | None:
    """Per-mode hop penalty, or ``None`` to let the caller fall back to
    the module-level ``HOP_PENALTY`` constant."""
    if intent is None:
        return None
    return intent.hop_penalty


def hop_cap_for(intent: IntentMode | None, default: int) -> int:
    """Hop cap from ``intent`` or ``default`` when intent is None."""
    if intent is None:
        return default
    return int(intent.hop_cap)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_modes -v`
Expected: all tests pass. `scripts/intent_modes.py` is now complete.

- [ ] **Step 5: Run the full suite to verify no regression**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: 269 + ~25 new tests pass (baseline depends on tranche). Actual new count ~25 across Tasks 1–6.

- [ ] **Step 6: Commit**

```bash
git add scripts/intent_modes.py tests/test_intent_modes.py
git commit -m "Add traversal helpers: is_relation_allowed, hop_penalty_for, hop_cap_for"
```

---

## Milestone 2 — Integrate intent into `_score_record_detailed`

Goal: plumb the `intent` parameter into the scoring helper and apply all four multipliers, while preserving backward-compatible scores when `intent is None`.

### Task 7: Thread `intent` through `_score_record_detailed` and `record_weight` (no-op first)

**Files:**
- Modify: `scripts/context_graph_core.py` (signature only)
- Test: `tests/test_core.py` (extend)

- [ ] **Step 1: Add a failing backward-compat test**

Append to `tests/test_core.py` (or wherever scoring tests live):

```python
class IntentScoringBackwardCompatTests(unittest.TestCase):
    """Ensure passing intent=None yields byte-identical detail to not
    passing intent at all."""

    def test_score_record_detailed_intent_none_matches_absent(self):
        import sys
        from pathlib import Path
        ROOT = Path(__file__).resolve().parents[1]
        SCRIPTS = ROOT / "scripts"
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        from context_graph_core import _score_record_detailed  # noqa: E402

        record = {
            "id": "r1",
            "markers": {"type": "bug", "severity": "high", "status": "in-progress"},
            "tokens": ["webhook", "retry"],
            "updatedAt": "2025-01-01T00:00:00Z",
        }
        qm = {"type": "bug"}
        qt = {"webhook"}

        a = _score_record_detailed(record, qm, qt, None)
        b = _score_record_detailed(record, qm, qt, None, intent=None)
        self.assertEqual(a, b)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_core.IntentScoringBackwardCompatTests -v`
Expected: TypeError — `_score_record_detailed` does not accept `intent`.

- [ ] **Step 3: Add `intent=None` to `_score_record_detailed` and `record_weight` signatures (no logic change yet)**

In `scripts/context_graph_core.py`, locate `_score_record_detailed` (around line 1095) and `record_weight` (around line 1184).

Add to the import block near the top of the file (with the other local imports):

```python
from intent_modes import (
    IntentMode,
    apply_marker_weight,
    apply_type_boost,
    apply_status_bias,
    apply_freshness_multiplier,
    hop_cap_for,
    hop_penalty_for,
    is_relation_allowed,
    resolve_intent,
)
```

Extend the `_score_record_detailed` signature and forward-compat the `record_weight` wrapper:

```python
def _score_record_detailed(
    record: dict[str, Any],
    query_markers: dict[str, str],
    query_tokens: set[str],
    importance: dict[str, float] | None = None,
    intent: IntentMode | None = None,
) -> dict[str, Any]:
    ...  # body unchanged for this task; intent is received but not yet used


def record_weight(
    record: dict[str, Any],
    query_markers: dict[str, str],
    query_tokens: set[str],
    importance: dict[str, float] | None = None,
    intent: IntentMode | None = None,
) -> tuple[float, list[str]]:
    detail = _score_record_detailed(record, query_markers, query_tokens, importance, intent)
    return detail["score"], detail["matchedMarkers"]
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_core.IntentScoringBackwardCompatTests -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite to verify no regression**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all tests pass — no logic change yet.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_core.py
git commit -m "Add intent parameter to _score_record_detailed and record_weight (no-op)"
```

---

### Task 8: Apply marker weight and type boost inside `_score_record_detailed`

**Files:**
- Modify: `scripts/context_graph_core.py` (body of `_score_record_detailed`)
- Test: `tests/test_intent_modes.py` (extend) or new `tests/test_intent_integration.py`

- [ ] **Step 1: Add failing tests for marker-weight and type-boost integration**

Create `tests/test_intent_integration.py`:

```python
# tests/test_intent_integration.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import _score_record_detailed  # noqa: E402
from intent_modes import PRESETS, resolve_intent  # noqa: E402


class ScoreMarkerWeightIntegrationTests(unittest.TestCase):
    def _record(self, **overrides):
        record = {
            "id": "r1",
            "markers": {"type": "bug", "severity": "high", "status": "in-progress"},
            "tokens": ["webhook"],
            "updatedAt": "2025-01-01T00:00:00Z",
        }
        record.update(overrides)
        return record

    def test_intent_marker_factor_recorded(self):
        record = self._record()
        detail = _score_record_detailed(
            record, {"severity": "high"}, {"webhook"}, None, intent=PRESETS["debug"]
        )
        # Under debug, severity has weight 2.5. There should be an intent
        # multiplier section that reflects this.
        self.assertIn("intentMarkerMultiplier", detail["factors"])
        self.assertEqual(detail["factors"]["intentMarkerMultiplier"]["severity"], 2.5)

    def test_intent_type_boost_recorded(self):
        record = self._record()
        detail = _score_record_detailed(
            record, {"type": "bug"}, {"webhook"}, None, intent=PRESETS["debug"]
        )
        self.assertIn("intentTypeBoost", detail["factors"])
        self.assertEqual(detail["factors"]["intentTypeBoost"]["value"], 1.5)

    def test_intent_boosts_final_score(self):
        record = self._record()
        low = _score_record_detailed(record, {"type": "bug"}, {"webhook"}, None, intent=None)
        high = _score_record_detailed(record, {"type": "bug"}, {"webhook"}, None, intent=PRESETS["debug"])
        self.assertGreater(high["score"], low["score"])

    def test_intent_none_still_neutral(self):
        record = self._record()
        neutral = _score_record_detailed(record, {"type": "bug"}, {"webhook"}, None, intent=None)
        self.assertEqual(neutral["factors"].get("intentMarkerMultiplier"), None)
        self.assertEqual(neutral["factors"].get("intentTypeBoost"), None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: tests fail — `intent*` factors missing / score not boosted.

- [ ] **Step 3: Integrate marker weight and type boost in `_score_record_detailed`**

Modify the body of `_score_record_detailed` in `scripts/context_graph_core.py` (around line 1095). Replace the scoring block that computes `exactness`, `severity_weight`, and the `total` so that intent multipliers are applied AFTER the base computation but BEFORE the final round; also add the `intent*` factor entries.

Full revised body (replace from `matched_markers = ...` through the `return` block):

```python
    markers = record.get("markers", {})
    matched_markers = [key for key, value in query_markers.items() if markers.get(key) == value]

    # Per-axis marker weight under intent is applied before the weighted
    # aggregate so the existing exactness pipeline stays one step.
    def _per_axis_intent_factor(axis: str) -> float:
        return apply_marker_weight(axis, intent)

    per_axis_intent: dict[str, float] = {a: _per_axis_intent_factor(a) for a in matched_markers}
    exactness = _weighted_marker_score(matched_markers, query_markers, importance or {})
    # Fold the per-axis intent multipliers into exactness: take the
    # average of per-axis factors as the exactness multiplier so a match
    # on a heavily-weighted axis boosts exactness proportionally.
    if per_axis_intent:
        exactness *= sum(per_axis_intent.values()) / len(per_axis_intent)

    record_tokens = set(record.get("tokens", []))
    matched_tokens = sorted(query_tokens & record_tokens)
    token_overlap = len(matched_tokens) / max(len(query_tokens), 1)

    severity_value = markers.get("severity")
    severity_weight = {
        "critical": 1.0,
        "high": 0.7,
        "medium": 0.4,
        "low": 0.2,
    }.get(severity_value, 0.0)
    status_value = markers.get("status")
    status_weight = {
        "in-progress": 1.0,
        "known-risk": 0.85,
        "new": 0.6,
        "fixed": 0.45,
        "done": 0.35,
        "archived": 0.1,
    }.get(status_value, 0.25)
    freshness = recency_score(record.get("updatedAt") or record.get("classifiedAt"))

    base_total = (
        exactness * 0.45
        + token_overlap * 0.2
        + severity_weight * 0.15
        + status_weight * 0.1
        + freshness * 0.1
    )

    # Intent post-multipliers, applied to the base_total in order:
    # markerWeights already folded into exactness above.
    type_boost_factor = apply_type_boost(markers.get("type"), intent)
    status_bias_factor = apply_status_bias(status_value, intent)
    freshness_mult_factor = apply_freshness_multiplier(1.0, intent)

    total = base_total * type_boost_factor * status_bias_factor * freshness_mult_factor
    score = round(total, 3)

    factors: dict[str, Any] = {
        "markerMatch": {
            "matched": matched_markers,
            "weight": 0.45,
            "value": exactness,
            "contribution": round(exactness * 0.45, 6),
        },
        "tokenMatch": {
            "matched": matched_tokens,
            "queryTokenCount": len(query_tokens),
            "recordTokenCount": len(record_tokens),
            "weight": 0.2,
            "value": token_overlap,
            "contribution": round(token_overlap * 0.2, 6),
        },
        "severity": {
            "value": severity_value,
            "weight": 0.15,
            "factor": severity_weight,
            "contribution": round(severity_weight * 0.15, 6),
        },
        "status": {
            "value": status_value,
            "weight": 0.1,
            "factor": status_weight,
            "contribution": round(status_weight * 0.1, 6),
        },
        "freshness": {
            "weight": 0.1,
            "factor": freshness,
            "contribution": round(freshness * 0.1, 6),
            "updatedAt": record.get("updatedAt") or record.get("classifiedAt"),
        },
    }
    if intent is not None:
        factors["intentMarkerMultiplier"] = per_axis_intent
        factors["intentTypeBoost"] = {"type": markers.get("type"), "value": type_boost_factor}
        factors["intentStatusBias"] = {"status": status_value, "value": status_bias_factor}
        factors["intentFreshnessMultiplier"] = {"value": freshness_mult_factor}

    return {
        "score": score,
        "matchedMarkers": matched_markers,
        "matchedTokens": matched_tokens,
        "factors": factors,
    }
```

- [ ] **Step 4: Run tests to verify integration tests pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Run the full suite to verify no regression**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all prior tests pass — `intent=None` path is neutral.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_intent_integration.py
git commit -m "Apply marker weight, type boost, status bias, freshness multiplier under intent"
```

---

## Milestone 3 — Plug intent into `build_context_pack` and traversal

### Task 9: Parse `intentMode` / `intentOverride` in `build_context_pack` and pass to scoring + acceptance test

**Files:**
- Modify: `scripts/context_graph_core.py::build_context_pack` (parse + pass)
- Test: `tests/test_intent_integration.py` (extend)

- [ ] **Step 1: Add failing acceptance test**

Append to `tests/test_intent_integration.py`:

```python
class BuildContextPackAcceptanceTests(unittest.TestCase):
    def test_same_query_different_modes_differ(self):
        import json
        import tempfile
        from context_graph_core import build_context_pack  # noqa: E402

        records = {
            "r-bug": {
                "id": "r-bug", "title": "Payment webhook crash",
                "content": "Stack trace on webhook retry.",
                "markers": {"type": "bug", "severity": "high", "status": "in-progress",
                            "domain": "payments", "flow": "webhook", "artifact": "webhook"},
                "tokens": ["payment", "webhook", "crash", "retry"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            "r-arch": {
                "id": "r-arch", "title": "Payment architecture decision",
                "content": "Idempotency key strategy.",
                "markers": {"type": "architecture", "status": "done",
                            "domain": "payments", "scope": "platform"},
                "tokens": ["payment", "idempotency", "architecture"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2025-01-01T00:00:00Z",
            },
        }
        graph = {"records": records, "edges": [], "schema": {"learned": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            graph_path.write_text(json.dumps(graph))
            pack_debug = build_context_pack({
                "graphPath": str(graph_path),
                "query": "payments",
                "k": 2,
                "intentMode": "debug",
            })
            pack_arch = build_context_pack({
                "graphPath": str(graph_path),
                "query": "payments",
                "k": 2,
                "intentMode": "architecture",
            })
            # Under debug, r-bug should rank first.
            self.assertEqual(pack_debug["topResults"][0]["id"], "r-bug")
            # Under architecture, r-arch should rank first.
            self.assertEqual(pack_arch["topResults"][0]["id"], "r-arch")

    def test_unknown_mode_raises(self):
        import tempfile, json
        from context_graph_core import build_context_pack  # noqa: E402
        with tempfile.TemporaryDirectory() as tmp:
            gp = Path(tmp) / "graph.json"
            gp.write_text(json.dumps({"records": {}, "edges": []}))
            with self.assertRaises(ValueError):
                build_context_pack({"graphPath": str(gp), "query": "x", "intentMode": "nope"})
```

Note: adjust the top-level key (`topResults` vs `top` etc.) to match the real return shape by reading `build_context_pack` first if necessary. If the real key differs, update the assertion and note the discrepancy in a comment.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: failures — intent not yet parsed in `build_context_pack`.

- [ ] **Step 3: Parse intent in `build_context_pack` and pass to scoring**

In `scripts/context_graph_core.py`, locate `build_context_pack` (around line 1194). Near the top of the function body, after reading `query` but before the `_score_record` closure is defined, add:

```python
    intent_mode = payload.get("intentMode")
    intent_override = payload.get("intentOverride")
    intent = resolve_intent(intent_mode, intent_override)
    # include_archived falls back to the intent's preference when the
    # payload does not override it explicitly.
    if intent is not None and intent.include_archived is not None and "includeArchived" not in payload:
        include_archived = bool(intent.include_archived)
```

Then update the `_score_record` closure to forward `intent`:

```python
    def _score_record(record: dict[str, Any]) -> tuple[float, list[str]]:
        raw, matched = record_weight(record, query_markers, query_tokens, importance, intent)
        factor = type_freshness_factor(record, half_life_override)
        return round(raw * factor, 3), matched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: the acceptance test and the unknown-mode test pass.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all prior tests pass; eval harness should still be green (`python3 scripts/context_graph_cli.py eval`).

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_intent_integration.py
git commit -m "Parse intentMode/intentOverride in build_context_pack and pass through to scoring"
```

---

### Task 10: Apply `hopCap`, `hopPenalty`, `allowedRelations` in `build_context_pack` traversal

**Files:**
- Modify: `scripts/context_graph_core.py::build_context_pack` (traversal loop)
- Test: `tests/test_intent_integration.py` (extend)

- [ ] **Step 1: Add failing traversal tests**

Append to `tests/test_intent_integration.py`:

```python
class TraversalIntentRoutingTests(unittest.TestCase):
    def _graph_with_relations(self, tmpdir: Path) -> Path:
        import json
        # r-seed direct-matches the query; r-affect is reached via
        # might_affect; r-derived via derived_from. Under debug only
        # r-affect should appear as a neighbor; under architecture only
        # r-derived (and traversal continues further).
        records = {
            "r-seed": {
                "id": "r-seed", "title": "Webhook retry loop", "content": "Retry loop.",
                "markers": {"type": "bug", "domain": "payments"},
                "tokens": ["webhook", "retry"],
                "relations": {
                    "explicit": [
                        {"type": "might_affect", "target": "r-affect"},
                        {"type": "derived_from", "target": "r-derived"},
                    ],
                    "inferred": [],
                },
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            "r-affect": {
                "id": "r-affect", "title": "Downstream charge timing",
                "content": "Charge event.", "markers": {"type": "incident", "domain": "payments"},
                "tokens": ["charge"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            "r-derived": {
                "id": "r-derived", "title": "Idempotency architecture",
                "content": "Decision on idempotency.",
                "markers": {"type": "architecture", "domain": "payments", "scope": "platform"},
                "tokens": ["idempotency"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2025-01-01T00:00:00Z",
            },
        }
        graph = {"records": records, "edges": [], "schema": {"learned": {}}}
        gp = tmpdir / "graph.json"
        gp.write_text(json.dumps(graph))
        return gp

    def test_debug_follows_might_affect_not_derived_from(self):
        import tempfile
        from context_graph_core import build_context_pack
        with tempfile.TemporaryDirectory() as tmp:
            gp = self._graph_with_relations(Path(tmp))
            pack = build_context_pack({
                "graphPath": str(gp),
                "query": "webhook retry",
                "k": 5,
                "intentMode": "debug",
            })
            ids = {item["id"] for item in pack["topResults"]}
            self.assertIn("r-seed", ids)
            self.assertIn("r-affect", ids)
            self.assertNotIn("r-derived", ids)

    def test_architecture_follows_derived_from_not_might_affect(self):
        import tempfile
        from context_graph_core import build_context_pack
        with tempfile.TemporaryDirectory() as tmp:
            gp = self._graph_with_relations(Path(tmp))
            pack = build_context_pack({
                "graphPath": str(gp),
                "query": "payments",
                "k": 5,
                "intentMode": "architecture",
            })
            ids = {item["id"] for item in pack["topResults"]}
            self.assertIn("r-derived", ids)
            self.assertNotIn("r-affect", ids)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: both new tests fail — traversal ignores `allowedRelations`.

- [ ] **Step 3: Apply intent-driven hop cap, hop penalty, and relation filter in the traversal loop**

In `scripts/context_graph_core.py::build_context_pack`, locate the traversal block (roughly line 1298–1354). Replace:

```python
    effective_penalty = float(hop_penalty) if hop_penalty is not None else HOP_PENALTY
    ...
    while frontier and max_hops > 0:
```

with an intent-aware version:

```python
    # Intent can override both the hop cap and the per-hop penalty. When
    # the payload also passes an explicit max_hops / hop_penalty, the
    # payload wins (explicit request > implicit preset).
    if "maxHops" not in payload and intent is not None:
        max_hops = hop_cap_for(intent, default=max_hops)
    if "hopPenalty" not in payload and intent is not None:
        override_penalty = hop_penalty_for(intent)
        if override_penalty is not None:
            hop_penalty = override_penalty
    effective_penalty = float(hop_penalty) if hop_penalty is not None else HOP_PENALTY
```

Then, inside the traversal loop, add a relation filter. Locate the `for rel in normalize_explicit_relations(seed_record):` line and insert right after it:

```python
                rel_type = str(rel.get("type") or "")
                if intent is not None and not is_relation_allowed(rel_type, intent):
                    continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: all integration tests pass.

- [ ] **Step 5: Run the full suite + eval**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Run: `python3 scripts/context_graph_cli.py eval`
Expected: all tests pass; eval does NOT regress against current baseline (intent mode is opt-in).

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_intent_integration.py
git commit -m "Route build_context_pack traversal through intent hop cap, penalty, and relation filter"
```

---

## Milestone 4 — `search_graph` and `inspect_record`

### Task 11: Parse intent in `search_graph` and pass to scoring

**Files:**
- Modify: `scripts/context_graph_core.py::search_graph`
- Test: `tests/test_intent_integration.py` (extend)

- [ ] **Step 1: Add failing test**

Append to `tests/test_intent_integration.py`:

```python
class SearchGraphIntentTests(unittest.TestCase):
    def test_search_graph_differs_under_modes(self):
        import json, tempfile
        from context_graph_core import search_graph

        records = {
            "r-bug": {
                "id": "r-bug", "title": "Webhook crash",
                "markers": {"type": "bug", "severity": "high", "status": "in-progress"},
                "tokens": ["webhook"], "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            "r-arch": {
                "id": "r-arch", "title": "Webhook architecture",
                "markers": {"type": "architecture", "domain": "payments", "scope": "platform"},
                "tokens": ["webhook"], "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2025-01-01T00:00:00Z",
            },
        }
        graph = {"records": records, "edges": [], "schema": {"learned": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            gp = Path(tmp) / "graph.json"
            gp.write_text(json.dumps(graph))
            res_debug = search_graph({"graphPath": str(gp), "query": "webhook", "intentMode": "debug"})
            res_arch = search_graph({"graphPath": str(gp), "query": "webhook", "intentMode": "architecture"})
            self.assertEqual(res_debug["results"][0]["id"], "r-bug")
            self.assertEqual(res_arch["results"][0]["id"], "r-arch")
```

Adjust `results` key if the real `search_graph` uses a different name.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration.SearchGraphIntentTests -v`
Expected: fail — `search_graph` does not read intent.

- [ ] **Step 3: Parse intent in `search_graph`**

In `scripts/context_graph_core.py::search_graph` (around line 2375), add near the top of the function body (after the graph/schema load):

```python
    intent = resolve_intent(payload.get("intentMode"), payload.get("intentOverride"))
```

Then wherever the function calls `record_weight` or `_score_record_detailed`, forward `intent`:

```python
    score, matched = record_weight(record, query_markers, query_tokens, importance, intent)
```

(Search within the function for existing `record_weight` / `_score_record_detailed` calls and update each site.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration.SearchGraphIntentTests -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_intent_integration.py
git commit -m "Route search_graph scoring through intent"
```

---

### Task 12: Accept intent in `inspect_record`, surface factors, add CLI flags

**Files:**
- Modify: `scripts/context_graph_core.py` (`inspect_record`, `format_inspect_record`)
- Modify: `scripts/context_graph_cli.py` (`_run_inspect_record` gains `--mode` / `--override`)
- Test: `tests/test_intent_integration.py` (extend)

- [ ] **Step 1: Add failing test**

Append to `tests/test_intent_integration.py`:

```python
class InspectRecordIntentTests(unittest.TestCase):
    def test_inspect_record_under_mode_returns_intent_factors(self):
        import json, tempfile
        from context_graph_core import inspect_record

        record = {
            "id": "r1", "title": "Webhook crash",
            "markers": {"type": "bug", "severity": "high", "status": "in-progress"},
            "tokens": ["webhook"], "relations": {"explicit": [], "inferred": []},
            "updatedAt": "2026-04-01T00:00:00Z",
        }
        graph = {"records": {"r1": record}, "edges": [], "schema": {"learned": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            gp = Path(tmp) / "graph.json"
            gp.write_text(json.dumps(graph))
            result = inspect_record({
                "graphPath": str(gp),
                "recordId": "r1",
                "query": "webhook",
                "intentMode": "debug",
            })
            factors = result.get("factors") or result.get("score", {}).get("factors")
            # Adjust the lookup if the real inspect_record return shape differs.
            self.assertIn("intentMarkerMultiplier", factors)
            self.assertIn("intentTypeBoost", factors)
            self.assertIn("intentStatusBias", factors)
            self.assertIn("intentFreshnessMultiplier", factors)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration.InspectRecordIntentTests -v`
Expected: fail — factors missing.

- [ ] **Step 3: Thread intent through `inspect_record` and extend `format_inspect_record`**

In `scripts/context_graph_core.py::inspect_record` (around line 3111), parse intent from the payload and pass it to `_score_record_detailed`:

```python
    intent = resolve_intent(payload.get("intentMode"), payload.get("intentOverride"))
    ...  # existing code; when it calls _score_record_detailed, add intent=intent
```

In `scripts/context_graph_core.py::format_inspect_record` (around line 3207), when the factors block is printed, add conditional lines for the four intent fields (show them only when present — keeps non-intent output unchanged):

```python
    if "intentMarkerMultiplier" in factors:
        lines.append("  intentMarkerMultiplier:")
        for axis, val in sorted(factors["intentMarkerMultiplier"].items()):
            lines.append(f"    {axis}: {val}")
    if "intentTypeBoost" in factors:
        tb = factors["intentTypeBoost"]
        lines.append(f"  intentTypeBoost: {tb.get('type')} -> {tb.get('value')}")
    if "intentStatusBias" in factors:
        sb = factors["intentStatusBias"]
        lines.append(f"  intentStatusBias: {sb.get('status')} -> {sb.get('value')}")
    if "intentFreshnessMultiplier" in factors:
        fm = factors["intentFreshnessMultiplier"]
        lines.append(f"  intentFreshnessMultiplier: {fm.get('value')}")
```

Place these additions in the existing factor-printing block of `format_inspect_record`.

Now in `scripts/context_graph_cli.py::_run_inspect_record`, add two flags and thread them into the payload:

```python
    sub_parser.add_argument("--mode", dest="mode", default=None,
                            help="Intent preset name: debug, implementation, architecture, product")
    sub_parser.add_argument("--override", dest="override_path", default=None,
                            help="Path to JSON file with an intentOverride object")
    ...
    if sub_args.mode:
        payload["intentMode"] = sub_args.mode
    if sub_args.override_path:
        import json as _json
        payload["intentOverride"] = _json.loads(Path(sub_args.override_path).read_text())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_intent_integration -v`
Expected: all intent integration tests pass.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py scripts/context_graph_cli.py tests/test_intent_integration.py
git commit -m "Surface intent factors in inspect_record and add --mode/--override CLI flags"
```

---

## Milestone 5 — MCP schemas

### Task 13: Extend MCP schemas for `build_context_pack`, `search_graph`, `inspect_record`

**Files:**
- Modify: `scripts/context_graph_mcp.py` (three ToolSpec input_schemas)
- Test: `tests/test_mcp_observability.py` (extend) or new `tests/test_mcp_intent.py`

- [ ] **Step 1: Add failing test**

Create `tests/test_mcp_intent.py`:

```python
# tests/test_mcp_intent.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import context_graph_mcp  # noqa: E402


class MCPIntentSchemaTests(unittest.TestCase):
    def _tool(self, name: str):
        for t in context_graph_mcp.TOOLS:
            if t.name == name:
                return t
        self.fail(f"Tool {name} not registered")

    def test_build_context_pack_advertises_intentMode(self):
        tool = self._tool("build_context_pack")
        props = tool.input_schema.get("properties", {})
        self.assertIn("intentMode", props)
        enum = props["intentMode"].get("enum")
        for name in ("debug", "implementation", "architecture", "product"):
            self.assertIn(name, enum)
        self.assertIn("intentOverride", props)

    def test_search_graph_advertises_intentMode(self):
        tool = self._tool("search_graph")
        props = tool.input_schema.get("properties", {})
        self.assertIn("intentMode", props)
        self.assertIn("intentOverride", props)

    def test_inspect_record_advertises_intentMode(self):
        tool = self._tool("inspect_record")
        props = tool.input_schema.get("properties", {})
        self.assertIn("intentMode", props)
        self.assertIn("intentOverride", props)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_mcp_intent -v`
Expected: fail — schemas lack `intentMode`/`intentOverride`.

- [ ] **Step 3: Extend MCP input_schemas**

In `scripts/context_graph_mcp.py`, add to the `properties` section of each of the three ToolSpec entries (`build_context_pack`, `search_graph`, `inspect_record`):

```python
            "intentMode": {
                "type": ["string", "null"],
                "enum": ["debug", "implementation", "architecture", "product", None],
                "description": "Query intent preset. Optional.",
            },
            "intentOverride": {
                "type": ["object", "null"],
                "properties": {
                    "markerWeights": {"type": "object"},
                    "typeBoost": {"type": "object"},
                    "statusBias": {"type": "object"},
                    "freshnessMultiplier": {"type": "number"},
                    "hopPenalty": {"type": ["number", "null"]},
                    "hopCap": {"type": "integer"},
                    "allowedRelations": {"type": ["array", "null"], "items": {"type": "string"}},
                    "includeArchived": {"type": ["boolean", "null"]},
                },
                "additionalProperties": False,
            },
```

Preserve `additionalProperties: False` on the tool if it was already set — do not accidentally loosen the schema.

- [ ] **Step 4: Run the test**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_mcp_intent -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_mcp.py tests/test_mcp_intent.py
git commit -m "Advertise intentMode and intentOverride on MCP schemas"
```

---

## Milestone 6 — Slash command

### Task 14: `/cg-search` parses `--mode`

**Files:**
- Modify: `commands/cg-search.md`

- [ ] **Step 1: Read the existing command doc to preserve structure**

Read `commands/cg-search.md` to confirm the current argument-parsing prose and result format.

- [ ] **Step 2: Update the frontmatter and the argument parsing section**

Replace the `description:` and `argument-hint:` fields in the frontmatter:

```yaml
description: Search the Context Graph for records relevant to a query, optionally under an intent preset.
argument-hint: [--mode <name>] <query>  (intent presets: debug, implementation, architecture, product)
```

In the body, add a new section before the main search steps:

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git add commands/cg-search.md
git commit -m "Add --mode intent preset flag to /cg-search"
```

---

## Milestone 7 — Eval harness integration + baseline

### Task 15: Eval harness passes `intentMode` from each query

**Files:**
- Modify: `scripts/eval_harness.py::run_harness` (or equivalent)
- Test: `tests/test_eval_harness.py` (extend)

- [ ] **Step 1: Add failing test**

Append to `tests/test_eval_harness.py`:

```python
class EvalHarnessIntentPassThroughTests(unittest.TestCase):
    def test_run_harness_passes_intent_per_query(self):
        import json, tempfile
        from eval_harness import run_harness, EvalQuery
        # Seed a minimal graph where only the debug preset reaches the
        # expected record (e.g. by type boost on "bug").
        records = {
            "r-bug": {
                "id": "r-bug", "title": "Payment bug",
                "markers": {"type": "bug", "severity": "high", "status": "in-progress"},
                "tokens": ["payment"], "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            "r-arch": {
                "id": "r-arch", "title": "Payment architecture",
                "markers": {"type": "architecture", "domain": "payments", "scope": "platform"},
                "tokens": ["payment"], "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2025-01-01T00:00:00Z",
            },
        }
        graph = {"records": records, "edges": [], "schema": {"learned": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            gp = Path(tmp) / "graph.json"
            gp.write_text(json.dumps(graph))
            queries = [
                EvalQuery(id="q1", query="payment", intent="debug",
                          expectedDirectMatches=["r-bug"], expectedSupporting=[], k=2),
            ]
            results = run_harness(queries, gp, k=2)
            # Under debug intent, r-bug must be in the top-k for a
            # precision>0 outcome. The pre-change harness (intent
            # ignored) also produces this outcome for this tiny
            # fixture, so the real assertion is that the passed intent
            # surfaces in either logs, return metadata, or observable
            # ordering. Assert observable effect: debug boosts r-bug
            # strictly above r-arch.
            # Access pack ids from the first result's pack contents if
            # exposed; otherwise assert precision > 0.
            self.assertGreater(results[0].precisionAtK, 0.0)
```

Adjust the exact assertion to match the real `EvalQuery` / `EvalResult` shape defined in `scripts/eval_harness.py`.

- [ ] **Step 2: Run test to verify failure if applicable**

If the test passes even without the change (because the fixture is small), tighten it: add a second record that would outrank the expected match when intent is ignored, so only the intent-aware path surfaces the correct record.

- [ ] **Step 3: Modify `run_harness` to forward `intent`**

Open `scripts/eval_harness.py`. In `run_harness`, locate the `build_context_pack(...)` call. Add the intent field:

```python
    pack = build_context_pack({
        "graphPath": str(graph_path),
        "query": query.query,
        "k": k,
        "intentMode": query.intent,  # forward the declared intent
    })
```

If `query.intent` is currently a str (required) no further changes needed. If it can be None/missing, guard with `"intentMode": query.intent if query.intent else None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_eval_harness -v`
Expected: all eval-harness tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_harness.py tests/test_eval_harness.py
git commit -m "Pass declared query intent into build_context_pack in run_harness"
```

---

### Task 16: Run eval, inspect per-query results, and decide on baseline update

This task is procedural: run the harness, inspect the diff vs current baseline, and make one of two decisions. No TDD step.

- [ ] **Step 1: Run the harness without the baseline gate (or with a high tolerance) to see current numbers**

```bash
python3 scripts/context_graph_cli.py eval --tolerance 1.0 2>&1 | tee /tmp/eval-with-intent.log
```

The `--tolerance 1.0` lets the command exit 0 even when the new scoring has regressions, so we can read the per-query report.

- [ ] **Step 2: Inspect the per-query report for any precision drop**

Open `/tmp/eval-with-intent.log`. For each query in `data/eval/queries.json`, compare its precision@k before (baseline.json) and after. Any drop is a signal that the preset's weights are wrong for that intent.

- [ ] **Step 3: Decide**

- **All per-query precision ≥ baseline (or equal):** proceed to Step 4 (baseline rewrite).
- **Any regression:** STOP and report to the human partner:
  - Which query regressed
  - Under which `intent`
  - The likely preset knob that caused it
  Do NOT rewrite the baseline in this case. Tuning is a design decision.

- [ ] **Step 4: If no regressions, rewrite the baseline**

```bash
python3 scripts/context_graph_cli.py eval --save-baseline --tolerance 0
```

This writes a new `data/eval/baseline.json` with the new numbers.

Verify the rewrite:

```bash
python3 scripts/context_graph_cli.py eval
# Expected: exit 0, "Baseline check: no regression".
```

- [ ] **Step 5: Commit the baseline rewrite**

```bash
git add data/eval/baseline.json
git commit -m "$(cat <<'EOF'
Rewrite eval baseline after wiring declared intent into the harness

Before intent routing: mean precision@k 0.683 / mean recall@k 1.000
After intent routing:  (fill from eval output)
All per-query precision@k stayed ≥ previous baseline.
EOF
)"
```

Replace "(fill from eval output)" with the actual numbers from Step 1's log before committing.

---

## Milestone 8 — Docs + CHANGELOG

### Task 17: Update `docs/retrieval.md`, `docs/observability.md`, and `CHANGELOG.md`

**Files:**
- Modify: `docs/retrieval.md` (add "Intent modes" section)
- Modify: `docs/observability.md` (add `--mode` example)
- Modify: `CHANGELOG.md` (extend Unreleased)

- [ ] **Step 1: Append the Intent Modes section to `docs/retrieval.md`**

Append to `docs/retrieval.md`:

````markdown
## Intent modes

`build_context_pack` and `search_graph` accept an optional `intentMode`
that tunes scoring and traversal for a specific kind of query.

| Mode | Emphasis | Hop cap | Follows |
|---|---|---|---|
| `debug` | severity, artifact; current-state records | 2 | `might_affect`, `same_pattern_as` |
| `implementation` | flow, artifact; done/fixed records | 1 | `same_pattern_as`, `derived_from` |
| `architecture` | domain, scope; old decisions still rank | 3 | `derived_from`, `related_pattern` |
| `product` | goal, project, room; forward-looking statuses | 2 | `related_pattern`, `derived_from` |

Full per-mode constants live in `scripts/intent_modes.py::PRESETS`.

### Override

Pass `intentOverride` to tune any preset field at query time:

```json
{
  "query": "payment retry",
  "intentMode": "debug",
  "intentOverride": { "hopCap": 3 }
}
```

Without `intentMode`, `intentOverride` is applied to a neutral baseline
(all weights 1.0, hopCap 1, all relations allowed).

### How to extend

The four presets cover the common cases. To experiment with a new
shape without editing source, pass `intentOverride` with the fields you
want to try and run the eval harness. If a new preset proves itself
across queries, add it to `PRESETS` in a follow-up PR — the docs and
the MCP/enum schemas must be updated together.
````

- [ ] **Step 2: Append an inspect-record example to `docs/observability.md`**

Append:

````markdown
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
````

- [ ] **Step 3: Update `CHANGELOG.md`**

In the `## [Unreleased]` section, add:

```markdown
### Added

- Query intent modes: `debug`, `implementation`, `architecture`, `product`
  — explicit `intentMode` payload field on `build_context_pack`,
  `search_graph`, and `inspect_record`; optional `intentOverride`
  escape hatch for tuning at query time; slash command
  `/cg-search --mode <name>`. Eval harness now routes each query
  through its declared intent; baseline updated accordingly.
  See `docs/retrieval.md` and `docs/superpowers/specs/2026-04-24-intent-modes-design.md`.
```

- [ ] **Step 4: Commit**

```bash
git add docs/retrieval.md docs/observability.md CHANGELOG.md
git commit -m "Document intent modes: retrieval.md, observability.md, CHANGELOG"
```

---

## Milestone 9 — Final verification

### Task 18: Full suite + eval + MCP sanity + roadmap update

**Files:**
- Modify: `docs/roadmap.md` (check off the final Phase 5 item)

- [ ] **Step 1: Run the full test suite**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: previous count + ~30 new tests. No failures.
```

- [ ] **Step 2: Run the eval harness**

```bash
python3 scripts/context_graph_cli.py eval
# Expected: exit 0, "no regression".
```

- [ ] **Step 3: MCP sanity**

```bash
python3 -c "import sys; sys.path.insert(0,'scripts'); import context_graph_mcp as m; [print(t.name, list(t.input_schema.get('properties',{}).keys())) for t in m.TOOLS if t.name in ('build_context_pack','search_graph','inspect_record')]"
# Expected: each tool's properties list includes 'intentMode' and 'intentOverride'.
```

- [ ] **Step 4: Update the roadmap**

In `docs/roadmap.md`, under the Phase 5 section, check off the intent-modes item:

```markdown
- [x] Add query intent modes: `debug`, `implementation`, `architecture`, `product` — presets in `scripts/intent_modes.py`, `intentMode`/`intentOverride` on `build_context_pack` / `search_graph` / `inspect_record`, eval harness routes by declared intent
```

If helpful, drop the now-obsolete acceptance line noting "Query intent modes still open — requires design discussion" (added in an earlier roadmap update).

- [ ] **Step 5: Commit the roadmap update**

```bash
git add docs/roadmap.md
git commit -m "Mark Phase 5 query intent modes complete in roadmap"
```

- [ ] **Step 6: Final smoke**

```bash
# Quick end-to-end sanity:
tmp=$(mktemp -d); cd "$tmp"
python3 /Users/maksnalyvaiko/context-graph/scripts/context_graph_cli.py init-workspace <<< '{}'
# Seed a minimal record set and exercise build_context_pack with each mode:
for mode in debug implementation architecture product; do
  echo "=== $mode ==="
  echo '{"query":"payment","intentMode":"'$mode'"}' | \
    python3 /Users/maksnalyvaiko/context-graph/scripts/context_graph_cli.py build-context-pack
done
```

Expected: each invocation returns a valid pack (likely empty since no records are seeded, but the command exits 0 under each mode).

---

## Self-review checklist

**Spec coverage:**

- [x] Model B+C (weight tuning + relation filter + hop cap) — Task 8 applies weights, Task 10 applies hop cap + relation filter
- [x] Invocation: explicit only — Tasks 9, 11, 12, 14
- [x] Presets + partial override — Tasks 2 (presets), 3 (`resolve_intent` + `_merge_override`)
- [x] Eight knobs per mode — Tasks 4, 5, 6 (pure helpers); Task 2 seeds them; Task 8 applies five of them; Task 10 applies hop cap + hop penalty + allowedRelations + includeArchived
- [x] `inspect_record` surfaces intent factors — Task 12
- [x] MCP schemas — Task 13
- [x] Slash command `/cg-search --mode` — Task 14
- [x] Eval harness routes declared intent — Task 15; baseline rewrite — Task 16
- [x] Docs + CHANGELOG — Task 17
- [x] Roadmap check-off — Task 18
- [x] Backward compatibility (`intent=None` → no-op) — Task 7 adds the param no-op first; Task 8's neutral-path tests assert score-identical behavior

**Placeholder scan:**

- All code blocks contain actual code, not "fill in".
- All test blocks contain actual assertions.
- Task 16 (baseline rewrite) is procedural, not TDD — it's a manual decision step and is labeled as such.
- Test assertions reference return-shape keys (`topResults`, `results`, `factors`) that the executor MUST confirm against the actual `build_context_pack` / `search_graph` / `inspect_record` / `EvalResult` shapes before running; noted inline.

**Type consistency:**

- `IntentMode` fields (snake_case): `marker_weights`, `type_boost`, `status_bias`, `freshness_multiplier`, `hop_penalty`, `hop_cap`, `allowed_relations`, `include_archived` — consistent across Tasks 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12.
- Payload fields (camelCase): `intentMode`, `intentOverride` with sub-fields `markerWeights`, `typeBoost`, `statusBias`, `freshnessMultiplier`, `hopPenalty`, `hopCap`, `allowedRelations`, `includeArchived` — consistent in payload-parsing tasks (9, 11, 12, 15) and MCP schema (13).
- `_OVERRIDE_FIELD_MAP` in Task 3 explicitly bridges camelCase → snake_case — single source of truth.

**Scope:**

- This plan produces working software on its own: the PR can land and ship intent modes without depending on any other parallel work.
- No out-of-scope items (query auto-detection, workspace defaults, user-defined modes) leak into any task.

---

## Open questions deferred to implementation

1. Exact return-shape key of `build_context_pack` (`topResults` vs `top` etc.) — read the real signature in Task 9 Step 1 and adjust assertions. Same for `search_graph` (`results` key) and `inspect_record` (`factors` path). Cost is a 5-second read at the start of each relevant task.
2. `EvalQuery` / `EvalResult` shape — Task 15's test uses illustrative field names; read `scripts/eval_harness.py` and match them.
3. Per-query regressions in Task 16 — if any appear, pause and bring the per-mode constants to a tuning conversation before rewriting the baseline.
