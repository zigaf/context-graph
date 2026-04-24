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
