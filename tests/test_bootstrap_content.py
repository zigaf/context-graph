from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bootstrap_content import (  # noqa: E402
    build_dir_paragraph,
    build_root_body,
)


class BuildDirParagraphTests(unittest.TestCase):
    def test_empty_dir_falls_back_to_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "bl-api"
            dir_path.mkdir()
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("bl-api/", paragraph)
            self.assertIn("Purpose:", paragraph)

    def test_dir_with_readme_paragraph_uses_first_paragraph(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "core"
            dir_path.mkdir()
            (dir_path / "README.md").write_text(
                "# Core\n\nThe core service of the platform.\n\nMore details below.\n",
                encoding="utf-8",
            )
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("The core service of the platform.", paragraph)

    def test_dir_with_package_json_lists_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "admin"
            dir_path.mkdir()
            (dir_path / "package.json").write_text(
                '{"name":"admin","dependencies":{"@angular/core":"16","rxjs":"7"}}',
                encoding="utf-8",
            )
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("Stack:", paragraph)
            self.assertIn("@angular/core", paragraph)

    def test_dir_with_files_lists_entry_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "scripts"
            dir_path.mkdir()
            for name in ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"):
                (dir_path / name).write_text("# noop\n", encoding="utf-8")
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("Entry points:", paragraph)
            self.assertEqual(paragraph.count(".py"), 5)


class BuildRootBodyTests(unittest.TestCase):
    def test_root_body_includes_tagline_and_dir_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "README.md").write_text(
                "# MyProject\n\nA distributed widget factory.\n",
                encoding="utf-8",
            )
            body = build_root_body(
                repo,
                project_title="MyProject",
                top_level_dirs=[
                    {"path": "admin/", "purpose": ""},
                    {"path": "core/", "purpose": ""},
                ],
            )
            self.assertIn("A distributed widget factory.", body)
            self.assertIn("admin/", body)
            self.assertIn("core/", body)
            self.assertIn("Indexes", body)


if __name__ == "__main__":
    unittest.main()
