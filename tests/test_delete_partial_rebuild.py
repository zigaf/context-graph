"""Tests for per-neighbor partial edge rebuild on delete.

These tests pin down the invariant that `delete_record` must produce the same
graph (up to time-dependent `updatedAt` stamps) as a legacy full-rebuild
delete would. They also cover createdAt preservation, untouched
non-dirty edges, zero-neighbor deletes, cascades, and explicit-edge survival.
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    delete_record,
    index_records,
    load_graph,
    load_schema,
    rebuild_edges,
    rebuild_edges_for_neighbors,
    write_graph,
)


def _normalize_edge(edge: dict) -> tuple:
    """Return a comparable tuple for an edge, dropping time-dependent fields.

    `updatedAt` is deliberately excluded because it always reflects
    `now_iso()` at write time and is not load-bearing for the invariant.
    `createdAt` IS included — it must survive the rebuild.
    """
    return (
        edge.get("source"),
        edge.get("target"),
        edge.get("type"),
        edge.get("kind"),
        round(float(edge.get("confidence", 0.0)), 6),
        tuple(edge.get("matchedMarkers", []) or []),
        tuple(edge.get("sharedTokens", []) or []),
        edge.get("createdAt"),
    )


def _normalized_edge_set(edges):
    return sorted(_normalize_edge(edge) for edge in edges)


def _normalized_record_ids(records):
    return sorted(records.keys())


def delete_record_legacy_fullrebuild(graph_path: str, record_id: str) -> None:
    """Test-only helper: the pre-Phase-6 delete behavior.

    Drops edges touching the deleted record, then invokes a full
    `rebuild_edges` over every survivor. This is the invariant target the
    new per-neighbor implementation must match.
    """
    schema = load_schema()
    graph = load_graph(graph_path)
    records = dict(graph.get("records", {}))
    if record_id not in records:
        return
    records.pop(record_id)
    remaining = [
        edge for edge in graph.get("edges", [])
        if edge.get("source") != record_id and edge.get("target") != record_id
    ]
    graph["records"] = records
    graph["edges"] = rebuild_edges(records, schema, remaining)
    write_graph(graph, graph_path)


def _ten_record_corpus() -> list[dict]:
    """Seed a modest corpus with marker overlap so inference produces edges.

    Records share markers in clusters so inference emits non-trivial
    inferred edges. Some records also carry explicit relations to exercise
    the explicit path.
    """
    corpus = [
        {
            "id": f"r{i}",
            "title": f"Record {i}",
            "content": f"Payment webhook reliability note number {i} for deposit flow.",
            "markers": {
                "type": "bug" if i % 2 == 0 else "rule",
                "domain": "payments",
                "flow": "webhook",
                "artifact": "endpoint" if i % 3 == 0 else "service",
                "goal": "stabilize-flow",
                "status": "in-progress" if i % 2 == 0 else "done",
                "severity": "critical" if i % 2 == 0 else "high",
            },
        }
        for i in range(10)
    ]
    # Add an explicit relation from r0 -> r1 and r2 -> r3 so we exercise
    # explicit edge preservation.
    corpus[0]["relations"] = {
        "explicit": [{"type": "related_to", "target": "r1"}],
        "inferred": [],
    }
    corpus[2]["relations"] = {
        "explicit": [{"type": "depends_on", "target": "r3"}],
        "inferred": [],
    }
    return corpus


class PerNeighborRebuildEquivalenceTests(unittest.TestCase):
    def test_delete_matches_full_rebuild_output(self):
        """Invariant: per-neighbor delete must yield the same graph as
        full-rebuild delete, ignoring only time-dependent updatedAt fields."""
        records = _ten_record_corpus()
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = str(Path(tmpdir) / "new.json")
            legacy_path = str(Path(tmpdir) / "legacy.json")

            index_records({"graphPath": new_path, "records": copy.deepcopy(records)})
            index_records({"graphPath": legacy_path, "records": copy.deepcopy(records)})

            # Age inferred createdAt stamps on both copies to something
            # stable so we can check createdAt preservation after delete.
            for path in (new_path, legacy_path):
                g = load_graph(path)
                for edge in g["edges"]:
                    if edge.get("kind") == "inferred":
                        edge["createdAt"] = "2025-01-01T00:00:00+00:00"
                write_graph(g, path)

            # Run the production delete on one copy, the legacy full-rebuild
            # delete on the other.
            delete_record({"graphPath": new_path, "recordId": "r5"})
            delete_record_legacy_fullrebuild(legacy_path, "r5")

            new_graph = load_graph(new_path)
            legacy_graph = load_graph(legacy_path)

            self.assertEqual(
                _normalized_record_ids(new_graph["records"]),
                _normalized_record_ids(legacy_graph["records"]),
            )
            self.assertEqual(
                _normalized_edge_set(new_graph["edges"]),
                _normalized_edge_set(legacy_graph["edges"]),
            )

    def test_createdAt_preserved_on_surviving_inferred_edges(self):
        records = _ten_record_corpus()
        stamp = "2024-06-01T12:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "g.json")
            index_records({"graphPath": graph_path, "records": records})
            g = load_graph(graph_path)
            for edge in g["edges"]:
                if edge.get("kind") == "inferred":
                    edge["createdAt"] = stamp
            write_graph(g, graph_path)

            # Find an inferred edge that will survive (neither endpoint is r5
            # and at least one endpoint is NOT a neighbor of r5).
            pre = load_graph(graph_path)
            r5_neighbors = set()
            for edge in pre["edges"]:
                if edge.get("source") == "r5":
                    r5_neighbors.add(edge.get("target"))
                elif edge.get("target") == "r5":
                    r5_neighbors.add(edge.get("source"))
            inferred_survivors_pre = [
                (e["source"], e["target"], e["type"])
                for e in pre["edges"]
                if e.get("kind") == "inferred"
                and e.get("source") != "r5" and e.get("target") != "r5"
            ]
            self.assertGreater(len(inferred_survivors_pre), 0)

            delete_record({"graphPath": graph_path, "recordId": "r5"})

            post = load_graph(graph_path)
            post_inferred = [e for e in post["edges"] if e.get("kind") == "inferred"]
            self.assertGreater(len(post_inferred), 0)
            for edge in post_inferred:
                self.assertEqual(
                    edge.get("createdAt"), stamp,
                    f"inferred edge {edge.get('source')}->{edge.get('target')} "
                    f"lost its createdAt stamp"
                )

    def test_non_dirty_edges_bit_identical(self):
        """An edge whose endpoints are both outside the dirty set must be
        unchanged in every field (including createdAt and updatedAt)."""
        # Build a graph with two disjoint clusters so the deletion's dirty
        # set cannot reach the far cluster.
        cluster_a = [
            {
                "id": "a1",
                "title": "A1",
                "content": "alpha content one",
                "markers": {"type": "bug", "domain": "alpha", "flow": "one", "artifact": "endpoint"},
            },
            {
                "id": "a2",
                "title": "A2",
                "content": "alpha content two",
                "markers": {"type": "bug", "domain": "alpha", "flow": "one", "artifact": "endpoint"},
            },
        ]
        cluster_c = [
            {
                "id": "c1",
                "title": "C1",
                "content": "gamma content alpha beta",
                "markers": {"type": "rule", "domain": "gamma", "flow": "four", "artifact": "service"},
            },
            {
                "id": "c2",
                "title": "C2",
                "content": "gamma content alpha beta",
                "markers": {"type": "rule", "domain": "gamma", "flow": "four", "artifact": "service"},
            },
        ]
        to_delete = {
            "id": "r",
            "title": "R",
            "content": "alpha content one",
            "markers": {"type": "bug", "domain": "alpha", "flow": "one", "artifact": "endpoint"},
        }
        all_records = cluster_a + cluster_c + [to_delete]
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "g.json")
            index_records({"graphPath": graph_path, "records": all_records})
            # Stamp every inferred edge with a known createdAt.
            g = load_graph(graph_path)
            for edge in g["edges"]:
                if edge.get("kind") == "inferred":
                    edge["createdAt"] = "2024-03-03T03:03:03+00:00"
            write_graph(g, graph_path)

            pre = load_graph(graph_path)
            pre_edges_by_key = {
                (e["source"], e["target"], e["type"]): e for e in pre["edges"]
            }
            # Identify edges whose both endpoints are strictly in cluster_c
            # and hence cannot be in the dirty set of deleting r.
            c_ids = {"c1", "c2"}
            far_keys = [
                k for k, e in pre_edges_by_key.items()
                if e["source"] in c_ids and e["target"] in c_ids
            ]
            self.assertGreater(len(far_keys), 0, "fixture must produce at least one c-cluster edge")

            delete_record({"graphPath": graph_path, "recordId": "r"})
            post = load_graph(graph_path)
            post_edges_by_key = {
                (e["source"], e["target"], e["type"]): e for e in post["edges"]
            }
            for key in far_keys:
                self.assertIn(key, post_edges_by_key)
                before = pre_edges_by_key[key]
                after = post_edges_by_key[key]
                # Every load-bearing field must be bit-identical, including
                # updatedAt — we never touched this edge.
                for field in ("source", "target", "type", "kind", "confidence",
                              "matchedMarkers", "sharedTokens",
                              "createdAt", "updatedAt"):
                    self.assertEqual(
                        before.get(field), after.get(field),
                        f"field {field!r} changed on untouched edge {key}"
                    )

    def test_zero_neighbor_delete_is_noop_except_record_removal(self):
        # A record with no edges to anyone: build it isolated.
        iso = {
            "id": "iso",
            "title": "Isolated",
            "content": "zzz nothing matches this content anywhere",
            "markers": {},
        }
        other = {
            "id": "o1",
            "title": "Other",
            "content": "payments webhook deposit flow",
            "markers": {
                "type": "bug", "domain": "payments", "flow": "webhook",
                "artifact": "endpoint",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "g.json")
            index_records({"graphPath": graph_path, "records": [iso, other]})
            pre = load_graph(graph_path)
            # Confirm iso has no edges.
            for edge in pre["edges"]:
                self.assertNotEqual(edge.get("source"), "iso")
                self.assertNotEqual(edge.get("target"), "iso")
            edges_before = _normalized_edge_set(pre["edges"])
            delete_record({"graphPath": graph_path, "recordId": "iso"})
            post = load_graph(graph_path)
            self.assertNotIn("iso", post["records"])
            self.assertEqual(_normalized_edge_set(post["edges"]), edges_before)

    def test_cascade_delete_matches_two_legacy_full_rebuilds(self):
        records = _ten_record_corpus()
        with tempfile.TemporaryDirectory() as tmpdir:
            new_path = str(Path(tmpdir) / "n.json")
            legacy_path = str(Path(tmpdir) / "l.json")
            index_records({"graphPath": new_path, "records": copy.deepcopy(records)})
            index_records({"graphPath": legacy_path, "records": copy.deepcopy(records)})

            # Stamp createdAt so preservation is testable.
            for path in (new_path, legacy_path):
                g = load_graph(path)
                for edge in g["edges"]:
                    if edge.get("kind") == "inferred":
                        edge["createdAt"] = "2025-01-01T00:00:00+00:00"
                write_graph(g, path)

            delete_record({"graphPath": new_path, "recordId": "r3"})
            delete_record({"graphPath": new_path, "recordId": "r7"})
            delete_record_legacy_fullrebuild(legacy_path, "r3")
            delete_record_legacy_fullrebuild(legacy_path, "r7")

            new_g = load_graph(new_path)
            leg_g = load_graph(legacy_path)
            self.assertEqual(
                _normalized_record_ids(new_g["records"]),
                _normalized_record_ids(leg_g["records"]),
            )
            self.assertEqual(
                _normalized_edge_set(new_g["edges"]),
                _normalized_edge_set(leg_g["edges"]),
            )

    def test_explicit_edges_on_neighbors_survive(self):
        # Build three records where a2 has an explicit edge to a3. Then
        # delete a1 which is a marker-overlap neighbor of a2. The explicit
        # edge a2 -> a3 must survive.
        a1 = {
            "id": "a1",
            "title": "A1",
            "content": "payments webhook deposit",
            "markers": {
                "type": "bug", "domain": "payments", "flow": "webhook",
                "artifact": "endpoint",
            },
        }
        a2 = {
            "id": "a2",
            "title": "A2",
            "content": "payments webhook deposit idempotency",
            "markers": {
                "type": "bug", "domain": "payments", "flow": "webhook",
                "artifact": "endpoint",
            },
            "relations": {
                "explicit": [{"type": "fixes", "target": "a3"}],
                "inferred": [],
            },
        }
        a3 = {
            "id": "a3",
            "title": "A3",
            "content": "unrelated document about the foo bar",
            "markers": {"type": "rule", "domain": "other"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "g.json")
            index_records({"graphPath": graph_path, "records": [a1, a2, a3]})
            pre = load_graph(graph_path)
            explicit_pre = [
                e for e in pre["edges"]
                if e.get("kind") == "explicit" and e.get("source") == "a2"
                and e.get("target") == "a3"
            ]
            self.assertEqual(len(explicit_pre), 1)

            delete_record({"graphPath": graph_path, "recordId": "a1"})
            post = load_graph(graph_path)
            explicit_post = [
                e for e in post["edges"]
                if e.get("kind") == "explicit" and e.get("source") == "a2"
                and e.get("target") == "a3"
            ]
            self.assertEqual(len(explicit_post), 1,
                             "explicit edge a2->a3 must survive deletion of a1")

    def test_helper_with_empty_dirty_set_returns_graph_unchanged(self):
        records = _ten_record_corpus()
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_path = str(Path(tmpdir) / "g.json")
            index_records({"graphPath": graph_path, "records": records})
            g = load_graph(graph_path)
            before = _normalized_edge_set(g["edges"])
            rebuild_edges_for_neighbors(g, set())
            after = _normalized_edge_set(g["edges"])
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
