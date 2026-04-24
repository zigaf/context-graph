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


class BuildContextPackAcceptanceTests(unittest.TestCase):
    def test_same_query_different_modes_differ(self):
        # build_context_pack takes records inline via payload["records"],
        # not a graphPath. The plan's illustrative graphPath/topResults
        # wording is adjusted to the real API here.
        from context_graph_core import build_context_pack  # noqa: E402

        records = [
            {
                "id": "r-bug", "title": "Payment webhook crash",
                "content": "Stack trace on webhook retry.",
                "markers": {"type": "bug", "severity": "high", "status": "in-progress",
                            "domain": "payments", "flow": "webhook", "artifact": "webhook"},
                "tokens": ["payment", "webhook", "crash", "retry"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            {
                "id": "r-arch", "title": "Payment architecture decision",
                "content": "Idempotency key strategy.",
                "markers": {"type": "architecture", "status": "done",
                            "domain": "payments", "scope": "platform"},
                "tokens": ["payment", "idempotency", "architecture"],
                "relations": {"explicit": [], "inferred": []},
                # Use the same recent timestamp as r-bug so the
                # type_freshness_factor age decay is equal for both and
                # the test exercises only the intent-mode sorting
                # discrimination. (Plan used 2025-01-01 which combined
                # with architecture's half-life 180d made age decay
                # overwhelm the intent signal.)
                "updatedAt": "2026-04-01T00:00:00Z",
            },
        ]
        pack_debug = build_context_pack({
            "query": "payments",
            "records": records,
            "limit": 2,
            "intentMode": "debug",
        })
        pack_arch = build_context_pack({
            "query": "payments",
            "records": records,
            "limit": 2,
            "intentMode": "architecture",
        })
        # Under debug, r-bug should rank first.
        # real key in build_context_pack return is "directMatches"
        self.assertEqual(pack_debug["directMatches"][0]["id"], "r-bug")
        # Under architecture, r-arch should rank first.
        self.assertEqual(pack_arch["directMatches"][0]["id"], "r-arch")

    def test_unknown_mode_raises(self):
        from context_graph_core import build_context_pack  # noqa: E402
        with self.assertRaises(ValueError):
            build_context_pack({"query": "x", "records": [], "intentMode": "nope"})
