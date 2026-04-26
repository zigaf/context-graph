from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from trigger_detect import (  # noqa: E402
    is_keyword_trigger,
    is_git_trigger,
    is_slash_trigger,
    main as trigger_main,
)


class KeywordTriggerTests(unittest.TestCase):
    def test_russian_keywords_match(self):
        for phrase in ("готово", "ship it", "merged", "закоммитим", "doc done"):
            self.assertTrue(
                is_keyword_trigger(phrase),
                f"expected match for {phrase!r}",
            )

    def test_unrelated_text_does_not_match(self):
        self.assertFalse(is_keyword_trigger("just some random sentence"))
        self.assertFalse(is_keyword_trigger(""))


class GitTriggerTests(unittest.TestCase):
    def test_git_commit_matches(self):
        self.assertTrue(is_git_trigger("git commit -m 'feat: x'"))
        self.assertTrue(is_git_trigger("git push origin main"))
        self.assertTrue(is_git_trigger("git merge feature/x"))
        self.assertTrue(is_git_trigger("git tag v0.1.0"))

    def test_non_git_bash_skipped(self):
        self.assertFalse(is_git_trigger("ls -la"))
        self.assertFalse(is_git_trigger("git status"))
        self.assertFalse(is_git_trigger(""))


class SlashTriggerTests(unittest.TestCase):
    def test_listed_slash_commands_match(self):
        self.assertTrue(is_slash_trigger("/commit"))
        self.assertTrue(is_slash_trigger("/create-pr"))
        self.assertTrue(is_slash_trigger("/ship"))
        self.assertTrue(is_slash_trigger("/pr-review"))

    def test_unlisted_slash_commands_skipped(self):
        self.assertFalse(is_slash_trigger("/cg-search"))
        self.assertFalse(is_slash_trigger("/help"))
        self.assertFalse(is_slash_trigger(""))


class WorkspaceGatingTests(unittest.TestCase):
    def test_no_workspace_means_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=tmp)
            self.assertEqual(exit_code, 0)
            self.assertEqual(buf.getvalue().strip(), "")

    def test_workspace_with_trigger_emits_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp).resolve()
            (ws / ".context-graph").mkdir(parents=True, exist_ok=True)
            (ws / ".context-graph" / "workspace.json").write_text(
                json.dumps({"version": "1", "id": "t", "rootPath": str(ws)}),
                encoding="utf-8",
            )
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=str(ws))
            self.assertEqual(exit_code, 0)
            output = buf.getvalue().strip()
            self.assertIn("Run /cg-sync-notion auto", output)


class AutoPushOptOutTests(unittest.TestCase):
    def test_disabled_workspace_skips_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp).resolve()
            (ws / ".context-graph").mkdir(parents=True, exist_ok=True)
            (ws / ".context-graph" / "workspace.json").write_text(
                json.dumps({
                    "version": "1",
                    "id": "t",
                    "rootPath": str(ws),
                    "autoPush": {"enabled": False},
                }),
                encoding="utf-8",
            )
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=str(ws))
            self.assertEqual(exit_code, 0)
            self.assertEqual(buf.getvalue().strip(), "")
