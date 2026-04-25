from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StartCommandSmokeTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-start.md"

    def test_command_file_exists(self):
        self.assertTrue(self.COMMAND_PATH.exists(), f"Missing command at {self.COMMAND_PATH}")

    def test_frontmatter_declares_command(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "Command must start with YAML frontmatter")
        end = text.find("\n---\n", 4)
        self.assertGreater(end, 0, "Frontmatter has no closing delimiter")
        front = text[4:end]
        self.assertIn("description:", front)
        self.assertIn("argument-hint:", front)

    def test_hybrid_sources_are_documented(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "Notion",
            "Local markdown",
            "Skip",
            "init_workspace",
            "ingest_markdown",
            "load_notion_cursor",
            "filter_pages_by_cursor",
            "save_notion_cursor",
        ):
            self.assertIn(phrase, text)

    def test_user_facing_summary_is_required(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "Context Graph is ready",
            "pages pulled",
            "pages skipped",
            "files processed",
            "/cg-search",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
