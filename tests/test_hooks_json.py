from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HooksJsonTests(unittest.TestCase):
    HOOKS_PATH = ROOT / "hooks.json"

    def setUp(self):
        self.data = json.loads(self.HOOKS_PATH.read_text(encoding="utf-8"))

    def test_existing_hooks_preserved(self):
        events = self.data["hooks"]
        self.assertIn("SessionStart", events)
        self.assertIn("PostToolUse", events)

    def test_user_prompt_submit_trigger_added(self):
        events = self.data["hooks"]
        self.assertIn("UserPromptSubmit", events)
        commands = [
            entry["command"]
            for matcher in events["UserPromptSubmit"]
            for entry in matcher.get("hooks", [])
        ]
        self.assertTrue(any("trigger_detect.py" in cmd for cmd in commands))

    def test_post_tool_use_bash_trigger_added(self):
        events = self.data["hooks"]
        # The new Bash hook must coexist with the existing Write|Edit hook.
        post_tool = events["PostToolUse"]
        matchers = [m["matcher"] for m in post_tool]
        self.assertIn("Write|Edit", matchers)
        self.assertIn("Bash", matchers)
        bash_entry = next(m for m in post_tool if m["matcher"] == "Bash")
        commands = [h["command"] for h in bash_entry["hooks"]]
        self.assertTrue(any("trigger_detect.py" in cmd for cmd in commands))

    def test_slash_command_trigger_added(self):
        events = self.data["hooks"]
        self.assertIn("SlashCommand", events)
        slash = events["SlashCommand"]
        matchers = [m["matcher"] for m in slash]
        self.assertTrue(any("commit" in m and "create-pr" in m for m in matchers))


if __name__ == "__main__":
    unittest.main()
