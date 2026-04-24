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

    # Pull all type=rule, type=decision, type=convention records the local
    # graph has — these become the rule book Claude sees at session start.
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

    return {
        "workspace": manifest.get("id"),
        "rootPath": str(root),
        "rules": rules,
        "bootstrapNeeded": is_bootstrap_needed(root),
        "notionConnected": bool((manifest.get("notion") or {}).get("rootPageId")),
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
