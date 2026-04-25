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
    dequeue_push,
    enqueue_push,
    list_pending_pushes,
    load_push_state,
    save_push_state,
)


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps({"version": "1", "id": "ws-test", "rootPath": str(root)}),
        encoding="utf-8",
    )
    return root


class LoadPushStateLegacyShapeTests(unittest.TestCase):
    def test_loads_legacy_flat_mapping_as_per_record_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            (ws / ".context-graph" / "notion_push.json").write_text(
                json.dumps({"notion:abc": "page-1", "notion:def": "page-2"}),
                encoding="utf-8",
            )
            state = load_push_state(ws)
            self.assertEqual(state["records"]["notion:abc"]["notionPageId"], "page-1")
            self.assertEqual(state["records"]["notion:def"]["notionPageId"], "page-2")
            self.assertEqual(state["pending"], [])


class LoadPushStateNewShapeTests(unittest.TestCase):
    def test_loads_new_shape_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            payload = {
                "pending": ["notion:abc"],
                "records": {
                    "notion:def": {
                        "notionPageId": "page-2",
                        "lastPushedRevision": 3,
                        "lastPushedAt": "2026-04-26T18:30:00Z",
                    }
                },
            }
            (ws / ".context-graph" / "notion_push.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            state = load_push_state(ws)
            self.assertEqual(state["pending"], ["notion:abc"])
            self.assertEqual(state["records"]["notion:def"]["lastPushedRevision"], 3)


class SavePushStateTests(unittest.TestCase):
    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            payload = {
                "pending": ["notion:abc", "notion:def"],
                "records": {
                    "notion:def": {
                        "notionPageId": "page-2",
                        "lastPushedRevision": 4,
                        "lastPushedAt": "2026-04-26T19:00:00Z",
                    }
                },
            }
            save_push_state(payload, ws)
            again = load_push_state(ws)
            self.assertEqual(again, payload)

    def test_save_rejects_non_dict_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            with self.assertRaises(TypeError):
                save_push_state(["notion:abc"], ws)  # type: ignore[arg-type]


class QueueOpsTests(unittest.TestCase):
    def test_enqueue_then_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-a", workspace_root=ws)
            enqueue_push("notion:rule-b", workspace_root=ws)
            pending = list_pending_pushes(workspace_root=ws)
            self.assertEqual(pending, ["notion:rule-a", "notion:rule-b"])

    def test_enqueue_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-a", workspace_root=ws)
            enqueue_push("notion:rule-a", workspace_root=ws)
            self.assertEqual(list_pending_pushes(workspace_root=ws), ["notion:rule-a"])

    def test_dequeue_removes_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-a", workspace_root=ws)
            enqueue_push("notion:rule-b", workspace_root=ws)
            dequeue_push("notion:rule-a", workspace_root=ws)
            self.assertEqual(list_pending_pushes(workspace_root=ws), ["notion:rule-b"])

    def test_dequeue_unknown_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            dequeue_push("notion:none", workspace_root=ws)  # must not raise
            self.assertEqual(list_pending_pushes(workspace_root=ws), [])


class CliQueueSubcommandTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "context_graph_cli.py"

    def _run(self, payload: dict, command: str, ws: Path) -> dict:
        proc = subprocess.run(
            ["python3", str(self.SCRIPT), command],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(ws),
            check=False,
        )
        if proc.returncode != 0:
            self.fail(f"{command} failed: {proc.stderr}")
        return json.loads(proc.stdout)

    def test_enqueue_then_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            self._run({"recordId": "notion:abc"}, "enqueue-push", ws)
            self._run({"recordId": "notion:def"}, "enqueue-push", ws)
            result = self._run({}, "list-pending-pushes", ws)
            self.assertEqual(result["pending"], ["notion:abc", "notion:def"])


if __name__ == "__main__":
    unittest.main()
