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

import context_graph_mcp  # noqa: E402


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps({"version": "1", "id": "ws-test", "rootPath": str(root)}),
        encoding="utf-8",
    )
    return root


def _handler(name: str):
    by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
    return by_name[name].handler


class EnqueuePushTests(unittest.TestCase):
    def test_enqueue_round_trips_through_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue = _handler("enqueue_push")
            list_pending = _handler("list_pending_pushes")

            enqueue({"recordId": "notion:rule-a", "workspaceRoot": str(ws)})
            result = list_pending({"workspaceRoot": str(ws)})
            self.assertEqual(result["pending"], ["notion:rule-a"])

    def test_dequeue_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue = _handler("enqueue_push")
            dequeue = _handler("dequeue_push")
            list_pending = _handler("list_pending_pushes")

            enqueue({"recordId": "notion:rule-z", "workspaceRoot": str(ws)})
            dequeue({"recordId": "notion:rule-z", "workspaceRoot": str(ws)})
            result = list_pending({"workspaceRoot": str(ws)})
            self.assertEqual(result["pending"], [])

    def test_enqueue_missing_record_id_raises(self):
        enqueue = _handler("enqueue_push")
        with self.assertRaises(ValueError):
            enqueue({"workspaceRoot": "/tmp"})

    def test_dequeue_missing_record_id_raises(self):
        dequeue = _handler("dequeue_push")
        with self.assertRaises(ValueError):
            dequeue({"workspaceRoot": "/tmp"})


if __name__ == "__main__":
    unittest.main()
