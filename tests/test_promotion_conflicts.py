"""Tests for conflict-aware promotion splitting.

Phase 5 item: when promote_pattern sees source records with opposing hints on
the same subject, split the cohort and emit multiple narrower promotions
instead of a single self-contradicting promotion.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    detect_content_conflicts,
    promote_pattern,
)


class ContentConflictDetectorTests(unittest.TestCase):
    def test_retry_vs_do_not_retry_flagged(self) -> None:
        records = [
            {
                "id": "a",
                "title": "Webhook retry",
                "content": "Always retry the webhook callback on 5xx errors.",
                "markers": {"domain": "payments", "flow": "webhook"},
            },
            {
                "id": "b",
                "title": "Webhook retry policy",
                "content": "Do not retry the webhook callback on 4xx errors.",
                "markers": {"domain": "payments", "flow": "webhook"},
            },
        ]
        conflicts = detect_content_conflicts(records)
        self.assertTrue(conflicts, "expected at least one content conflict")
        # The conflict should name the shared keyword that is negated in one
        # record and affirmed in the other.
        kinds = {entry.get("kind") for entry in conflicts}
        self.assertIn("content-negation", kinds)
        negated = next(entry for entry in conflicts if entry.get("kind") == "content-negation")
        self.assertIn("a", negated["recordIds"])
        self.assertIn("b", negated["recordIds"])
        self.assertIn("retry", negated["token"].lower())

    def test_no_conflict_when_stances_agree(self) -> None:
        records = [
            {"id": "a", "content": "Always retry webhooks.", "markers": {}},
            {"id": "b", "content": "Retry webhooks with backoff.", "markers": {}},
        ]
        conflicts = detect_content_conflicts(records)
        # No negation in either record so no negation conflict.
        self.assertFalse([c for c in conflicts if c.get("kind") == "content-negation"])


class PromoteConflictSplittingTests(unittest.TestCase):
    def _opposing_cohort(self) -> list[dict]:
        return [
            {
                "id": "retry-1",
                "title": "Webhook retry rule",
                "content": "Always retry the payment webhook on transient errors.",
                "markers": {
                    "type": "rule",
                    "domain": "payments",
                    "flow": "webhook",
                    "status": "done",
                    "goal": "prevent-regression",
                },
            },
            {
                "id": "retry-2",
                "title": "Retry policy for replay",
                "content": "Retry payment webhook callbacks with exponential backoff.",
                "markers": {
                    "type": "rule",
                    "domain": "payments",
                    "flow": "webhook",
                    "status": "done",
                    "goal": "prevent-regression",
                },
            },
            {
                "id": "noretry-1",
                "title": "Do not retry duplicate",
                "content": "Do not retry the payment webhook after a duplicate charge is detected.",
                "markers": {
                    "type": "rule",
                    "domain": "payments",
                    "flow": "webhook",
                    "status": "done",
                    "goal": "prevent-regression",
                },
            },
            {
                "id": "noretry-2",
                "title": "Stop retry on final failure",
                "content": "Do not retry the payment webhook once the provider returns a permanent error.",
                "markers": {
                    "type": "rule",
                    "domain": "payments",
                    "flow": "webhook",
                    "status": "done",
                    "goal": "prevent-regression",
                },
            },
        ]

    def test_returns_two_narrower_proposals_when_content_conflicts(self) -> None:
        result = promote_pattern({"records": self._opposing_cohort()})
        self.assertIn("promotedRecords", result)
        self.assertGreaterEqual(len(result["promotedRecords"]), 2)
        # Each narrower proposal should carry a scope marker in its title or
        # content that distinguishes it from the other.
        titles = [record["title"] for record in result["promotedRecords"]]
        self.assertEqual(len(titles), len(set(titles)), "proposals should have distinct titles")

        # Backward compatibility: legacy single-record field still present for
        # callers that ignore the plural field.
        self.assertIn("promotedRecord", result)
        self.assertIn(result["promotedRecord"]["id"], [r["id"] for r in result["promotedRecords"]])

    def test_conflicts_field_is_non_empty_on_opposing_cohort(self) -> None:
        result = promote_pattern({"records": self._opposing_cohort()})
        self.assertIn("conflicts", result)
        self.assertIsInstance(result["conflicts"], list)
        self.assertTrue(result["conflicts"], "conflicts should be non-empty on opposing cohort")
        # At least one content-negation conflict naming both groups.
        negation = [c for c in result["conflicts"] if c.get("kind") == "content-negation"]
        self.assertTrue(negation)
        entry = negation[0]
        self.assertIn("retry", entry["token"].lower())
        self.assertGreaterEqual(len(entry["recordIds"]), 2)

    def test_conflicts_field_is_empty_when_no_conflicts(self) -> None:
        cohort = [
            {
                "id": "a",
                "title": "Webhook retry rule one",
                "content": "Always retry the webhook on transient errors.",
                "markers": {
                    "type": "rule",
                    "domain": "payments",
                    "flow": "webhook",
                    "status": "done",
                    "goal": "prevent-regression",
                },
            },
            {
                "id": "b",
                "title": "Webhook retry rule two",
                "content": "Retry the webhook with jittered backoff.",
                "markers": {
                    "type": "rule",
                    "domain": "payments",
                    "flow": "webhook",
                    "status": "done",
                    "goal": "prevent-regression",
                },
            },
        ]
        result = promote_pattern({"records": cohort})
        self.assertEqual(result["conflicts"], [])
        self.assertIn("promotedRecords", result)
        self.assertEqual(len(result["promotedRecords"]), 1)


if __name__ == "__main__":
    unittest.main()
