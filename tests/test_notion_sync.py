from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import index_records, load_graph  # noqa: E402
from notion_sync import sync_notion  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "notion_sync"


def load_scenario(name: str) -> dict:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


class FakeNotionClient:
    def __init__(self, scenario: dict):
        self._pages = list(scenario.get("pages", []))
        self._blocks = dict(scenario.get("blocks", {}))
        self.calls: list[tuple[str, tuple, dict]] = []

    def list_database_pages(self, database_id, filter_=None, cursor=None, page_size=100):
        self.calls.append(("list_database_pages", (database_id,), {"cursor": cursor}))
        return {"pages": self._pages, "next_cursor": None, "has_more": False}

    def list_child_pages(self, parent_page_id, cursor=None, page_size=100):
        self.calls.append(("list_child_pages", (parent_page_id,), {"cursor": cursor}))
        return {"pages": self._pages, "next_cursor": None, "has_more": False}

    def get_page(self, page_id: str):
        for page in self._pages:
            if page.get("id") == page_id:
                return page
        raise KeyError(page_id)

    def get_blocks(self, page_id, cursor=None, page_size=100):
        self.calls.append(("get_blocks", (page_id,), {"cursor": cursor}))
        return {
            "blocks": self._blocks.get(page_id, []),
            "next_cursor": None,
            "has_more": False,
        }


def make_client_factory(scenario: dict):
    created: list[FakeNotionClient] = []

    def factory(token: str) -> FakeNotionClient:
        client = FakeNotionClient(scenario)
        client.token = token
        created.append(client)
        return client

    factory.created = created  # type: ignore[attr-defined]
    return factory


def make_markdown_converter(scenario: dict):
    mapping = scenario.get("markdown", {})

    def converter(page, blocks):
        page_id = page.get("id")
        entry = mapping.get(page_id, {})
        title = entry.get("title", f"Untitled {page_id}")
        content = entry.get("content", "")
        metadata = dict(entry.get("metadata", {}))
        metadata["blockCount"] = len(blocks)
        return title, content, metadata

    return converter


