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
            "load_markdown_cursor",
            "save_markdown_cursor",
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

    def test_notion_first_batch_cap_is_precise(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Continue beyond the first 50", text)
        self.assertNotIn("the first 50 matching pages", text)
        self.assertIn("Page size: 25", text)
        self.assertIn("hard cap for the first batch is 25 pages", text)
        self.assertIn("Only the first 25 matching pages were considered.", text)

    def test_markdown_path_runs_arbitration_before_indexing(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        self.assertIn('"index": false', text)
        self.assertIn("pending-arbitration", text)
        self.assertIn("index_records", text)

    def test_already_initialized_workspace_is_not_fatal(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        self.assertIn("alreadyExists", text)


if __name__ == "__main__":
    unittest.main()
