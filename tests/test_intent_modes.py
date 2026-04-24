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


if __name__ == "__main__":
    unittest.main()
