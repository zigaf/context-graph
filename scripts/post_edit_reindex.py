#!/usr/bin/env python3
"""PostToolUse hook helper for Context Graph.

Only reindexes a markdown file's directory when the file lives under a
directory that has already been ingested into the graph. Prevents the
hook from sweeping unrelated `.md` files Claude happens to edit anywhere
on disk into the plugin's `data/graph.json`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


INGEST_SYSTEMS = {"markdown", "notion-export"}


def read_payload() -> dict[str, Any]:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def derive_ingest_root(source: dict[str, Any]) -> Path | None:
    if source.get("system") not in INGEST_SYSTEMS:
        return None
    url = source.get("url")
    path = source.get("path")
    if not url or not path:
        return None
    try:
        if Path(path).is_absolute():
            return None
        url_parts = Path(url).parts
        path_parts = Path(path).parts
        if len(path_parts) > len(url_parts):
            return None
        if url_parts[-len(path_parts):] != path_parts:
            return None
        root_parts = url_parts[: len(url_parts) - len(path_parts)]
        if not root_parts:
            return None
        return Path(*root_parts)
    except Exception:
        return None


def find_best_root(edited_dir: Path, graph: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    for record in (graph.get("records") or {}).values():
        root = derive_ingest_root(record.get("source") or {})
        if not root:
            continue
        try:
            resolved = root.resolve()
        except Exception:
            continue
        try:
            edited_dir.relative_to(resolved)
        except ValueError:
            continue
        candidates.append(resolved)
    if not candidates:
        return None
    return max(candidates, key=lambda p: len(p.parts))


def plan_reindex(
    payload: dict[str, Any], graph: dict[str, Any]
) -> tuple[Path, Path] | None:
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not isinstance(file_path, str) or not file_path.endswith(".md"):
        return None
    try:
        edited_dir = Path(os.path.abspath(file_path)).parent.resolve()
    except Exception:
        return None
    ingest_root = find_best_root(edited_dir, graph)
    if ingest_root is None:
        return None
    return edited_dir, ingest_root


def main() -> int:
    payload = read_payload()
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or os.getcwd())
    graph_path = plugin_root / "data" / "graph.json"
    if not graph_path.exists():
        return 0
    try:
        with graph_path.open("r", encoding="utf-8") as f:
            graph = json.load(f)
    except Exception:
        return 0

    decision = plan_reindex(payload, graph)
    if decision is None:
        return 0
    _, ingest_root = decision

    cli = plugin_root / "scripts" / "context_graph_cli.py"
    if not cli.exists():
        return 0
    try:
        subprocess.run(
            ["python3", str(cli), "ingest-markdown"],
            input=json.dumps(
                {"rootPath": str(ingest_root), "graphPath": str(graph_path)}
            ),
            text=True,
            capture_output=True,
            timeout=20,
            cwd=str(plugin_root),
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
