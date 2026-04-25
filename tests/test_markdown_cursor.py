from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from context_graph_core import (  # noqa: E402
    ingest_markdown,
    init_workspace,
    load_markdown_cursor,
    markdown_cursor_path,
    save_markdown_cursor,
)


SAMPLE_NOTE = """---
type: bug
domain: payments
status: in-progress
---

# Webhook race in deposit flow

Duplicate payment creation after callback retry.
"""


class MarkdownCursorTests(unittest.TestCase):
    def test_load_returns_empty_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            self.assertEqual(load_markdown_cursor(tmp), {})

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            cursor = {"/abs/path/a.md": 1234567890.5}
            save_markdown_cursor(cursor, tmp)
            self.assertEqual(load_markdown_cursor(tmp), cursor)
            self.assertTrue(markdown_cursor_path(Path(tmp)).exists())

    def test_ingest_markdown_without_cursor_processes_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            notes = Path(tmp) / "notes"
            notes.mkdir()
            init_workspace({"rootPath": str(workspace)})
            (notes / "one.md").write_text(SAMPLE_NOTE, encoding="utf-8")
            (notes / "two.md").write_text(SAMPLE_NOTE, encoding="utf-8")

            result = ingest_markdown(
                {
                    "rootPath": str(notes),
                    "recursive": True,
                    "index": False,
                }
            )

            self.assertEqual(result["fileCount"], 2)
            self.assertNotIn("skippedFileCount", result)
            self.assertNotIn("cursor", result)

    def test_ingest_markdown_with_cursor_skips_unchanged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            notes = Path(tmp) / "notes"
            notes.mkdir()
            init_workspace({"rootPath": str(workspace)})
            file_a = notes / "a.md"
            file_b = notes / "b.md"
            file_a.write_text(SAMPLE_NOTE, encoding="utf-8")
            file_b.write_text(SAMPLE_NOTE, encoding="utf-8")

            first = ingest_markdown(
                {
                    "rootPath": str(notes),
                    "recursive": True,
                    "index": False,
                    "cursor": {},
                }
            )

            self.assertEqual(first["fileCount"], 2)
            self.assertEqual(first["skippedFileCount"], 0)
            cursor_after_first = first["cursor"]
            self.assertIn(str(file_a.resolve()), cursor_after_first)
            self.assertIn(str(file_b.resolve()), cursor_after_first)

            time.sleep(0.05)
            file_b.write_text(SAMPLE_NOTE + "\nUpdated.\n", encoding="utf-8")
            new_b_mtime = file_b.stat().st_mtime
            self.assertGreater(new_b_mtime, cursor_after_first[str(file_b.resolve())])

            second = ingest_markdown(
                {
                    "rootPath": str(notes),
                    "recursive": True,
                    "index": False,
                    "cursor": cursor_after_first,
                }
            )

            self.assertEqual(second["fileCount"], 1)
            self.assertEqual(second["skippedFileCount"], 1)
            self.assertEqual(second["skippedFiles"], [str(file_a.resolve())])
            self.assertEqual(
                second["cursor"][str(file_b.resolve())],
                new_b_mtime,
            )
            self.assertEqual(
                second["cursor"][str(file_a.resolve())],
                cursor_after_first[str(file_a.resolve())],
            )

    def test_ingest_markdown_cursor_param_must_be_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "notes"
            notes.mkdir()
            (notes / "x.md").write_text(SAMPLE_NOTE, encoding="utf-8")

            with self.assertRaises(ValueError):
                ingest_markdown(
                    {
                        "rootPath": str(notes),
                        "recursive": True,
                        "index": False,
                        "cursor": "not a dict",
                    }
                )


if __name__ == "__main__":
    unittest.main()
