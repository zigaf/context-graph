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
    apply_proposal_decision,
    index_records,
    init_workspace,
    learn_schema,
    list_proposals,
)


class ProposalLifecycleTests(unittest.TestCase):
    def _make_workspace(self, tmp: Path) -> Path:
        root = tmp.resolve()
        init_workspace({"rootPath": str(root)})
        return root

    def test_learn_schema_writes_learned_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            records = [
                {
                    "id": "1",
                    "title": "challenge payment",
                    "content": "challenge payment flow",
                    "source": {"metadata": {"parent": "kenmore > Tasks"}},
                },
                {
                    "id": "2",
                    "title": "challenge payment",
                    "content": "challenge payment retry",
                    "source": {"metadata": {"parent": "kenmore > Tasks"}},
                },
            ]
            index_records(
                {
                    "graphPath": str(root / ".context-graph" / "graph.json"),
                    "records": records,
                    "workspaceRoot": str(root),
                }
            )
            result = learn_schema({"workspaceRoot": str(root)})
            proposal_count = sum(len(items) for items in result["proposals"].values())
            self.assertGreater(proposal_count, 0)
            self.assertTrue((root / ".context-graph" / "schema.learned.json").exists())

    def test_list_proposals_returns_pending_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            records = [
                {
                    "id": "1",
                    "title": "challenge payment",
                    "content": "challenge payment challenge payment challenge payment",
                    "source": {"metadata": {"parent": "kenmore > Tasks"}},
                },
                {
                    "id": "2",
                    "title": "challenge payment",
                    "content": "challenge payment challenge payment challenge payment",
                    "source": {"metadata": {"parent": "kenmore > Tasks"}},
                },
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            learn_schema({"workspaceRoot": str(root)})
            result = list_proposals({"workspaceRoot": str(root)})
            self.assertIn("pending", result)

    def test_apply_accept_moves_to_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            (root / ".context-graph" / "schema.learned.json").write_text(
                json.dumps(
                    {
                        "version": "1",
                        "proposals": {
                            "pending": [
                                {"value": "challenge", "source": "hierarchy", "confidence": 0.95}
                            ],
                            "rejected": [],
                        },
                        "accepted": {},
                    }
                ),
                encoding="utf-8",
            )
            apply_proposal_decision(
                {
                    "workspaceRoot": str(root),
                    "value": "challenge",
                    "decision": "accept",
                    "field": "domain",
                }
            )
            learned = json.loads((root / ".context-graph" / "schema.learned.json").read_text())
            self.assertIn("challenge", learned["accepted"].get("domain", []))
            self.assertEqual(learned["proposals"]["pending"], [])

    def test_apply_reject_moves_to_rejected_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            (root / ".context-graph" / "schema.learned.json").write_text(
                json.dumps(
                    {
                        "version": "1",
                        "proposals": {
                            "pending": [
                                {"value": "bl-api", "source": "hierarchy", "confidence": 0.80}
                            ],
                            "rejected": [],
                        },
                        "accepted": {},
                    }
                ),
                encoding="utf-8",
            )
            apply_proposal_decision(
                {
                    "workspaceRoot": str(root),
                    "value": "bl-api",
                    "decision": "reject",
                }
            )
            learned = json.loads((root / ".context-graph" / "schema.learned.json").read_text())
            rejected_values = [item["value"] for item in learned["proposals"]["rejected"]]
            self.assertIn("bl-api", rejected_values)


if __name__ == "__main__":
    unittest.main()
