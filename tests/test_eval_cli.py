from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from eval_cli import main as eval_main  # noqa: E402


FIXTURES_ROOT = ROOT / "data" / "eval"


class EvalCLITests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = eval_main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_exit_zero_when_no_baseline_available(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "missing-baseline.json"
            code, stdout, _ = self._run(
                [
                    "--queries",
                    str(FIXTURES_ROOT / "queries.json"),
                    "--graph",
                    str(FIXTURES_ROOT / "fixtures" / "graph.json"),
                    "--baseline",
                    str(baseline_path),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("Mean", stdout)

    def test_save_baseline_writes_json(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            code, _, _ = self._run(
                [
                    "--queries",
                    str(FIXTURES_ROOT / "queries.json"),
                    "--graph",
                    str(FIXTURES_ROOT / "fixtures" / "graph.json"),
                    "--baseline",
                    str(baseline_path),
                    "--save-baseline",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(baseline_path.exists())
            saved = json.loads(baseline_path.read_text(encoding="utf-8"))
            self.assertIn("meanPrecisionAtK", saved)
            self.assertIn("meanRecallAtK", saved)

    def test_regression_returns_exit_one(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            # A baseline claiming perfect precision -- current retrieval cannot match it.
            baseline_path.write_text(
                json.dumps({"meanPrecisionAtK": 1.0, "meanRecallAtK": 1.0})
            )
            code, stdout, _ = self._run(
                [
                    "--queries",
                    str(FIXTURES_ROOT / "queries.json"),
                    "--graph",
                    str(FIXTURES_ROOT / "fixtures" / "graph.json"),
                    "--baseline",
                    str(baseline_path),
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("regress", stdout.lower())

    def test_cli_error_on_missing_graph_returns_two(self):
        code, _, stderr = self._run(
            [
                "--queries",
                str(FIXTURES_ROOT / "queries.json"),
                "--graph",
                "/does/not/exist/graph.json",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("not exist", stderr.lower() + "")  # defensive concatenation


class CLISubcommandTests(unittest.TestCase):
    def test_context_graph_cli_has_eval_subcommand(self):
        from context_graph_cli import main as cli_main  # noqa: E402
        out = io.StringIO()
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            baseline_path = Path(td) / "baseline.json"
            argv = [
                "eval",
                "--queries",
                str(FIXTURES_ROOT / "queries.json"),
                "--graph",
                str(FIXTURES_ROOT / "fixtures" / "graph.json"),
                "--baseline",
                str(baseline_path),
                "--save-baseline",
            ]
            with redirect_stdout(out), redirect_stderr(err):
                code = cli_main(argv)
            self.assertEqual(code, 0)
            self.assertTrue(baseline_path.exists())


class MCPToolRegistrationTests(unittest.TestCase):
    def test_eval_retrieval_tool_is_registered(self):
        import context_graph_mcp as mcp
        tool_names = [tool.name for tool in mcp.TOOLS]
        self.assertIn("eval_retrieval", tool_names)
        tool = next(t for t in mcp.TOOLS if t.name == "eval_retrieval")
        # Contract: required input fields are queriesPath and graphPath.
        self.assertEqual(
            set(tool.input_schema.get("required", [])),
            {"queriesPath", "graphPath"},
        )

    def test_eval_retrieval_handler_returns_summary(self):
        import context_graph_mcp as mcp
        result = mcp.handle_retrieval_scoring(
            {
                "queriesPath": str(FIXTURES_ROOT / "queries.json"),
                "graphPath": str(FIXTURES_ROOT / "fixtures" / "graph.json"),
            }
        )
        self.assertIn("summary", result)
        self.assertIn("meanPrecisionAtK", result["summary"])
        self.assertIn("perQuery", result)


if __name__ == "__main__":
    unittest.main()
