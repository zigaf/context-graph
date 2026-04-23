from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from post_edit_reindex import (  # noqa: E402
    derive_ingest_root,
    find_best_root,
    plan_reindex,
)


class DeriveIngestRootTests(unittest.TestCase):
    def test_markdown_source_yields_root(self):
        source = {
            "system": "markdown",
            "path": "sub/file.md",
            "url": "/abs/notes/sub/file.md",
        }
        self.assertEqual(derive_ingest_root(source), Path("/abs/notes"))

    def test_notion_export_source_yields_root(self):
        source = {
            "system": "notion-export",
            "path": "Page 11111111111111111111111111111111.md",
            "url": "/exports/workspace/Page 11111111111111111111111111111111.md",
        }
        self.assertEqual(derive_ingest_root(source), Path("/exports/workspace"))

    def test_live_notion_source_is_ignored(self):
        source = {"system": "notion", "path": "anything", "url": "https://notion.so/..."}
        self.assertIsNone(derive_ingest_root(source))

    def test_missing_url_or_path_returns_none(self):
        self.assertIsNone(derive_ingest_root({"system": "markdown"}))
        self.assertIsNone(derive_ingest_root({"system": "markdown", "path": "x.md"}))
        self.assertIsNone(derive_ingest_root({"system": "markdown", "url": "/x.md"}))

    def test_absolute_path_is_rejected(self):
        source = {
            "system": "markdown",
            "path": "/abs/notes/sub/file.md",
            "url": "/abs/notes/sub/file.md",
        }
        self.assertIsNone(derive_ingest_root(source))

    def test_mismatched_url_path_tail_returns_none(self):
        source = {
            "system": "markdown",
            "path": "sub/file.md",
            "url": "/abs/other/sub/different.md",
        }
        self.assertIsNone(derive_ingest_root(source))


class FindBestRootTests(unittest.TestCase):
    def _graph_with_records(self, *sources: dict) -> dict:
        return {
            "records": {f"id{i}": {"source": src} for i, src in enumerate(sources)}
        }

    def test_picks_deepest_ancestor(self):
        graph = self._graph_with_records(
            {"system": "markdown", "path": "a/file.md", "url": "/root/a/file.md"},
            {"system": "markdown", "path": "file.md", "url": "/root/a/b/file.md"},
        )
        edited = Path("/root/a/b/new.md").parent.resolve()
        self.assertEqual(find_best_root(edited, graph), Path("/root/a/b").resolve())

    def test_returns_none_when_no_ancestor_matches(self):
        graph = self._graph_with_records(
            {"system": "markdown", "path": "file.md", "url": "/root/notes/file.md"},
        )
        edited = Path("/tmp/scratch.md").parent.resolve()
        self.assertIsNone(find_best_root(edited, graph))

    def test_returns_none_for_empty_graph(self):
        self.assertIsNone(find_best_root(Path("/any").resolve(), {"records": {}}))


class PlanReindexTests(unittest.TestCase):
    def _graph_tracking(self, tracked_root: str) -> dict:
        return {
            "records": {
                "r1": {
                    "source": {
                        "system": "markdown",
                        "path": "file.md",
                        "url": f"{tracked_root}/file.md",
                    }
                }
            }
        }

    def test_skips_non_markdown_file(self):
        graph = self._graph_tracking("/notes")
        payload = {"tool_input": {"file_path": "/notes/script.py"}}
        self.assertIsNone(plan_reindex(payload, graph))

    def test_skips_when_edited_dir_not_tracked(self):
        graph = self._graph_tracking("/notes")
        payload = {"tool_input": {"file_path": "/tmp/scratch.md"}}
        self.assertIsNone(plan_reindex(payload, graph))

    def test_returns_tracked_root_for_edit_inside_tracked_dir(self):
        tracked = Path("/notes").resolve()
        graph = self._graph_tracking(str(tracked))
        payload = {"tool_input": {"file_path": str(tracked / "new.md")}}
        decision = plan_reindex(payload, graph)
        self.assertIsNotNone(decision)
        edited_dir, ingest_root = decision
        self.assertEqual(edited_dir, tracked)
        self.assertEqual(ingest_root, tracked)

    def test_handles_missing_tool_input(self):
        graph = self._graph_tracking("/notes")
        self.assertIsNone(plan_reindex({}, graph))
        self.assertIsNone(plan_reindex({"tool_input": {}}, graph))


if __name__ == "__main__":
    unittest.main()
