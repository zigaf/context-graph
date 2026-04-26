"""Pure planner for Context Graph auto-push.

The planner reads the local push queue, the local graph, and the
workspace manifest, and returns a structured plan that the slash-command
layer (commands/cg-sync-notion.md auto-mode) executes against the
official Notion MCP.

This module never makes network calls. It produces JSON-serialisable
dicts only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_graph_core import (
    default_graph_path,
    list_pending_pushes,
    list_pushable_records,
    load_push_state,
    now_iso,
)


PUSHABLE_TYPES = {
    "rule",
    "decision",
    "gotcha",
    "module-boundary",
    "convention",
    "task",
    "bug",
    "bug-fix",
}


def _load_workspace_manifest(workspace_root: Path) -> dict[str, Any]:
    manifest_path = workspace_root / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_parent_page_id(
    record: dict[str, Any],
    notion_config: dict[str, Any],
) -> str | None:
    dir_pages: dict[str, str] = notion_config.get("dirPageIds") or {}
    root_id: str | None = notion_config.get("rootPageId")
    markers = record.get("markers") or {}
    notion_dir_override = markers.get("notionDir")
    if notion_dir_override:
        if notion_dir_override in dir_pages:
            return dir_pages[notion_dir_override]
        # Marker normalisation may strip trailing slashes; the dirPageIds
        # map keys typically include them. Try the slashed variant too.
        if (notion_dir_override + "/") in dir_pages:
            return dir_pages[notion_dir_override + "/"]
    parent = ((record.get("source") or {}).get("metadata") or {}).get("parent") or ""
    segments = [seg.strip() for seg in parent.split(">") if seg.strip()]
    for segment in reversed(segments):
        if segment in dir_pages:
            return dir_pages[segment]
        if (segment + "/") in dir_pages:
            return dir_pages[segment + "/"]
    return root_id


def _record_revision(record: dict[str, Any]) -> int | None:
    revision = record.get("revision")
    if isinstance(revision, dict) and isinstance(revision.get("version"), int):
        return int(revision["version"])
    return None


def _is_pushable(record: dict[str, Any]) -> tuple[bool, str | None]:
    markers = record.get("markers") or {}
    record_type = markers.get("type")
    if record_type not in PUSHABLE_TYPES:
        return False, "non-pushable-type"
    classifier_notes = (
        ((record.get("source") or {}).get("metadata") or {})
        .get("classifierNotes")
        or {}
    )
    if classifier_notes.get("arbiter") == "pending-arbitration":
        return False, "pending-arbitration"
    return True, None


def build_plan(*, workspace_root: Path | str) -> dict[str, Any]:
    """Build an auto-push plan for the workspace's pending queue."""
    ws = Path(str(workspace_root))
    manifest = _load_workspace_manifest(ws)
    notion_config = manifest.get("notion") or {}
    if not notion_config.get("rootPageId"):
        return {
            "blocked": True,
            "reason": "no-notion-root",
            "creates": [],
            "updates": [],
            "skipped": {},
            "generatedAt": now_iso(),
        }
    pending_ids = list_pending_pushes(workspace_root=ws)
    if not pending_ids:
        return {
            "blocked": False,
            "creates": [],
            "updates": [],
            "skipped": {},
            "generatedAt": now_iso(),
        }
    graph_path = str(default_graph_path(ws))
    pending_set = set(pending_ids)
    candidate_records = [
        record
        for record in list_pushable_records(graph_path, record_ids=pending_ids)
        if record.get("id") in pending_set
    ]
    by_id = {str(rec.get("id")): rec for rec in candidate_records}
    push_state = load_push_state(ws)
    state_records = push_state.get("records") or {}
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    for record_id in pending_ids:
        record = by_id.get(record_id)
        if record is None:
            skipped[record_id] = "missing-from-graph"
            continue
        ok, reason = _is_pushable(record)
        if not ok:
            skipped[record_id] = reason or "not-pushable"
            continue
        parent_page_id = _resolve_parent_page_id(record, notion_config)
        if not parent_page_id:
            skipped[record_id] = "no-parent-resolved"
            continue
        revision = _record_revision(record)
        existing = state_records.get(record_id) or {}
        if existing.get("notionPageId"):
            last_pushed = existing.get("lastPushedRevision")
            if (
                revision is not None
                and last_pushed is not None
                and revision <= int(last_pushed)
            ):
                skipped[record_id] = "no-revision-change"
                continue
            updates.append({
                "recordId": record_id,
                "notionPageId": existing["notionPageId"],
                "revision": revision,
            })
        else:
            creates.append({
                "recordId": record_id,
                "parentPageId": parent_page_id,
                "revision": revision,
            })
    return {
        "blocked": False,
        "creates": creates,
        "updates": updates,
        "skipped": skipped,
        "generatedAt": now_iso(),
    }
