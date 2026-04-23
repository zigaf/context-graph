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
    archive_record,
    build_context_pack,
    classify_record,
    clear_redactors,
    delete_record,
    index_records,
    init_workspace,
    ingest_notion_export,
    load_graph,
    merge_record,
    promote_pattern,
    register_redactor,
    search_graph,
    strip_obvious_secrets,
    unarchive_record,
    write_graph,
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

    def test_delete_record_removes_record_and_incident_edges(self):
        records = self.load_fixture("records/basic_records.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            index_records({"graphPath": graph_path, "records": records})
            graph_before = load_graph(graph_path)
            self.assertIn("r1", graph_before["records"])
            edges_touching_r1 = [
                edge for edge in graph_before["edges"]
                if edge.get("source") == "r1" or edge.get("target") == "r1"
            ]
            self.assertGreater(len(edges_touching_r1), 0)

            result = delete_record({"graphPath": graph_path, "recordId": "r1"})
            self.assertEqual(result["deletedId"], "r1")
            self.assertFalse(result["notFound"])
            self.assertEqual(result["recordCount"], 1)

            graph_after = load_graph(graph_path)
            self.assertNotIn("r1", graph_after["records"])
            for edge in graph_after["edges"]:
                self.assertNotEqual(edge.get("source"), "r1")
                self.assertNotEqual(edge.get("target"), "r1")

    def test_delete_record_not_found_returns_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            # Seed an empty graph file by indexing no records.
            index_records({"graphPath": graph_path, "records": []})
            result = delete_record({"graphPath": graph_path, "recordId": "missing"})
            self.assertEqual(result["deletedId"], "missing")
            self.assertTrue(result["notFound"])
            self.assertEqual(result["recordCount"], 0)

    def test_archive_hides_record_in_context_pack_and_unarchive_restores(self):
        records = self.load_fixture("records/basic_records.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            index_records({"graphPath": graph_path, "records": records})

            archive_result = archive_record({"graphPath": graph_path, "recordId": "r1"})
            self.assertTrue(archive_result["archived"])

            graph = load_graph(graph_path)
            default_pack = build_context_pack(
                {
                    "query": "payment webhook reliability issue",
                    "records": list(graph["records"].values()),
                    "limit": 4,
                }
            )
            self.assertFalse(any(item["id"] == "r1" for item in default_pack["directMatches"]))
            self.assertFalse(any(item["id"] == "r1" for item in default_pack["supportingRelations"]))

            included_pack = build_context_pack(
                {
                    "query": "payment webhook reliability issue",
                    "records": list(graph["records"].values()),
                    "limit": 4,
                    "includeArchived": True,
                }
            )
            self.assertTrue(any(item["id"] == "r1" for item in included_pack["directMatches"]))

            # search_graph should also skip archived records by default.
            search_default = search_graph({"graphPath": graph_path, "query": "payment webhook reliability issue"})
            self.assertFalse(any(item["id"] == "r1" for item in search_default["directMatches"]))

            unarchive_result = unarchive_record({"graphPath": graph_path, "recordId": "r1"})
            self.assertFalse(unarchive_result["archived"])
            graph = load_graph(graph_path)
            self.assertNotIn("archived", graph["records"]["r1"])
            restored_pack = build_context_pack(
                {
                    "query": "payment webhook reliability issue",
                    "records": list(graph["records"].values()),
                    "limit": 4,
                }
            )
            self.assertTrue(any(item["id"] == "r1" for item in restored_pack["directMatches"]))

    def test_search_graph_ttl_filters_old_inferred_edges(self):
        records = self.load_fixture("records/basic_records.json")
        # Give r1 an explicit relation to r2 so we can confirm explicit edges
        # survive TTL filtering regardless of age.
        explicit_records = [dict(record) for record in records]
        explicit_records[0] = dict(explicit_records[0])
        explicit_records[0]["relations"] = {
            "explicit": [{"type": "related_to", "target": "r2"}],
            "inferred": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            index_records({"graphPath": graph_path, "records": explicit_records})
            graph = load_graph(graph_path)
            # Age every inferred edge beyond the default TTL and stamp the
            # explicit edge with an equally ancient createdAt to confirm it is
            # never filtered.
            for edge in graph["edges"]:
                edge["createdAt"] = "2020-01-01T00:00:00+00:00"
            write_graph(graph, graph_path)

            # Default TTL (30d) should drop every inferred edge.
            result_default = search_graph({"graphPath": graph_path, "query": "payment webhook reliability issue"})
            for item in result_default["directMatches"]:
                for edge in item.get("outgoingEdges", []):
                    self.assertNotEqual(edge.get("kind"), "inferred")
            # The explicit edge from r1 to r2 must still be present.
            r1_item = next(item for item in result_default["directMatches"] if item["id"] == "r1")
            explicit_targets = [edge["target"] for edge in r1_item.get("outgoingEdges", []) if edge.get("kind") == "explicit"]
            self.assertIn("r2", explicit_targets)

            # A very generous TTL should let inferred edges back in.
            result_loose = search_graph(
                {
                    "graphPath": graph_path,
                    "query": "payment webhook reliability issue",
                    "inferredEdgeTtlDays": 100000,
                }
            )
            inferred_kinds = [
                edge.get("kind")
                for item in result_loose["directMatches"]
                for edge in item.get("outgoingEdges", [])
            ]
            self.assertIn("inferred", inferred_kinds)

    def test_redactor_applies_without_mutating_graph(self):
        records = self.load_fixture("records/basic_records.json")
        sensitive = [dict(record) for record in records]
        sensitive[0] = dict(sensitive[0])
        sensitive[0]["content"] = (
            "Contact ops@example.com with the bearer token "
            "sk-ABCDEFGHIJKLMNOPQRSTUV after the webhook retries."
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "graph.json")
            index_records({"graphPath": graph_path, "records": sensitive})
            graph = load_graph(graph_path)

            clear_redactors()
            self.addCleanup(clear_redactors)
            register_redactor(strip_obvious_secrets)

            pack = build_context_pack(
                {
                    "query": "payment webhook reliability issue",
                    "records": list(graph["records"].values()),
                    "limit": 4,
                }
            )
            top = next(item for item in pack["directMatches"] if item["id"] == "r1")
            self.assertIn("[redacted-email]", top["content"])
            self.assertIn("[redacted-secret]", top["content"])
            self.assertNotIn("ops@example.com", top["content"])
            self.assertNotIn("sk-ABCDEFGHIJKLMNOPQRSTUV", top["content"])

            # Underlying record in the graph must be unchanged.
            graph_again = load_graph(graph_path)
            self.assertIn("ops@example.com", graph_again["records"]["r1"]["content"])
            self.assertIn("sk-ABCDEFGHIJKLMNOPQRSTUV", graph_again["records"]["r1"]["content"])

            # clear_redactors should disable the redactor going forward.
            clear_redactors()
            pack_plain = build_context_pack(
                {
                    "query": "payment webhook reliability issue",
                    "records": list(graph["records"].values()),
                    "limit": 4,
                }
            )
            # Without redactors, content is not surfaced; the important
            # assertion is that nothing redacted leaked into the ranked items.
            for item in pack_plain["directMatches"]:
                self.assertNotIn("content", item)


class MarkerImportanceRetrievalTests(unittest.TestCase):
    def test_importance_weights_applied_when_learned_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            records = [
                {
                    "id": "a",
                    "title": "A",
                    "content": "payments",
                    "markers": {"type": "task"},
                },
                {
                    "id": "b",
                    "title": "B",
                    "content": "payments",
                    "markers": {"flow": "deposit", "severity": "critical"},
                },
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            learned_path = root / ".context-graph" / "schema.learned.json"
            learned = json.loads(learned_path.read_text(encoding="utf-8"))
            learned["markerImportance"] = {"type": 1.0, "flow": 0.1}
            learned_path.write_text(json.dumps(learned), encoding="utf-8")

            result = search_graph(
                {
                    "workspaceRoot": str(root),
                    "query": "payments",
                    "markers": {"type": "task", "flow": "deposit"},
                }
            )

            ids = [match["id"] for match in result["directMatches"]]
            self.assertEqual(ids[0], "a")


if __name__ == "__main__":
    unittest.main()
