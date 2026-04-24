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

import context_graph_mcp as m  # noqa: E402


class MCPCuratorToolsTests(unittest.TestCase):
    def _names(self) -> list[str]:
        return [t.name for t in m.TOOLS]

    def test_three_curator_tools_registered(self):
        names = self._names()
        self.assertIn("bootstrap_preview", names)
        self.assertIn("apply_bootstrap_decision", names)
        self.assertIn("parse_hashtags", names)

    def test_bootstrap_preview_runs(self):
        from context_graph_core import init_workspace
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            (Path(tmp) / "README.md").write_text("# proj\n")
            (Path(tmp) / "src").mkdir()
            tool = next(t for t in m.TOOLS if t.name == "bootstrap_preview")
            result = tool.handler({"workspaceRoot": tmp})
            self.assertEqual(result["projectTitle"], "proj")
            self.assertTrue(result["bootstrapNeeded"])

    def test_bootstrap_preview_reports_not_needed_after_decline(self):
        from context_graph_core import init_workspace
        from curator_bootstrap import mark_bootstrap_declined
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            mark_bootstrap_declined(Path(tmp))
            tool = next(t for t in m.TOOLS if t.name == "bootstrap_preview")
            result = tool.handler({"workspaceRoot": tmp})
            self.assertFalse(result["bootstrapNeeded"])

    def test_apply_bootstrap_decision_accept(self):
        from context_graph_core import init_workspace, load_workspace_manifest
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            tool = next(t for t in m.TOOLS if t.name == "apply_bootstrap_decision")
            result = tool.handler({
                "workspaceRoot": tmp,
                "decision": "accept",
                "rootPageId": "root1",
                "rootPageUrl": "https://x/root1",
                "dirPageIds": {"src/": "p1"},
            })
            self.assertTrue(result["recorded"])
            manifest = load_workspace_manifest(Path(tmp))
            self.assertEqual(manifest["notion"]["rootPageId"], "root1")

    def test_apply_bootstrap_decision_decline(self):
        from context_graph_core import init_workspace, load_workspace_manifest
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            tool = next(t for t in m.TOOLS if t.name == "apply_bootstrap_decision")
            result = tool.handler({"workspaceRoot": tmp, "decision": "decline"})
            self.assertTrue(result["recorded"])
            manifest = load_workspace_manifest(Path(tmp))
            self.assertTrue(manifest["notion"]["bootstrapDeclined"])

    def test_apply_bootstrap_decision_unknown_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = next(t for t in m.TOOLS if t.name == "apply_bootstrap_decision")
            with self.assertRaises(ValueError):
                tool.handler({"workspaceRoot": tmp, "decision": "maybe"})

    def test_parse_hashtags_runs(self):
        tool = next(t for t in m.TOOLS if t.name == "parse_hashtags")
        result = tool.handler({"query": "#rule payments"})
        self.assertEqual(result["query"], "payments")
        self.assertEqual(result["markers"], {"type": "rule"})


if __name__ == "__main__":
    unittest.main()
