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
    enqueue_push,
    index_records,
    save_push_state,
)
from auto_push import build_plan  # noqa: E402


def _make_workspace(tmp: str, *, notion_root: str | None = None,
                    dir_pages: dict | None = None) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "version": "1",
        "id": "ws-test",
        "rootPath": str(root),
    }
    if notion_root:
        manifest["notion"] = {
            "rootPageId": notion_root,
            "dirPageIds": dir_pages or {},
        }
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return root


class BuildPlanTests(unittest.TestCase):
    def test_no_workspace_notion_means_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)  # no notion config
            enqueue_push("notion:abc", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertTrue(plan["blocked"])
            self.assertEqual(plan["reason"], "no-notion-root")
            self.assertEqual(plan["creates"], [])
            self.assertEqual(plan["updates"], [])

    def test_pending_create_resolved_to_dir_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                tmp,
                notion_root="root-page",
                dir_pages={"bl-api/": "bl-api-page"},
            )
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-x",
                    "title": "Rule X",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore > bl-api/"},
                    },
                }],
            })
            enqueue_push("notion:rule-x", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertFalse(plan["blocked"])
            self.assertEqual(len(plan["creates"]), 1)
            self.assertEqual(plan["creates"][0]["recordId"], "notion:rule-x")
            self.assertEqual(plan["creates"][0]["parentPageId"], "bl-api-page")

    def test_pending_create_with_explicit_notion_dir_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                tmp,
                notion_root="root-page",
                dir_pages={"core/": "core-page", "admin/": "admin-page"},
            )
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-y",
                    "title": "Rule Y",
                    "content": "Body",
                    "markers": {
                        "type": "rule",
                        "status": "done",
                        "notionDir": "admin/",
                    },
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore > core/"},
                    },
                }],
            })
            enqueue_push("notion:rule-y", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"][0]["parentPageId"], "admin-page")

    def test_pending_cross_cutting_falls_back_to_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                tmp,
                notion_root="root-page",
                dir_pages={"bl-api/": "bl-api-page"},
            )
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-z",
                    "title": "Rule Z",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore"},
                    },
                }],
            })
            enqueue_push("notion:rule-z", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"][0]["parentPageId"], "root-page")

    def test_pending_arbitration_record_is_skipped_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page", dir_pages={})
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:pending",
                    "title": "Unresolved",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {
                            "classifierNotes": {"arbiter": "pending-arbitration"},
                        },
                    },
                }],
            })
            enqueue_push("notion:pending", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"], [])
            self.assertEqual(plan["updates"], [])
            self.assertIn("notion:pending", plan["skipped"])
            self.assertEqual(plan["skipped"]["notion:pending"], "pending-arbitration")

    def test_revision_unchanged_means_skip_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page", dir_pages={})
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-w",
                    "title": "Rule W",
                    "content": "Body v1",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {"system": "notion", "metadata": {}},
                    "revision": {"version": 1},
                }],
            })
            save_push_state({
                "pending": ["notion:rule-w"],
                "records": {
                    "notion:rule-w": {
                        "notionPageId": "page-w",
                        "lastPushedRevision": 1,
                        "lastPushedAt": "2026-04-25T12:00:00Z",
                    }
                },
            }, workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"], [])
            self.assertEqual(plan["updates"], [])
            self.assertEqual(plan["skipped"]["notion:rule-w"], "no-revision-change")


class CliPrepareAutoPushTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "context_graph_cli.py"

    def test_prepare_writes_plan_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page", dir_pages={})
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-cli",
                    "title": "CLI rule",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {"system": "notion", "metadata": {}},
                }],
            })
            enqueue_push("notion:rule-cli", workspace_root=ws)
            proc = subprocess.run(
                ["python3", str(self.SCRIPT), "prepare-auto-push"],
                input="{}",
                capture_output=True,
                text=True,
                cwd=str(ws),
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["plan"]["blocked"])
            self.assertEqual(len(payload["plan"]["creates"]), 1)
            plan_path = ws / ".context-graph" / "auto_push_plan.json"
            self.assertTrue(plan_path.exists())
            self.assertEqual(
                json.loads(plan_path.read_text())["creates"][0]["recordId"],
                "notion:rule-cli",
            )
