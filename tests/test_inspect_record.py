"""Tests for ``inspect_record``.

Seeds a graph where a target record has a specific marker/token profile
against a known query, and asserts:
- the score returned by ``inspect_record`` equals the score ``search_graph``
  reports for the same record (the "no-drift" invariant)
- the per-factor breakdown includes matched markers and matched tokens
- refactoring the scoring helper has not changed the scoring math
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    _score_record_detailed,
    index_records,
    inspect_record,
    record_weight,
    search_graph,
    tokenize,
)


def _seed_graph(graph_path: Path) -> None:
    records = [
        {
            "id": "r-bug",
            "title": "Critical webhook bug in deposit flow",
            "content": "Duplicate payment creation after callback retry when idempotency is missing",
            "markers": {
                "type": "bug",
                "domain": "payments",
                "flow": "webhook",
                "goal": "stabilize-flow",
                "status": "in-progress",
                "severity": "critical",
            },
        },
        {
            "id": "r-rule",
            "title": "Retry-safe webhook design for deposit callbacks",
            "content": "Adds idempotency guard for payment callback duplicates",
            "markers": {
                "type": "rule",
                "domain": "payments",
                "flow": "webhook",
                "goal": "prevent-regression",
                "status": "done",
                "severity": "high",
            },
        },
        {
            "id": "r-other",
            "title": "Unrelated note",
            "content": "Nothing to do with payments",
            "markers": {"type": "bug", "domain": "billing"},
        },
    ]
    index_records({"graphPath": str(graph_path), "records": records})


class InspectRecordTests(unittest.TestCase):
    def test_inspect_record_returns_same_score_as_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            _seed_graph(graph_path)
            query = "webhook payment duplicate"

            search_result = search_graph(
                {"graphPath": str(graph_path), "query": query, "limit": 5}
            )
            search_scores = {
                item["id"]: item["score"]
                for item in search_result["directMatches"]
            }

            insp = inspect_record(
                {"graphPath": str(graph_path), "recordId": "r-rule", "query": query}
            )
            # Same number the ranker reports. If we ever drift, this test
            # pins down the contract.
            self.assertAlmostEqual(insp["score"], search_scores["r-rule"], places=6)

    def test_inspect_record_surfaces_matched_markers_and_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            _seed_graph(graph_path)
            # The query mentions two markers ("webhook" => flow, "payments" =>
            # domain) and overlaps with several tokens in the target record.
            query = "webhook payments retry"
            insp = inspect_record(
                {"graphPath": str(graph_path), "recordId": "r-rule", "query": query}
            )
            # Markers that resolve from the query + match the record.
            self.assertIn("flow", insp["matchedMarkers"])
            self.assertIn("domain", insp["matchedMarkers"])
            # At least one token overlap — "webhook" survives normalization
            # and is in the record's tokens.
            self.assertIn("webhook", insp["matchedTokens"])
            # Factor breakdown present with all five factors.
            for key in ("markerMatch", "tokenMatch", "severity", "status", "freshness"):
                self.assertIn(key, insp["factors"])
            # Contributions sum within rounding to the final score.
            contributions = sum(
                float(f.get("contribution", 0)) for f in insp["factors"].values()
            )
            self.assertAlmostEqual(round(contributions, 3), insp["score"], places=3)

    def test_inspect_record_reports_rank_in_top_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            _seed_graph(graph_path)
            query = "webhook payment retry"
            insp = inspect_record(
                {"graphPath": str(graph_path), "recordId": "r-rule", "query": query, "limit": 2}
            )
            self.assertIsNotNone(insp["rank"])
            self.assertLessEqual(insp["rank"], 3)
            self.assertEqual(insp["limit"], 2)

    def test_inspect_record_missing_id_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            _seed_graph(graph_path)
            with self.assertRaises(ValueError):
                inspect_record(
                    {
                        "graphPath": str(graph_path),
                        "recordId": "does-not-exist",
                        "query": "anything",
                    }
                )

    def test_cli_json_and_text_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            _seed_graph(graph_path)
            query = "webhook payment retry"

            json_proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "context_graph_cli.py"),
                    "inspect-record",
                    "--graph",
                    str(graph_path),
                    "--record",
                    "r-rule",
                    "--query",
                    query,
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(json_proc.stdout)
            self.assertEqual(payload["id"], "r-rule")
            self.assertIn("factors", payload)

            text_proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "context_graph_cli.py"),
                    "inspect-record",
                    "--graph",
                    str(graph_path),
                    "--record",
                    "r-rule",
                    "--query",
                    query,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("r-rule", text_proc.stdout)
            self.assertIn("Final score", text_proc.stdout)


class ScoringRefactorRegressionTests(unittest.TestCase):
    """Pin scoring math: ``record_weight`` and ``_score_record_detailed``
    must agree on the score bit-for-bit, and must remain stable across
    refactors. This is the regression gate referenced in the task brief.
    """

    def test_record_weight_and_detailed_agree(self):
        record = {
            "id": "r",
            "markers": {
                "type": "rule",
                "domain": "payments",
                "flow": "webhook",
                "status": "in-progress",
                "severity": "high",
            },
            "tokens": ["payment", "webhook", "retry", "callback"],
            "updatedAt": "2025-04-01T00:00:00+00:00",
        }
        query_markers = {"domain": "payments", "flow": "webhook"}
        query_tokens = tokenize("payment webhook retry")
        importance = {"domain": 0.5, "flow": 0.5}

        score, matched = record_weight(record, query_markers, query_tokens, importance)
        detail = _score_record_detailed(record, query_markers, query_tokens, importance)
        self.assertEqual(score, detail["score"])
        self.assertEqual(matched, detail["matchedMarkers"])

    def test_eval_harness_no_regression_vs_baseline(self):
        """The refactor must not shift any precision number in the baseline.

        Equivalent to ``python3 scripts/context_graph_cli.py eval`` — this
        test exists so the refactor is covered by unittest discovery even
        in environments where direct CLI invocation is sandboxed.
        """
        import eval_harness as h
        queries_path = ROOT / "data" / "eval" / "queries.json"
        graph_path = ROOT / "data" / "eval" / "fixtures" / "graph.json"
        baseline_path = ROOT / "data" / "eval" / "baseline.json"
        if not queries_path.exists() or not baseline_path.exists():
            self.skipTest("eval fixtures missing; eval harness not available in this env")
        queries = h.load_queries(queries_path)
        results = h.run_harness(queries, graph_path, k=5)
        summary = h.summarize(results)
        is_regression, reason = h.compare_against_baseline(
            summary, baseline_path, precision_tolerance=0.0
        )
        self.assertFalse(is_regression, reason)


if __name__ == "__main__":
    unittest.main()
