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
        # Step 6a must reference the build_root_body output as the page body.
        # Heuristic: the doc must mention the helper name in the same sentence
        # as the create-pages call, OR explicitly mention "body =" plus the
        # helper. We just check both helpers appear at least twice — once in
        # the generation block, once at the call site.
        self.assertGreaterEqual(text.count("build_root_body"), 2)
        self.assertGreaterEqual(text.count("build_dir_paragraph"), 2)


if __name__ == "__main__":
    unittest.main()
