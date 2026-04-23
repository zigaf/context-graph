from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    build_context_pack,
    classify_record,
    index_records,
    ingest_notion_export,
    load_graph,
    merge_record,
    promote_pattern,
)


class ContextGraphCoreTests(unittest.TestCase):
    def load_fixture(self, relative_path: str):
        path = ROOT / "tests" / "fixtures" / relative_path
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def test_classify_record_normalizes_aliases(self):
        fixture = self.load_fixture("records/classify_aliases.json")
        result = classify_record({"record": fixture})
        self.assertEqual(result["markers"]["status"], "in-progress")
        self.assertEqual(result["markers"]["goal"], "stabilize-flow")
        self.assertEqual(result["markers"]["type"], "bug")
        self.assertEqual(result["markers"]["domain"], "payments")
        self.assertEqual(result["markers"]["flow"], "webhook")

    def test_index_and_search_context_pack(self):
        records = self.load_fixture("records/basic_records.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            index_result = index_records({"graphPath": graph_path, "records": records})
            self.assertEqual(index_result["recordCount"], 2)
            self.assertGreaterEqual(index_result["edgeCount"], 1)

            graph = load_graph(graph_path)
            context = build_context_pack(
                {
                    "query": "payment webhook reliability issue",
                    "records": list(graph["records"].values()),
                    "limit": 2,
                }
            )
            self.assertEqual(context["directMatches"][0]["id"], "r1")
            self.assertTrue(any(item["id"] == "r2" for item in context["promotedRules"]))

    def test_promote_pattern_returns_quality_and_split_suggestions(self):
        records = self.load_fixture("records/conflict_records.json")
        result = promote_pattern({"records": records})
        self.assertEqual(result["quality"]["recommendation"], "split")
        self.assertGreater(len(result["splitSuggestions"]), 0)
        self.assertIn("type", result["quality"]["conflicts"])
        self.assertEqual(result["promotedRecord"]["source"]["metadata"]["promotionQuality"]["recommendation"], "split")

    def test_merge_record_rejects_stale_last_edited_time(self):
        previous = {
            "id": "notion:abc",
            "content": "FRESH",
            "revision": {"version": 3, "updatedAt": "2026-04-10T10:00:00+00:00"},
            "source": {"metadata": {"last_edited_time": "2026-04-10T10:00:00Z"}},
        }
        stale = {
            "id": "notion:abc",
            "content": "STALE",
            "revision": {"version": 1, "updatedAt": "2026-04-01T10:00:00+00:00"},
            "source": {"metadata": {"last_edited_time": "2026-04-01T10:00:00Z"}},
        }
        fresh = {
            "id": "notion:abc",
            "content": "NEWER",
            "revision": {"version": 1, "updatedAt": "2026-04-20T10:00:00+00:00"},
            "source": {"metadata": {"last_edited_time": "2026-04-20T10:00:00Z"}},
        }
        self.assertEqual(merge_record(previous, stale)["content"], "FRESH")
        self.assertEqual(merge_record(previous, stale)["revision"]["version"], 3)
        merged_fresh = merge_record(previous, fresh)
        self.assertEqual(merged_fresh["content"], "NEWER")
        self.assertEqual(merged_fresh["revision"]["version"], 4)

    def test_merge_record_falls_back_when_timestamps_missing(self):
        previous = {"id": "x", "content": "OLD", "revision": {"version": 2}, "source": {"metadata": {}}}
        current = {"id": "x", "content": "NEW", "revision": {"version": 1}, "source": {"metadata": {}}}
        merged = merge_record(previous, current)
        self.assertEqual(merged["content"], "NEW")
        self.assertEqual(merged["revision"]["version"], 3)

    def test_ingest_notion_export_preserves_page_ids_and_links(self):
        fixture_root = ROOT / "tests" / "fixtures" / "notion_export"
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            result = ingest_notion_export({"rootPath": str(fixture_root), "graphPath": graph_path})
            self.assertEqual(result["fileCount"], 2)
            self.assertIn("notion:11111111111111111111111111111111", result["recordIds"])
            hub = next(record for record in result["records"] if record["id"] == "notion:11111111111111111111111111111111")
            self.assertEqual(hub["source"]["metadata"]["notionPageId"], "11111111111111111111111111111111")
            self.assertEqual(hub["relations"]["explicit"][0]["target"], "notion:22222222222222222222222222222222")


if __name__ == "__main__":
    unittest.main()
