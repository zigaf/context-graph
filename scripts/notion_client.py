"""Minimal read-only Notion API v1 client (stdlib only).

This module provides a small HTTP client for Notion's public REST API. It
only implements the read-only surface needed for the Context Graph live
sync: listing database pages, listing child pages, fetching a single page,
and paging through block children.

Design notes:
- stdlib only (urllib + json); no third-party dependencies.
- No retries; callers decide how to handle transient failures.
- No stdout writes; the MCP server requires clean stdout.
- All non-2xx responses raise ``NotionAPIError`` with the status code and
  the raw response body for downstream diagnostics.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


NOTION_API_VERSION = "2022-06-28"
DEFAULT_BASE_URL = "https://api.notion.com/v1"


class NotionAPIError(Exception):
    """Raised when the Notion API returns a non-2xx response."""

    def __init__(self, status: int, body: str):
        self.status = int(status)
        self.body = body or ""
        super().__init__(f"Notion API error {self.status}: {self.body}")


class NotionClient:
    """Read-only HTTP client for the Notion v1 REST API."""

    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL):
        if token is None:
            raise ValueError("NotionClient requires a non-empty token.")
        resolved = token
        if isinstance(resolved, str) and resolved.strip().lower() == "env":
            resolved = os.environ.get("NOTION_TOKEN", "")
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("NotionClient requires a non-empty token.")
        self._token = resolved.strip()
        self._base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")

    # ----- internal helpers -------------------------------------------------

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self._base_url}{path}"
        if query:
            encoded = urllib_parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
            if encoded:
                url = f"{url}?{encoded}"
        return url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._build_url(path, query=query)
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        request = urllib_request.Request(
            url=url,
            data=data,
            method=method.upper(),
            headers=self._headers(),
        )
        try:
            with urllib_request.urlopen(request) as response:
                raw = response.read()
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if not (200 <= int(status) < 300):
                    raise NotionAPIError(int(status), raw.decode("utf-8", "replace"))
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except urllib_error.HTTPError as exc:
            try:
                body_text = exc.read().decode("utf-8", "replace")
            except Exception:
                body_text = ""
            raise NotionAPIError(int(exc.code), body_text) from None

    @staticmethod
    def _pagination(
        cursor: str | None, page_size: int, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"page_size": int(page_size)}
        if cursor:
            payload["start_cursor"] = cursor
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _envelope(response: dict[str, Any], items_key: str) -> dict[str, Any]:
        results = response.get("results") or []
        return {
            items_key: list(results),
            "next_cursor": response.get("next_cursor"),
            "has_more": bool(response.get("has_more", False)),
        }

    # ----- public API -------------------------------------------------------

    def list_database_pages(
        self,
        database_id: str,
        filter_: dict | None = None,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """Query a database and return the paginated page results."""
        extra: dict[str, Any] = {}
        if filter_ is not None:
            extra["filter"] = filter_
        body = self._pagination(cursor, page_size, extra)
        response = self._request(
            "POST",
            f"/databases/{database_id}/query",
            body=body,
        )
        return self._envelope(response, "pages")

    def list_child_pages(
        self,
        parent_page_id: str,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """Return direct child blocks of a page, filtered to ``child_page`` blocks."""
        response = self._request(
            "GET",
            f"/blocks/{parent_page_id}/children",
            query={
                "page_size": int(page_size),
                "start_cursor": cursor,
            },
        )
        results = [
            item
            for item in response.get("results", [])
            if isinstance(item, dict) and item.get("type") == "child_page"
        ]
        return {
            "pages": results,
            "next_cursor": response.get("next_cursor"),
            "has_more": bool(response.get("has_more", False)),
        }

    def get_page(self, page_id: str) -> dict:
        """Return raw page metadata."""
        return self._request("GET", f"/pages/{page_id}")

    def get_blocks(
        self,
        page_id: str,
        cursor: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """Return a page of block children in the shared envelope shape."""
        response = self._request(
            "GET",
            f"/blocks/{page_id}/children",
            query={
                "page_size": int(page_size),
                "start_cursor": cursor,
            },
        )
        return self._envelope(response, "blocks")

    # ----- push surface -----------------------------------------------------

    def create_page(
        self,
        parent_page_id: str,
        title: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a new child page under ``parent_page_id`` with ``blocks``.

        The ``properties.title`` shape is the one Notion expects for
        non-database (regular) pages — a single ``title`` property whose
        value is a list of rich-text runs. Callers using the push path must
        have a Notion root page id configured at the workspace level.
        """
        body: dict[str, Any] = {
            "parent": {"page_id": str(parent_page_id)},
            "properties": {
                "title": {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": str(title or "")},
                        }
                    ]
                }
            },
            "children": list(blocks or []),
        }
        return self._request("POST", "/pages", body=body)

    def update_page_blocks(
        self,
        page_id: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Replace a page's body with ``blocks``.

        "Replace" is deliberately simple: delete every existing child block
        (server-side archive), then append the new set. This is the safest
        correct behavior for the first cut — append-only would leak stale
        content, and a diff-based update needs a proper markdown AST. A
        future iteration can swap this for a diff or for the MCP
        ``update_content`` semantic that updates specific substrings.
        """
        existing = self.get_blocks(page_id).get("blocks", [])
        for block in existing:
            block_id = block.get("id") if isinstance(block, dict) else None
            if not block_id:
                continue
            try:
                self._request("DELETE", f"/blocks/{block_id}")
            except NotionAPIError:
                # One failing block deletion should not abort the update;
                # the append below still repopulates the page body.
                continue
        body = {"children": list(blocks or [])}
        return self._request("PATCH", f"/blocks/{page_id}/children", body=body)