class NotionSyncTests(unittest.TestCase):
    def setUp(self):
        # Keep NOTION_TOKEN stable across tests; each test sets what it needs.
        self._previous_token = os.environ.get("NOTION_TOKEN")
        os.environ["NOTION_TOKEN"] = "test-token"

    def tearDown(self):
        if self._previous_token is None:
            os.environ.pop("NOTION_TOKEN", None)
        else:
            os.environ["NOTION_TOKEN"] = self._previous_token

    def test_two_pages_in_database_indexed_with_notion_id_scheme(self):
        scenario = load_scenario("pages_two.json")
        client_factory = make_client_factory(scenario)
        markdown_converter = make_markdown_converter(scenario)

        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            cursor_path = str(Path(tmpdir) / "notion_cursor.json")

            result = sync_notion(
                {
                    "databaseId": "db-1",
                    "graphPath": graph_path,
                    "cursorPath": cursor_path,
                    "clientFactory": client_factory,
                    "markdownConverter": markdown_converter,
                }
            )

            self.assertEqual(result["pagesPulled"], 2)
            self.assertFalse(result["noChangesSince"])
            self.assertEqual(
                set(result["recordIds"]),
                {
                    "notion:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "notion:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                },
            )
            for record_id in result["recordIds"]:
                _, raw = record_id.split(":", 1)
                self.assertEqual(len(raw), 32)
                self.assertTrue(all(ch in "0123456789abcdef" for ch in raw))

            graph = load_graph(graph_path)
            self.assertEqual(len(graph["records"]), 2)
            hub = graph["records"]["notion:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
            self.assertEqual(hub["title"], "Payments Hub")
            self.assertEqual(hub["source"]["system"], "notion")
            self.assertEqual(
                hub["source"]["metadata"]["notionPageId"],
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )
            self.assertIn("last_edited_time", hub["source"]["metadata"])
            self.assertEqual(hub["source"]["metadata"]["last_edited_time"], "2026-04-22T10:00:00.000Z")

            self.assertEqual(result["newCursor"], "2026-04-23T08:30:00.000Z")
            with open(cursor_path, "r", encoding="utf-8") as f:
                persisted = json.load(f)
            # Per-page cursor schema: {<raw-page-id>: <iso>}.
            page_a_raw_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            page_b_raw_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            self.assertEqual(persisted[page_a_raw_id], "2026-04-22T10:00:00.000Z")
            self.assertEqual(persisted[page_b_raw_id], "2026-04-23T08:30:00.000Z")

    def test_cursor_at_latest_reports_no_changes_and_does_not_reindex(self):
        scenario = load_scenario("pages_two.json")
        client_factory = make_client_factory(scenario)
        markdown_converter = make_markdown_converter(scenario)

        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            cursor_path = Path(tmpdir) / "notion_cursor.json"

            # Pre-seed a per-page cursor already at-or-after each page's last_edited_time.
            page_a_raw_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            page_b_raw_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            cursor_path.write_text(
                json.dumps(
                    {
                        page_a_raw_id: "2026-04-24T00:00:00.000Z",
                        page_b_raw_id: "2026-04-24T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )
            cursor_mtime_before = cursor_path.stat().st_mtime_ns
            cursor_content_before = cursor_path.read_text(encoding="utf-8")

            graph_exists_before = Path(graph_path).exists()

            result = sync_notion(
                {
                    "databaseId": "db-1",
                    "graphPath": graph_path,
                    "cursorPath": str(cursor_path),
                    "clientFactory": client_factory,
                    "markdownConverter": markdown_converter,
                }
            )

            self.assertTrue(result["noChangesSince"])
            self.assertEqual(result["pagesPulled"], 0)
            self.assertEqual(result["recordIds"], [])
            self.assertIsNone(result["indexResult"])
            # On no-op sync, `newCursor` is None — nothing new was pulled.
            self.assertIsNone(result["newCursor"])

            self.assertEqual(cursor_path.read_text(encoding="utf-8"), cursor_content_before)
            self.assertEqual(cursor_path.stat().st_mtime_ns, cursor_mtime_before)
            self.assertEqual(Path(graph_path).exists(), graph_exists_before)

    def test_dedup_against_preseeded_notion_record_updates_content(self):
        scenario = load_scenario("pages_two.json")
        page_a_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        preseed_record_id = f"notion:{page_a_id}"

        preseed_scenario = {
            "pages": [scenario["pages"][0]],
            "blocks": scenario["blocks"],
            "markdown": scenario["markdown"],
        }
        trimmed_markdown = dict(scenario["markdown"])
        preseed_scenario["markdown"] = trimmed_markdown

        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            cursor_path = str(Path(tmpdir) / "notion_cursor.json")

            # Pre-seed the graph with a record whose id matches the scheme produced by
            # ingest_notion_export ("notion:<32-hex>"). The live sync must dedup against it.
            index_records(
                {
                    "graphPath": graph_path,
                    "records": [
                        {
                            "id": preseed_record_id,
                            "title": "Payments Hub (stale export)",
                            "content": "OLD EXPORT CONTENT",
                            "source": {
                                "system": "notion-export",
                                "path": f"Payments Hub {page_a_id}.md",
                                "metadata": {"notionPageId": page_a_id},
                            },
                        }
                    ],
                }
            )

            graph_before = load_graph(graph_path)
            self.assertEqual(len(graph_before["records"]), 1)
            self.assertIn(preseed_record_id, graph_before["records"])
            self.assertEqual(
                graph_before["records"][preseed_record_id]["content"], "OLD EXPORT CONTENT"
            )

            # Now run the live sync. The page has an updated title/content; the record
            # should be merged (same id) rather than duplicated.
            single_page_scenario = {
                "pages": [scenario["pages"][0]],
                "blocks": {page_a_id: scenario["blocks"].get(scenario["pages"][0]["id"], [])},
                "markdown": {
                    scenario["pages"][0]["id"]: {
                        "title": "Payments Hub",
                        "content": "NEW LIVE CONTENT",
                        "metadata": {"notionPageId": page_a_id},
                    }
                },
            }
            client_factory = make_client_factory(single_page_scenario)
            markdown_converter = make_markdown_converter(single_page_scenario)

            result = sync_notion(
                {
                    "databaseId": "db-1",
                    "graphPath": graph_path,
                    "cursorPath": cursor_path,
                    "clientFactory": client_factory,
                    "markdownConverter": markdown_converter,
                }
            )

            self.assertEqual(result["pagesPulled"], 1)
            self.assertEqual(result["recordIds"], [preseed_record_id])

            graph_after = load_graph(graph_path)
            self.assertEqual(len(graph_after["records"]), 1)
            record = graph_after["records"][preseed_record_id]
            self.assertEqual(record["content"], "NEW LIVE CONTENT")
            self.assertEqual(record["source"]["system"], "notion")
            # Revision version should have bumped from the merge.
            self.assertGreaterEqual(record["revision"].get("version", 1), 2)

    def test_per_page_cursor_skips_only_stale_pages(self):
        # Regression: when one page is already at-cursor and another is newer,
        # the newer page is fetched and indexed while the older one is skipped
        # before the block fetch call — no wasted `get_blocks` for stale pages.
        scenario = load_scenario("pages_two.json")
        client_factory = make_client_factory(scenario)
        markdown_converter = make_markdown_converter(scenario)

        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            cursor_path = Path(tmpdir) / "notion_cursor.json"

            page_a_raw_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            page_b_raw_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            # Page A is already synced up to its current last_edited_time.
            # Page B has a newer last_edited_time than what the cursor records.
            cursor_path.write_text(
                json.dumps(
                    {
                        page_a_raw_id: "2026-04-22T10:00:00.000Z",
                        page_b_raw_id: "2026-04-22T00:00:00.000Z",
                    }
                ),
                encoding="utf-8",
            )

            result = sync_notion(
                {
                    "databaseId": "db-1",
                    "graphPath": graph_path,
                    "cursorPath": str(cursor_path),
                    "clientFactory": client_factory,
                    "markdownConverter": markdown_converter,
                }
            )

            self.assertEqual(result["pagesPulled"], 1)
            self.assertEqual(
                result["recordIds"],
                ["notion:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
            )
            self.assertFalse(result["noChangesSince"])
            self.assertEqual(result["newCursor"], "2026-04-23T08:30:00.000Z")

            # No block fetch for page A — it was filtered before any fetch call.
            fetched_block_pages = [
                call[1][0]
                for call in client_factory.created[0].calls
                if call[0] == "get_blocks"
            ]
            self.assertNotIn(page_a_raw_id, fetched_block_pages)
            self.assertIn(page_b_raw_id, fetched_block_pages)

            # Cursor advances only for the fresh page; page A's entry is preserved.
            with cursor_path.open("r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted[page_a_raw_id], "2026-04-22T10:00:00.000Z")
            self.assertEqual(persisted[page_b_raw_id], "2026-04-23T08:30:00.000Z")

    def test_missing_notion_token_raises_value_error(self):
        os.environ.pop("NOTION_TOKEN", None)

        scenario = load_scenario("pages_two.json")
        client_factory = make_client_factory(scenario)
        markdown_converter = make_markdown_converter(scenario)

        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            cursor_path = str(Path(tmpdir) / "notion_cursor.json")

            with self.assertRaises(ValueError):
                sync_notion(
                    {
                        "databaseId": "db-1",
                        "graphPath": graph_path,
                        "cursorPath": cursor_path,
                        "clientFactory": client_factory,
                        "markdownConverter": markdown_converter,
                    }
                )

            # Also verify explicit empty token triggers the same error.
            with self.assertRaises(ValueError):
                sync_notion(
                    {
                        "token": "",
                        "databaseId": "db-1",
                        "graphPath": graph_path,
                        "cursorPath": cursor_path,
                        "clientFactory": client_factory,
                        "markdownConverter": markdown_converter,
                    }
                )


class SyncFallbackArbiterTests(unittest.TestCase):
    def setUp(self):
        self._previous_token = os.environ.get("NOTION_TOKEN")
        os.environ["NOTION_TOKEN"] = "test-token"

    def tearDown(self):
        if self._previous_token is None:
            os.environ.pop("NOTION_TOKEN", None)
        else:
            os.environ["NOTION_TOKEN"] = self._previous_token

    def test_pending_arbitration_degrades_to_fallback(self):
        page_id = "cccccccccccccccccccccccccccccccc"
        scenario = {
            "pages": [
                {
                    "id": page_id,
                    "url": f"https://www.notion.so/{page_id}",
                    "last_edited_time": "2026-04-23T08:30:00.000Z",
                    "created_time": "2026-04-23T08:00:00.000Z",
                    "parent": {"type": "page_id", "page_id": "parent"},
                }
            ],
            "blocks": {page_id: []},
            "markdown": {
                page_id: {
                    "title": "Payments",
                    "content": "payments",
                    "metadata": {"parent": "kenmore > Payments"},
                }
            },
        }
        client_factory = make_client_factory(scenario)
        markdown_converter = make_markdown_converter(scenario)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = sync_notion(
                {
                    "databaseId": "db-1",
                    "graphPath": str(Path(tmpdir) / "graph.json"),
                    "cursorPath": str(Path(tmpdir) / "notion_cursor.json"),
                    "clientFactory": client_factory,
                    "markdownConverter": markdown_converter,
                }
            )

        self.assertGreaterEqual(result["fallbackCount"], 1)


if __name__ == "__main__":
    unittest.main()
