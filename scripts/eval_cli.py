"""Evaluation CLI runner.

Invoked both standalone (``python3 scripts/eval_cli.py ...``) and by the main
``context_graph_cli.py eval`` subcommand. Keeps argparse in a single place so
the exit-code contract (0 = ok, 1 = regression, 2 = CLI error) is easy to
audit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import eval_harness as _harness


DEFAULT_QUERIES = Path("data/eval/queries.json")
DEFAULT_GRAPH = Path("data/eval/fixtures/graph.json")
DEFAULT_BASELINE = Path("data/eval/baseline.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cg-eval",
        description="Run the Context Graph retrieval evaluation harness.",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_QUERIES,
        help="Path to the queries JSON (default: data/eval/queries.json).",
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=DEFAULT_GRAPH,
        help="Path to the fixture records or graph.json (default: data/eval/fixtures/graph.json).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Path to the baseline JSON (default: data/eval/baseline.json).",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Overwrite the baseline file with the current summary and exit 0.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="Fractional tolerance for precision drop (e.g. 0.05 = 5%%).",
    )
    parser.add_argument(
        "-k",
        type=int,
        default=5,
        help="Fallback cutoff k when a query does not specify its own k.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    queries_path = Path(args.queries)
    graph_path = Path(args.graph)

    if not queries_path.exists():
        sys.stderr.write(f"queries file does not exist: {queries_path}\n")
        return 2
    if not graph_path.exists():
        sys.stderr.write(f"graph file does not exist: {graph_path}\n")
        return 2

    try:
        queries = _harness.load_queries(queries_path)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"failed to load queries: {exc}\n")
        return 2

    try:
        results = _harness.run_harness(queries, graph_path, k=args.k)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"harness failed: {exc}\n")
        return 2

    summary = _harness.summarize(results)
    report = _harness.format_report(results, summary)
    sys.stdout.write(report)

    if args.save_baseline:
        try:
            _harness.save_baseline(summary, args.baseline)
        except OSError as exc:
            sys.stderr.write(f"failed to write baseline: {exc}\n")
            return 2
        sys.stdout.write(f"Baseline written to {args.baseline}\n")
        return 0

    is_regression, reason = _harness.compare_against_baseline(
        summary, args.baseline, precision_tolerance=args.tolerance
    )
    sys.stdout.write(f"Baseline check: {reason}\n")
    return 1 if is_regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
