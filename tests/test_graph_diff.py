"""Tests for the ``graph-diff`` tool.

Two small graphs that differ by exactly:
- one added record
- one removed record
- one modified record (title + markers + content hash)
- one added edge
- one removed edge

All five categories must be populated correctly. The JSON mode must
serialize the same structure so callers can pipe it into other tools.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import graph_diff  # noqa: E402


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=True, indent=2)
        f.write("\n")


def _build_graphs() -> tuple[dict, dict]:
    # Left graph: three records, two edges.
    left = {
        "version": "0.1.0",
        "updatedAt": "2026-04-20T00:00:00+00:00",
        "records": {
            "r1": {
                "id": "r1",
                "title": "Original title",
                "content": "Original body",
                "markers": {"type": "bug", "domain": "payments"},
                "last_edited_time": "2026-04-20T00:00:00+00:00",
                "revision": {"version": 1, "updatedAt": "2026-04-20T00:00:00+00:00"},
            },
            "r2": {
                "id": "r2",
                "title": "Stable note",
                "content": "Stable body",
                "markers": {"type": "rule"},
                "last_edited_time": "2026-04-20T00:00:00+00:00",
                "revision": {"version": 1, "updatedAt": "2026-04-20T00:00:00+00:00"},
            },
            "r3": {
                "id": "r3",
                "title": "Doomed note",
                "content": "Will be gone in right",
                "markers": {"type": "task"},
                "last_edited_time": "2026-04-20T00:00:00+00:00",
                "revision": {"version": 1, "updatedAt": "2026-04-20T00:00:00+00:00"},
            },
        },
        "edges": [
            {"source": "r1", "target": "r2", "type": "related_pattern", "kind": "inferred", "confidence": 0.5},
            {"source": "r1", "target": "r3", "type": "might_affect", "kind": "inferred", "confidence": 0.4},
        ],
        "stats": {"recordCount": 3, "edgeCount": 2},
    }
    # Right graph: r3 removed, r4 added, r1 modified; edges: r1->r3 removed,
    # r1->r4 added.
    right = {
        "version": "0.1.0",
        "updatedAt": "2026-04-22T00:00:00+00:00",
        "records": {
            "r1": {
                "id": "r1",
                "title": "Updated title",
                "content": "Updated body",
                "markers": {"type": "bug", "domain": "billing"},
                "last_edited_time": "2026-04-22T00:00:00+00:00",
                "revision": {"version": 2, "updatedAt": "2026-04-22T00:00:00+00:00"},
            },
            "r2": {
                "id": "r2",
                "title": "Stable note",
                "content": "Stable body",
                "markers": {"type": "rule"},
                "last_edited_time": "2026-04-20T00:00:00+00:00",
                "revision": {"version": 1, "updatedAt": "2026-04-20T00:00:00+00:00"},
            },
            "r4": {
                "id": "r4",
                "title": "Brand new note",
                "content": "Added in right",
                "markers": {"type": "decision"},
                "last_edited_time": "2026-04-22T00:00:00+00:00",
                "revision": {"version": 1, "updatedAt": "2026-04-22T00:00:00+00:00"},
            },
        },
        "edges": [
            {"source": "r1", "target": "r2", "type": "related_pattern", "kind": "inferred", "confidence": 0.5},
            {"source": "r1", "target": "r4", "type": "might_affect", "kind": "inferred", "confidence": 0.45},
        ],
        "stats": {"recordCount": 3, "edgeCount": 2},
    }
    return left, right


class GraphDiffTests(unittest.TestCase):
    def test_diff_populates_all_categories(self):
        left, right = _build_graphs()
        result = graph_diff({"left": left, "right": right})

        added_ids = {item["id"] for item in result["recordsAdded"]}
        removed_ids = {item["id"] for item in result["recordsRemoved"]}
        modified_ids = {item["id"] for item in result["recordsModified"]}
        self.assertEqual(added_ids, {"r4"})
        self.assertEqual(removed_ids, {"r3"})
        self.assertEqual(modified_ids, {"r1"})

        # The modified entry reports per-field changes including the content
        # hash rather than full bodies.
        mod = next(item for item in result["recordsModified"] if item["id"] == "r1")
        self.assertIn("title", mod["changes"])
        self.assertIn("contentHash", mod["changes"])
        self.assertIn("markers", mod["changes"])
        # Nothing leaks the raw body — only a hash of it.
        self.assertNotIn("Updated body", json.dumps(mod))

        edges_added = {(e["source"], e["target"], e["type"]) for e in result["edgesAdded"]}
        edges_removed = {(e["source"], e["target"], e["type"]) for e in result["edgesRemoved"]}
        self.assertEqual(edges_added, {("r1", "r4", "might_affect")})
        self.assertEqual(edges_removed, {("r1", "r3", "might_affect")})

        self.assertEqual(result["summary"]["recordsAdded"], 1)
        self.assertEqual(result["summary"]["recordsRemoved"], 1)
        self.assertEqual(result["summary"]["recordsModified"], 1)
        self.assertEqual(result["summary"]["edgesAdded"], 1)
        self.assertEqual(result["summary"]["edgesRemoved"], 1)

    def test_diff_from_paths(self):
        left, right = _build_graphs()
        with tempfile.TemporaryDirectory() as tmp:
            left_path = Path(tmp) / "left" / "graph.json"
            right_path = Path(tmp) / "right" / "graph.json"
            _write(left_path, left)
            _write(right_path, right)
            result = graph_diff({"leftPath": str(left_path), "rightPath": str(right_path)})
            self.assertEqual(result["summary"]["recordsAdded"], 1)
            self.assertEqual(result["summary"]["recordsRemoved"], 1)

    def test_cli_json_mode(self):
        left, right = _build_graphs()
        with tempfile.TemporaryDirectory() as tmp:
            left_path = Path(tmp) / "left" / "graph.json"
            right_path = Path(tmp) / "right" / "graph.json"
            _write(left_path, left)
            _write(right_path, right)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "context_graph_cli.py"),
                    "graph-diff",
                    "--left",
                    str(left_path),
                    "--right",
                    str(right_path),
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["recordsAdded"], 1)
            self.assertEqual(payload["summary"]["recordsRemoved"], 1)
            self.assertEqual(payload["summary"]["edgesAdded"], 1)

    def test_cli_text_mode(self):
        left, right = _build_graphs()
        with tempfile.TemporaryDirectory() as tmp:
            left_path = Path(tmp) / "left" / "graph.json"
            right_path = Path(tmp) / "right" / "graph.json"
            _write(left_path, left)
            _write(right_path, right)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "context_graph_cli.py"),
                    "graph-diff",
                    "--left",
                    str(left_path),
                    "--right",
                    str(right_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            out = proc.stdout
            # Summary line appears verbatim.
            self.assertIn("1 records added", out)
            self.assertIn("1 removed", out)
            self.assertIn("1 modified", out)
            self.assertIn("1 edges added", out)

    def test_identical_graphs_diff_empty(self):
        left, _ = _build_graphs()
        # Diff a graph with itself — every list empty and summary zeros.
        result = graph_diff({"left": left, "right": left})
        self.assertEqual(result["recordsAdded"], [])
        self.assertEqual(result["recordsRemoved"], [])
        self.assertEqual(result["recordsModified"], [])
        self.assertEqual(result["edgesAdded"], [])
        self.assertEqual(result["edgesRemoved"], [])


if __name__ == "__main__":
    unittest.main()
