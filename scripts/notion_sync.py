from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from context_graph_core import (  # noqa: E402
    default_graph_path,
    index_records,
    notion_cursor_path,
)


def _default_cursor_path() -> Path:
    return notion_cursor_path()


def _normalize_notion_id(raw_id: str) -> str:
    return str(raw_id).replace("-", "").lower()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_cursor(cursor_path: Path) -> str | None:
    if not cursor_path.exists():
        return None
    try:
        with cursor_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, dict):
        cursor = data.get("cursor")
        return str(cursor) if cursor else None
    return None


def _write_cursor(cursor_path: Path, cursor_value: str) -> None:
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    with cursor_path.open("w", encoding="utf-8") as f:
        json.dump({"cursor": cursor_value}, f, ensure_ascii=True, indent=2)
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

    since = payload.get("since")
    stored_cursor = _read_cursor(cursor_path)
    effective_cursor_raw = since if since else stored_cursor
    cursor_dt = _parse_iso(effective_cursor_raw) if effective_cursor_raw else None

    client_factory = payload.get("clientFactory") or _lazy_default_client_factory()
    markdown_converter = payload.get("markdownConverter") or _lazy_default_markdown_converter()

    client = client_factory(token)

    all_pages = _collect_pages(client, database_id, parent_page_id)

    if cursor_dt is not None:
        filtered_pages: list[dict[str, Any]] = []
        for page in all_pages:
            page_dt = _parse_iso(page.get("last_edited_time"))
            if page_dt is None or page_dt > cursor_dt:
                filtered_pages.append(page)
    else:
        filtered_pages = list(all_pages)

    if not filtered_pages:
        return {
            "pagesPulled": 0,
            "recordIds": [],
            "newCursor": stored_cursor,
            "indexResult": None,
            "noChangesSince": True,
        }

    records: list[dict[str, Any]] = []
    latest_iso: str | None = None
    latest_dt: datetime | None = None

    for page in filtered_pages:
        page_id = str(page.get("id") or "")
        if not page_id:
            continue
        blocks = _collect_blocks(client, page_id)
        title, content, extra_metadata = markdown_converter(page, blocks)
        record = _build_record(page, title, content, extra_metadata)
        records.append(record)

        page_dt = _parse_iso(page.get("last_edited_time"))
        if page_dt is not None and (latest_dt is None or page_dt > latest_dt):
            latest_dt = page_dt
            latest_iso = str(page.get("last_edited_time"))

    if not records:
        return {
            "pagesPulled": 0,
            "recordIds": [],
            "newCursor": stored_cursor,
            "indexResult": None,
            "noChangesSince": True,
        }

    index_result: dict[str, Any] | None = None
    do_index = bool(payload.get("index", True))
    if do_index:
        index_result = index_records(
            {"graphPath": graph_path, "records": records},
            schema,
        )

    new_cursor_to_persist: str | None
    if latest_iso:
        new_cursor_to_persist = latest_iso
    elif latest_dt is not None:
        new_cursor_to_persist = latest_dt.astimezone(timezone.utc).isoformat()
    else:
        new_cursor_to_persist = None

    if new_cursor_to_persist:
        _write_cursor(cursor_path, new_cursor_to_persist)

    return {
        "pagesPulled": len(records),
        "recordIds": [record["id"] for record in records],
        "newCursor": new_cursor_to_persist,
        "indexResult": index_result,
        "noChangesSince": False,
    }
