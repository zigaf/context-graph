"""Hook entry script for Context Graph auto-push triggers.

Reads a JSON event payload from stdin (Claude Code hook contract),
decides whether the event qualifies as a session-end trigger
(keyword phrase, git operation, or listed slash command), confirms a
workspace exists, and emits an instruction line on stdout that tells
Claude to run ``/cg-sync-notion auto``.

This script never makes Notion API calls. The slash-command layer does.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


KEYWORDS_RU = (
    "готово", "закоммить", "закоммитим", "закругляемся",
    "закрываем задачу", "закрыли", "запушил", "запуш",
    "задеплоил", "деплой", "доделал", "доделали",
    "закончил", "закончили", "мержим", "замержил",
    "завершил", "работа сделана", "шипим", "ок все",
    "готово к мёрджу",
)

KEYWORDS_EN = (
    "ship", "ship it", "shipped", "merge", "merging", "merged",
    "commit this", "committed", "done", "we're done", "all done",
    "task complete", "completed", "wrap up", "wrapped",
    "closing this out", "pushed", "deployed", "pr is up", "pr opened",
    "lgtm", "that's it", "and we're done", "all set",
)

GIT_VERBS = ("git commit", "git push", "git merge", "git tag")
SLASH_COMMANDS = ("/commit", "/create-pr", "/ship", "/pr-review")


def is_keyword_trigger(text: str) -> bool:
    if not text:
        return False
    haystack = text.lower()
    for needle in KEYWORDS_RU + KEYWORDS_EN:
        if needle.lower() in haystack:
            return True
    return False


def is_git_trigger(command: str) -> bool:
    if not command:
        return False
    stripped = command.strip()
    return any(stripped.startswith(verb) for verb in GIT_VERBS)


def is_slash_trigger(name: str) -> bool:
    if not name:
        return False
    stripped = name.strip()
    return stripped in SLASH_COMMANDS


def _walk_up_for_workspace(start: Path) -> Path | None:
    current = start.resolve()
    while True:
        if (current / ".context-graph" / "workspace.json").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _read_event() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _trigger_text_for_source(source: str, event: dict) -> str:
    if source == "keyword":
        return event.get("text") or event.get("prompt") or ""
    if source == "git":
        tool_input = event.get("toolInput") or {}
        return tool_input.get("command") or event.get("command") or ""
    if source == "slash":
        return event.get("name") or event.get("command") or ""
    return ""


def _is_trigger(source: str, text: str) -> bool:
    if source == "keyword":
        return is_keyword_trigger(text)
    if source == "git":
        return is_git_trigger(text)
    if source == "slash":
        return is_slash_trigger(text)
    return False


def _is_auto_push_enabled(workspace: Path) -> bool:
    manifest_path = workspace / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        return True
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    flag = (data.get("autoPush") or {}).get("enabled")
    if flag is False:
        return False
    return True


def _run_prepare(workspace: Path) -> bool:
    cli = Path(__file__).resolve().parent / "context_graph_cli.py"
    proc = subprocess.run(
        ["python3", str(cli), "prepare-auto-push"],
        input="{}",
        capture_output=True,
        text=True,
        cwd=str(workspace),
        check=False,
    )
    return proc.returncode == 0


def main(argv: Iterable[str] | None = None, *, cwd: str | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=("keyword", "git", "slash"),
        required=True,
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    event = _read_event()
    text = _trigger_text_for_source(args.source, event)
    if not _is_trigger(args.source, text):
        return 0
    start = Path(cwd) if cwd else Path.cwd()
    workspace = _walk_up_for_workspace(start)
    if workspace is None:
        return 0
    if not _is_auto_push_enabled(workspace):
        return 0
    ok = _run_prepare(workspace)
    if not ok:
        return 0
    sys.stdout.write(
        "Auto-push trigger fired. Run /cg-sync-notion auto to drain "
        ".context-graph/auto_push_plan.json.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
