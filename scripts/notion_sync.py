from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from context_graph_core import (  # noqa: E402
    classify_record,
    cursor_is_fresh,
    default_graph_path,
    index_records,
    notion_cursor_path,
    update_cursor,
)


def _default_cursor_path() -> Path:
    return notion_cursor_path()


def _normalize_notion_id(raw_id: str) -> str:
    return str(raw_id).replace("-", "").lower()


def _read_cursor_file(cursor_path: Path) -> dict[str, Any]:
    """Read the per-page cursor dict from an explicit file path.

    The in-core helpers (`load_notion_cursor`) resolve paths from a workspace
    root, but the Python fallback also accepts an arbitrary `cursorPath`
    override (used by tests and by scheduled-job callers who don't live inside
    a workspace). This helper handles that explicit-path case using the same
    on-disk contract as `load_notion_cursor`.
    """
    if not cursor_path.exists():
        return {}
    try:
        with cursor_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_cursor_file(cursor_path: Path, cursor: dict[str, Any]) -> None:
    """Persist the per-page cursor dict to an explicit file path.

    Parallels `save_notion_cursor` for the arbitrary-path override case.
    """
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    with cursor_path.open("w", encoding="utf-8") as f:
        json.dump(cursor, f, ensure_ascii=True, indent=2)
        f.write("\n")


def _lazy_default_client_factory() -> Callable[[str], Any]:
    def factory(token: str) -> Any:
        from notion_client import NotionClient  # type: ignore  # noqa: WPS433

        return NotionClient(token=token)

    return factory


