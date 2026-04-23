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
