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


from eval_harness import (  # noqa: E402
    EvalQuery,
    EvalResult,
    compare_against_baseline,
    format_report,
    load_queries,
    precision_at_k,
    recall_at_k,
    run_eval,
    summarize,
)


FIXTURES_ROOT = ROOT / "data" / "eval"


class PrecisionRecallMathTests(unittest.TestCase):
    def test_precision_at_k_basic(self):
        retrieved = ["a", "b", "c", "d"]
        expected = {"a", "c"}
        self.assertAlmostEqual(precision_at_k(retrieved, expected, k=4), 0.5)

    def test_precision_at_k_truncates(self):
        retrieved = ["a", "b", "c", "d"]
        expected = {"c", "d"}
        self.assertAlmostEqual(precision_at_k(retrieved, expected, k=2), 0.0)

    def test_precision_empty_retrieved_is_zero(self):
        self.assertAlmostEqual(precision_at_k([], {"x"}, k=5), 0.0)

    def test_precision_empty_expected_is_one(self):
        self.assertAlmostEqual(precision_at_k(["a", "b"], set(), k=5), 1.0)

    def test_recall_at_k_basic(self):
        retrieved = ["a", "b", "c"]
        expected = {"a", "c", "z"}
        self.assertAlmostEqual(recall_at_k(retrieved, expected, k=5), 2 / 3)

    def test_recall_at_k_truncates(self):
        retrieved = ["a", "b", "c"]
        expected = {"a", "c"}
        self.assertAlmostEqual(recall_at_k(retrieved, expected, k=1), 0.5)

    def test_recall_empty_expected_is_one(self):
        self.assertAlmostEqual(recall_at_k(["a"], set(), k=5), 1.0)


class SummarizeTests(unittest.TestCase):
    def _mk_result(self, qid: str, p: float, r: float, pack: int, full: int) -> EvalResult:
        return EvalResult(
            queryId=qid,
            precisionAtK=p,
            recallAtK=r,
            packSizeChars=pack,
            packSizeRecords=1,
            fullDumpSizeChars=full,
            foundDirect=[],
            missedDirect=[],
            foundSupporting=[],
            missedSupporting=[],
        )

    def test_summarize_mean(self):
        results = [
            self._mk_result("q1", 1.0, 0.5, 100, 1000),
            self._mk_result("q2", 0.5, 1.0, 200, 1000),
        ]
        summary = summarize(results)
        self.assertAlmostEqual(summary["meanPrecisionAtK"], 0.75)
        self.assertAlmostEqual(summary["meanRecallAtK"], 0.75)
        self.assertEqual(summary["queryCount"], 2)
        self.assertEqual(summary["totalPackSizeChars"], 300)
        self.assertEqual(summary["totalFullDumpSizeChars"], 2000)
        self.assertAlmostEqual(summary["packToFullDumpRatio"], 0.15)

    def test_summarize_empty(self):
        summary = summarize([])
        self.assertEqual(summary["queryCount"], 0)
        self.assertEqual(summary["meanPrecisionAtK"], 0.0)
        self.assertEqual(summary["meanRecallAtK"], 0.0)
        self.assertEqual(summary["packToFullDumpRatio"], 0.0)


