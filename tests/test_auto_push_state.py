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

from context_graph_core import (  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
