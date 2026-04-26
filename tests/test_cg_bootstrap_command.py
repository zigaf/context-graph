from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CgBootstrapDocumentationTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-bootstrap.md"

    def test_command_calls_bootstrap_content_generator(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "build_dir_paragraph",
            "build_root_body",
            "scripts/bootstrap_content.py",
            "--refresh",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
