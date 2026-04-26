from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CgSyncNotionAutoModeTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-sync-notion.md"

    def test_auto_mode_is_documented(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "auto",
            ".context-graph/auto_push_plan.json",
            "prepare-auto-push",
            "apply-auto-push-result",
            "Auto-pushed to Notion",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
