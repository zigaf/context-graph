from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from session_start_prime import prime_session  # noqa: E402


class SessionStartPrimeTests(unittest.TestCase):
    def _make_workspace_with_rules(self, tmp: str) -> Path:
        from context_graph_core import init_workspace, index_records
        init_workspace({"rootPath": tmp})
        index_records({
            "graphPath": str(Path(tmp) / ".context-graph" / "graph.json"),
            "records": [
                {
                    "id": "r-rule-1",
                    "title": "Always idempotent webhooks",
                    "content": "Use Idempotency-Key header on every webhook POST.",
                    "markers": {"type": "rule", "scope": "convention",
                                "domain": "payments", "goal": "prevent-regression",
                                "status": "done"},
                    "tokens": ["webhook", "idempotency"],
                },
                {
                    "id": "r-task-1",
                    "title": "Add refund endpoint",
                    "content": "PM asked for a /refund POST.",
                    "markers": {"type": "task", "domain": "payments",
                                "goal": "fix-bug", "status": "in-progress"},
                    "tokens": ["refund", "endpoint"],
                },
            ],
        })
        return Path(tmp).resolve()

    def test_returns_payload_with_rules_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_workspace_with_rules(tmp)
            payload = prime_session(workspace_root=Path(tmp))
            ids = {r["id"] for r in payload.get("rules", [])}
            self.assertIn("r-rule-1", ids)
            self.assertNotIn("r-task-1", ids)  # task is not a rule

    def test_returns_empty_when_no_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = prime_session(workspace_root=Path(tmp))
            self.assertEqual(payload, {"workspace": None})

    def test_includes_bootstrap_hint_when_needed(self):
        from context_graph_core import init_workspace
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            payload = prime_session(workspace_root=Path(tmp))
            self.assertTrue(payload.get("bootstrapNeeded"))

    def test_no_bootstrap_hint_after_decline(self):
        from context_graph_core import init_workspace
        from curator_bootstrap import mark_bootstrap_declined
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            mark_bootstrap_declined(Path(tmp))
            payload = prime_session(workspace_root=Path(tmp))
            self.assertFalse(payload.get("bootstrapNeeded"))

    def test_main_prints_json(self):
        from context_graph_core import init_workspace
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            buf = io.StringIO()
            from session_start_prime import main as prime_main
            with redirect_stdout(buf):
                exit_code = prime_main(["--workspace-root", tmp])
            self.assertEqual(exit_code, 0)
            data = json.loads(buf.getvalue())
            self.assertIn("workspace", data)


if __name__ == "__main__":
    unittest.main()
