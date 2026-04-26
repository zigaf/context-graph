from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CgInitDocsTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-init.md"

    def test_init_documents_auto_push_hooks(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in ("hooks.json", "trigger_detect.py", "auto-push"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