def _lazy_default_markdown_converter() -> Callable[[dict[str, Any], list[dict[str, Any]]], tuple[str, str, dict[str, Any]]]:
    def converter(page: dict[str, Any], blocks: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
        from notion_markdown import page_to_markdown  # type: ignore  # noqa: WPS433

        return page_to_markdown(page, blocks)

    return converter


def _collect_pages(
    client: Any,
    database_id: str | None,
    parent_page_id: str | None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        if database_id:
            response = client.list_database_pages(database_id, cursor=cursor)
        else:
            response = client.list_child_pages(parent_page_id, cursor=cursor)
        pages.extend(response.get("pages", []) or [])
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            break
    return pages


# Block types whose rendered output depends on nested children
# (table rows, toggle body, callout body, column layout). For these we
# recursively fetch and attach the children under ``_children`` so the
# markdown converter can render the full tree without further API
# access.
_CHILD_BEARING_BLOCK_TYPES = {
    "table",
    "toggle",
    "callout",
    "column_list",
    "column",
}


def _fetch_block_page(
    client: Any, block_id: str, cursor: str | None
) -> dict[str, Any]:
    # ``get_blocks`` is the shared envelope from ``notion_client``. The
    # same endpoint (``/blocks/{id}/children``) serves both page
    # top-level children and nested block children, so we reuse it here.
    return client.get_blocks(block_id, cursor=cursor)


def _collect_block_children(client: Any, block_id: str) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        response = _fetch_block_page(client, block_id, cursor)
        children.extend(response.get("blocks", []) or [])
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            break
    for child in children:
        _maybe_hydrate_children(client, child)
    return children


def _maybe_hydrate_children(client: Any, block: dict[str, Any]) -> None:
    if not isinstance(block, dict):
        return
    block_type = block.get("type")
    if block_type not in _CHILD_BEARING_BLOCK_TYPES:
        return
    if not block.get("has_children", False):
        # Notion marks blocks that actually have children. Trust the
        # flag to avoid an extra request for empty tables/toggles.
        block.setdefault("_children", [])
        return
    block_id = block.get("id")
    if not block_id:
        block.setdefault("_children", [])
        return
    block["_children"] = _collect_block_children(client, str(block_id))


def _collect_blocks(client: Any, page_id: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        response = client.get_blocks(page_id, cursor=cursor)
        blocks.extend(response.get("blocks", []) or [])
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            break
    for block in blocks:
        _maybe_hydrate_children(client, block)
    return blocks


def _build_record(
    page: dict[str, Any],
    title: str,
    content: str,
    extra_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_page_id = str(page.get("id") or "")
    normalized_id = _normalize_notion_id(raw_page_id)
    record_id = f"notion:{normalized_id}"

    metadata: dict[str, Any] = {
        "notionPageId": normalized_id,
        "last_edited_time": page.get("last_edited_time"),
        "created_time": page.get("created_time"),
        "parent": page.get("parent"),
    }
    if extra_metadata:
        for key, value in extra_metadata.items():
            if key not in metadata or metadata.get(key) is None:
                metadata[key] = value

    return {
        "id": record_id,
        "title": title,
        "content": content,
        "source": {
            "system": "notion",
            "url": page.get("url"),
            "metadata": metadata,
        },
        "revision": {
            "updatedAt": page.get("last_edited_time"),
        },
    }


def _apply_since_floor(cursor: dict[str, Any], pages: list[dict[str, Any]], since: str) -> dict[str, Any]:
    """Merge a `since` floor into the cursor.

    When the caller passes an explicit `since` (ISO-8601), every page listed is
    treated as "last seen at >= since" — so `cursor_is_fresh` will only keep
    pages whose `last_edited_time` is strictly greater than `since`. This
    preserves the legacy global-cursor semantics on top of the per-page store.
    """
    merged = dict(cursor)
    since_str = str(since)
    for page in pages:
        page_id = page.get("id")
        if not page_id:
            continue
        key = str(page_id)
        existing = merged.get(key, "")
        if since_str > str(existing or ""):
            merged[key] = since_str
    return merged


def sync_notion(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})

    token_input = payload.get("token", "env")
    token = str(token_input or "").strip()
    if token in ("", "env"):
        token = os.environ.get("NOTION_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "NOTION_TOKEN is not set. Provide `token` in the payload or export NOTION_TOKEN."
        )

    database_id = payload.get("databaseId")
    parent_page_id = payload.get("parentPageId")
    if bool(database_id) == bool(parent_page_id):
        raise ValueError("sync_notion requires exactly one of databaseId or parentPageId.")

    graph_path_input = payload.get("graphPath")
    graph_path = str(graph_path_input) if graph_path_input else str(default_graph_path())

    cursor_path_input = payload.get("cursorPath")
    cursor_path = Path(str(cursor_path_input)) if cursor_path_input else _default_cursor_path()

    stored_cursor = _read_cursor_file(cursor_path)

    client_factory = payload.get("clientFactory") or _lazy_default_client_factory()
    markdown_converter = payload.get("markdownConverter") or _lazy_default_markdown_converter()

    client = client_factory(token)

    all_pages = _collect_pages(client, database_id, parent_page_id)

    # `since` is a legacy global floor that overrides the per-page cursor for
    # this run. Merge it into an effective cursor so the pure `cursor_is_fresh`
    # helper can enforce the same semantics uniformly.
    since = payload.get("since")
    effective_cursor = (
        _apply_since_floor(stored_cursor, all_pages, str(since)) if since else stored_cursor
    )

    filtered_pages = [page for page in all_pages if cursor_is_fresh(page, effective_cursor)]

    if not filtered_pages:
        return {
            "pagesPulled": 0,
            "recordIds": [],
            "newCursor": None,
            "indexResult": None,
            "noChangesSince": True,
            "fallbackCount": 0,
        }

    records: list[dict[str, Any]] = []
    advanced_cursor = dict(stored_cursor)
    latest_iso: str | None = None

    for page in filtered_pages:
        page_id = str(page.get("id") or "")
        if not page_id:
            continue
        blocks = _collect_blocks(client, page_id)
        title, content, extra_metadata = markdown_converter(page, blocks)
        record = _build_record(page, title, content, extra_metadata)
        records.append(record)

        advanced_cursor = update_cursor(advanced_cursor, page)
        last_edited = page.get("last_edited_time")
        if isinstance(last_edited, str) and (latest_iso is None or last_edited > latest_iso):
            latest_iso = last_edited

    if not records:
        return {
            "pagesPulled": 0,
            "recordIds": [],
            "newCursor": None,
            "indexResult": None,
            "noChangesSince": True,
            "fallbackCount": 0,
        }

    finalized_records: list[dict[str, Any]] = []
    fallback_count = 0
    for raw_record in records:
        classified = classify_record(
            {"record": raw_record, "workspaceRoot": payload.get("workspaceRoot")},
            schema,
        )
        metadata = classified.setdefault("source", {}).setdefault("metadata", {})
        notes = metadata.get("classifierNotes") if isinstance(metadata.get("classifierNotes"), dict) else {}
        if notes.get("arbiter") == "pending-arbitration":
            notes["arbiter"] = "fallback"
            notes["reasoning"] = "Headless sync cannot use in-session arbitration."
            fallback_count += 1
        finalized_records.append(classified)
    records = finalized_records

    index_result: dict[str, Any] | None = None
    do_index = bool(payload.get("index", True))
    if do_index:
        index_result = index_records(
            {
                "graphPath": graph_path,
                "records": records,
                "workspaceRoot": payload.get("workspaceRoot"),
            },
            schema,
        )

    if advanced_cursor != stored_cursor:
        _write_cursor_file(cursor_path, advanced_cursor)

    return {
        "pagesPulled": len(records),
        "recordIds": [record["id"] for record in records],
        "newCursor": latest_iso,
        "indexResult": index_result,
        "noChangesSince": False,
        "fallbackCount": fallback_count,
    }
