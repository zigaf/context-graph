"""Baseline regression check invoked as a unit test.

Runs the harness over the committed queries and fixtures, compares against
``data/eval/baseline.json`` with the default tolerance of 0.0, and fails the
test if precision@k regresses. Exists so the Phase 5 retrieval changes can be
verified inside the normal test discovery pipeline without needing a separate
CLI invocation.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval_cli import main as eval_main  # noqa: E402


FIXTURES_ROOT = ROOT / "data" / "eval"


class BaselineRegressionCheck(unittest.TestCase):
    def test_committed_baseline_not_regressed(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = eval_main(
                [
                    "--queries",
                    str(FIXTURES_ROOT / "queries.json"),
                    "--graph",
                    str(FIXTURES_ROOT / "fixtures" / "graph.json"),
                    "--baseline",
                    str(FIXTURES_ROOT / "baseline.json"),
                ]
            )
        # Surface the report so CI logs keep the precision/recall line visible.
        sys.stdout.write("\n" + out.getvalue())
        self.assertEqual(
            code,
            0,
            f"eval harness regressed against baseline:\n{out.getvalue()}\n{err.getvalue()}",
        )


if __name__ == "__main__":
    unittest.main()
