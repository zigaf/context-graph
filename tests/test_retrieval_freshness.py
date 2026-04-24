"""Tests for freshness decay by record type in build_context_pack.

Phase 5 item: "rules and decisions decay slower than tasks and incidents".
Implementation multiplies a type-specific half-life decay factor into each
candidate's score. A fresh record beats an old one of the same type, and a
year-old rule still beats a year-old task because the rule's half-life is much
longer.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    FRESHNESS_HALF_LIFE_DAYS,
    build_context_pack,
    type_freshness_factor,
)


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _record(rid: str, record_type: str, days_ago: float, title: str | None = None) -> dict:
    return {
        "id": rid,
        "title": title or f"{record_type} record {rid}",
        "content": "payment webhook flow",
        "markers": {
            "type": record_type,
            "domain": "payments",
            "flow": "webhook",
            "goal": "prevent-regression",
            "status": "done",
        },
        "revision": {"version": 1, "updatedAt": _iso(days_ago)},
    }


class FreshnessHalfLifeTests(unittest.TestCase):
    def test_default_half_life_map_has_expected_defaults(self) -> None:
        # Sanity: the defaults shape matches the roadmap spec.
        self.assertGreaterEqual(FRESHNESS_HALF_LIFE_DAYS.get("rule", 0), 365)
        self.assertGreaterEqual(FRESHNESS_HALF_LIFE_DAYS.get("decision", 0), 180)
        self.assertLessEqual(FRESHNESS_HALF_LIFE_DAYS.get("task", 9999), 60)
        self.assertLessEqual(FRESHNESS_HALF_LIFE_DAYS.get("incident", 9999), 60)
        self.assertLessEqual(FRESHNESS_HALF_LIFE_DAYS.get("bug", 9999), 60)
        self.assertIn("default", FRESHNESS_HALF_LIFE_DAYS)

    def test_factor_one_for_missing_timestamp(self) -> None:
        # Records without a timestamp should not be penalized. 1.0 = no change.
        record = {"id": "x", "markers": {"type": "rule"}}
        self.assertAlmostEqual(type_freshness_factor(record), 1.0, places=5)

    def test_factor_decays_slower_for_rules_than_tasks(self) -> None:
        one_year = _record("a", "rule", days_ago=365)
        one_year_task = _record("b", "task", days_ago=365)
        rule_factor = type_freshness_factor(one_year)
        task_factor = type_freshness_factor(one_year_task)
        self.assertGreater(rule_factor, task_factor)

    def test_fresh_task_beats_old_task(self) -> None:
        records = [_record("fresh", "task", days_ago=1), _record("old", "task", days_ago=180)]
        pack = build_context_pack({"query": "payment webhook flow", "records": records, "limit": 5})
        ids = [item["id"] for item in pack["directMatches"]]
        self.assertEqual(ids[0], "fresh")
        self.assertEqual(ids[1], "old")

    def test_year_old_rule_beats_year_old_task(self) -> None:
        # Both one year old, identical markers. Rule half-life (365) vs task
        # half-life (30) should make the rule dominate.
        records = [_record("r", "rule", days_ago=365), _record("t", "task", days_ago=365)]
        pack = build_context_pack({"query": "payment webhook flow", "records": records, "limit": 5})
        ids = [item["id"] for item in pack["directMatches"]]
        self.assertEqual(ids[0], "r")

    def test_payload_override_flips_default_behavior(self) -> None:
        # Explicitly set task half-life to 10_000 so a year-old task does not
        # decay; the old task should then beat the older rule because the raw
        # scores are tied and the task now has the higher freshness factor.
        # We also downgrade the rule half-life so the override is observable.
        records = [_record("r", "rule", days_ago=365), _record("t", "task", days_ago=365)]
        pack = build_context_pack(
            {
                "query": "payment webhook flow",
                "records": records,
                "limit": 5,
                "freshnessHalfLifeDays": {"rule": 10, "task": 10000},
            }
        )
        ids = [item["id"] for item in pack["directMatches"]]
        self.assertEqual(ids[0], "t")

    def test_override_null_uses_defaults(self) -> None:
        records = [_record("r", "rule", days_ago=365), _record("t", "task", days_ago=365)]
        pack = build_context_pack(
            {
                "query": "payment webhook flow",
                "records": records,
                "limit": 5,
                "freshnessHalfLifeDays": None,
            }
        )
        ids = [item["id"] for item in pack["directMatches"]]
        # Defaults restore the rule-dominates-task ordering.
        self.assertEqual(ids[0], "r")


if __name__ == "__main__":
    unittest.main()
