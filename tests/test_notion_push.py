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
    apply_push_result,
    index_records,
    list_pushable_records,
    load_push_state,
    plan_push,
    push_state_path,
    record_to_notion_blocks,
    save_push_state,
)
from notion_sync import push_to_notion  # noqa: E402


def _make_workspace(tmp: str, notion_root: str | None = None) -> Path:
    root = Path(tmp).resolve()
    cg = root / ".context-graph"
    cg.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "version": "1",
        "id": "ws-test",
        "rootPath": str(root),
    }
    if notion_root:
        manifest["notion"] = {"rootPageId": notion_root}
    (cg / "workspace.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _seed_records(graph_path: str, workspace_root: Path) -> list[str]:
    """Seed a graph with two rule-like and one task-like record."""
    index_records(
        {
            "graphPath": graph_path,
            "workspaceRoot": str(workspace_root),
            "records": [
                {
                    "id": "promoted:rule-a",
                    "title": "Rule A",
                    "content": "# Rule A\n\nAlways retry on 5xx.",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {"system": "context-graph", "metadata": {}},
                },
                {
                    "id": "promoted:decision-b",
                    "title": "Decision B",
                    "content": "# Decision B\n\nAdopt idempotency keys.",
                    "markers": {"type": "decision", "status": "done"},
                    "source": {"system": "context-graph", "metadata": {}},
                },
                {
                    "id": "task:c",
                    "title": "Task C",
                    "content": "Ordinary incident, should be skipped.",
                    "markers": {"type": "incident", "status": "in-progress"},
                    "source": {"system": "markdown", "metadata": {}},
                },
            ],
        }
    )
    return ["promoted:rule-a", "promoted:decision-b", "task:c"]


class ListPushableRecordsTests(unittest.TestCase):
    def test_filters_to_rule_and_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            records = list_pushable_records(graph_path)
            ids = sorted(record["id"] for record in records)
            self.assertEqual(ids, ["promoted:decision-b", "promoted:rule-a"])

    def test_respects_explicit_record_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            records = list_pushable_records(
                graph_path, record_ids=["promoted:rule-a", "task:c"]
            )
            ids = sorted(record["id"] for record in records)
            # Explicit record_ids bypass the marker filter so callers can push
            # any record they name; the default scope is the only filter.
            self.assertEqual(ids, ["promoted:rule-a", "task:c"])

    def test_missing_record_id_is_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            records = list_pushable_records(graph_path, record_ids=["does-not-exist"])
            self.assertEqual(records, [])


class PushStateRoundTripTests(unittest.TestCase):
    def test_load_missing_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page")
            self.assertEqual(load_push_state(workspace), {"pending": [], "records": {}})

    def test_round_trip_preserves_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page")
            state = {
                "pending": [],
                "records": {
                    "promoted:rule-a": {
                        "notionPageId": "notion-page-1",
                        "lastPushedRevision": None,
                        "lastPushedAt": None,
                    },
                    "promoted:decision-b": {
                        "notionPageId": "notion-page-2",
                        "lastPushedRevision": None,
                        "lastPushedAt": None,
                    },
                },
            }
            save_push_state(state, workspace)
            loaded = load_push_state(workspace)
            self.assertEqual(loaded, state)

    def test_path_helper_points_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page")
            path = push_state_path(workspace)
            self.assertEqual(path, workspace / ".context-graph" / "notion_push.json")


class PlanPushTests(unittest.TestCase):
    def test_empty_state_classifies_everything_as_create(self):
        records = [
            {"id": "promoted:rule-a", "title": "Rule A"},
            {"id": "promoted:decision-b", "title": "Decision B"},
        ]
        plan = plan_push(records, {"pending": [], "records": {}})
        self.assertEqual([item["id"] for item in plan["creates"]], ["promoted:rule-a", "promoted:decision-b"])
        self.assertEqual(plan["updates"], [])

    def test_seeded_state_classifies_known_ids_as_updates(self):
        records = [
            {"id": "promoted:rule-a", "title": "Rule A"},
            {"id": "promoted:decision-b", "title": "Decision B"},
        ]
        state = {
            "pending": [],
            "records": {
                "promoted:rule-a": {
                    "notionPageId": "notion-page-1",
                    "lastPushedRevision": None,
                    "lastPushedAt": None,
                }
            },
        }
        plan = plan_push(records, state)
        self.assertEqual([item["id"] for item in plan["creates"]], ["promoted:decision-b"])
        self.assertEqual(len(plan["updates"]), 1)
        self.assertEqual(plan["updates"][0]["record"]["id"], "promoted:rule-a")
        self.assertEqual(plan["updates"][0]["notionPageId"], "notion-page-1")

    def test_all_known_yields_no_creates(self):
        records = [{"id": "promoted:rule-a"}]
        state = {
            "pending": [],
            "records": {
                "promoted:rule-a": {
                    "notionPageId": "notion-page-1",
                    "lastPushedRevision": None,
                    "lastPushedAt": None,
                }
            },
        }
        plan = plan_push(records, state)
        self.assertEqual(plan["creates"], [])
        self.assertEqual(len(plan["updates"]), 1)


class ApplyPushResultTests(unittest.TestCase):
    def test_adds_new_mapping(self):
        state = {"pending": [], "records": {}}
        result = apply_push_result("promoted:rule-a", "notion-page-1", state)
        self.assertEqual(
            result,
            {
                "pending": [],
                "records": {
                    "promoted:rule-a": {
                        "notionPageId": "notion-page-1",
                        "lastPushedRevision": None,
                        "lastPushedAt": None,
                    }
                },
            },
        )

    def test_overwrites_existing_mapping(self):
        state = {
            "pending": [],
            "records": {
                "promoted:rule-a": {
                    "notionPageId": "old-page",
                    "lastPushedRevision": None,
                    "lastPushedAt": None,
                }
            },
        }
        result = apply_push_result("promoted:rule-a", "new-page", state)
        self.assertEqual(result["records"]["promoted:rule-a"]["notionPageId"], "new-page")

    def test_preserves_other_entries(self):
        state = {
            "pending": [],
            "records": {
                "promoted:rule-a": {
                    "notionPageId": "page-a",
                    "lastPushedRevision": None,
                    "lastPushedAt": None,
                },
                "promoted:decision-b": {
                    "notionPageId": "page-b",
                    "lastPushedRevision": None,
                    "lastPushedAt": None,
                },
            },
        }
        result = apply_push_result("promoted:rule-a", "page-a2", state)
        self.assertEqual(result["records"]["promoted:decision-b"]["notionPageId"], "page-b")
        self.assertEqual(result["records"]["promoted:rule-a"]["notionPageId"], "page-a2")

    def test_does_not_mutate_input(self):
        state = {
            "pending": [],
            "records": {
                "promoted:rule-a": {
                    "notionPageId": "page-a",
                    "lastPushedRevision": None,
                    "lastPushedAt": None,
                }
            },
        }
        apply_push_result("promoted:decision-b", "page-b", state)
        # Input state left untouched; callers must use the returned value.
        self.assertNotIn("promoted:decision-b", state["records"])

    def test_drains_pending_on_success(self):
        state = {
            "pending": ["promoted:rule-a", "promoted:rule-b"],
            "records": {},
        }
        new_state = apply_push_result("promoted:rule-a", "page-1", state)
        self.assertEqual(new_state["pending"], ["promoted:rule-b"])
        # input state must not be mutated
        self.assertEqual(state["pending"], ["promoted:rule-a", "promoted:rule-b"])

    def test_records_revision_and_timestamp(self):
        state = {"pending": [], "records": {}}
        new_state = apply_push_result(
            "promoted:rule-a",
            "page-1",
            state,
            revision=3,
            pushed_at="2026-04-26T18:30:00Z",
        )
        self.assertEqual(
            new_state["records"]["promoted:rule-a"]["lastPushedRevision"], 3
        )
        self.assertEqual(
            new_state["records"]["promoted:rule-a"]["lastPushedAt"],
            "2026-04-26T18:30:00Z",
        )

    def test_defaults_revision_and_timestamp_to_none(self):
        state = {"pending": [], "records": {}}
        new_state = apply_push_result("promoted:rule-a", "page-1", state)
        self.assertIsNone(
            new_state["records"]["promoted:rule-a"]["lastPushedRevision"]
        )
        self.assertIsNone(
            new_state["records"]["promoted:rule-a"]["lastPushedAt"]
        )


class RecordToNotionBlocksTests(unittest.TestCase):
    def test_heading_produces_heading_1_block(self):
        record = {"title": "Rule A", "content": "# Rule A\n\nBody text."}
        blocks = record_to_notion_blocks(record)
        self.assertEqual(blocks[0]["type"], "heading_1")
        heading_payload = blocks[0]["heading_1"]
        self.assertEqual(heading_payload["rich_text"][0]["plain_text"], "Rule A")

    def test_paragraph_produces_paragraph_block(self):
        record = {"title": "Rule A", "content": "Body text."}
        blocks = record_to_notion_blocks(record)
        types = [block["type"] for block in blocks]
        self.assertIn("paragraph", types)
        paragraph = next(block for block in blocks if block["type"] == "paragraph")
        self.assertEqual(paragraph["paragraph"]["rich_text"][0]["plain_text"], "Body text.")

    def test_bulleted_list_produces_bulleted_blocks(self):
        record = {"title": "Rule", "content": "- one\n- two"}
        blocks = record_to_notion_blocks(record)
        bulleted = [block for block in blocks if block["type"] == "bulleted_list_item"]
        self.assertEqual(len(bulleted), 2)
        self.assertEqual(bulleted[0]["bulleted_list_item"]["rich_text"][0]["plain_text"], "one")

    def test_numbered_list_produces_numbered_blocks(self):
        record = {"title": "Rule", "content": "1. first\n2. second"}
        blocks = record_to_notion_blocks(record)
        numbered = [block for block in blocks if block["type"] == "numbered_list_item"]
        self.assertEqual(len(numbered), 2)

    def test_to_do_produces_to_do_blocks(self):
        record = {"title": "Rule", "content": "- [x] done\n- [ ] pending"}
        blocks = record_to_notion_blocks(record)
        todos = [block for block in blocks if block["type"] == "to_do"]
        self.assertEqual(len(todos), 2)
        self.assertTrue(todos[0]["to_do"]["checked"])
        self.assertFalse(todos[1]["to_do"]["checked"])

    def test_code_fence_produces_code_block(self):
        record = {"title": "Rule", "content": "```python\nprint('x')\n```"}
        blocks = record_to_notion_blocks(record)
        code = [block for block in blocks if block["type"] == "code"]
        self.assertEqual(len(code), 1)
        self.assertEqual(code[0]["code"]["language"], "python")
        self.assertIn("print", code[0]["code"]["rich_text"][0]["plain_text"])

    def test_quote_produces_quote_block(self):
        record = {"title": "Rule", "content": "> reliability first"}
        blocks = record_to_notion_blocks(record)
        quotes = [block for block in blocks if block["type"] == "quote"]
        self.assertEqual(len(quotes), 1)
        self.assertIn("reliability", quotes[0]["quote"]["rich_text"][0]["plain_text"])

    def test_divider_produces_divider_block(self):
        record = {"title": "Rule", "content": "---"}
        blocks = record_to_notion_blocks(record)
        dividers = [block for block in blocks if block["type"] == "divider"]
        self.assertEqual(len(dividers), 1)

    def test_empty_content_returns_empty_block_list(self):
        record = {"title": "Empty", "content": ""}
        blocks = record_to_notion_blocks(record)
        self.assertEqual(blocks, [])


class FakeNotionPushClient:
    """Minimal client that mirrors the interface expected by push_to_notion."""

    def __init__(self, new_page_id: str = "new-page-xyz"):
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self._new_page_id = new_page_id

    def create_page(
        self,
        parent_page_id: str,
        title: str,
        blocks: list[dict],
    ) -> dict:
        self.create_calls.append(
            {"parent_page_id": parent_page_id, "title": title, "blocks": blocks}
        )
        return {"id": self._new_page_id}

    def update_page_blocks(self, page_id: str, blocks: list[dict]) -> dict:
        self.update_calls.append({"page_id": page_id, "blocks": blocks})
        return {"id": page_id}


class PushToNotionTests(unittest.TestCase):
    def test_dry_run_returns_plan_without_calling_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page-id")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            client = FakeNotionPushClient()

            result = push_to_notion(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "client": client,
                    # dry_run defaults to True.
                }
            )

            self.assertTrue(result["dryRun"])
            self.assertEqual(len(result["plan"]["creates"]), 2)
            self.assertEqual(result["plan"]["updates"], [])
            self.assertEqual(client.create_calls, [])
            self.assertEqual(client.update_calls, [])

            # State file must not have been written during a dry run.
            state_path = push_state_path(workspace)
            self.assertFalse(state_path.exists())

    def test_apply_mode_creates_new_pages_and_records_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page-id")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            client = FakeNotionPushClient(new_page_id="notion-new-1")

            result = push_to_notion(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "client": client,
                    "dryRun": False,
                }
            )

            self.assertFalse(result["dryRun"])
            self.assertEqual(len(client.create_calls), 2)
            self.assertEqual(len(client.update_calls), 0)

            # All create calls used the root page id from the workspace manifest.
            parents = {call["parent_page_id"] for call in client.create_calls}
            self.assertEqual(parents, {"root-page-id"})

            # Push state now has entries for both promoted records.
            state = load_push_state(workspace)
            self.assertEqual(state["records"]["promoted:rule-a"]["notionPageId"], "notion-new-1")
            self.assertEqual(state["records"]["promoted:decision-b"]["notionPageId"], "notion-new-1")

    def test_apply_mode_updates_existing_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page-id")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            # Pre-seed the push state so both rules already map to Notion pages.
            save_push_state(
                {
                    "pending": [],
                    "records": {
                        "promoted:rule-a": {
                            "notionPageId": "existing-page-a",
                            "lastPushedRevision": None,
                            "lastPushedAt": None,
                        },
                        "promoted:decision-b": {
                            "notionPageId": "existing-page-b",
                            "lastPushedRevision": None,
                            "lastPushedAt": None,
                        },
                    },
                },
                workspace,
            )

            client = FakeNotionPushClient()

            result = push_to_notion(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "client": client,
                    "dryRun": False,
                }
            )

            self.assertFalse(result["dryRun"])
            self.assertEqual(len(client.create_calls), 0)
            self.assertEqual(len(client.update_calls), 2)
            updated_ids = {call["page_id"] for call in client.update_calls}
            self.assertEqual(updated_ids, {"existing-page-a", "existing-page-b"})

    def test_re_run_is_idempotent(self):
        """A second apply-mode push must not create duplicate Notion pages."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page-id")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            first_client = FakeNotionPushClient(new_page_id="notion-first")
            push_to_notion(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "client": first_client,
                    "dryRun": False,
                }
            )
            self.assertEqual(len(first_client.create_calls), 2)

            # Second run with a fresh client: every record should route through
            # update_page_blocks using the ids from the first run. No creates.
            second_client = FakeNotionPushClient(new_page_id="WOULD-BE-DUPLICATE")
            second_result = push_to_notion(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "client": second_client,
                    "dryRun": False,
                }
            )

            self.assertEqual(
                second_client.create_calls,
                [],
                "Second push must not create duplicate Notion pages.",
            )
            self.assertEqual(len(second_client.update_calls), 2)
            self.assertFalse(second_result["dryRun"])

    def test_missing_notion_root_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root=None)
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            with self.assertRaises(ValueError) as ctx:
                push_to_notion(
                    {
                        "graphPath": graph_path,
                        "workspaceRoot": str(workspace),
                        "client": FakeNotionPushClient(),
                        "dryRun": False,
                    }
                )
            self.assertIn("notionRootPageId", str(ctx.exception))

    def test_explicit_record_ids_scope_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = _make_workspace(tmp, notion_root="root-page-id")
            graph_path = str(workspace / ".context-graph" / "graph.json")
            _seed_records(graph_path, workspace)

            client = FakeNotionPushClient(new_page_id="notion-scoped")
            result = push_to_notion(
                {
                    "graphPath": graph_path,
                    "workspaceRoot": str(workspace),
                    "client": client,
                    "recordIds": ["promoted:rule-a"],
                    "dryRun": False,
                }
            )

            self.assertFalse(result["dryRun"])
            self.assertEqual(len(client.create_calls), 1)
            state = load_push_state(workspace)
            self.assertIn("promoted:rule-a", state["records"])
            self.assertNotIn("promoted:decision-b", state["records"])


class PushableTypeExpansionTests(unittest.TestCase):
    PUSHABLE_TYPES = {
        "rule", "decision", "gotcha", "module-boundary",
        "convention", "task", "bug", "bug-fix",
    }

    def test_all_seven_curator_types_are_pushable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page")
            graph_path = str(ws / ".context-graph" / "graph.json")
            records = []
            for marker_type in sorted(self.PUSHABLE_TYPES):
                rid = f"promoted:{marker_type}"
                records.append({
                    "id": rid,
                    "title": f"Sample {marker_type}",
                    "content": f"# {marker_type}\n\nBody.",
                    "markers": {"type": marker_type, "status": "done"},
                    "source": {"system": "context-graph", "metadata": {}},
                })
            index_records({"graphPath": graph_path, "workspaceRoot": str(ws), "records": records})
            pushable = {r["id"] for r in list_pushable_records(graph_path)}
            for marker_type in sorted(self.PUSHABLE_TYPES):
                self.assertIn(f"promoted:{marker_type}", pushable)


if __name__ == "__main__":
    unittest.main()
