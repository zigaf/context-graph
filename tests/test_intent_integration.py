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


class TraversalIntentRoutingTests(unittest.TestCase):
    def _records(self):
        # r-seed direct-matches the query; r-affect is reached via
        # might_affect; r-derived via derived_from. Under debug only
        # r-affect should appear as a neighbor; under architecture only
        # r-derived (and traversal continues further).
        return [
            {
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
            {
                "id": "r-affect", "title": "Downstream charge timing",
                "content": "Charge event.", "markers": {"type": "incident", "domain": "payments"},
                "tokens": ["charge"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2026-04-01T00:00:00Z",
            },
            {
                "id": "r-derived", "title": "Idempotency architecture",
                "content": "Decision on idempotency.",
                "markers": {"type": "architecture", "domain": "payments", "scope": "platform"},
                "tokens": ["idempotency"],
                "relations": {"explicit": [], "inferred": []},
                "updatedAt": "2025-01-01T00:00:00Z",
            },
        ]

    def test_debug_follows_might_affect_not_derived_from(self):
        from context_graph_core import build_context_pack
        pack = build_context_pack({
            "query": "webhook retry",
            "records": self._records(),
            "limit": 5,
            "intentMode": "debug",
        })
        ids = {item["id"] for item in pack["directMatches"]}
        self.assertIn("r-seed", ids)
        self.assertIn("r-affect", ids)
        self.assertNotIn("r-derived", ids)

    def test_architecture_follows_derived_from_not_might_affect(self):
        from context_graph_core import build_context_pack
        # Query matches r-seed directly (webhook/retry tokens). r-affect
        # and r-derived are reachable only via traversal so the
        # allowedRelations filter is what decides which one appears in
        # the pack. (Plan originally used "payments" which direct-hit
        # all three records via domain=payments, masking the filter.)
        pack = build_context_pack({
            "query": "webhook retry",
            "records": self._records(),
            "limit": 5,
            "intentMode": "architecture",
        })
        ids = {item["id"] for item in pack["directMatches"]}
        self.assertIn("r-derived", ids)
        self.assertNotIn("r-affect", ids)


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
                # Plan used 2025-01-01 — same note as
                # BuildContextPackAcceptanceTests above: type_freshness
                # age decay would overwhelm the intent sorting signal.
                # Use the same recent timestamp as r-bug so the test
                # exercises only intent-mode discrimination.
                "updatedAt": "2026-04-01T00:00:00Z",
            },
        }
        graph = {"records": records, "edges": [], "schema": {"learned": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            gp = Path(tmp) / "graph.json"
            gp.write_text(json.dumps(graph))
            res_debug = search_graph({"graphPath": str(gp), "query": "webhook", "intentMode": "debug"})
            res_arch = search_graph({"graphPath": str(gp), "query": "webhook", "intentMode": "architecture"})
            # real key in search_graph return is "directMatches" (it
            # delegates to build_context_pack). Plan's "results" would
            # have KeyError'd.
            self.assertEqual(res_debug["directMatches"][0]["id"], "r-bug")
            self.assertEqual(res_arch["directMatches"][0]["id"], "r-arch")


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
            # real inspect_record return has factors at the top level
            # ("factors"), not nested under "score". Plan's fallback
            # (result.get("score", {}).get("factors")) is unused here.
            factors = result.get("factors") or result.get("score", {}).get("factors")
            self.assertIn("intentMarkerMultiplier", factors)
            self.assertIn("intentTypeBoost", factors)
            self.assertIn("intentStatusBias", factors)
            self.assertIn("intentFreshnessMultiplier", factors)
