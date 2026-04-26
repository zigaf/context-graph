from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import enqueue_push, index_records  # noqa: E402
from trigger_detect import main as trigger_main  # noqa: E402


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1",
        "id": "ws-e2e",
        "rootPath": str(root),
        "notion": {
            "rootPageId": "root-page",
            "dirPageIds": {"core/": "core-page"},
        },
    }
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return root


class AutoPushEndToEndTests(unittest.TestCase):
    def test_keyword_trigger_writes_plan_with_one_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-e2e",
                    "title": "E2E rule",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore > core/"},
                    },
                }],
            })
            enqueue_push("notion:rule-e2e", workspace_root=ws)
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=str(ws))
            self.assertEqual(exit_code, 0)
            self.assertIn("Run /cg-sync-notion auto", buf.getvalue())
            plan_path = ws / ".context-graph" / "auto_push_plan.json"
            self.assertTrue(plan_path.exists(),
                "trigger_detect must have called prepare-auto-push")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(len(plan["creates"]), 1)
            self.assertEqual(plan["creates"][0]["recordId"], "notion:rule-e2e")
            self.assertEqual(plan["creates"][0]["parentPageId"], "core-page")


if __name__ == "__main__":
    unittest.main()
