"""Tests for ``scripts/notion_markdown.py``.

These cover the extended block-type support (table, toggle, callout,
column_list, link_to_page, image) as well as regression coverage for the
existing behavior — unknown blocks are still ignored, rich-text
annotations still render inside the new block types, and
``page_to_markdown`` still emits the expected title/metadata envelope.

The Notion REST API returns nested children via a separate
``/blocks/{id}/children`` call. The production collector in
``scripts/notion_sync.py`` attaches those children to their parent
block under a ``_children`` key before handing the tree to
``page_to_markdown``. These tests build fixtures in that already-hydrated
shape — nested children live under ``_children`` — so the renderer can be
exercised in isolation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from notion_markdown import page_to_markdown  # noqa: E402


# ----- helpers --------------------------------------------------------------


def _run(text: str, **annotations: bool) -> dict[str, Any]:
    """Build a minimal rich-text run with optional annotations."""
    return {
        "type": "text",
        "text": {"content": text, "link": None},
        "plain_text": text,
        "annotations": {
            "bold": annotations.get("bold", False),
            "italic": annotations.get("italic", False),
            "code": annotations.get("code", False),
        },
    }


def _link_run(text: str, url: str) -> dict[str, Any]:
    return {
        "type": "text",
        "text": {"content": text, "link": {"url": url}},
        "plain_text": text,
        "href": url,
        "annotations": {"bold": False, "italic": False, "code": False},
    }


def _page(page_id: str = "00000000-0000-0000-0000-000000000000") -> dict[str, Any]:
    return {
        "id": page_id,
        "created_time": "2026-04-01T00:00:00.000Z",
        "last_edited_time": "2026-04-02T00:00:00.000Z",
        "parent": {"type": "workspace", "workspace": True},
        "properties": {
            "Name": {
                "type": "title",
                "title": [_run("Doc")],
            }
        },
    }


def _render(blocks: list[dict[str, Any]]) -> str:
    _, body, _ = page_to_markdown(_page(), blocks)
    return body


# ----- table ----------------------------------------------------------------


class TableBlockTests(unittest.TestCase):
    def test_simple_table_with_column_header(self):
        block = {
            "type": "table",
            "table": {
                "table_width": 2,
                "has_column_header": True,
                "has_row_header": False,
            },
            "_children": [
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("Name")], [_run("Role")]]},
                },
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("Ada")], [_run("engineer")]]},
                },
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("Grace")], [_run("admiral")]]},
                },
            ],
        }

        expected = (
            "| Name | Role |\n"
            "| --- | --- |\n"
            "| Ada | engineer |\n"
            "| Grace | admiral |"
        )
        self.assertEqual(_render([block]), expected)

    def test_table_without_column_header_uses_blank_header(self):
        # GFM requires a header row; when Notion reports
        # ``has_column_header: false`` we emit an empty header line so the
        # rendered table is still valid GFM.
        block = {
            "type": "table",
            "table": {
                "table_width": 2,
                "has_column_header": False,
                "has_row_header": False,
            },
            "_children": [
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("a")], [_run("b")]]},
                },
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("c")], [_run("d")]]},
                },
            ],
        }
        expected = (
            "|  |  |\n"
            "| --- | --- |\n"
            "| a | b |\n"
            "| c | d |"
        )
        self.assertEqual(_render([block]), expected)

    def test_table_cells_render_rich_text_annotations(self):
        block = {
            "type": "table",
            "table": {
                "table_width": 2,
                "has_column_header": True,
                "has_row_header": False,
            },
            "_children": [
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("H1")], [_run("H2")]]},
                },
                {
                    "type": "table_row",
                    "table_row": {
                        "cells": [
                            [_run("bold", bold=True)],
                            [_link_run("docs", "https://example.com")],
                        ]
                    },
                },
            ],
        }
        expected = (
            "| H1 | H2 |\n"
            "| --- | --- |\n"
            "| **bold** | [docs](https://example.com) |"
        )
        self.assertEqual(_render([block]), expected)

    def test_pipe_in_cell_is_escaped(self):
        block = {
            "type": "table",
            "table": {
                "table_width": 1,
                "has_column_header": True,
                "has_row_header": False,
            },
            "_children": [
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("H")]]},
                },
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("a | b")]]},
                },
            ],
        }
        body = _render([block])
        self.assertIn("a \\| b", body)

    def test_empty_table_renders_nothing(self):
        block = {
            "type": "table",
            "table": {"table_width": 2, "has_column_header": True},
            "_children": [],
        }
        self.assertEqual(_render([block]), "")


# ----- toggle ---------------------------------------------------------------


class ToggleBlockTests(unittest.TestCase):
    def test_toggle_renders_as_details_summary(self):
        block = {
            "type": "toggle",
            "toggle": {"rich_text": [_run("Open me")]},
            "_children": [
                {"type": "paragraph", "paragraph": {"rich_text": [_run("hidden body")]}},
            ],
        }
        expected = (
            "<details><summary>Open me</summary>\n\n"
            "hidden body\n\n"
            "</details>"
        )
        self.assertEqual(_render([block]), expected)

    def test_toggle_with_annotated_summary_and_nested_list(self):
        block = {
            "type": "toggle",
            "toggle": {"rich_text": [_run("Checklist", bold=True)]},
            "_children": [
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [_run("first")]},
                },
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [_run("second")]},
                },
            ],
        }
        rendered = _render([block])
        self.assertIn("<summary>**Checklist**</summary>", rendered)
        self.assertIn("- first\n- second", rendered)
        self.assertTrue(rendered.endswith("</details>"))

    def test_empty_toggle_renders_details_wrapper(self):
        block = {
            "type": "toggle",
            "toggle": {"rich_text": [_run("Empty")]},
            "_children": [],
        }
        expected = "<details><summary>Empty</summary>\n\n</details>"
        self.assertEqual(_render([block]), expected)


# ----- callout --------------------------------------------------------------


class CalloutBlockTests(unittest.TestCase):
    def test_callout_with_emoji_icon(self):
        block = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "💡"},
                "rich_text": [_run("Remember to read the runbook.")],
            },
        }
        self.assertEqual(
            _render([block]),
            "> 💡 Remember to read the runbook.",
        )

    def test_callout_without_icon(self):
        block = {
            "type": "callout",
            "callout": {
                "icon": None,
                "rich_text": [_run("Plain callout body.")],
            },
        }
        self.assertEqual(_render([block]), "> Plain callout body.")

    def test_callout_with_children_renders_as_continuation_quote(self):
        # Child blocks are separated by ``\n\n`` the same way top-level
        # blocks are. Inside a blockquote that gap becomes an empty
        # ``>`` line — standard markdown for "separate paragraphs inside
        # the same quote".
        block = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "⚠️"},
                "rich_text": [_run("Heads up.")],
            },
            "_children": [
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": [_run("Extra detail.")]},
                },
                {
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [_run("Item A")]},
                },
            ],
        }
        expected = (
            "> ⚠️ Heads up.\n"
            "> Extra detail.\n"
            ">\n"
            "> - Item A"
        )
        self.assertEqual(_render([block]), expected)

    def test_callout_preserves_rich_text_annotations(self):
        block = {
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "💡"},
                "rich_text": [
                    _run("See "),
                    _link_run("runbook", "https://example.com/rb"),
                    _run("."),
                ],
            },
        }
        self.assertEqual(
            _render([block]),
            "> 💡 See [runbook](https://example.com/rb).",
        )


# ----- column_list / column -------------------------------------------------


class ColumnListTests(unittest.TestCase):
    def test_column_list_flattens_columns_with_blank_line_separator(self):
        block = {
            "type": "column_list",
            "column_list": {},
            "_children": [
                {
                    "type": "column",
                    "column": {},
                    "_children": [
                        {
                            "type": "paragraph",
                            "paragraph": {"rich_text": [_run("left column")]},
                        }
                    ],
                },
                {
                    "type": "column",
                    "column": {},
                    "_children": [
                        {
                            "type": "paragraph",
                            "paragraph": {"rich_text": [_run("right column")]},
                        },
                        {
                            "type": "bulleted_list_item",
                            "bulleted_list_item": {"rich_text": [_run("item")]},
                        },
                    ],
                },
            ],
        }
        expected = "left column\n\nright column\n\n- item"
        self.assertEqual(_render([block]), expected)

    def test_standalone_column_also_renders(self):
        block = {
            "type": "column",
            "column": {},
            "_children": [
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": [_run("lone column")]},
                }
            ],
        }
        self.assertEqual(_render([block]), "lone column")


# ----- link_to_page ---------------------------------------------------------


class LinkToPageTests(unittest.TestCase):
    def test_link_to_page_with_page_id_renders_notion_url(self):
        block = {
            "type": "link_to_page",
            "link_to_page": {
                "type": "page_id",
                "page_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
            },
        }
        self.assertEqual(
            _render([block]),
            "[Untitled](notion://ffffffffffffffffffffffffffffffff)",
        )

    def test_link_to_page_with_database_id(self):
        block = {
            "type": "link_to_page",
            "link_to_page": {
                "type": "database_id",
                "database_id": "12345678-1234-1234-1234-123456789012",
            },
        }
        self.assertEqual(
            _render([block]),
            "[Untitled](notion://12345678123412341234123456789012)",
        )

    def test_link_to_page_missing_id_is_skipped(self):
        block = {
            "type": "link_to_page",
            "link_to_page": {"type": "page_id"},
        }
        # No id at all — nothing to link; renderer returns empty string
        # and the join strips it.
        self.assertEqual(_render([block]), "")


# ----- image ----------------------------------------------------------------


class ImageBlockTests(unittest.TestCase):
    def test_external_image_renders_with_empty_alt(self):
        block = {
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": "https://example.com/a.png"},
                "caption": [],
            },
        }
        self.assertEqual(
            _render([block]),
            "![](https://example.com/a.png)",
        )

    def test_hosted_image_with_caption_uses_caption_as_alt(self):
        block = {
            "type": "image",
            "image": {
                "type": "file",
                "file": {"url": "https://notion.so/signed/abc.png"},
                "caption": [_run("diagram of flow")],
            },
        }
        self.assertEqual(
            _render([block]),
            "![diagram of flow](https://notion.so/signed/abc.png)",
        )

    def test_image_caption_preserves_annotations(self):
        block = {
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": "https://example.com/x.png"},
                "caption": [_run("bold", bold=True), _run(" label")],
            },
        }
        self.assertEqual(
            _render([block]),
            "![**bold** label](https://example.com/x.png)",
        )

    def test_image_without_url_is_skipped(self):
        block = {"type": "image", "image": {"type": "external", "external": {}}}
        self.assertEqual(_render([block]), "")


# ----- unknown-block regression --------------------------------------------


class UnknownBlockRegressionTests(unittest.TestCase):
    def test_truly_unknown_block_is_still_skipped(self):
        blocks = [
            {"type": "bookmark", "bookmark": {"url": "https://example.com"}},
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [_run("still here")]},
            },
        ]
        self.assertEqual(_render(blocks), "still here")

    def test_malformed_block_with_missing_payload_is_skipped(self):
        blocks = [
            {"type": "equation"},  # payload omitted entirely
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [_run("retained")]},
            },
        ]
        self.assertEqual(_render(blocks), "retained")


# ----- end-to-end mixed page ------------------------------------------------


class MixedPageEndToEndTests(unittest.TestCase):
    def test_page_with_every_new_block_type(self):
        blocks = [
            {"type": "heading_1", "heading_1": {"rich_text": [_run("Runbook")]}},
            {
                "type": "callout",
                "callout": {
                    "icon": {"type": "emoji", "emoji": "💡"},
                    "rich_text": [_run("Keep it short.")],
                },
            },
            {
                "type": "toggle",
                "toggle": {"rich_text": [_run("Details")]},
                "_children": [
                    {
                        "type": "paragraph",
                        "paragraph": {"rich_text": [_run("hidden")]},
                    }
                ],
            },
            {
                "type": "table",
                "table": {
                    "table_width": 2,
                    "has_column_header": True,
                    "has_row_header": False,
                },
                "_children": [
                    {
                        "type": "table_row",
                        "table_row": {"cells": [[_run("k")], [_run("v")]]},
                    },
                    {
                        "type": "table_row",
                        "table_row": {"cells": [[_run("1")], [_run("2")]]},
                    },
                ],
            },
            {
                "type": "column_list",
                "column_list": {},
                "_children": [
                    {
                        "type": "column",
                        "column": {},
                        "_children": [
                            {
                                "type": "paragraph",
                                "paragraph": {"rich_text": [_run("L")]},
                            }
                        ],
                    },
                    {
                        "type": "column",
                        "column": {},
                        "_children": [
                            {
                                "type": "paragraph",
                                "paragraph": {"rich_text": [_run("R")]},
                            }
                        ],
                    },
                ],
            },
            {
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": "https://example.com/x.png"},
                    "caption": [_run("diagram")],
                },
            },
            {
                "type": "link_to_page",
                "link_to_page": {
                    "type": "page_id",
                    "page_id": "11111111-2222-3333-4444-555555555555",
                },
            },
            {"type": "paragraph", "paragraph": {"rich_text": [_run("End.")]}},
        ]

        title, body, _ = page_to_markdown(_page(), blocks)
        self.assertEqual(title, "Doc")

        expected = (
            "# Runbook\n\n"
            "> 💡 Keep it short.\n\n"
            "<details><summary>Details</summary>\n\n"
            "hidden\n\n"
            "</details>\n\n"
            "| k | v |\n"
            "| --- | --- |\n"
            "| 1 | 2 |\n\n"
            "L\n\n"
            "R\n\n"
            "![diagram](https://example.com/x.png)\n\n"
            "[Untitled](notion://11111111222233334444555555555555)\n\n"
            "End."
        )
        self.assertEqual(body, expected)


class SyncChildHydrationTests(unittest.TestCase):
    """Cross-module test: ``_collect_blocks`` should fetch nested children
    for the container block types and attach them under ``_children`` so
    ``page_to_markdown`` renders the full tree."""

    def test_collect_blocks_hydrates_table_rows(self):
        from notion_sync import _collect_blocks  # noqa: WPS433

        table_block_id = "table-1"
        page_id = "page-1"
        # Fake Notion client: records every ``get_blocks`` call and
        # returns the right envelope based on the id.
        responses = {
            page_id: [
                {
                    "type": "table",
                    "id": table_block_id,
                    "has_children": True,
                    "table": {
                        "table_width": 2,
                        "has_column_header": True,
                        "has_row_header": False,
                    },
                },
            ],
            table_block_id: [
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("H1")], [_run("H2")]]},
                },
                {
                    "type": "table_row",
                    "table_row": {"cells": [[_run("a")], [_run("b")]]},
                },
            ],
        }

        class FakeClient:
            def __init__(self) -> None:
                self.call_log: list[str] = []

            def get_blocks(
                self, block_id: str, cursor: str | None = None, page_size: int = 100
            ) -> dict[str, Any]:
                self.call_log.append(block_id)
                return {
                    "blocks": list(responses.get(block_id, [])),
                    "next_cursor": None,
                    "has_more": False,
                }

        client = FakeClient()
        blocks = _collect_blocks(client, page_id)

        # One call for the page children, one for the table's children.
        self.assertEqual(client.call_log, [page_id, table_block_id])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "table")
        self.assertEqual(len(blocks[0]["_children"]), 2)

        # And the hydrated block renders correctly end-to-end.
        _, body, _ = page_to_markdown(_page(), blocks)
        self.assertEqual(
            body,
            "| H1 | H2 |\n| --- | --- |\n| a | b |",
        )

    def test_collect_blocks_skips_child_fetch_when_has_children_false(self):
        from notion_sync import _collect_blocks  # noqa: WPS433

        page_id = "p"

        class FakeClient:
            def __init__(self) -> None:
                self.call_log: list[str] = []

            def get_blocks(self, block_id, cursor=None, page_size=100):
                self.call_log.append(block_id)
                if block_id == page_id:
                    return {
                        "blocks": [
                            {
                                "type": "toggle",
                                "id": "t-empty",
                                "has_children": False,
                                "toggle": {"rich_text": [_run("closed")]},
                            }
                        ],
                        "next_cursor": None,
                        "has_more": False,
                    }
                raise AssertionError(
                    f"unexpected extra fetch for {block_id}"
                )

        client = FakeClient()
        blocks = _collect_blocks(client, page_id)
        # No extra fetch for the toggle's (empty) children.
        self.assertEqual(client.call_log, [page_id])
        self.assertEqual(blocks[0]["_children"], [])


if __name__ == "__main__":
    unittest.main()
