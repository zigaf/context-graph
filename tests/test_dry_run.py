"""Dry-run flag tests for all mutators.

Each test asserts the on-disk bytes of ``graph.json`` are unchanged after a
dry-run call and that the returned payload carries ``dryRun: true``.
Classifier-type functions that never touch disk are also covered: they only
echo the ``dryRun: true`` marker.
"""
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
    classify_record,
    delete_record,
    index_records,
    ingest_markdown,
    ingest_notion_export,
    init_workspace,
    promote_pattern,
    unarchive_record,
)


def _seed_records() -> list[dict]:
    return [
        {
            "id": "r1",
            "title": "Critical webhook bug in deposit flow",
            "content": "Duplicate payment creation after callback retry",
            "markers": {
                "type": "bug",
                "domain": "payments",
                "flow": "webhook",
                "artifact": "endpoint",
                "goal": "stabilize-flow",
                "status": "in-progress",
                "severity": "critical",
            },
        },
        {
            "id": "r2",
            "title": "Retry-safe webhook design for deposit callbacks",
            "content": "Adds idempotency guard for payment callback duplicates",
            "markers": {
                "type": "rule",
                "domain": "payments",
                "flow": "webhook",
                "artifact": "service",
                "goal": "prevent-regression",
                "status": "done",
                "severity": "high",
            },
        },
    ]


def _seed_graph(tmp: Path) -> str:
    graph_path = str(tmp / "graph.json")
    index_records({"graphPath": graph_path, "records": _seed_records()})
    return graph_path


class DryRunTests(unittest.TestCase):
    """Each mutator must preserve graph bytes under dryRun=True."""

    def test_classify_record_dry_run_echoes_marker(self):
        # classify_record doesn't write, but a dryRun call must still
        # echo the flag so callers can tell it's a dry run.
        result = classify_record(
            {
                "record": {"id": "tmp", "title": "hi", "markers": {}},
                "dryRun": True,
            }
        )
        self.assertTrue(result.get("dryRun"))

    def test_index_records_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = _seed_graph(Path(tmp))
            before = Path(graph_path).read_bytes()

            new_record = {
                "id": "r3",
                "title": "Unrelated note",
                "content": "Should not be written in dry-run",
                "markers": {"type": "bug"},
            }
            result = index_records(
                {
                    "graphPath": graph_path,
                    "records": [new_record],
                    "dryRun": True,
                }
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            # The summary must reflect what would happen.
            self.assertIn("r3", result["upsertedIds"])
            self.assertEqual(result["recordCount"], 3)

    def test_delete_record_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = _seed_graph(Path(tmp))
            before = Path(graph_path).read_bytes()
            result = delete_record(
                {"graphPath": graph_path, "recordId": "r1", "dryRun": True}
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            self.assertEqual(result["deletedId"], "r1")
            # Summary reflects what WOULD happen: record count drops by 1.
            self.assertEqual(result["recordCount"], 1)

    def test_archive_record_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = _seed_graph(Path(tmp))
            before = Path(graph_path).read_bytes()
            result = archive_record(
                {"graphPath": graph_path, "recordId": "r1", "dryRun": True}
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            self.assertTrue(result["archived"])

    def test_unarchive_record_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = _seed_graph(Path(tmp))
            # First archive for real so unarchive has something to clear.
            archive_record({"graphPath": graph_path, "recordId": "r1"})
            before = Path(graph_path).read_bytes()
            result = unarchive_record(
                {"graphPath": graph_path, "recordId": "r1", "dryRun": True}
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            self.assertFalse(result["archived"])

    def test_promote_pattern_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = _seed_graph(Path(tmp))
            before = Path(graph_path).read_bytes()
            result = promote_pattern(
                {
                    "graphPath": graph_path,
                    "recordIds": ["r1", "r2"],
                    "writeToGraph": True,
                    "dryRun": True,
                }
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            # Promoted record should still be computed.
            self.assertIsNotNone(result.get("promotedRecord"))

    def test_ingest_markdown_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            graph_path = _seed_graph(tmp_path)
            md_dir = tmp_path / "mdroot"
            md_dir.mkdir()
            (md_dir / "a.md").write_text(
                "---\ntype: bug\n---\n# Sample\nbody\n", encoding="utf-8"
            )
            before = Path(graph_path).read_bytes()
            result = ingest_markdown(
                {
                    "rootPath": str(md_dir),
                    "graphPath": graph_path,
                    "index": True,
                    "dryRun": True,
                }
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            # The records list is still computed and surfaced.
            self.assertEqual(result["fileCount"], 1)
            self.assertTrue(result["indexResult"].get("dryRun"))

    def test_ingest_notion_export_dry_run_leaves_graph_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            graph_path = _seed_graph(tmp_path)
            exp_dir = tmp_path / "export"
            exp_dir.mkdir()
            (exp_dir / "Note aabbccddeeff00112233445566778899.md").write_text(
                "# Notion Note\nbody\n", encoding="utf-8"
            )
            before = Path(graph_path).read_bytes()
            result = ingest_notion_export(
                {
                    "rootPath": str(exp_dir),
                    "graphPath": graph_path,
                    "index": True,
                    "dryRun": True,
                }
            )
            after = Path(graph_path).read_bytes()
            self.assertEqual(before, after)
            self.assertTrue(result.get("dryRun"))
            self.assertEqual(result["fileCount"], 1)
            self.assertTrue(result["indexResult"].get("dryRun"))


if __name__ == "__main__":
    unittest.main()
