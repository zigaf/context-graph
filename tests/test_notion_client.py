from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from notion_client import NotionAPIError, NotionClient  # noqa: E402
from notion_markdown import page_to_markdown  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "notion_api"


def _load_fixture(name: str) -> dict:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as f:
        return json.load(f)


def _mock_response(payload: dict, status: int = 200) -> MagicMock:
    body = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.read.return_value = body
    response.status = status
    response.getcode.return_value = status
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class NotionClientInitTests(unittest.TestCase):
    def test_rejects_empty_token(self):
        with self.assertRaises(ValueError):
            NotionClient("")

    def test_rejects_whitespace_token(self):
        with self.assertRaises(ValueError):
            NotionClient("   ")

    def test_accepts_explicit_token(self):
        client = NotionClient("secret_abc")
        self.assertIsInstance(client, NotionClient)

    def test_env_token_reads_environment(self):
        with patch.dict("os.environ", {"NOTION_TOKEN": "env_secret"}, clear=False):
            client = NotionClient("env")
        self.assertIsInstance(client, NotionClient)

    def test_env_token_missing_raises(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "NOTION_TOKEN"}
        with patch.dict("os.environ", env, clear=True):
            with self.assertRaises(ValueError):
                NotionClient("env")


class NotionClientHTTPTests(unittest.TestCase):
    def setUp(self):
        self.client = NotionClient("secret_test_token")

    def test_list_database_pages_parses_envelope(self):
        fixture = _load_fixture("database_pages_response.json")
        with patch("notion_client.urllib_request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(fixture)
            result = self.client.list_database_pages(
                "dddddddd-dddd-dddd-dddd-dddddddddddd"
            )

        self.assertEqual(set(result.keys()), {"pages", "next_cursor", "has_more"})
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(result["next_cursor"], "cursor-2")
        self.assertTrue(result["has_more"])
        self.assertEqual(result["pages"][0]["id"], "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        # Verify the request was a POST to the query endpoint with auth headers.
        args, _ = urlopen.call_args
        request = args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("/databases/dddddddd-dddd-dddd-dddd-dddddddddddd/query", request.full_url)
        # urllib.request.Request normalizes header keys via str.capitalize(),
        # so the headers are stored as "Authorization", "Notion-version",
        # "Content-type" — HTTP headers are case-insensitive on the wire.
        self.assertEqual(
            request.get_header("Authorization"), "Bearer secret_test_token"
        )
        self.assertEqual(request.get_header("Notion-version"), "2022-06-28")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["page_size"], 100)
        self.assertNotIn("start_cursor", body)

    def test_list_database_pages_forwards_cursor_and_filter(self):
        fixture = _load_fixture("database_pages_response.json")
        custom_filter = {"property": "Status", "select": {"equals": "Active"}}
        with patch("notion_client.urllib_request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(fixture)
            self.client.list_database_pages(
                "db-id",
                filter_=custom_filter,
                cursor="cursor-1",
                page_size=25,
            )

        request = urlopen.call_args[0][0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["start_cursor"], "cursor-1")
        self.assertEqual(body["page_size"], 25)
        self.assertEqual(body["filter"], custom_filter)

    def test_get_blocks_pagination_shape(self):
        fixture = _load_fixture("blocks_response.json")
        with patch("notion_client.urllib_request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(fixture)
            result = self.client.get_blocks(
                "11111111-1111-1111-1111-111111111111",
                cursor="next-cursor",
                page_size=50,
            )

        self.assertEqual(set(result.keys()), {"blocks", "next_cursor", "has_more"})
        self.assertEqual(len(result["blocks"]), 2)
        self.assertIsNone(result["next_cursor"])
        self.assertFalse(result["has_more"])

        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("/blocks/11111111-1111-1111-1111-111111111111/children", request.full_url)
        self.assertIn("start_cursor=next-cursor", request.full_url)
        self.assertIn("page_size=50", request.full_url)

    def test_get_page_returns_raw_payload(self):
        page_payload = {"object": "page", "id": "page-1"}
        with patch("notion_client.urllib_request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(page_payload)
            result = self.client.get_page("page-1")
        self.assertEqual(result, page_payload)

    def test_list_child_pages_filters_child_page_blocks(self):
        mixed = {
            "results": [
                {"type": "paragraph", "id": "b1"},
                {"type": "child_page", "id": "cp-1", "child_page": {"title": "A"}},
                {"type": "child_page", "id": "cp-2", "child_page": {"title": "B"}},
            ],
            "next_cursor": None,
            "has_more": False,
        }
        with patch("notion_client.urllib_request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(mixed)
            result = self.client.list_child_pages("parent")
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual([p["id"] for p in result["pages"]], ["cp-1", "cp-2"])

    def test_http_401_raises_notion_api_error(self):
        body = json.dumps({"code": "unauthorized", "message": "Invalid token"})
        error = HTTPError(
            url="https://api.notion.com/v1/databases/x/query",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(body.encode("utf-8")),
        )
        with patch("notion_client.urllib_request.urlopen", side_effect=error):
            with self.assertRaises(NotionAPIError) as ctx:
                self.client.list_database_pages("db-id")
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("unauthorized", ctx.exception.body)


class PageToMarkdownTests(unittest.TestCase):
    def test_mixed_blocks_render_to_expected_markdown(self):
        fixture = _load_fixture("page_with_mixed_blocks.json")
        title, markdown, metadata = page_to_markdown(
            fixture["page"], fixture["blocks"]
        )

        self.assertEqual(title, "Payments Hub")
        expected = (
            "# Payments Hub\n\n"
            "Central **payments** overview and [*runbook*](https://example.com/runbook).\n\n"
            "## Checklist\n\n"
            "- [x] Audit webhook retries\n"
            "- [ ] Add idempotency keys\n\n"
            "- Owner: payments team\n"
            "- SLA: 24h\n\n"
            "```python\n"
            "print('hello')\n"
            "```\n\n"
            "> Reliability is a feature.\n\n"
            "---\n\n"
            "- [Webhook Incident](notion://ccccccccccccccccccccccccccccccc"
            "c)"
        )
        self.assertEqual(markdown, expected)

        self.assertEqual(
            metadata["notion_page_id"],
            "11111111111111111111111111111111",
        )
        self.assertEqual(metadata["last_edited_time"], "2026-04-20T12:34:56.000Z")
        self.assertEqual(metadata["created_time"], "2026-04-01T10:00:00.000Z")
        self.assertEqual(
            metadata["url"],
            "https://www.notion.so/Payments-Hub-11111111111111111111111111111111",
        )
        self.assertEqual(
            metadata["parent"],
            {"type": "database_id", "id": "dddddddd-dddd-dddd-dddd-dddddddddddd"},
        )

    def test_missing_title_returns_empty_string(self):
        page = {
            "id": "abc",
            "created_time": "2026-04-01T00:00:00.000Z",
            "last_edited_time": "2026-04-02T00:00:00.000Z",
            "parent": {"type": "workspace", "workspace": True},
            "properties": {},
        }
        title, body, metadata = page_to_markdown(page, [])
        self.assertEqual(title, "")
        self.assertEqual(body, "")
        self.assertEqual(metadata["parent"], {"type": "workspace", "id": None})
        self.assertEqual(metadata["notion_page_id"], "abc")

    def test_unknown_blocks_are_skipped(self):
        page = {
            "id": "11111111-1111-1111-1111-111111111111",
            "created_time": "2026-04-01T00:00:00.000Z",
            "last_edited_time": "2026-04-02T00:00:00.000Z",
            "parent": {"type": "workspace", "workspace": True},
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [
                        {"plain_text": "Hi", "annotations": {}, "text": {"content": "Hi"}}
                    ],
                }
            },
        }
        blocks = [
            {"type": "bookmark", "bookmark": {"url": "https://example.com"}},
            {"type": "paragraph", "paragraph": {"rich_text": [
                {"plain_text": "kept", "annotations": {}, "text": {"content": "kept"}}
            ]}},
        ]
        title, body, _ = page_to_markdown(page, blocks)
        self.assertEqual(title, "Hi")
        self.assertEqual(body, "kept")


if __name__ == "__main__":
    unittest.main()
