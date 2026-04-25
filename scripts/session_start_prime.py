"""SessionStart hook entry point.

Enhances the previous tiny ``search_graph`` warmup with:
- Pulling rules/decisions/conventions from the local graph for the
  current scope (so Claude starts the session with the rule book in
  context).
- Reporting whether the workspace still needs Notion bootstrap.

The hook prints a small JSON payload to stdout. Claude Code's
SessionStart machinery surfaces hook stdout as system context for the
next turn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Resolve ``scripts/`` on sys.path when invoked outside the plugin context.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def prime_session(workspace_root: Path | None = None) -> dict[str, Any]:
    """Return a prime payload for the current session.

    When ``workspace_root`` is None, walks up from CWD. When no workspace
    is found, returns ``{"workspace": None}`` and stops.
    """
    from context_graph_core import (
        find_workspace_root,
        load_workspace_manifest,
        default_graph_path,
        search_graph,
    )
    from curator_bootstrap import is_bootstrap_needed

    start = Path(workspace_root) if workspace_root else None
    root = find_workspace_root(start)
    if root is None:
        return {"workspace": None}

    try:
        manifest = load_workspace_manifest(root)
    except FileNotFoundError:
        return {"workspace": None}

    # Pull all type=rule and type=decision records the local graph has —
    # these become the rule book Claude sees at session start. Conventions
    # are stored as type=rule + scope=convention per the curator skill, so
    # the type=rule pass already covers them.
    rules: list[dict[str, Any]] = []
    try:
        for marker_type in ("rule", "decision"):
            res = search_graph({
                "graphPath": str(default_graph_path(root)),
                "query": "",
                "markers": {"type": marker_type},
                "limit": 25,
            })
            for hit in res.get("directMatches", []) or []:
                rules.append({
                    "id": hit.get("id"),
                    "title": hit.get("title"),
                    "markers": hit.get("markers"),
                })
    except Exception:
        # Don't fail the hook on retrieval errors — a broken prime is
        # better than a broken session.
        rules = []

    notion_connected = bool((manifest.get("notion") or {}).get("rootPageId"))
    bootstrap_needed = is_bootstrap_needed(root)

    # Explicit instructions for the assistant. Claude Code surfaces hook
    # stdout as system context for the next turn, so these lines are the
    # first thing the assistant sees in a fresh session against this
    # workspace. They make the curator skill the default capture path —
    # before the assistant reaches for built-in memory.
    instructions = [
        "PROJECT MEMORY ACTIVE: this workspace has a Context Graph. "
        "When the user reveals project knowledge (rules, conventions, "
        "gotchas, decisions, module intersections, tasks, bug fixes), "
        "use the `context-graph-curator` skill INSTEAD OF the built-in "
        "memory tool. The curator captures structured records via "
        "`mcp__plugin_context-graph_context-graph__classify_record` + "
        "`index_records` and the records persist across sessions.",
        "To search captured knowledge use `mcp__plugin_context-graph_"
        "context-graph__search_graph` (filter by markers) or the "
        "`/cg-search #tag` slash command.",
    ]
    if notion_connected:
        instructions.append(
            "Notion is connected to this workspace. Captured rules and "
            "decisions auto-push as Notion pages under the matching "
            "directory subpage (`workspace.notion.dirPageIds`). The "
            "curator skill orchestrates `plan_notion_push` + "
            "`record_to_notion_payload` + `notion-create-pages` + "
            "`apply_notion_push_result`."
        )
    elif bootstrap_needed:
        instructions.append(
            "Notion is NOT connected for this workspace. To enable "
            "Notion sync, suggest the user run `/cg-bootstrap` (when a "
            "Notion MCP is available) or `/cg-sync-notion` to authorize "
            "Notion. Until then, capture knowledge locally only — the "
            "curator skill works against the local graph without Notion."
        )

    return {
        "workspace": manifest.get("id"),
        "rootPath": str(root),
        "rules": rules,
        "bootstrapNeeded": bootstrap_needed,
        "notionConnected": notion_connected,
        "instructions": instructions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="session-start-prime")
    parser.add_argument("--workspace-root", dest="workspace_root", default=None)
    args = parser.parse_args(argv)

    start = Path(args.workspace_root) if args.workspace_root else None
    payload = prime_session(workspace_root=start)
    json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
