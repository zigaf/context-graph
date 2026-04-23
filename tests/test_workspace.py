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


if __name__ == "__main__":
    unittest.main()
