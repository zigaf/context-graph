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
from context_graph_core import index_records, save_push_state, load_push_state  # noqa: E402


def _make_workspace(tmp: str, notion_root: str | None = "root-page-id") -> Path:
    root = Path(tmp).resolve()
    cg = root / ".context-graph"
    cg.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"version": "1", "id": "ws-test", "rootPath": str(root)}
    if notion_root:
        manifest["notion"] = {"rootPageId": notion_root}
    (cg / "workspace.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _seed(graph_path: str, workspace: Path) -> None:
    index_records(
        {
            "graphPath": graph_path,
            "workspaceRoot": str(workspace),
            "records": [
                {
                    "id": "promoted:rule-a",
                    "title": "Rule A",
                    "content": "# Rule A\n\nAlways retry on 5xx.",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {"system": "context-graph", "metadata": {}},
                },
                {
                    "id": "promoted:decision-b",
                    "title": "Decision B",
                    "content": "Adopt idempotency keys.",
                    "markers": {"type": "decision", "status": "done"},
                    "source": {"system": "context-graph", "metadata": {}},
                },
            ],
        }
    )


class McpPushToolsRegisteredTests(unittest.TestCase):
    def test_new_tools_exist(self):
        names = {tool.name for tool in context_graph_mcp.TOOLS}
        self.assertIn("plan_notion_push", names)
        self.assertIn("apply_notion_push_result", names)
        self.assertIn("record_to_notion_payload", names)

    def test_tool_specs_have_clean_schemas(self):
        by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
        for name in ("plan_notion_push", "apply_notion_push_result", "record_to_notion_payload"):
            tool = by_name[name]
            self.assertIsInstance(tool.input_schema, dict)
            self.assertEqual(tool.input_schema.get("type"), "object")
            self.assertIn("properties", tool.input_schema)


class PlanNotionPushHandlerTests(unittest.TestCase):
    def test_returns_plan_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp)
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed(graph_path, workspace)

            by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
            handler = by_name["plan_notion_push"].handler

            result = handler(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                }
            )
            self.assertIn("plan", result)
            self.assertIn("pushState", result)
            self.assertEqual(len(result["plan"]["creates"]), 2)
            self.assertEqual(result["plan"]["updates"], [])
            self.assertEqual(result["pushState"], {})

    def test_plan_respects_record_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp)
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed(graph_path, workspace)

            by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
            handler = by_name["plan_notion_push"].handler

            result = handler(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "recordIds": ["promoted:rule-a"],
                }
            )
            self.assertEqual(len(result["plan"]["creates"]), 1)
            self.assertEqual(result["plan"]["creates"][0]["id"], "promoted:rule-a")


class ApplyNotionPushResultHandlerTests(unittest.TestCase):
    def test_persists_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp)

            by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
            handler = by_name["apply_notion_push_result"].handler

            result = handler(
                {
                    "recordId": "promoted:rule-a",
                    "notionPageId": "page-xyz",
                    "workspaceRoot": str(workspace),
                }
            )
            self.assertIn("pushState", result)
            self.assertEqual(result["pushState"]["promoted:rule-a"], "page-xyz")

            stored = load_push_state(workspace)
            self.assertEqual(stored["promoted:rule-a"], "page-xyz")


class RecordToNotionPayloadHandlerTests(unittest.TestCase):
    def test_returns_payload_with_title_blocks_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page-id")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed(graph_path, workspace)

            by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
            handler = by_name["record_to_notion_payload"].handler

            result = handler(
                {
                    "recordId": "promoted:rule-a",
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                }
            )
            self.assertEqual(result["title"], "Rule A")
            self.assertEqual(result["parentPageId"], "root-page-id")
            self.assertIsInstance(result["blocks"], list)
            self.assertTrue(result["blocks"])
            self.assertIsInstance(result.get("content"), str)
            self.assertIn("Rule A", result["content"])

    def test_missing_record_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp)
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed(graph_path, workspace)

            by_name = {tool.name: tool for tool in context_graph_mcp.TOOLS}
            handler = by_name["record_to_notion_payload"].handler
            with self.assertRaises(ValueError):
                handler(
                    {
                        "recordId": "does-not-exist",
                        "graphPath": graph_path,
                        "workspaceRoot": str(workspace),
                    }
                )


if __name__ == "__main__":
    unittest.main()
