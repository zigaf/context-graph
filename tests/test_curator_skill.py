from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class CuratorSkillSmokeTests(unittest.TestCase):
    SKILL_PATH = ROOT / "skills" / "context-graph-curator" / "SKILL.md"

    def test_skill_file_exists(self):
        self.assertTrue(self.SKILL_PATH.exists(), f"Missing skill at {self.SKILL_PATH}")

    def test_frontmatter_present(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "Skill must start with YAML frontmatter")
        # Frontmatter ends at the second '---'
        end = text.find("\n---\n", 4)
        self.assertGreater(end, 0, "Frontmatter has no closing delimiter")
        front = text[4:end]
        self.assertIn("name: context-graph-curator", front)
        self.assertIn("description:", front)

    def test_signal_table_present(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        # The signal table must mention each of the seven signal types so
        # Claude has an explicit decision tree.
        for signal in ("Rule", "Gotcha", "Decision", "Module boundary",
                       "Convention", "Task", "Bug fix"):
            self.assertIn(signal, text, f"Signal '{signal}' missing from skill table")

    def test_marker_axes_referenced(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        # Every axis the skill instructs Claude to set must be a real axis
        # in the schema. Spot-check the common ones.
        for axis in ("type", "scope", "domain", "artifact", "status"):
            self.assertIn(axis, text)

    def test_mcp_tool_calls_referenced(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        for tool in ("classify_record", "index_records", "plan_notion_push",
                     "apply_notion_push_result", "record_to_notion_payload"):
            self.assertIn(tool, text, f"Skill must mention {tool}")


if __name__ == "__main__":
    unittest.main()
