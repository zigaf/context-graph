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
    cursor_is_fresh,
    filter_pages_by_cursor,
    load_notion_cursor,
    save_notion_cursor,
    update_cursor,
)
import context_graph_mcp  # noqa: E402


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir()
    (root / ".context-graph" / "workspace.json").write_text(
        '{"version":"1"}', encoding="utf-8"
    )
    return root


class CursorIsFreshTests(unittest.TestCase):
    def test_page_absent_from_cursor_is_fresh(self):
        page = {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"}
        self.assertTrue(cursor_is_fresh(page, {}))

    def test_page_newer_than_stored_is_fresh(self):
        page = {"id": "p1", "last_edited_time": "2026-04-22T11:00:00.000Z"}
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        self.assertTrue(cursor_is_fresh(page, cursor))

    def test_page_equal_to_stored_is_not_fresh(self):
        page = {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"}
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        self.assertFalse(cursor_is_fresh(page, cursor))

    def test_page_older_than_stored_is_not_fresh(self):
        page = {"id": "p1", "last_edited_time": "2026-04-22T09:00:00.000Z"}
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        self.assertFalse(cursor_is_fresh(page, cursor))

    def test_page_without_last_edited_time_is_treated_as_fresh(self):
        page = {"id": "p1"}
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        self.assertTrue(cursor_is_fresh(page, cursor))


class UpdateCursorTests(unittest.TestCase):
    def test_pure_does_not_mutate_input(self):
        cursor = {"p1": "2026-04-01T00:00:00.000Z"}
        page = {"id": "p2", "last_edited_time": "2026-04-22T10:00:00.000Z"}
        result = update_cursor(cursor, page)
        self.assertEqual(cursor, {"p1": "2026-04-01T00:00:00.000Z"})
        self.assertEqual(
            result,
            {
                "p1": "2026-04-01T00:00:00.000Z",
                "p2": "2026-04-22T10:00:00.000Z",
            },
        )

    def test_advances_existing_entry(self):
        cursor = {"p1": "2026-04-01T00:00:00.000Z"}
        page = {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"}
        result = update_cursor(cursor, page)
        self.assertEqual(result, {"p1": "2026-04-22T10:00:00.000Z"})

    def test_does_not_rewind_on_older_last_edited_time(self):
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        page = {"id": "p1", "last_edited_time": "2026-04-01T00:00:00.000Z"}
        result = update_cursor(cursor, page)
        self.assertEqual(result, {"p1": "2026-04-22T10:00:00.000Z"})

    def test_ignores_page_without_id(self):
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        page = {"last_edited_time": "2026-04-23T10:00:00.000Z"}
        result = update_cursor(cursor, page)
        self.assertEqual(result, cursor)

    def test_ignores_page_without_last_edited_time(self):
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        page = {"id": "p2"}
        result = update_cursor(cursor, page)
        self.assertEqual(result, cursor)


class LoadSaveNotionCursorTests(unittest.TestCase):
    def test_load_returns_empty_dict_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            self.assertEqual(load_notion_cursor(root), {})

    def test_round_trip_dict_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            cursor = {
                "p1": "2026-04-22T10:00:00.000Z",
                "p2": "2026-04-23T08:30:00.000Z",
            }
            save_notion_cursor(cursor, root)
            reloaded = load_notion_cursor(root)
            self.assertEqual(reloaded, cursor)

    def test_persisted_file_is_plain_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            cursor = {"p1": "2026-04-22T10:00:00.000Z"}
            save_notion_cursor(cursor, root)
            cursor_file = root / ".context-graph" / "notion_cursor.json"
            self.assertTrue(cursor_file.exists())
            with cursor_file.open("r", encoding="utf-8") as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk, cursor)

    def test_load_treats_corrupt_json_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            cursor_file = root / ".context-graph" / "notion_cursor.json"
            cursor_file.write_text("not json", encoding="utf-8")
            self.assertEqual(load_notion_cursor(root), {})

    def test_load_treats_non_dict_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            cursor_file = root / ".context-graph" / "notion_cursor.json"
            cursor_file.write_text("[1,2,3]", encoding="utf-8")
            self.assertEqual(load_notion_cursor(root), {})


class FilterPagesByCursorTests(unittest.TestCase):
    def test_empty_cursor_returns_all_pages_fresh(self):
        pages = [
            {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"},
            {"id": "p2", "last_edited_time": "2026-04-23T08:30:00.000Z"},
        ]
        result = filter_pages_by_cursor({"pages": pages, "cursor": {}})
        self.assertEqual(result["fresh"], pages)
        self.assertEqual(result["stale"], [])
        self.assertEqual(result["newCursorHint"], "2026-04-23T08:30:00.000Z")

    def test_cursor_splits_pages(self):
        pages = [
            {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"},
            {"id": "p2", "last_edited_time": "2026-04-23T08:30:00.000Z"},
        ]
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        result = filter_pages_by_cursor({"pages": pages, "cursor": cursor})
        self.assertEqual(len(result["fresh"]), 1)
        self.assertEqual(result["fresh"][0]["id"], "p2")
        self.assertEqual(len(result["stale"]), 1)
        self.assertEqual(result["stale"][0]["id"], "p1")
        self.assertEqual(result["newCursorHint"], "2026-04-23T08:30:00.000Z")

    def test_all_pages_stale(self):
        pages = [
            {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"},
            {"id": "p2", "last_edited_time": "2026-04-23T08:30:00.000Z"},
        ]
        cursor = {
            "p1": "2026-04-22T10:00:00.000Z",
            "p2": "2026-04-23T08:30:00.000Z",
        }
        result = filter_pages_by_cursor({"pages": pages, "cursor": cursor})
        self.assertEqual(result["fresh"], [])
        self.assertEqual(len(result["stale"]), 2)
        self.assertIsNone(result["newCursorHint"])

    def test_is_pure_does_not_mutate_inputs(self):
        pages = [{"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"}]
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        pages_snapshot = [dict(p) for p in pages]
        cursor_snapshot = dict(cursor)
        filter_pages_by_cursor({"pages": pages, "cursor": cursor})
        self.assertEqual(pages, pages_snapshot)
        self.assertEqual(cursor, cursor_snapshot)

    def test_missing_args_defaults(self):
        # No pages or cursor: returns empty lists and None hint.
        result = filter_pages_by_cursor({})
        self.assertEqual(result["fresh"], [])
        self.assertEqual(result["stale"], [])
        self.assertIsNone(result["newCursorHint"])


class MCPToolSurfaceTests(unittest.TestCase):
    def _tool(self, name: str):
        for tool in context_graph_mcp.TOOLS:
            if tool.name == name:
                return tool
        self.fail(f"MCP tool not registered: {name}")

    def test_load_notion_cursor_tool_registered(self):
        tool = self._tool("load_notion_cursor")
        self.assertEqual(tool.title, "Load Notion Cursor")
        self.assertIn("cursor", tool.output_schema.get("properties", {}))

    def test_save_notion_cursor_tool_registered(self):
        tool = self._tool("save_notion_cursor")
        self.assertIn("cursor", tool.input_schema.get("required", []))

    def test_filter_pages_by_cursor_tool_registered(self):
        tool = self._tool("filter_pages_by_cursor")
        output_props = tool.output_schema.get("properties", {})
        self.assertIn("fresh", output_props)
        self.assertIn("stale", output_props)
        self.assertIn("newCursorHint", output_props)

    def test_load_and_save_via_mcp_handlers_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            cursor = {"p1": "2026-04-22T10:00:00.000Z"}

            save_tool = self._tool("save_notion_cursor")
            save_result = save_tool.handler(
                {"workspaceRoot": str(root), "cursor": cursor}
            )
            self.assertTrue(save_result["saved"])

            load_tool = self._tool("load_notion_cursor")
            load_result = load_tool.handler({"workspaceRoot": str(root)})
            self.assertEqual(load_result["cursor"], cursor)

    def test_filter_pages_by_cursor_handler_is_pure(self):
        filter_tool = self._tool("filter_pages_by_cursor")
        pages = [
            {"id": "p1", "last_edited_time": "2026-04-22T10:00:00.000Z"},
            {"id": "p2", "last_edited_time": "2026-04-23T08:30:00.000Z"},
        ]
        cursor = {"p1": "2026-04-22T10:00:00.000Z"}
        result = filter_tool.handler({"pages": pages, "cursor": cursor})
        self.assertEqual(len(result["fresh"]), 1)
        self.assertEqual(result["fresh"][0]["id"], "p2")
        self.assertEqual(result["newCursorHint"], "2026-04-23T08:30:00.000Z")

    def test_save_notion_cursor_rejects_non_dict(self):
        save_tool = self._tool("save_notion_cursor")
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_workspace(tmp)
            with self.assertRaises(ValueError):
                save_tool.handler({"workspaceRoot": str(root), "cursor": "nope"})

    def test_new_tools_present_in_tool_names(self):
        # Pins the exact check the verification step expects.
        names = sorted(tool.name for tool in context_graph_mcp.TOOLS)
        self.assertIn("load_notion_cursor", names)
        self.assertIn("save_notion_cursor", names)
        self.assertIn("filter_pages_by_cursor", names)


if __name__ == "__main__":
    unittest.main()