class CompareAgainstBaselineTests(unittest.TestCase):
    def test_pass_when_precision_matches(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            baseline_path.write_text(json.dumps({"meanPrecisionAtK": 0.75, "meanRecallAtK": 0.7}))
            current = {"meanPrecisionAtK": 0.75, "meanRecallAtK": 0.7}
            is_regression, reason = compare_against_baseline(current, baseline_path)
            self.assertFalse(is_regression)
            self.assertIn("no regression", reason.lower())

    def test_pass_when_precision_improves(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            baseline_path.write_text(json.dumps({"meanPrecisionAtK": 0.5, "meanRecallAtK": 0.5}))
            current = {"meanPrecisionAtK": 0.9, "meanRecallAtK": 0.9}
            is_regression, _ = compare_against_baseline(current, baseline_path)
            self.assertFalse(is_regression)

    def test_regression_on_precision_drop(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            baseline_path.write_text(json.dumps({"meanPrecisionAtK": 0.75, "meanRecallAtK": 0.5}))
            current = {"meanPrecisionAtK": 0.50, "meanRecallAtK": 0.5}
            is_regression, reason = compare_against_baseline(current, baseline_path)
            self.assertTrue(is_regression)
            self.assertIn("regress", reason.lower())

    def test_tolerance_allows_small_drop(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            baseline_path.write_text(json.dumps({"meanPrecisionAtK": 0.8, "meanRecallAtK": 0.5}))
            current_ok = {"meanPrecisionAtK": 0.77, "meanRecallAtK": 0.5}
            current_bad = {"meanPrecisionAtK": 0.75, "meanRecallAtK": 0.5}
            self.assertFalse(
                compare_against_baseline(current_ok, baseline_path, precision_tolerance=0.05)[0]
            )
            self.assertTrue(
                compare_against_baseline(current_bad, baseline_path, precision_tolerance=0.05)[0]
            )

    def test_missing_baseline_returns_no_regression(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "does-not-exist.json"
            current = {"meanPrecisionAtK": 0.5, "meanRecallAtK": 0.5}
            is_regression, reason = compare_against_baseline(current, baseline_path)
            self.assertFalse(is_regression)
            self.assertIn("no baseline", reason.lower())


class FormatReportTests(unittest.TestCase):
    def test_format_report_contains_key_sections(self):
        results = [
            EvalResult(
                queryId="q1",
                precisionAtK=0.5,
                recallAtK=1.0,
                packSizeChars=120,
                packSizeRecords=3,
                fullDumpSizeChars=1000,
                foundDirect=["r:a"],
                missedDirect=[],
                foundSupporting=["r:b"],
                missedSupporting=["r:c"],
            )
        ]
        summary = summarize(results)
        text = format_report(results, summary)
        self.assertIn("q1", text)
        self.assertIn("precision", text.lower())
        self.assertIn("recall", text.lower())
        self.assertIn("Mean", text)


class LoadQueriesTests(unittest.TestCase):
    def test_load_queries_parses_fixture(self):
        queries = load_queries(FIXTURES_ROOT / "queries.json")
        self.assertGreaterEqual(len(queries), 5)
        first = queries[0]
        self.assertIsInstance(first, EvalQuery)
        self.assertTrue(first.id)
        self.assertTrue(first.query)
        self.assertIsInstance(first.expectedDirectMatches, list)
        self.assertIsInstance(first.expectedSupporting, list)

    def test_load_queries_rejects_bad_version(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text(json.dumps({"version": "999", "queries": []}))
            with self.assertRaises(ValueError):
                load_queries(path)


class RunHarnessEndToEndTests(unittest.TestCase):
    def test_run_eval_against_fixture_hits_precision(self):
        queries = load_queries(FIXTURES_ROOT / "queries.json")
        graph_path = FIXTURES_ROOT / "fixtures" / "graph.json"
        results = run_eval(queries, graph_path)
        self.assertEqual(len(results), len(queries))
        self.assertTrue(
            any(result.precisionAtK >= 0.5 for result in results),
            "No query reached precision@k >= 0.5 against the seed fixture",
        )
        self.assertTrue(
            any(
                result.packSizeChars < result.fullDumpSizeChars
                for result in results
            ),
            "Context pack never smaller than full-dump baseline",
        )

    def test_run_eval_is_deterministic(self):
        queries = load_queries(FIXTURES_ROOT / "queries.json")
        graph_path = FIXTURES_ROOT / "fixtures" / "graph.json"
        results_a = run_eval(queries, graph_path)
        results_b = run_eval(queries, graph_path)
        a_scores = [(r.queryId, r.precisionAtK, r.recallAtK) for r in results_a]
        b_scores = [(r.queryId, r.precisionAtK, r.recallAtK) for r in results_b]
        self.assertEqual(a_scores, b_scores)


if __name__ == "__main__":
    unittest.main()
