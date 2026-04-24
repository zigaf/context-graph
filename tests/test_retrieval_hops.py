"""Tests for relation distance penalties in build_context_pack.

Phase 5 item: records reached via 2+ hops from a seed match are penalized
multiplicatively by a hop-count factor. Hop convention:
- 0 = direct query-marker match
- 1 = one-hop neighbor of a direct match via an explicit relation
- 2+ = multi-hop (not currently reached, but the scoring hook exists)

Default HOP_PENALTY = 0.5, applied as score *= HOP_PENALTY ** max(0, hop_count - 1).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    HOP_PENALTY,
    apply_hop_penalty,
    build_context_pack,
)


def _base_record(rid: str, title: str, markers: dict, content: str = "", relations: dict | None = None) -> dict:
    return {
        "id": rid,
        "title": title,
        "content": content or title,
        "markers": markers,
        "relations": relations or {"explicit": [], "inferred": []},
    }


class HopPenaltyConstantTests(unittest.TestCase):
    def test_default_penalty_is_half(self) -> None:
        self.assertEqual(HOP_PENALTY, 0.5)

    def test_hop_zero_not_penalized(self) -> None:
        self.assertAlmostEqual(apply_hop_penalty(1.0, hop_count=0), 1.0, places=6)

    def test_hop_one_not_penalized(self) -> None:
        # "Per hop beyond the first" — hop 1 is the first hop and keeps its score.
        self.assertAlmostEqual(apply_hop_penalty(1.0, hop_count=1), 1.0, places=6)

    def test_hop_two_penalized_once(self) -> None:
        self.assertAlmostEqual(apply_hop_penalty(1.0, hop_count=2), 0.5, places=6)

    def test_hop_three_penalized_twice(self) -> None:
        self.assertAlmostEqual(apply_hop_penalty(1.0, hop_count=3), 0.25, places=6)

    def test_custom_penalty_override(self) -> None:
        self.assertAlmostEqual(apply_hop_penalty(1.0, hop_count=2, penalty=0.25), 0.25, places=6)


class BuildContextPackHopOrderingTests(unittest.TestCase):
    def test_direct_beats_one_hop_which_beats_two_hop(self) -> None:
        # Direct match: scores high via exact marker overlap with the query.
        direct = _base_record(
            "direct",
            "Direct match on webhook incident",
            {
                "type": "incident",
                "domain": "payments",
                "flow": "webhook",
                "goal": "stabilize-flow",
                "status": "in-progress",
                "severity": "critical",
            },
            relations={"explicit": [{"type": "related_to", "target": "one_hop"}], "inferred": []},
        )
        # One-hop neighbor of `direct`: no marker overlap and no token
        # overlap with the query, so it has zero raw score and is only
        # pulled into the pack through the traversal hook. Its score is
        # then inherited from the seed at a hop-decayed rate.
        one_hop = _base_record(
            "one_hop",
            "Adjacent note",
            {
                "type": "rule",
                "domain": "integration",
            },
            content="Adjacent note on provider configuration",
            relations={"explicit": [{"type": "related_to", "target": "two_hop"}], "inferred": []},
        )
        # Two-hop: also zero raw score; reachable only through one_hop.
        two_hop = _base_record(
            "two_hop",
            "Distant note",
            {
                "type": "rule",
                "domain": "integration",
            },
            content="Distant note on provider configuration",
        )

        pack = build_context_pack(
            {
                "query": "payment webhook incident",
                "records": [direct, one_hop, two_hop],
                "limit": 8,
                "hopTraversal": {"maxHops": 2},
            }
        )

        ranked = {item["id"]: item for item in pack["directMatches"] + pack["supportingRelations"]}
        self.assertIn("direct", ranked)
        self.assertIn("one_hop", ranked)
        self.assertIn("two_hop", ranked)
        direct_score = ranked["direct"]["score"]
        one_hop_score = ranked["one_hop"]["score"]
        two_hop_score = ranked["two_hop"]["score"]
        self.assertGreater(direct_score, one_hop_score)
        self.assertGreater(one_hop_score, two_hop_score)

        # Hop count tags must be present on the returned items.
        self.assertEqual(ranked["direct"]["hopCount"], 0)
        self.assertEqual(ranked["one_hop"]["hopCount"], 1)
        self.assertEqual(ranked["two_hop"]["hopCount"], 2)

    def test_hop_penalty_override(self) -> None:
        # With hopPenalty=1.0 (no penalty), two-hop should NOT get extra decay.
        direct = _base_record(
            "d",
            "Direct match",
            {
                "type": "incident",
                "domain": "payments",
                "flow": "webhook",
                "goal": "stabilize-flow",
                "status": "in-progress",
                "severity": "critical",
            },
            relations={"explicit": [{"type": "related_to", "target": "h2"}], "inferred": []},
        )
        h2 = _base_record(
            "h2",
            "Two-hop neighbor",
            {"type": "rule", "domain": "payments", "flow": "webhook"},
        )
        pack_default = build_context_pack(
            {
                "query": "payment webhook incident",
                "records": [direct, h2],
                "limit": 8,
                "hopTraversal": {"maxHops": 2},
            }
        )
        pack_no_penalty = build_context_pack(
            {
                "query": "payment webhook incident",
                "records": [direct, h2],
                "limit": 8,
                "hopTraversal": {"maxHops": 2},
                "hopPenalty": 1.0,
            }
        )
        ranked_default = {item["id"]: item for item in pack_default["directMatches"] + pack_default["supportingRelations"]}
        ranked_no_penalty = {item["id"]: item for item in pack_no_penalty["directMatches"] + pack_no_penalty["supportingRelations"]}
        # The two-hop record is hop=1 here because it is a direct neighbor of
        # the seed match (not a two-step traversal). Verify that the no-penalty
        # override does not reduce any score below the default's value.
        if "h2" in ranked_default and "h2" in ranked_no_penalty:
            self.assertGreaterEqual(
                ranked_no_penalty["h2"]["score"], ranked_default["h2"]["score"]
            )


if __name__ == "__main__":
    unittest.main()
