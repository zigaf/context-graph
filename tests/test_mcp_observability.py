"""Verify that the observability MCP tools are registered and callable.

Loads the MCP module via the same sys.path manipulation other tests use,
asserts that ``graph_diff`` and ``inspect_record`` appear in the tool
registry, and that the CLI exposes them as subcommands.
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

import context_graph_mcp  # noqa: E402
from context_graph_core import index_records  # noqa: E402


class ObservabilityMCPTests(unittest.TestCase):
    def test_graph_diff_and_inspect_record_are_registered(self):
        names = sorted(tool.name for tool in context_graph_mcp.TOOLS)
        self.assertIn("graph_diff", names)
        self.assertIn("inspect_record", names)

    def test_mcp_graph_diff_handler_roundtrips(self):
        left = {"records": {"a": {"id": "a"}}, "edges": []}
        right = {"records": {"a": {"id": "a"}, "b": {"id": "b"}}, "edges": []}
        result = context_graph_mcp.handle_graph_diff({"left": left, "right": right})
        self.assertEqual(result["summary"]["recordsAdded"], 1)

    def test_mcp_inspect_record_handler_requires_record_id(self):
        with self.assertRaises(ValueError):
            context_graph_mcp.handle_inspect_record({})

    def test_mcp_inspect_record_handler_runs_against_fresh_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"
            index_records(
                {
                    "graphPath": str(graph_path),
                    "records": [
                        {
                            "id": "r",
                            "title": "Webhook rule",
                            "content": "retry webhook",
                            "markers": {"type": "rule", "flow": "webhook"},
                        }
                    ],
                }
            )
            result = context_graph_mcp.handle_inspect_record(
                {"graphPath": str(graph_path), "recordId": "r", "query": "webhook"}
            )
            self.assertEqual(result["id"], "r")
            self.assertIn("factors", result)

    def test_cli_help_lists_new_subcommands(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "context_graph_cli.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        out = proc.stdout
        self.assertIn("graph-diff", out)
        self.assertIn("inspect-record", out)


if __name__ == "__main__":
    unittest.main()
