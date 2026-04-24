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
