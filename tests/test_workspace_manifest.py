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
    init_workspace,
    load_workspace_manifest,
    update_workspace_manifest,
)


class WorkspaceManifestHelperTests(unittest.TestCase):
    def test_load_returns_full_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            manifest = load_workspace_manifest(Path(tmp))
            self.assertEqual(manifest["version"], "1")
            self.assertIn("id", manifest)
            self.assertIn("createdAt", manifest)

    def test_load_raises_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_workspace_manifest(Path(tmp))

    def test_update_merges_top_level_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            update_workspace_manifest(
                Path(tmp),
                {"notion": {"rootPageId": "abc123", "dirPageIds": {"src/": "p1"}}},
            )
            manifest = load_workspace_manifest(Path(tmp))
            self.assertEqual(manifest["notion"]["rootPageId"], "abc123")
            self.assertEqual(manifest["notion"]["dirPageIds"], {"src/": "p1"})

    def test_update_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            original = load_workspace_manifest(Path(tmp))
            update_workspace_manifest(Path(tmp), {"notion": {"rootPageId": "x"}})
            after = load_workspace_manifest(Path(tmp))
            self.assertEqual(after["id"], original["id"])
            self.assertEqual(after["createdAt"], original["createdAt"])

    def test_update_bumps_updatedAt(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            before = load_workspace_manifest(Path(tmp))["updatedAt"]
            # Sleep just enough for ISO-8601 string to advance.
            # now_iso() rounds to whole seconds, so we need >1s here.
            import time; time.sleep(1.05)
            update_workspace_manifest(Path(tmp), {"notion": {"x": 1}})
            after = load_workspace_manifest(Path(tmp))["updatedAt"]
            self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
