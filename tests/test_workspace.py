from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    WorkspaceNotInitializedError,
    find_workspace_root,
    init_workspace,
    require_workspace,
)


class FindWorkspaceRootTests(unittest.TestCase):
    def test_finds_workspace_from_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".context-graph").mkdir()
            (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
            self.assertEqual(find_workspace_root(root), root)

    def test_finds_workspace_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".context-graph").mkdir()
            (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
            sub = root / "src" / "nested"
            sub.mkdir(parents=True)
            self.assertEqual(find_workspace_root(sub), root)

    def test_returns_none_when_no_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_workspace_root(Path(tmp)))

    def test_require_workspace_raises_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkspaceNotInitializedError):
                require_workspace(Path(tmp))

    def test_require_workspace_returns_root_when_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".context-graph").mkdir()
            (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
            self.assertEqual(require_workspace(root), root)


from context_graph_core import (  # noqa: E402
    default_graph_path,
    idf_stats_path,
    notion_cursor_path,
    schema_feedback_path,
    schema_learned_path,
    schema_overlay_path,
)


class PathResolverTests(unittest.TestCase):
    def _make_workspace(self, tmp: str) -> Path:
        root = Path(tmp).resolve()
        (root / ".context-graph").mkdir()
        (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
        return root

    def test_default_graph_path_under_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            self.assertEqual(
                default_graph_path(root), root / ".context-graph" / "graph.json"
            )

    def test_all_resolvers_point_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            cg = root / ".context-graph"
            self.assertEqual(schema_learned_path(root),  cg / "schema.learned.json")
            self.assertEqual(schema_overlay_path(root),  cg / "schema.overlay.json")
            self.assertEqual(schema_feedback_path(root), cg / "schema.feedback.json")
            self.assertEqual(idf_stats_path(root),       cg / "idf_stats.json")
            self.assertEqual(notion_cursor_path(root),   cg / "notion_cursor.json")

    def test_legacy_env_var_keeps_plugin_data(self):
        # When CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1 AND no workspace, fall back
        # to plugin-local data/graph.json (for the plugin's own test env).
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CONTEXT_GRAPH_LEGACY_PLUGIN_DATA"] = "1"
            try:
                path = default_graph_path(start=Path(tmp))
                self.assertTrue(path.name == "graph.json")
                self.assertIn("data", path.parts)
            finally:
                os.environ.pop("CONTEXT_GRAPH_LEGACY_PLUGIN_DATA", None)


class InitWorkspaceTests(unittest.TestCase):
    def test_initializes_workspace_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result = init_workspace({"rootPath": str(root), "notionRootPageId": None})

            self.assertEqual(result["rootPath"], str(root))
            self.assertTrue((root / ".context-graph" / "workspace.json").exists())
            manifest = json.loads((root / ".context-graph" / "workspace.json").read_text())
            self.assertEqual(manifest["version"], "1")
            self.assertEqual(manifest["rootPath"], str(root))
            self.assertIsNotNone(manifest["id"])
            self.assertIn("createdAt", manifest)

    def test_refuses_if_already_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})

            with self.assertRaises(ValueError) as ctx:
                init_workspace({"rootPath": str(root)})

            self.assertIn("already initialized", str(ctx.exception).lower())

    def test_appends_to_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            gi = root / ".gitignore"
            gi.write_text("*.pyc\n", encoding="utf-8")

            init_workspace({"rootPath": str(root)})

            text = gi.read_text(encoding="utf-8")
            self.assertIn(".context-graph/graph.json", text)
            self.assertIn(".context-graph/schema.learned.json", text)
            self.assertIn(".context-graph/schema.feedback.json", text)
            self.assertIn(".context-graph/idf_stats.json", text)
            self.assertIn(".context-graph/notion_cursor.json", text)

    def test_creates_gitignore_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            self.assertTrue((root / ".gitignore").exists())

    def test_stores_notion_metadata_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            page_id = "34a37bbb09ff81839b2ae100879d1089"
            page_url = "https://www.notion.so/Myapp-34a37bbb09ff81839b2ae100879d1089"

            init_workspace(
                {
                    "rootPath": str(root),
                    "notionRootPageId": page_id,
                    "notionRootPageUrl": page_url,
                }
            )

            manifest = json.loads((root / ".context-graph" / "workspace.json").read_text())
            self.assertEqual(manifest["notion"]["rootPageId"], page_id)
            self.assertEqual(manifest["notion"]["rootPageUrl"], page_url)


if __name__ == "__main__":
    unittest.main()
