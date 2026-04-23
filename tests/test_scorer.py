from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_scorer import (  # noqa: E402
    HIGH_CONFIDENCE,
    MIN_GAP,
    MIN_SCORE,
    REGION_WEIGHTS,
    arbitrate,
    score_field,
)


class ScoreFieldTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "markers": {"domain": ["payments", "trading", "challenge"]},
            "aliases": {"domain": {"payments": ["payment", "billing"]}},
        }

    def test_exact_match_in_title_scores_high(self):
        regions = {
            "frontmatter": "",
            "metadataBlock": "",
            "titleText": "Payments Hub",
            "breadcrumb": "",
            "body": "",
        }
        scores = score_field("domain", regions, self.schema, idf={})
        top = scores[0]
        self.assertEqual(top["value"], "payments")
        self.assertGreater(top["score"], 0.0)

    def test_alias_match_counts_as_canonical(self):
        regions = {
            "frontmatter": "",
            "metadataBlock": "",
            "titleText": "Billing update",
            "breadcrumb": "",
            "body": "",
        }
        scores = score_field("domain", regions, self.schema, idf={})
        self.assertEqual(scores[0]["value"], "payments")

    def test_idf_downweights_frequent_tokens(self):
        regions = {
            "frontmatter": "",
            "metadataBlock": "",
            "titleText": "",
            "breadcrumb": "",
            "body": "trading trading trading payments",
        }
        uniform = score_field("domain", regions, self.schema, idf={})
        self.assertEqual(uniform[0]["value"], "trading")

        idf_weighted = score_field(
            "domain",
            regions,
            self.schema,
            idf={"trading": 10, "payments": 10},
        )
        self.assertEqual(idf_weighted[0]["value"], "payments")


class ArbitrateTests(unittest.TestCase):
    def test_deterministic_when_top_clear(self):
        scores = [{"value": "a", "score": 0.9}, {"value": "b", "score": 0.4}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "deterministic")
        self.assertEqual(decision["value"], "a")

    def test_pending_when_top_below_high(self):
        scores = [{"value": "a", "score": 0.5}, {"value": "b", "score": 0.35}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "pending-arbitration")
        self.assertEqual(decision["value"], "a")

    def test_pending_when_gap_too_small(self):
        scores = [{"value": "a", "score": 0.8}, {"value": "b", "score": 0.75}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "pending-arbitration")

    def test_fallback_when_all_below_min(self):
        scores = [{"value": "a", "score": 0.05}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "fallback")
        self.assertIsNone(decision["value"])


class RegionWeightsTests(unittest.TestCase):
    def test_weights_are_frozen(self):
        self.assertEqual(REGION_WEIGHTS["frontmatter"], 5.0)
        self.assertEqual(REGION_WEIGHTS["metadataBlock"], 4.0)
        self.assertEqual(REGION_WEIGHTS["titleText"], 3.0)
        self.assertEqual(REGION_WEIGHTS["breadcrumb"], 2.0)
        self.assertEqual(REGION_WEIGHTS["body"], 1.0)

    def test_thresholds_are_frozen(self):
        self.assertEqual(HIGH_CONFIDENCE, 0.75)
        self.assertEqual(MIN_GAP, 0.15)
        self.assertEqual(MIN_SCORE, 0.20)


if __name__ == "__main__":
    unittest.main()
