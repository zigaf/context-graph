#!/usr/bin/env python3
"""Manual smoke-test for live Notion sync.

Usage:
  export NOTION_TOKEN=secret_xxxxxx
  python3 scripts/smoke_notion.py --database <database_id>
  # or
  python3 scripts/smoke_notion.py --parent <page_id>

The script uses a temporary graph path so your real data/graph.json is
not touched. It runs three checks:
  1. Raw NotionClient can list pages (auth + access).
  2. sync_notion pulls and indexes the pages.
  3. A second sync_notion call is a no-op (delta cursor works).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def log_step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")
    print("-" * (4 + len(title)))


def log_ok(msg: str) -> None:
    print(f"  ok   {msg}")


def log_info(msg: str) -> None:
    print(f"       {msg}")


def log_fail(msg: str) -> None:
    print(f"  FAIL {msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke-test for Notion sync.")
    parser.add_argument("--database", help="Notion database id")
    parser.add_argument("--parent", help="Notion parent page id")
    parser.add_argument("--max-pages", type=int, default=5, help="Soft cap for readable output")
    args = parser.parse_args()

    if not args.database and not args.parent:
        parser.error("Provide --database or --parent.")
    if args.database and args.parent:
        parser.error("Provide either --database or --parent, not both.")

    token = os.environ.get("NOTION_TOKEN")
    if not token:
        print("error: NOTION_TOKEN env var is not set.", file=sys.stderr)
        return 2

    from notion_client import NotionAPIError, NotionClient
    from notion_sync import sync_notion

    log_step(1, "Raw Notion API reach")
    client = NotionClient(token)
    try:
        if args.database:
            raw = client.list_database_pages(args.database, page_size=args.max_pages)
        else:
            raw = client.list_child_pages(args.parent, page_size=args.max_pages)
    except NotionAPIError as exc:
        status, body = exc.args[0], exc.args[1] if len(exc.args) > 1 else ""
        log_fail(f"NotionAPIError status={status}")
        log_info(str(body)[:200])
        log_info("common fixes: invalid token, integration not added to the resource, wrong id")
        return 1
    pages = raw.get("pages") or []
    log_ok(f"fetched {len(pages)} page(s); has_more={raw.get('has_more')}")
    for page in pages[:3]:
        log_info(f"id={page.get('id')} edited={page.get('last_edited_time')}")
    if not pages:
        log_fail("no pages returned — check the id and that the integration has access")
        return 1

    log_step(2, "First sync_notion call")
    with tempfile.TemporaryDirectory() as tmpdir:
        graph_path = str(Path(tmpdir) / "graph.json")
        cursor_path = str(Path(tmpdir) / "notion_cursor.json")
        payload = {
            "token": token,
            "graphPath": graph_path,
            "cursorPath": cursor_path,
            "index": True,
        }
        if args.database:
            payload["databaseId"] = args.database
        else:
            payload["parentPageId"] = args.parent
        try:
            result = sync_notion(payload)
        except Exception as exc:
            log_fail(f"sync_notion raised {type(exc).__name__}: {exc}")
            return 1
        log_ok(f"pagesPulled={result.get('pagesPulled')}")
        log_info(f"newCursor={result.get('newCursor')}")
        record_ids = result.get("recordIds") or []
        log_info(f"recordIds (first 3): {record_ids[:3]}")
        if not record_ids:
            log_fail("no records indexed — sync engine saw pages but produced no records")
            return 1

        log_step(3, "Second sync_notion call (expect no changes)")
        result2 = sync_notion(payload)
        if result2.get("noChangesSince"):
            log_ok("noChangesSince=True — delta cursor works")
        else:
            log_fail(
                f"expected noChangesSince=True; got pagesPulled={result2.get('pagesPulled')}"
            )
            log_info("possible causes: a page was edited between calls, or delta filter bug")
            return 1

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
