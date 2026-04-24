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

from curator_bootstrap import bootstrap_project_skeleton  # noqa: E402
from curator_bootstrap import (  # noqa: E402
    is_bootstrap_needed,
    mark_bootstrap_declined,
    record_bootstrap_result,
)


class BootstrapSnifferTests(unittest.TestCase):
    def _seed(self, tmp: str, *, readme: str | None, dirs: list[str]) -> Path:
        root = Path(tmp).resolve()
        if readme is not None:
            (root / "README.md").write_text(readme)
        for d in dirs:
            (root / d).mkdir(parents=True, exist_ok=True)
            (root / d / ".keep").write_text("")
        return root

    def test_returns_minimal_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme="# My Project\n\nA short tagline.\n", dirs=["src", "tests", "docs"])
            preview = bootstrap_project_skeleton(root)
            self.assertEqual(preview["projectTitle"], "My Project")
            self.assertIn("A short tagline", preview["tagline"])
            paths = sorted(d["path"] for d in preview["topLevelDirs"])
            self.assertEqual(paths, ["docs/", "src/", "tests/"])

    def test_uses_directory_name_when_no_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme=None, dirs=["src"])
            preview = bootstrap_project_skeleton(root)
            self.assertEqual(preview["projectTitle"], root.name)
            self.assertEqual(preview["tagline"], "")

    def test_excludes_known_noise_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(
                tmp, readme="# x\n",
                dirs=[".git", "node_modules", "dist", "build", "__pycache__", "src"],
            )
            preview = bootstrap_project_skeleton(root)
            paths = [d["path"] for d in preview["topLevelDirs"]]
            self.assertEqual(paths, ["src/"])

    def test_caps_dir_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = [f"d{i}" for i in range(50)]
            root = self._seed(tmp, readme="# x\n", dirs=dirs)
            preview = bootstrap_project_skeleton(root)
            self.assertLessEqual(len(preview["topLevelDirs"]), 30)

    def test_reads_package_json_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme=None, dirs=["src"])
            (root / "package.json").write_text(json.dumps(
                {"name": "my-pkg", "description": "JS lib for X"}
            ))
            preview = bootstrap_project_skeleton(root)
            # README absent — manifest provides title and tagline.
            self.assertEqual(preview["projectTitle"], "my-pkg")
            self.assertEqual(preview["tagline"], "JS lib for X")

    def test_readme_overrides_manifest_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme="# README Title\n", dirs=["src"])
            (root / "package.json").write_text(json.dumps(
                {"name": "manifest-name", "description": "ignored"}
            ))
            preview = bootstrap_project_skeleton(root)
            self.assertEqual(preview["projectTitle"], "README Title")

    def test_readme_truncated_to_200_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            long_readme = "# Project\n\n" + "filler line\n" * 5000
            root = self._seed(tmp, readme=long_readme, dirs=["src"])
            preview = bootstrap_project_skeleton(root)
            # Tagline is the first non-empty paragraph after the heading;
            # we just confirm the sniffer didn't choke on the size.
            self.assertEqual(preview["projectTitle"], "Project")


class BootstrapStateTests(unittest.TestCase):
    def _make_workspace(self, tmp: str) -> Path:
        from context_graph_core import init_workspace
        init_workspace({"rootPath": tmp})
        return Path(tmp).resolve()

    def test_is_bootstrap_needed_when_no_notion_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            self.assertTrue(is_bootstrap_needed(root))

    def test_not_needed_when_rootPageId_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            from context_graph_core import update_workspace_manifest
            root = self._make_workspace(tmp)
            update_workspace_manifest(root, {"notion": {"rootPageId": "abc"}})
            self.assertFalse(is_bootstrap_needed(root))

    def test_not_needed_when_declined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            mark_bootstrap_declined(root)
            self.assertFalse(is_bootstrap_needed(root))

    def test_not_needed_when_workspace_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_bootstrap_needed(Path(tmp)))

    def test_record_bootstrap_result_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            from context_graph_core import load_workspace_manifest
            root = self._make_workspace(tmp)
            record_bootstrap_result(
                root,
                root_page_id="rootP",
                root_page_url="https://notion/rootP",
                dir_page_ids={"src/": "p1", "tests/": "p2"},
            )
            manifest = load_workspace_manifest(root)
            self.assertEqual(manifest["notion"]["rootPageId"], "rootP")
            self.assertEqual(manifest["notion"]["rootPageUrl"], "https://notion/rootP")
            self.assertEqual(manifest["notion"]["dirPageIds"], {"src/": "p1", "tests/": "p2"})

    def test_mark_declined_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            from context_graph_core import load_workspace_manifest
            root = self._make_workspace(tmp)
            mark_bootstrap_declined(root)
            manifest = load_workspace_manifest(root)
            self.assertTrue(manifest["notion"]["bootstrapDeclined"])


class CLIBootstrapTests(unittest.TestCase):
    def test_dry_run_prints_preview(self):
        import io, sys, tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from context_graph_core import init_workspace
        # Re-import context_graph_cli main fresh:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from context_graph_cli import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            (Path(tmp) / "README.md").write_text("# Sample\n")
            (Path(tmp) / "src").mkdir()
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main(["bootstrap", "--dry-run", "--workspace-root", tmp])
            self.assertEqual(code, 0)
            output = json.loads(buf.getvalue())
            self.assertEqual(output["projectTitle"], "Sample")
            paths = [d["path"] for d in output["topLevelDirs"]]
            self.assertIn("src/", paths)


if __name__ == "__main__":
    unittest.main()
