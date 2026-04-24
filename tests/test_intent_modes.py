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


if __name__ == "__main__":
    unittest.main()
