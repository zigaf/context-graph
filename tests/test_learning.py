from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_learning import (  # noqa: E402
    compute_marker_importance,
    mine_code_paths,
    mine_hierarchy,
    mine_ngrams,
    run_full_pass,
)
from context_graph_core import index_records, init_workspace  # noqa: E402


class HierarchyMiningTests(unittest.TestCase):
    def _rec(self, rid: str, parent: str) -> dict:
        return {"id": rid, "source": {"metadata": {"parent": parent}}}

    def test_extracts_repeated_ancestors(self):
        records = [
            self._rec("1", "kenmore > Tasks"),
            self._rec("2", "kenmore > Tasks"),
            self._rec("3", "kenmore > Architecture"),
            self._rec("4", "kenmore > Architecture > bl-api"),
            self._rec("5", "kenmore > Architecture > bl-api"),
        ]
        proposals = mine_hierarchy(records)
        values = [proposal["value"] for proposal in proposals]
        self.assertNotIn("kenmore", values)
        self.assertIn("tasks", values)
        self.assertIn("architecture", values)
        self.assertIn("bl-api", values)

    def test_drops_ancestors_below_support_threshold(self):
        records = [
            self._rec("1", "alpha > beta"),
            self._rec("2", "gamma > delta"),
        ]
        proposals = mine_hierarchy(records)
        self.assertEqual(proposals, [])

    def test_confidence_decreases_with_depth(self):
        records = [
            self._rec("1", "kenmore > Shared > Architecture > Deep > Deeper"),
            self._rec("2", "kenmore > Shared > Architecture > Deep"),
            self._rec("3", "kenmore > Shared > Architecture > Other"),
        ]
        proposals = mine_hierarchy(records)
        architecture = next(proposal for proposal in proposals if proposal["value"] == "architecture")
        deep = next((proposal for proposal in proposals if proposal["value"] == "deep"), None)
        if deep is not None:
            self.assertGreaterEqual(architecture["confidence"], deep["confidence"])


class NgramMiningTests(unittest.TestCase):
    def test_finds_strong_collocations(self):
        records = [
            {"id": "1", "title": "challenge payment flow", "content": "challenge payment ninjacharge"},
            {"id": "2", "title": "challenge payment retry", "content": "challenge payment again"},
            {"id": "3", "title": "challenge payment status", "content": "challenge payment status"},
            {"id": "4", "title": "unrelated", "content": "lorem ipsum"},
        ]
        proposals = mine_ngrams(records)
        values = [proposal["value"] for proposal in proposals]
        self.assertIn("challenge-payment", values)

    def test_skips_universal_bigrams(self):
        records = [
            {"id": "1", "title": "", "content": "and the boss said"},
            {"id": "2", "title": "", "content": "and the problem was"},
            {"id": "3", "title": "", "content": "and the fix is"},
        ]
        proposals = mine_ngrams(records)
        self.assertNotIn("and-the", [proposal["value"] for proposal in proposals])


class CodePathMiningTests(unittest.TestCase):
    def test_extracts_path_components(self):
        records = [
            {
                "id": "1",
                "title": "",
                "content": "look at bl-api/modules/trader/challenge/index.js",
            },
            {
                "id": "2",
                "title": "",
                "content": "fix bl-api/modules/trader/challenge/retry.js",
            },
        ]
        proposals = mine_code_paths(records)
        values = [proposal["value"] for proposal in proposals]
        self.assertIn("challenge", values)
        self.assertIn("trader", values)

    def test_ignores_common_prefixes(self):
        records = [
            {"id": "1", "title": "", "content": "look at src/foo.js"},
            {"id": "2", "title": "", "content": "look at src/bar.js"},
        ]
        proposals = mine_code_paths(records)
        values = [proposal["value"] for proposal in proposals]
        self.assertNotIn("src", values)


class MarkerImportanceTests(unittest.TestCase):
    def _rec(self, rid: str, markers: dict, regions_used: list[str] | None = None) -> dict:
        return {
            "id": rid,
            "markers": markers,
            "source": {
                "metadata": {
                    "classifierNotes": {"regionsUsed": regions_used or ["body"]}
                }
            },
        }

    def test_field_populated_everywhere_has_higher_presence(self):
        records = [
            self._rec("1", {"type": "task", "domain": "payments"}),
            self._rec("2", {"type": "bug", "domain": "payments"}),
            self._rec("3", {"type": "task"}),
        ]
        importance = compute_marker_importance(records)
        self.assertGreater(importance["type"], importance["domain"])

    def test_explicit_metadata_boosts_importance(self):
        records = [
            self._rec("1", {"status": "done"}, regions_used=["metadataBlock"]),
            self._rec("2", {"status": "new"}, regions_used=["frontmatter"]),
        ]
        importance = compute_marker_importance(records)
        self.assertGreater(importance.get("status", 0), 0.7)


class RunFullPassTests(unittest.TestCase):
    def test_returns_proposals_and_importance(self):
        records = [
            {
                "id": "1",
                "title": "challenge payment flow",
                "content": "see bl-api/modules/trader/challenge/index.js",
                "markers": {"type": "task"},
                "source": {"metadata": {"parent": "kenmore > Tasks"}},
            },
            {
                "id": "2",
                "title": "challenge payment retry",
                "content": "bl-api/modules/trader/challenge/retry.js",
                "markers": {"type": "bug"},
                "source": {"metadata": {"parent": "kenmore > Tasks"}},
            },
        ]
        result = run_full_pass(records)
        self.assertIn("proposals", result)
        self.assertIn("markerImportance", result)
        self.assertIn("type", result["markerImportance"])


class IndexRecordsSideEffectsTests(unittest.TestCase):
    def test_index_records_refreshes_idf_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            records = [
                {"id": "1", "title": "alpha", "content": "alpha beta"},
                {"id": "2", "title": "gamma", "content": "alpha gamma"},
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            idf_path = root / ".context-graph" / "idf_stats.json"
            self.assertTrue(idf_path.exists())
            stats = json.loads(idf_path.read_text(encoding="utf-8"))
            self.assertEqual(stats["corpusSize"], 2)
            self.assertEqual(stats["tokenDocumentFrequency"]["alpha"], 2)

    def test_index_records_triggers_light_learn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            records = [
                {
                    "id": f"{idx}",
                    "title": "challenge payment",
                    "content": "challenge payment flow",
                    "source": {"metadata": {"parent": "kenmore > Tasks"}},
                }
                for idx in range(3)
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            learned_path = root / ".context-graph" / "schema.learned.json"
            self.assertTrue(learned_path.exists())


if __name__ == "__main__":
    unittest.main()
