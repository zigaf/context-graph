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

    def test_step_6_uses_generated_bodies(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        # Each helper must appear at the generation block (1), at the
        # corresponding step 6 call site (2), and in the Refresh path (3),
        # plus the bash heredoc itself (4) — so at least 4 occurrences.
        # If step 6a/6c stop referencing the helpers we'd drop to 3.
        self.assertGreaterEqual(text.count("build_root_body"), 4)
        self.assertGreaterEqual(text.count("build_dir_paragraph"), 4)


if __name__ == "__main__":
    unittest.main()
