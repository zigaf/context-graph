# Auto Notion Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual `/cg-sync-notion push` and empty `/cg-bootstrap` skeletons with an auto-push pipeline. Curator captures land in a local queue; recognised "session-end" triggers (keyword phrases, git operations, slash-command completions) drain the queue silently into Notion. `/cg-bootstrap` writes one-paragraph descriptions into every page it creates.

**Architecture:** Extend `notion_push.json` with a queue and per-record revision state. Add a small Python orchestrator (`scripts/auto_push.py`) and a trigger script (`scripts/trigger_detect.py`) wired into `hooks.json`. Bootstrap content generation lives in a new `scripts/bootstrap_content.py` and is called from `commands/cg-bootstrap.md`. The curator skill stops pushing inline and only enqueues record ids. Notion writes still go through the official Notion MCP via the slash-command layer; the new Python scripts only manipulate local state and emit instructions for the slash-command layer to execute. (Slash commands can call MCP tools; standalone Python hooks cannot, so the orchestrator splits its plan into a JSON instruction file that `commands/cg-sync-notion.md` consumes.)

**Tech Stack:** Python 3 stdlib `unittest`, existing Context Graph CLI/MCP, official Notion MCP/OAuth tools, Claude Code hooks (`UserPromptSubmit`, `PostToolUse`, `SlashCommand`).

---

## File Structure

- Modify: `scripts/context_graph_core.py`
  - Extend `load_push_state` / `save_push_state` to support the new schema (per-record dict with `notionPageId`, `lastPushedRevision`, `lastPushedAt`, plus a top-level `pending` list).
  - Add `enqueue_push`, `dequeue_push`, `list_pending_pushes` queue helpers.
  - Update `apply_push_result` to record `lastPushedRevision` and `lastPushedAt`.
  - Update `list_pushable_records` to include all seven curator types
    (`rule`, `decision`, `gotcha`, `module-boundary`, `convention`, `task`, `bug-fix`)
    via marker normalisation, not just `rule` and `decision`.
- Modify: `scripts/context_graph_cli.py`
  - Wire new subcommands: `enqueue-push`, `list-pending-pushes`,
    `prepare-auto-push`, `apply-auto-push-result`.
- Modify: `scripts/context_graph_mcp.py`
  - Expose the same operations as MCP tools so slash commands can call them.
- Create: `scripts/auto_push.py`
  - Pure planner: reads queue + graph + workspace, returns an
    `AutoPushPlan` describing which Notion creates/updates the slash
    command should perform. Does **not** call Notion itself.
- Create: `scripts/trigger_detect.py`
  - Hook entry script. Decides if an event is a real trigger, walks up to
    find the workspace, calls the Python planner, writes the resulting
    plan to `.context-graph/auto_push_plan.json`, and prints a single
    instruction line so Claude knows to run `/cg-sync-notion auto`.
- Create: `scripts/bootstrap_content.py`
  - Generates the per-page paragraphs (root + per-dir + lazy type-index)
    used by `/cg-bootstrap`. Pure — reads files, returns dicts. No network.
- Modify: `commands/cg-bootstrap.md`
  - Call `bootstrap_content` for each page body. Add `--refresh` arg to
    re-run the content generator over existing pages.
- Modify: `commands/cg-sync-notion.md`
  - Add an `auto` mode that reads `.context-graph/auto_push_plan.json`
    and executes the queued create/update calls through the official
    Notion MCP, then calls `apply_auto_push_result`.
- Modify: `commands/cg-init.md`
  - On init, merge auto-push hook entries into `hooks.json` (idempotent).
- Modify: `hooks.json`
  - Add `UserPromptSubmit`, `PostToolUse:Bash`, `SlashCommand` entries
    that invoke `scripts/trigger_detect.py`.
- Modify: `skills/context-graph-curator/SKILL.md`
  - Replace step 5 (inline Notion push) with a single enqueue call.
- Modify: `docs/schema.json`
  - Document the new `notion_push.json` shape and add the
    `markers.notionDir` optional field.
- Create: `tests/test_auto_push_state.py`
  - Tests for the extended push state schema, queue ops, and revision
    tracking in `apply_push_result`.
- Create: `tests/test_auto_push_planner.py`
  - Tests for `scripts/auto_push.py` (target resolution, type-index
    upserts, last-write-wins decisions).
- Create: `tests/test_trigger_detect.py`
  - Tests for keyword / git / slash detection and workspace gating.
- Create: `tests/test_bootstrap_content.py`
  - Tests for the per-dir paragraph generator.
- Modify: `tests/test_curator_skill.py`
  - Add coverage that the curator skill enqueues instead of pushing inline.
- Modify: `README.md`
  - Replace the manual pull-vs-push table with the new auto-push flow.

---

### Task 1: Extend Push State Schema (read path)

**Files:**
- Modify: `scripts/context_graph_core.py:2823-2858`
- Test: `tests/test_auto_push_state.py`

- [ ] **Step 1: Write the failing test for forward-compatible read**

Create `tests/test_auto_push_state.py`:

```python
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
    load_push_state,
    save_push_state,
)


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps({"version": "1", "id": "ws-test", "rootPath": str(root)}),
        encoding="utf-8",
    )
    return root


class LoadPushStateLegacyShapeTests(unittest.TestCase):
    def test_loads_legacy_flat_mapping_as_per_record_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            (ws / ".context-graph" / "notion_push.json").write_text(
                json.dumps({"notion:abc": "page-1", "notion:def": "page-2"}),
                encoding="utf-8",
            )
            state = load_push_state(ws)
            self.assertEqual(state["records"]["notion:abc"]["notionPageId"], "page-1")
            self.assertEqual(state["records"]["notion:def"]["notionPageId"], "page-2")
            self.assertEqual(state["pending"], [])


class LoadPushStateNewShapeTests(unittest.TestCase):
    def test_loads_new_shape_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            payload = {
                "pending": ["notion:abc"],
                "records": {
                    "notion:def": {
                        "notionPageId": "page-2",
                        "lastPushedRevision": 3,
                        "lastPushedAt": "2026-04-26T18:30:00Z",
                    }
                },
            }
            (ws / ".context-graph" / "notion_push.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            state = load_push_state(ws)
            self.assertEqual(state["pending"], ["notion:abc"])
            self.assertEqual(state["records"]["notion:def"]["lastPushedRevision"], 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state
```

Expected: failures because current `load_push_state` returns `{record_id: page_id}` not `{pending, records}`.

- [ ] **Step 3: Update `load_push_state` to return the new shape**

Edit `scripts/context_graph_core.py` around line 2823. Replace the body of `load_push_state` with:

```python
def load_push_state(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Read ``.context-graph/notion_push.json`` as the auto-push state.

    The returned shape is::

        {
            "pending": [recordId, ...],
            "records": {
                recordId: {
                    "notionPageId": str,
                    "lastPushedRevision": int | None,
                    "lastPushedAt": str | None,
                },
                ...,
            },
        }

    Legacy ``{record_id: page_id}`` files are migrated on read into the
    new shape with ``lastPushedRevision = None`` and ``lastPushedAt = None``.
    Missing or malformed files return ``{"pending": [], "records": {}}``.
    """
    start = Path(str(workspace_root)) if workspace_root else None
    try:
        path = push_state_path(start)
    except WorkspaceNotInitializedError:
        return {"pending": [], "records": {}}
    if not path.exists():
        return {"pending": [], "records": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"pending": [], "records": {}}
    if not isinstance(data, dict):
        return {"pending": [], "records": {}}
    if "records" in data or "pending" in data:
        records = data.get("records") or {}
        pending = data.get("pending") or []
        normalised: dict[str, dict[str, Any]] = {}
        for key, value in records.items():
            if isinstance(value, dict) and value.get("notionPageId"):
                normalised[str(key)] = {
                    "notionPageId": str(value["notionPageId"]),
                    "lastPushedRevision": value.get("lastPushedRevision"),
                    "lastPushedAt": value.get("lastPushedAt"),
                }
        return {
            "pending": [str(item) for item in pending if item],
            "records": normalised,
        }
    # Legacy: flat {recordId: pageId} mapping.
    legacy: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if value is None:
            continue
        legacy[str(key)] = {
            "notionPageId": str(value),
            "lastPushedRevision": None,
            "lastPushedAt": None,
        }
    return {"pending": [], "records": legacy}
```

- [ ] **Step 4: Run the test again to verify it passes**

Run:

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state
```

Expected: PASS for both legacy and new-shape loads.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_core.py tests/test_auto_push_state.py
git commit -m "feat: extend notion_push.json to per-record state with pending queue"
```

---

### Task 2: Update `save_push_state` for the new schema

**Files:**
- Modify: `scripts/context_graph_core.py:2847-2858`
- Test: `tests/test_auto_push_state.py`

- [ ] **Step 1: Add a save round-trip test**

Append to `tests/test_auto_push_state.py`:

```python
class SavePushStateTests(unittest.TestCase):
    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            payload = {
                "pending": ["notion:abc", "notion:def"],
                "records": {
                    "notion:def": {
                        "notionPageId": "page-2",
                        "lastPushedRevision": 4,
                        "lastPushedAt": "2026-04-26T19:00:00Z",
                    }
                },
            }
            save_push_state(payload, ws)
            again = load_push_state(ws)
            self.assertEqual(again, payload)

    def test_save_rejects_non_dict_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            with self.assertRaises(TypeError):
                save_push_state(["notion:abc"], ws)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the test**

Run:

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.SavePushStateTests
```

Expected: failure (current `save_push_state` flattens to `{record_id: page_id}`).

- [ ] **Step 3: Replace `save_push_state` in `scripts/context_graph_core.py`**

```python
def save_push_state(
    state: dict[str, Any],
    workspace_root: Path | str | None = None,
) -> None:
    """Persist the auto-push state to ``.context-graph/notion_push.json``."""
    if not isinstance(state, dict):
        raise TypeError("save_push_state expects a dict with 'pending' and 'records' keys")
    start = Path(str(workspace_root)) if workspace_root else None
    path = push_state_path(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending_in = state.get("pending") or []
    records_in = state.get("records") or {}
    serialised: dict[str, Any] = {
        "pending": [str(item) for item in pending_in if item],
        "records": {},
    }
    for key, value in records_in.items():
        if not isinstance(value, dict) or not value.get("notionPageId"):
            continue
        serialised["records"][str(key)] = {
            "notionPageId": str(value["notionPageId"]),
            "lastPushedRevision": value.get("lastPushedRevision"),
            "lastPushedAt": value.get("lastPushedAt"),
        }
    with path.open("w", encoding="utf-8") as f:
        json.dump(serialised, f, ensure_ascii=True, indent=2, sort_keys=True)
        f.write("\n")
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.SavePushStateTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_core.py tests/test_auto_push_state.py
git commit -m "feat: persist auto-push state with per-record revision tracking"
```

---

### Task 3: Migrate Existing Callers To New State Shape

**Files:**
- Modify: `scripts/context_graph_core.py` (callers of `load_push_state` / `save_push_state` / `plan_push` / `apply_push_result`)
- Modify: `scripts/context_graph_mcp.py:266-307`
- Modify: `tests/test_notion_push.py` (already passes against old shape; needs to pass against new shape too)

- [ ] **Step 1: Run the existing push tests to see what breaks**

Run:

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_push tests.test_mcp_push
```

Expected: failures because callers still expect `{record_id: page_id}` (a flat dict), not `{pending, records}`.

- [ ] **Step 2: Update `plan_push` to read the new shape**

Find `plan_push` in `scripts/context_graph_core.py` (around line 2861). Replace:

```python
def plan_push(
    records: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Classify ``records`` into creates vs updates against the auto-push ``state``.

    Pure: does not touch the network or disk. Records present in
    ``state["records"]`` become ``updates`` paired with their existing
    Notion page id; everything else becomes a ``create``.
    """
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    records_state = state.get("records") or {}
    for record in records:
        record_id = record.get("id")
        if not record_id:
            continue
        mapped = records_state.get(str(record_id))
        if isinstance(mapped, dict) and mapped.get("notionPageId"):
            updates.append({"record": record, "notionPageId": mapped["notionPageId"]})
        else:
            creates.append(record)
    return {"creates": creates, "updates": updates}
```

- [ ] **Step 3: Update `apply_push_result` to track revision and timestamp**

Replace `apply_push_result`:

```python
def apply_push_result(
    record_id: str,
    notion_page_id: str,
    state: dict[str, Any],
    *,
    revision: int | None = None,
    pushed_at: str | None = None,
) -> dict[str, Any]:
    """Return a new state dict with the per-record entry updated.

    Does not mutate the input state. ``revision`` and ``pushed_at`` are
    optional but should be passed by callers that have classified the
    record (so we know whether the next push needs to update or skip).
    """
    new_state: dict[str, Any] = {
        "pending": list(state.get("pending") or []),
        "records": dict(state.get("records") or {}),
    }
    new_state["records"][str(record_id)] = {
        "notionPageId": str(notion_page_id),
        "lastPushedRevision": revision,
        "lastPushedAt": pushed_at,
    }
    new_state["pending"] = [item for item in new_state["pending"] if item != str(record_id)]
    return new_state
```

- [ ] **Step 4: Update the MCP handler in `scripts/context_graph_mcp.py:291-306`**

Replace the body of `handle_apply_notion_push_result`:

```python
def handle_apply_notion_push_result(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    notion_page_id = arguments.get("notionPageId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    if not notion_page_id:
        raise ValueError("Missing required field: notionPageId")
    revision_input = arguments.get("revision")
    pushed_at_input = arguments.get("pushedAt")
    workspace = _workspace_from_args(arguments)
    state = load_push_state(workspace)
    new_state = apply_push_result(
        str(record_id),
        str(notion_page_id),
        state,
        revision=int(revision_input) if revision_input is not None else None,
        pushed_at=str(pushed_at_input) if pushed_at_input else None,
    )
    save_push_state(new_state, workspace)
    return {
        "recordId": str(record_id),
        "notionPageId": str(notion_page_id),
        "pushState": new_state,
    }
```

- [ ] **Step 5: Update existing tests in `tests/test_notion_push.py`**

The existing tests build state with the legacy flat dict. Adjust assertions to look up `state["records"][record_id]["notionPageId"]` instead of `state[record_id]`.

Search-and-replace inside the file:

- `self.assertEqual(state["promoted:rule-a"], "page-rule-a")`
  becomes
  `self.assertEqual(state["records"]["promoted:rule-a"]["notionPageId"], "page-rule-a")`

Apply the same pattern to every assertion that indexes the old flat shape. (Use the test runner output from Step 1 as a checklist of failing assertions.)

- [ ] **Step 6: Run the affected tests**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_push tests.test_mcp_push tests.test_auto_push_state
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/context_graph_core.py scripts/context_graph_mcp.py tests/test_notion_push.py
git commit -m "feat: track lastPushedRevision/lastPushedAt in apply_push_result"
```

---

### Task 4: Add `enqueue_push` / `dequeue_push` / `list_pending_pushes`

**Files:**
- Modify: `scripts/context_graph_core.py` (new functions near the existing push helpers, after Task 3 changes)
- Test: `tests/test_auto_push_state.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_push_state.py`:

```python
from context_graph_core import (  # noqa: E402
    enqueue_push,
    dequeue_push,
    list_pending_pushes,
)


class QueueOpsTests(unittest.TestCase):
    def test_enqueue_then_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-a", workspace_root=ws)
            enqueue_push("notion:rule-b", workspace_root=ws)
            pending = list_pending_pushes(workspace_root=ws)
            self.assertEqual(pending, ["notion:rule-a", "notion:rule-b"])

    def test_enqueue_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-a", workspace_root=ws)
            enqueue_push("notion:rule-a", workspace_root=ws)
            self.assertEqual(list_pending_pushes(workspace_root=ws), ["notion:rule-a"])

    def test_dequeue_removes_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-a", workspace_root=ws)
            enqueue_push("notion:rule-b", workspace_root=ws)
            dequeue_push("notion:rule-a", workspace_root=ws)
            self.assertEqual(list_pending_pushes(workspace_root=ws), ["notion:rule-b"])

    def test_dequeue_unknown_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            dequeue_push("notion:none", workspace_root=ws)  # must not raise
            self.assertEqual(list_pending_pushes(workspace_root=ws), [])
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.QueueOpsTests
```

Expected: failures because the helpers do not exist yet.

- [ ] **Step 3: Add the helpers to `scripts/context_graph_core.py`**

Add (just below `apply_push_result`):

```python
def list_pending_pushes(workspace_root: Path | str | None = None) -> list[str]:
    """Return the queued record ids in arrival order."""
    state = load_push_state(workspace_root)
    return list(state.get("pending") or [])


def enqueue_push(record_id: str, workspace_root: Path | str | None = None) -> list[str]:
    """Append ``record_id`` to the push queue if not already present.

    Returns the new queue.
    """
    record_id = str(record_id)
    state = load_push_state(workspace_root)
    pending = list(state.get("pending") or [])
    if record_id not in pending:
        pending.append(record_id)
    state["pending"] = pending
    save_push_state(state, workspace_root)
    return pending


def dequeue_push(record_id: str, workspace_root: Path | str | None = None) -> list[str]:
    """Remove ``record_id`` from the push queue. Idempotent."""
    record_id = str(record_id)
    state = load_push_state(workspace_root)
    pending = [item for item in (state.get("pending") or []) if item != record_id]
    state["pending"] = pending
    save_push_state(state, workspace_root)
    return pending
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.QueueOpsTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_core.py tests/test_auto_push_state.py
git commit -m "feat: add push queue ops (enqueue, dequeue, list)"
```

---

### Task 5: Expand `list_pushable_records` To All Seven Curator Types

**Files:**
- Modify: `scripts/context_graph_core.py` (find `list_pushable_records`)
- Test: `tests/test_notion_push.py`

- [ ] **Step 1: Locate the existing filter**

Run:

```bash
grep -n "def list_pushable_records" /Users/maksnalyvaiko/context-graph/scripts/context_graph_core.py
```

Expected: a single match. Read 30 lines starting at the match offset to see the current filter set.

- [ ] **Step 2: Add a failing test for the expanded type set**

Append to `tests/test_notion_push.py`:

```python
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
```

- [ ] **Step 3: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_push.PushableTypeExpansionTests
```

Expected: failure for the types currently not in the filter (everything except `rule` and `decision`).

- [ ] **Step 4: Update the filter in `scripts/context_graph_core.py`**

Inside `list_pushable_records`, replace the type-set literal with:

```python
PUSHABLE_TYPES = {
    "rule",
    "decision",
    "gotcha",
    "module-boundary",
    "convention",
    "task",
    "bug",
    "bug-fix",
}
```

(Both `bug` and `bug-fix` are accepted because the schema today uses
`type=bug, status=fixed` for the bug-fix curator signal.)

- [ ] **Step 5: Run all push-touching tests**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_push tests.test_mcp_push
```

Expected: all PASS, including the new test.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_notion_push.py
git commit -m "feat: push all seven curator types to Notion (not just rule/decision)"
```

---

### Task 6: Add CLI Subcommands For Queue Ops

**Files:**
- Modify: `scripts/context_graph_cli.py`
- Test: `tests/test_dry_run.py` (add a happy-path subcommand smoke test) or `tests/test_auto_push_state.py`

- [ ] **Step 1: Add a CLI smoke test**

Append to `tests/test_auto_push_state.py`:

```python
import subprocess


class CliQueueSubcommandTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "context_graph_cli.py"

    def _run(self, payload: dict, command: str, ws: Path) -> dict:
        proc = subprocess.run(
            ["python3", str(self.SCRIPT), command],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(ws),
            check=False,
        )
        if proc.returncode != 0:
            self.fail(f"{command} failed: {proc.stderr}")
        return json.loads(proc.stdout)

    def test_enqueue_then_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            self._run({"recordId": "notion:abc"}, "enqueue-push", ws)
            self._run({"recordId": "notion:def"}, "enqueue-push", ws)
            result = self._run({}, "list-pending-pushes", ws)
            self.assertEqual(result["pending"], ["notion:abc", "notion:def"])
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.CliQueueSubcommandTests
```

Expected: failure (subcommands not registered).

- [ ] **Step 3: Add the CLI handlers**

Open `scripts/context_graph_cli.py`. Find the dispatch dictionary (search for an existing case like `"index-records"` to anchor). Add three handlers and three dispatch entries:

```python
def _handle_enqueue_push(payload: dict) -> dict:
    record_id = payload.get("recordId") or payload.get("record_id")
    if not record_id:
        raise SystemExit("enqueue-push requires recordId")
    workspace = payload.get("workspaceRoot")
    pending = enqueue_push(str(record_id), workspace_root=workspace)
    return {"pending": pending}


def _handle_dequeue_push(payload: dict) -> dict:
    record_id = payload.get("recordId") or payload.get("record_id")
    if not record_id:
        raise SystemExit("dequeue-push requires recordId")
    workspace = payload.get("workspaceRoot")
    pending = dequeue_push(str(record_id), workspace_root=workspace)
    return {"pending": pending}


def _handle_list_pending_pushes(payload: dict) -> dict:
    workspace = payload.get("workspaceRoot")
    return {"pending": list_pending_pushes(workspace_root=workspace)}
```

Wire them into the dispatcher (next to existing entries):

```python
"enqueue-push": _handle_enqueue_push,
"dequeue-push": _handle_dequeue_push,
"list-pending-pushes": _handle_list_pending_pushes,
```

Make sure the imports at the top of `context_graph_cli.py` include the
new helpers:

```python
from context_graph_core import (
    ...,
    enqueue_push,
    dequeue_push,
    list_pending_pushes,
)
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.CliQueueSubcommandTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_cli.py tests/test_auto_push_state.py
git commit -m "feat(cli): expose enqueue-push, dequeue-push, list-pending-pushes"
```

---

### Task 7: Expose Queue Ops As MCP Tools

**Files:**
- Modify: `scripts/context_graph_mcp.py`
- Test: `tests/test_mcp_push.py` (add cases) or new `tests/test_mcp_auto_push.py`

- [ ] **Step 1: Write the failing MCP test**

Create `tests/test_mcp_auto_push.py`:

```python
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

from context_graph_mcp import handle_tool_call  # noqa: E402


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps({"version": "1", "id": "ws-test", "rootPath": str(root)}),
        encoding="utf-8",
    )
    return root


class EnqueuePushTests(unittest.TestCase):
    def test_enqueue_round_trips_through_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            handle_tool_call(
                "enqueue_push",
                {"recordId": "notion:rule-a", "workspaceRoot": str(ws)},
            )
            result = handle_tool_call(
                "list_pending_pushes", {"workspaceRoot": str(ws)}
            )
            self.assertEqual(result["pending"], ["notion:rule-a"])
```

(If `handle_tool_call` is named differently in `context_graph_mcp.py`,
substitute the public dispatch function used by the existing
`tests/test_mcp_push.py`. Run `grep -n "def handle" scripts/context_graph_mcp.py`
to confirm.)

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_mcp_auto_push
```

Expected: failure (tool not registered).

- [ ] **Step 3: Register the tools**

In `scripts/context_graph_mcp.py`, add three handler functions next to
`handle_apply_notion_push_result`:

```python
def handle_enqueue_push(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    workspace = _workspace_from_args(arguments)
    pending = enqueue_push(str(record_id), workspace_root=workspace)
    return {"pending": pending}


def handle_dequeue_push(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    workspace = _workspace_from_args(arguments)
    pending = dequeue_push(str(record_id), workspace_root=workspace)
    return {"pending": pending}


def handle_list_pending_pushes(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_args(arguments)
    return {"pending": list_pending_pushes(workspace_root=workspace)}
```

Then add three entries to the `TOOL_REGISTRY` (where `plan_notion_push`
and `apply_notion_push_result` live, around line 903):

```python
ToolDef(
    name="enqueue_push",
    description="Append a record id to the local Notion auto-push queue (deduped).",
    input_schema={...standard properties: recordId, workspaceRoot...},
    handler=handle_enqueue_push,
),
ToolDef(
    name="dequeue_push",
    description="Remove a record id from the local Notion auto-push queue.",
    input_schema={...},
    handler=handle_dequeue_push,
),
ToolDef(
    name="list_pending_pushes",
    description="Return the record ids waiting in the local Notion auto-push queue.",
    input_schema={...},
    handler=handle_list_pending_pushes,
),
```

The `input_schema` follows the same shape as `apply_notion_push_result`'s
schema — copy that one, drop the `notionPageId` field for the queue
ops, and keep `recordId` (required) plus the standard `workspaceRoot`
(optional). For `list_pending_pushes`, only `workspaceRoot` is
optional; no required fields.

Add the imports near the top of the file:

```python
from context_graph_core import (
    ...,
    enqueue_push,
    dequeue_push,
    list_pending_pushes,
)
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_mcp_auto_push
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_mcp.py tests/test_mcp_auto_push.py
git commit -m "feat(mcp): expose queue ops as MCP tools"
```

---

### Task 8: Implement `scripts/auto_push.py` Planner

**Files:**
- Create: `scripts/auto_push.py`
- Test: `tests/test_auto_push_planner.py`

- [ ] **Step 1: Write the failing planner test**

Create `tests/test_auto_push_planner.py`:

```python
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
    enqueue_push,
    index_records,
    save_push_state,
)
from auto_push import build_plan  # noqa: E402


def _make_workspace(tmp: str, *, notion_root: str | None = None,
                    dir_pages: dict | None = None) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "version": "1",
        "id": "ws-test",
        "rootPath": str(root),
    }
    if notion_root:
        manifest["notion"] = {
            "rootPageId": notion_root,
            "dirPageIds": dir_pages or {},
        }
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return root


class BuildPlanTests(unittest.TestCase):
    def test_no_workspace_notion_means_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)  # no notion config
            enqueue_push("notion:abc", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertTrue(plan["blocked"])
            self.assertEqual(plan["reason"], "no-notion-root")
            self.assertEqual(plan["creates"], [])
            self.assertEqual(plan["updates"], [])

    def test_pending_create_resolved_to_dir_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                tmp,
                notion_root="root-page",
                dir_pages={"bl-api/": "bl-api-page"},
            )
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-x",
                    "title": "Rule X",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore > bl-api/"},
                    },
                }],
            })
            enqueue_push("notion:rule-x", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertFalse(plan["blocked"])
            self.assertEqual(len(plan["creates"]), 1)
            self.assertEqual(plan["creates"][0]["recordId"], "notion:rule-x")
            self.assertEqual(plan["creates"][0]["parentPageId"], "bl-api-page")

    def test_pending_create_with_explicit_notion_dir_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                tmp,
                notion_root="root-page",
                dir_pages={"core/": "core-page", "admin/": "admin-page"},
            )
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-y",
                    "title": "Rule Y",
                    "content": "Body",
                    "markers": {
                        "type": "rule",
                        "status": "done",
                        "notionDir": "admin/",
                    },
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore > core/"},
                    },
                }],
            })
            enqueue_push("notion:rule-y", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"][0]["parentPageId"], "admin-page")

    def test_pending_cross_cutting_falls_back_to_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(
                tmp,
                notion_root="root-page",
                dir_pages={"bl-api/": "bl-api-page"},
            )
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-z",
                    "title": "Rule Z",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore"},
                    },
                }],
            })
            enqueue_push("notion:rule-z", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"][0]["parentPageId"], "root-page")

    def test_pending_arbitration_record_is_skipped_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page", dir_pages={})
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:pending",
                    "title": "Unresolved",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {
                            "classifierNotes": {"arbiter": "pending-arbitration"},
                        },
                    },
                }],
            })
            enqueue_push("notion:pending", workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"], [])
            self.assertEqual(plan["updates"], [])
            self.assertIn("notion:pending", plan["skipped"])
            self.assertEqual(plan["skipped"]["notion:pending"], "pending-arbitration")

    def test_revision_unchanged_means_skip_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page", dir_pages={})
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-w",
                    "title": "Rule W",
                    "content": "Body v1",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {"system": "notion", "metadata": {}},
                    "revision": {"version": 1},
                }],
            })
            save_push_state({
                "pending": ["notion:rule-w"],
                "records": {
                    "notion:rule-w": {
                        "notionPageId": "page-w",
                        "lastPushedRevision": 1,
                        "lastPushedAt": "2026-04-25T12:00:00Z",
                    }
                },
            }, workspace_root=ws)
            plan = build_plan(workspace_root=ws)
            self.assertEqual(plan["creates"], [])
            self.assertEqual(plan["updates"], [])
            self.assertEqual(plan["skipped"]["notion:rule-w"], "no-revision-change")
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_planner
```

Expected: failures (`auto_push.build_plan` does not exist).

- [ ] **Step 3: Implement `scripts/auto_push.py`**

Create the file:

```python
"""Pure planner for Context Graph auto-push.

The planner reads the local push queue, the local graph, and the
workspace manifest, and returns a structured plan that the slash-command
layer (commands/cg-sync-notion.md auto-mode) executes against the
official Notion MCP.

This module never makes network calls. It produces JSON-serialisable
dicts only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_graph_core import (
    default_graph_path,
    list_pending_pushes,
    list_pushable_records,
    load_push_state,
)


PUSHABLE_TYPES = {
    "rule",
    "decision",
    "gotcha",
    "module-boundary",
    "convention",
    "task",
    "bug",
    "bug-fix",
}


def _load_workspace_manifest(workspace_root: Path) -> dict[str, Any]:
    manifest_path = workspace_root / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_parent_page_id(
    record: dict[str, Any],
    notion_config: dict[str, Any],
) -> str | None:
    dir_pages: dict[str, str] = notion_config.get("dirPageIds") or {}
    root_id: str | None = notion_config.get("rootPageId")
    markers = record.get("markers") or {}
    notion_dir_override = markers.get("notionDir")
    if notion_dir_override and notion_dir_override in dir_pages:
        return dir_pages[notion_dir_override]
    parent = ((record.get("source") or {}).get("metadata") or {}).get("parent") or ""
    segments = [seg.strip() for seg in parent.split(">") if seg.strip()]
    for segment in reversed(segments):
        if segment in dir_pages:
            return dir_pages[segment]
        if (segment + "/") in dir_pages:
            return dir_pages[segment + "/"]
    return root_id


def _record_revision(record: dict[str, Any]) -> int | None:
    revision = record.get("revision")
    if isinstance(revision, dict) and isinstance(revision.get("version"), int):
        return int(revision["version"])
    return None


def _is_pushable(record: dict[str, Any]) -> tuple[bool, str | None]:
    markers = record.get("markers") or {}
    record_type = markers.get("type")
    if record_type not in PUSHABLE_TYPES:
        return False, "non-pushable-type"
    classifier_notes = (
        ((record.get("source") or {}).get("metadata") or {})
        .get("classifierNotes")
        or {}
    )
    if classifier_notes.get("arbiter") == "pending-arbitration":
        return False, "pending-arbitration"
    return True, None


def build_plan(*, workspace_root: Path | str) -> dict[str, Any]:
    """Build an auto-push plan for the workspace's pending queue."""
    ws = Path(str(workspace_root))
    manifest = _load_workspace_manifest(ws)
    notion_config = manifest.get("notion") or {}
    if not notion_config.get("rootPageId"):
        return {
            "blocked": True,
            "reason": "no-notion-root",
            "creates": [],
            "updates": [],
            "skipped": {},
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }
    pending_ids = list_pending_pushes(workspace_root=ws)
    if not pending_ids:
        return {
            "blocked": False,
            "creates": [],
            "updates": [],
            "skipped": {},
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }
    graph_path = str(default_graph_path(ws))
    pending_set = set(pending_ids)
    candidate_records = [
        record
        for record in list_pushable_records(graph_path, record_ids=pending_ids)
        if record.get("id") in pending_set
    ]
    by_id = {str(rec.get("id")): rec for rec in candidate_records}
    push_state = load_push_state(ws)
    state_records = push_state.get("records") or {}
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    for record_id in pending_ids:
        record = by_id.get(record_id)
        if record is None:
            skipped[record_id] = "missing-from-graph"
            continue
        ok, reason = _is_pushable(record)
        if not ok:
            skipped[record_id] = reason or "not-pushable"
            continue
        parent_page_id = _resolve_parent_page_id(record, notion_config)
        if not parent_page_id:
            skipped[record_id] = "no-parent-resolved"
            continue
        revision = _record_revision(record)
        existing = state_records.get(record_id) or {}
        if existing.get("notionPageId"):
            last_pushed = existing.get("lastPushedRevision")
            if (
                revision is not None
                and last_pushed is not None
                and revision <= int(last_pushed)
            ):
                skipped[record_id] = "no-revision-change"
                continue
            updates.append({
                "recordId": record_id,
                "notionPageId": existing["notionPageId"],
                "revision": revision,
            })
        else:
            creates.append({
                "recordId": record_id,
                "parentPageId": parent_page_id,
                "revision": revision,
            })
    return {
        "blocked": False,
        "creates": creates,
        "updates": updates,
        "skipped": skipped,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_planner
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/auto_push.py tests/test_auto_push_planner.py
git commit -m "feat: add auto-push planner that resolves parent pages and skip reasons"
```

---

### Task 9: Add `prepare-auto-push` CLI Subcommand

**Files:**
- Modify: `scripts/context_graph_cli.py`
- Test: `tests/test_auto_push_planner.py` (extend)

- [ ] **Step 1: Add the CLI smoke test**

Append to `tests/test_auto_push_planner.py`:

```python
import subprocess


class CliPrepareAutoPushTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "context_graph_cli.py"

    def test_prepare_writes_plan_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp, notion_root="root-page", dir_pages={})
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-cli",
                    "title": "CLI rule",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {"system": "notion", "metadata": {}},
                }],
            })
            enqueue_push("notion:rule-cli", workspace_root=ws)
            proc = subprocess.run(
                ["python3", str(self.SCRIPT), "prepare-auto-push"],
                input="{}",
                capture_output=True,
                text=True,
                cwd=str(ws),
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["plan"]["blocked"])
            self.assertEqual(len(payload["plan"]["creates"]), 1)
            plan_path = ws / ".context-graph" / "auto_push_plan.json"
            self.assertTrue(plan_path.exists())
            self.assertEqual(
                json.loads(plan_path.read_text())["creates"][0]["recordId"],
                "notion:rule-cli",
            )
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_planner.CliPrepareAutoPushTests
```

Expected: failure (subcommand not registered).

- [ ] **Step 3: Add `_handle_prepare_auto_push`**

Edit `scripts/context_graph_cli.py`. Import `build_plan`:

```python
from auto_push import build_plan
```

Add the handler:

```python
def _handle_prepare_auto_push(payload: dict) -> dict:
    workspace = payload.get("workspaceRoot")
    if workspace is None:
        # Default to cwd; build_plan will validate.
        workspace = "."
    workspace_path = Path(str(workspace)).resolve()
    plan = build_plan(workspace_root=workspace_path)
    plan_path = workspace_path / ".context-graph" / "auto_push_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    with plan_path.open("w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return {"planPath": str(plan_path), "plan": plan}
```

Wire it into the dispatch table:

```python
"prepare-auto-push": _handle_prepare_auto_push,
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_planner.CliPrepareAutoPushTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_cli.py tests/test_auto_push_planner.py
git commit -m "feat(cli): add prepare-auto-push that writes auto_push_plan.json"
```

---

### Task 10: Add `apply-auto-push-result` CLI Subcommand

**Files:**
- Modify: `scripts/context_graph_cli.py`
- Test: `tests/test_auto_push_state.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auto_push_state.py`:

```python
class CliApplyAutoPushResultTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "context_graph_cli.py"

    def test_apply_records_revision_and_dequeues(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            enqueue_push("notion:rule-z", workspace_root=ws)
            payload = {
                "results": [{
                    "recordId": "notion:rule-z",
                    "notionPageId": "page-z",
                    "revision": 2,
                    "pushedAt": "2026-04-26T20:00:00Z",
                }],
            }
            proc = subprocess.run(
                ["python3", str(self.SCRIPT), "apply-auto-push-result"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                cwd=str(ws),
                check=True,
            )
            result = json.loads(proc.stdout)
            self.assertEqual(result["pushState"]["pending"], [])
            self.assertEqual(
                result["pushState"]["records"]["notion:rule-z"]["lastPushedRevision"],
                2,
            )
            self.assertEqual(
                result["pushState"]["records"]["notion:rule-z"]["notionPageId"],
                "page-z",
            )
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.CliApplyAutoPushResultTests
```

Expected: failure.

- [ ] **Step 3: Add the handler**

Edit `scripts/context_graph_cli.py`. Add:

```python
def _handle_apply_auto_push_result(payload: dict) -> dict:
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise SystemExit("apply-auto-push-result requires 'results' to be a list")
    workspace = payload.get("workspaceRoot")
    state = load_push_state(workspace_root=workspace)
    for entry in results:
        record_id = entry.get("recordId")
        notion_page_id = entry.get("notionPageId")
        if not record_id or not notion_page_id:
            continue
        revision = entry.get("revision")
        pushed_at = entry.get("pushedAt")
        state = apply_push_result(
            str(record_id),
            str(notion_page_id),
            state,
            revision=int(revision) if revision is not None else None,
            pushed_at=str(pushed_at) if pushed_at else None,
        )
    save_push_state(state, workspace_root=workspace)
    return {"pushState": state}
```

Wire into dispatch:

```python
"apply-auto-push-result": _handle_apply_auto_push_result,
```

Make sure the imports include `load_push_state`, `save_push_state`, and
`apply_push_result`.

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_state.CliApplyAutoPushResultTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_cli.py tests/test_auto_push_state.py
git commit -m "feat(cli): add apply-auto-push-result that drains the queue per success"
```

---

### Task 11: Implement `scripts/trigger_detect.py`

**Files:**
- Create: `scripts/trigger_detect.py`
- Test: `tests/test_trigger_detect.py`

- [ ] **Step 1: Write the failing trigger-detection tests**

Create `tests/test_trigger_detect.py`:

```python
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from trigger_detect import (  # noqa: E402
    is_keyword_trigger,
    is_git_trigger,
    is_slash_trigger,
    main as trigger_main,
)


class KeywordTriggerTests(unittest.TestCase):
    def test_russian_keywords_match(self):
        for phrase in ("готово", "ship it", "merged", "закоммитим", "doc done"):
            self.assertTrue(
                is_keyword_trigger(phrase),
                f"expected match for {phrase!r}",
            )

    def test_unrelated_text_does_not_match(self):
        self.assertFalse(is_keyword_trigger("just some random sentence"))
        self.assertFalse(is_keyword_trigger(""))


class GitTriggerTests(unittest.TestCase):
    def test_git_commit_matches(self):
        self.assertTrue(is_git_trigger("git commit -m 'feat: x'"))
        self.assertTrue(is_git_trigger("git push origin main"))
        self.assertTrue(is_git_trigger("git merge feature/x"))
        self.assertTrue(is_git_trigger("git tag v0.1.0"))

    def test_non_git_bash_skipped(self):
        self.assertFalse(is_git_trigger("ls -la"))
        self.assertFalse(is_git_trigger("git status"))
        self.assertFalse(is_git_trigger(""))


class SlashTriggerTests(unittest.TestCase):
    def test_listed_slash_commands_match(self):
        self.assertTrue(is_slash_trigger("/commit"))
        self.assertTrue(is_slash_trigger("/create-pr"))
        self.assertTrue(is_slash_trigger("/ship"))
        self.assertTrue(is_slash_trigger("/pr-review"))

    def test_unlisted_slash_commands_skipped(self):
        self.assertFalse(is_slash_trigger("/cg-search"))
        self.assertFalse(is_slash_trigger("/help"))
        self.assertFalse(is_slash_trigger(""))


class WorkspaceGatingTests(unittest.TestCase):
    def test_no_workspace_means_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=tmp)
            self.assertEqual(exit_code, 0)
            self.assertEqual(buf.getvalue().strip(), "")

    def test_workspace_with_trigger_emits_instruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp).resolve()
            (ws / ".context-graph").mkdir(parents=True, exist_ok=True)
            (ws / ".context-graph" / "workspace.json").write_text(
                json.dumps({"version": "1", "id": "t", "rootPath": str(ws)}),
                encoding="utf-8",
            )
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=str(ws))
            self.assertEqual(exit_code, 0)
            output = buf.getvalue().strip()
            self.assertIn("Run /cg-sync-notion auto", output)
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_trigger_detect
```

Expected: failures (script does not exist).

- [ ] **Step 3: Implement `scripts/trigger_detect.py`**

```python
"""Hook entry script for Context Graph auto-push triggers.

Reads a JSON event payload from stdin (Claude Code hook contract),
decides whether the event qualifies as a session-end trigger
(keyword phrase, git operation, or listed slash command), confirms a
workspace exists, and emits an instruction line on stdout that tells
Claude to run ``/cg-sync-notion auto``.

This script never makes Notion API calls. The slash-command layer does.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


KEYWORDS_RU = (
    "готово", "закоммить", "закоммитим", "закругляемся",
    "закрываем задачу", "закрыли", "запушил", "запуш",
    "задеплоил", "деплой", "доделал", "доделали",
    "закончил", "закончили", "мержим", "замержил",
    "завершил", "работа сделана", "шипим", "ок все",
    "готово к мёрджу",
)

KEYWORDS_EN = (
    "ship", "ship it", "shipped", "merge", "merging", "merged",
    "commit this", "committed", "done", "we're done", "all done",
    "task complete", "completed", "wrap up", "wrapped",
    "closing this out", "pushed", "deployed", "pr is up", "pr opened",
    "lgtm", "that's it", "and we're done", "all set",
)

GIT_VERBS = ("git commit", "git push", "git merge", "git tag")
SLASH_COMMANDS = ("/commit", "/create-pr", "/ship", "/pr-review")


def is_keyword_trigger(text: str) -> bool:
    if not text:
        return False
    haystack = text.lower()
    for needle in KEYWORDS_RU + KEYWORDS_EN:
        if needle.lower() in haystack:
            return True
    return False


def is_git_trigger(command: str) -> bool:
    if not command:
        return False
    stripped = command.strip()
    return any(stripped.startswith(verb) for verb in GIT_VERBS)


def is_slash_trigger(name: str) -> bool:
    if not name:
        return False
    stripped = name.strip()
    return stripped in SLASH_COMMANDS


def _walk_up_for_workspace(start: Path) -> Path | None:
    current = start.resolve()
    while True:
        if (current / ".context-graph" / "workspace.json").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _read_event() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _trigger_text_for_source(source: str, event: dict) -> str:
    if source == "keyword":
        return event.get("text") or event.get("prompt") or ""
    if source == "git":
        tool_input = event.get("toolInput") or {}
        return tool_input.get("command") or event.get("command") or ""
    if source == "slash":
        return event.get("name") or event.get("command") or ""
    return ""


def _is_trigger(source: str, text: str) -> bool:
    if source == "keyword":
        return is_keyword_trigger(text)
    if source == "git":
        return is_git_trigger(text)
    if source == "slash":
        return is_slash_trigger(text)
    return False


def _run_prepare(workspace: Path) -> bool:
    cli = Path(__file__).resolve().parent / "context_graph_cli.py"
    proc = subprocess.run(
        ["python3", str(cli), "prepare-auto-push"],
        input="{}",
        capture_output=True,
        text=True,
        cwd=str(workspace),
        check=False,
    )
    return proc.returncode == 0


def main(argv: Iterable[str] | None = None, *, cwd: str | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=("keyword", "git", "slash"),
        required=True,
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    event = _read_event()
    text = _trigger_text_for_source(args.source, event)
    if not _is_trigger(args.source, text):
        return 0
    start = Path(cwd) if cwd else Path.cwd()
    workspace = _walk_up_for_workspace(start)
    if workspace is None:
        return 0
    ok = _run_prepare(workspace)
    if not ok:
        return 0
    sys.stdout.write(
        "Auto-push trigger fired. Run /cg-sync-notion auto to drain "
        ".context-graph/auto_push_plan.json.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_trigger_detect
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/trigger_detect.py tests/test_trigger_detect.py
git commit -m "feat: trigger detection script for keyword/git/slash hooks"
```

---

### Task 12: Add Auto-Push Mode To `commands/cg-sync-notion.md`

**Files:**
- Modify: `commands/cg-sync-notion.md`
- Test: `tests/test_notion_sync_command.py` (create if not present, or extend the existing smoke for cg-sync-notion)

- [ ] **Step 1: Write a smoke test for the new mode**

Create or extend `tests/test_notion_sync_command.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CgSyncNotionAutoModeTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-sync-notion.md"

    def test_auto_mode_is_documented(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "auto",
            ".context-graph/auto_push_plan.json",
            "prepare-auto-push",
            "apply-auto-push-result",
            "Auto-pushed to Notion",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_sync_command
```

Expected: failures (auto mode not yet documented).

- [ ] **Step 3: Edit `commands/cg-sync-notion.md`**

Add a new section after the existing `Pull (default)` and `Push (opt-in)`
sections:

```markdown
## Auto (driven by triggers)

Use this mode when invoked from `scripts/trigger_detect.py`. Do NOT
prompt for confirmation in this mode — the user has already opted in
during onboarding by accepting the auto-push hooks.

Steps:

1. Read `.context-graph/auto_push_plan.json`. If the file does not
   exist or `blocked` is true, exit silently with the appropriate
   one-line message:
   - `blocked == true` and `reason == "no-notion-root"`: `Auto-push paused: run /cg-bootstrap first.`
   - File missing: nothing — the trigger script already exited cleanly.
2. For each entry in `creates`:
   a. Call `mcp__context-graph__record_to_notion_payload` with `{recordId}`.
   b. Call `mcp__notion__notion-create-pages` with `parent: {type: "page_id", page_id: parentPageId}` and the returned title/body.
   c. Capture the new page id and append `{recordId, notionPageId, revision, pushedAt}` to a results list, where `pushedAt` is the current ISO-8601 UTC timestamp.
3. For each entry in `updates`:
   a. Call `mcp__context-graph__record_to_notion_payload`.
   b. Call `mcp__notion__notion-update-page` with `page_id: notionPageId`, `command: "replace_content"`, `allow_deleting_content: true`, and the returned content.
   c. Append `{recordId, notionPageId, revision, pushedAt}` to results.
4. Call the CLI subcommand `apply-auto-push-result` with `{"results": <list>}`. This dequeues successful records and writes their `lastPushedRevision`/`lastPushedAt` into `notion_push.json`.
5. Print a summary block:

```text
Auto-pushed to Notion
  + Rule: <title> → <dir>
  + Decision: <title> → <dir>
```

Skipped records (from `plan.skipped`) are mentioned as a single line at
the end if non-empty: `Skipped N records: <reason summary>`.

Failure handling: on per-record API error, do NOT include that record in
the results list. The CLI subcommand only dequeues successes, so the
record will be retried on the next trigger.
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_sync_command
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add commands/cg-sync-notion.md tests/test_notion_sync_command.py
git commit -m "feat(commands): document /cg-sync-notion auto mode"
```

---

### Task 13: Bootstrap Content Generator (`scripts/bootstrap_content.py`)

**Files:**
- Create: `scripts/bootstrap_content.py`
- Test: `tests/test_bootstrap_content.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bootstrap_content.py`:

```python
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from bootstrap_content import (  # noqa: E402
    build_dir_paragraph,
    build_root_body,
)


class BuildDirParagraphTests(unittest.TestCase):
    def test_empty_dir_falls_back_to_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "bl-api"
            dir_path.mkdir()
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("bl-api/", paragraph)
            self.assertIn("Purpose:", paragraph)

    def test_dir_with_readme_paragraph_uses_first_paragraph(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "core"
            dir_path.mkdir()
            (dir_path / "README.md").write_text(
                "# Core\n\nThe core service of the platform.\n\nMore details below.\n",
                encoding="utf-8",
            )
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("The core service of the platform.", paragraph)

    def test_dir_with_package_json_lists_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "admin"
            dir_path.mkdir()
            (dir_path / "package.json").write_text(
                '{"name":"admin","dependencies":{"@angular/core":"16","rxjs":"7"}}',
                encoding="utf-8",
            )
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("Stack:", paragraph)
            self.assertIn("@angular/core", paragraph)

    def test_dir_with_files_lists_entry_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_path = Path(tmp) / "scripts"
            dir_path.mkdir()
            for name in ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"):
                (dir_path / name).write_text("# noop\n", encoding="utf-8")
            paragraph = build_dir_paragraph(dir_path)
            self.assertIn("Entry points:", paragraph)
            self.assertEqual(paragraph.count(".py"), 5)


class BuildRootBodyTests(unittest.TestCase):
    def test_root_body_includes_tagline_and_dir_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "README.md").write_text(
                "# MyProject\n\nA distributed widget factory.\n",
                encoding="utf-8",
            )
            body = build_root_body(
                repo,
                project_title="MyProject",
                top_level_dirs=[
                    {"path": "admin/", "purpose": ""},
                    {"path": "core/", "purpose": ""},
                ],
            )
            self.assertIn("A distributed widget factory.", body)
            self.assertIn("admin/", body)
            self.assertIn("core/", body)
            self.assertIn("Indexes", body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_bootstrap_content
```

Expected: failures (`bootstrap_content` not implemented).

- [ ] **Step 3: Implement `scripts/bootstrap_content.py`**

```python
"""Bootstrap content generator for Context Graph Notion pages.

Pure functions: read project files, return strings. No network calls,
no LLM calls. Designed to be cheap so /cg-bootstrap can call it for
every dir without burning tokens.

Source materials per dir, in order:

- ``<dir>/README.md`` first paragraph (if present)
- ``<dir>/package.json#dependencies`` (or Cargo.toml / pyproject.toml /
  requirements.txt) for the Stack line
- A directory listing capped at five files for the Entry points line
- A heuristic fallback derived from the dir name when nothing matches
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


_HEURISTIC_FALLBACKS: dict[str, str] = {
    "api": "API service.",
    "admin": "Admin UI.",
    "core": "Core domain logic.",
    "db": "Database layer.",
    "docs": "Project documentation.",
    "scripts": "Operational and dev scripts.",
    "skills": "Plugin skill definitions.",
    "tests": "Test suite.",
    "commands": "Slash command definitions.",
}


def _first_paragraph(text: str) -> str:
    paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
    if not paragraphs:
        return ""
    first = paragraphs[0]
    if first.startswith("#"):
        # Skip the leading heading; use the next paragraph if any.
        if len(paragraphs) > 1:
            return paragraphs[1]
        return ""
    return first


def _read_dependencies(dir_path: Path) -> list[str]:
    pkg = dir_path / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        deps = data.get("dependencies") if isinstance(data, dict) else {}
        if isinstance(deps, dict):
            return list(deps.keys())[:5]
    pyproject = dir_path / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        # Cheap parse: look for a dependencies = [...] block.
        if "dependencies" in text:
            return ["python (pyproject)"]
    requirements = dir_path / "requirements.txt"
    if requirements.exists():
        try:
            lines = [
                line.strip().split("=")[0].split("<")[0].split(">")[0]
                for line in requirements.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except OSError:
            lines = []
        return lines[:5]
    return []


def _list_entry_files(dir_path: Path, *, max_files: int = 5) -> list[str]:
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    candidates = sorted(
        item.name for item in dir_path.iterdir()
        if item.is_file() and not item.name.startswith(".")
    )
    return candidates[:max_files]


def _heuristic_purpose(dir_name: str) -> str:
    plain = dir_name.rstrip("/")
    for keyword, label in _HEURISTIC_FALLBACKS.items():
        if keyword in plain.lower():
            return label
    return f"Module: {plain}/."


def build_dir_paragraph(dir_path: Path) -> str:
    """Return the auto-generated paragraph body for a top-level dir page."""
    name = dir_path.name + "/"
    readme = dir_path / "README.md"
    notes = ""
    if readme.exists():
        try:
            notes = _first_paragraph(readme.read_text(encoding="utf-8"))
        except OSError:
            notes = ""
    purpose = notes if notes else _heuristic_purpose(dir_path.name)
    deps = _read_dependencies(dir_path)
    files = _list_entry_files(dir_path)
    parts: list[str] = [f"Purpose: {purpose}"]
    if deps:
        parts.append("Stack: " + ", ".join(deps))
    if files:
        parts.append("Entry points: " + ", ".join(files))
    if notes and purpose != notes:
        parts.append(f"Notes: {notes}")
    parts.insert(0, name)
    return "\n".join(parts)


def build_root_body(
    repo: Path,
    *,
    project_title: str,
    top_level_dirs: Iterable[dict],
) -> str:
    """Return the Notion body for the root project page."""
    readme = repo / "README.md"
    tagline = ""
    if readme.exists():
        try:
            tagline = _first_paragraph(readme.read_text(encoding="utf-8"))
        except OSError:
            tagline = ""
    lines: list[str] = [f"# {project_title}", ""]
    if tagline:
        lines.extend([tagline, ""])
    lines.extend([
        "## Directories",
        "",
    ])
    for dir_entry in top_level_dirs:
        path = dir_entry.get("path") or ""
        if path:
            lines.append(f"- {path}")
    lines.extend([
        "",
        "## Indexes",
        "",
        "Index pages (Rules, Decisions, Gotchas, Module Boundaries, "
        "Conventions, Tasks, Bug Fixes) are created on the first auto-push "
        "of a record of that type.",
        "",
        "_Maintained by Context Graph plugin. Auto-sections are rewritten on "
        "/cg-bootstrap --refresh._",
    ])
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_bootstrap_content
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/bootstrap_content.py tests/test_bootstrap_content.py
git commit -m "feat: bootstrap content generator (root + per-dir paragraphs)"
```

---

### Task 14: Wire Bootstrap Content Into `/cg-bootstrap`

**Files:**
- Modify: `commands/cg-bootstrap.md`
- Test: `tests/test_curator_bootstrap.py` (extend) or new `tests/test_cg_bootstrap_command.py`

- [ ] **Step 1: Write a smoke test for the modified command file**

Create `tests/test_cg_bootstrap_command.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CgBootstrapDocumentationTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-bootstrap.md"

    def test_command_calls_bootstrap_content_generator(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "build_dir_paragraph",
            "build_root_body",
            "scripts/bootstrap_content.py",
            "--refresh",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_cg_bootstrap_command
```

Expected: failure.

- [ ] **Step 3: Edit `commands/cg-bootstrap.md`**

Find the `## Bootstrap path (default)` section. Replace step 6 (currently the
`notion-create-pages` flow with empty bodies) so it calls into the new
content generator. Insert just after the `bootstrapNeeded` check:

```markdown
For the root page body, run:

```bash
python3 -c "
import json, sys
from pathlib import Path
sys.path.insert(0, 'scripts')
from bootstrap_content import build_root_body
print(json.dumps(build_root_body(Path('.'),
    project_title=sys.argv[1],
    top_level_dirs=json.loads(sys.argv[2]))))
" "<projectTitle>" '<topLevelDirs JSON>'
```

The stdout JSON is the markdown body to pass to `notion-create-pages`
as the parent page content.

For each dir in `topLevelDirs`, run:

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, 'scripts')
from bootstrap_content import build_dir_paragraph
print(build_dir_paragraph(Path(sys.argv[1])))
" "<absolute dir path>"
```

The stdout text is the markdown body for the per-dir page. Pass it to
`notion-create-pages` together with the dir title (e.g. `bl-api/`).
```

Then, at the bottom of the command file, add:

```markdown
## Refresh path

If `$ARGUMENTS` contains `--refresh`:

1. Confirm a workspace exists with `notion.rootPageId` set. If not, tell
   the user to run `/cg-bootstrap` first and stop.
2. For the root page, regenerate the body via `build_root_body` and call
   `notion-update-page` with `command: "replace_content"`,
   `allow_deleting_content: true`. The `Curated` and `Indexes` sections
   are preserved because they live in their own child pages, not in the
   root body.
3. For each dir page recorded in `dirPageIds`, regenerate the paragraph
   via `build_dir_paragraph` and call `notion-update-page` similarly.
4. Print a short summary: `Refreshed root and N dir pages.`
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_cg_bootstrap_command
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add commands/cg-bootstrap.md tests/test_cg_bootstrap_command.py
git commit -m "feat(commands): /cg-bootstrap fills page bodies from bootstrap_content"
```

---

### Task 15: Update Curator Skill To Enqueue Instead Of Push

**Files:**
- Modify: `skills/context-graph-curator/SKILL.md`
- Test: `tests/test_curator_skill.py`

- [ ] **Step 1: Add a curator-enqueue contract test**

Append to `tests/test_curator_skill.py` (or replace the relevant
section):

```python
class CuratorEnqueueOnlyTests(unittest.TestCase):
    SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "context-graph-curator" / "SKILL.md"

    def test_skill_describes_enqueue_step(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("enqueue_push", text)
        self.assertIn("Will be auto-pushed on the next session-end trigger", text)

    def test_skill_does_not_call_notion_create_inline(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        # The skill should no longer call notion-create-pages directly
        # for captured signals — that path moved to the trigger flow.
        self.assertNotIn("notion-create-pages", text)
        self.assertNotIn("notion-update-page", text)
```

(If `tests/test_curator_skill.py` doesn't exist yet, create it with the
standard imports.)

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_skill.CuratorEnqueueOnlyTests
```

Expected: failure.

- [ ] **Step 3: Edit `skills/context-graph-curator/SKILL.md`**

Replace the `## The capture protocol (per signal)` section's step 5
(the entire "If Notion is connected" block, sub-steps a-d) with:

```markdown
5. **Enqueue for auto-push.** Call
   `mcp__context-graph__enqueue_push` with `{"recordId": <record.id>}`.
   The record will be auto-pushed on the next session-end trigger
   (a keyword phrase, a `git commit`/`push`/`merge`/`tag` command, or
   completion of `/commit`, `/create-pr`, `/ship`, `/pr-review`).
   No Notion API call from this skill.
```

Replace step 6 (the acknowledgment) with:

```markdown
6. **Acknowledge briefly.** A one-line confirmation back to the user
   (e.g. "Captured rule: idempotency keys for webhooks (#rule
   #payments). Will be auto-pushed on the next session-end trigger.").
```

In the `## Failure modes` section, remove the bullet that mentions
"Notion MCP returns an error". Replace it with:

```markdown
- **enqueue_push fails** (workspace not initialised, disk error). Report
  the error and stop. The local record is already saved, so the user
  can retry the capture or run `/cg-init` first.
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_skill.CuratorEnqueueOnlyTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills/context-graph-curator/SKILL.md tests/test_curator_skill.py
git commit -m "feat(curator): enqueue captures instead of pushing to Notion inline"
```

---

### Task 16: Add Hook Entries To `hooks.json`

**Files:**
- Modify: `hooks.json`
- Test: `tests/test_hooks_json.py` (create)

- [ ] **Step 1: Write the failing schema test**

Create `tests/test_hooks_json.py`:

```python
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HooksJsonTests(unittest.TestCase):
    HOOKS_PATH = ROOT / "hooks.json"

    def setUp(self):
        self.data = json.loads(self.HOOKS_PATH.read_text(encoding="utf-8"))

    def test_existing_hooks_preserved(self):
        events = self.data["hooks"]
        self.assertIn("SessionStart", events)
        self.assertIn("PostToolUse", events)

    def test_user_prompt_submit_trigger_added(self):
        events = self.data["hooks"]
        self.assertIn("UserPromptSubmit", events)
        commands = [
            entry["command"]
            for matcher in events["UserPromptSubmit"]
            for entry in matcher.get("hooks", [])
        ]
        self.assertTrue(any("trigger_detect.py" in cmd for cmd in commands))

    def test_post_tool_use_bash_trigger_added(self):
        events = self.data["hooks"]
        # The new Bash hook must coexist with the existing Write|Edit hook.
        post_tool = events["PostToolUse"]
        matchers = [m["matcher"] for m in post_tool]
        self.assertIn("Write|Edit", matchers)
        self.assertIn("Bash", matchers)
        bash_entry = next(m for m in post_tool if m["matcher"] == "Bash")
        commands = [h["command"] for h in bash_entry["hooks"]]
        self.assertTrue(any("trigger_detect.py" in cmd for cmd in commands))

    def test_slash_command_trigger_added(self):
        events = self.data["hooks"]
        self.assertIn("SlashCommand", events)
        slash = events["SlashCommand"]
        matchers = [m["matcher"] for m in slash]
        self.assertTrue(any("commit" in m and "create-pr" in m for m in matchers))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_hooks_json
```

Expected: failure (new hook events missing).

- [ ] **Step 3: Edit `hooks.json`**

Replace the entire file content with:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/session_start_prime.py\" 2>/dev/null || true",
            "timeout": 8
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/trigger_detect.py\" --source keyword 2>/dev/null || true",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/post_edit_reindex.py\" 2>/dev/null || true",
            "timeout": 25
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/trigger_detect.py\" --source git 2>/dev/null || true",
            "timeout": 5
          }
        ]
      }
    ],
    "SlashCommand": [
      {
        "matcher": "commit|create-pr|ship|pr-review",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/trigger_detect.py\" --source slash 2>/dev/null || true",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_hooks_json
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks.json tests/test_hooks_json.py
git commit -m "feat(hooks): wire trigger_detect.py for keyword/git/slash events"
```

---

### Task 17: Update `commands/cg-init.md` To Document Hook Merging

**Files:**
- Modify: `commands/cg-init.md`
- Test: extend `tests/test_hooks_json.py` or add a smoke test in
  `tests/test_cg_init_command.py`

- [ ] **Step 1: Add a smoke test**

Create `tests/test_cg_init_command.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CgInitDocsTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-init.md"

    def test_init_documents_auto_push_hooks(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in ("hooks.json", "trigger_detect.py", "auto-push"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_cg_init_command
```

Expected: failure.

- [ ] **Step 3: Edit `commands/cg-init.md`**

Add a section near the bottom:

```markdown
## Auto-push hooks

After the workspace is initialised, the auto-push hooks are inherited
from the plugin's repo-level `hooks.json`. New users do not need to
copy anything: the plugin's `hooks.json` is loaded by Claude Code
automatically when the plugin is enabled.

Claude Code merges plugin-level hooks with any user-level hooks the
user already has — there is nothing to copy or edit. To opt out of
auto-push, set `workspace.json.autoPush.enabled` to `false`; the
trigger script honours the flag and exits silently.
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_cg_init_command
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add commands/cg-init.md tests/test_cg_init_command.py
git commit -m "docs(cg-init): explain auto-push hook inheritance and opt-out"
```

---

### Task 18: Honour `autoPush.enabled = false` In Trigger Script

**Files:**
- Modify: `scripts/trigger_detect.py`
- Test: `tests/test_trigger_detect.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_trigger_detect.py`:

```python
class AutoPushOptOutTests(unittest.TestCase):
    def test_disabled_workspace_skips_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp).resolve()
            (ws / ".context-graph").mkdir(parents=True, exist_ok=True)
            (ws / ".context-graph" / "workspace.json").write_text(
                json.dumps({
                    "version": "1",
                    "id": "t",
                    "rootPath": str(ws),
                    "autoPush": {"enabled": False},
                }),
                encoding="utf-8",
            )
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=str(ws))
            self.assertEqual(exit_code, 0)
            self.assertEqual(buf.getvalue().strip(), "")
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_trigger_detect.AutoPushOptOutTests
```

Expected: failure (the script ignores the flag).

- [ ] **Step 3: Update `scripts/trigger_detect.py`**

Add a helper:

```python
def _is_auto_push_enabled(workspace: Path) -> bool:
    manifest_path = workspace / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        return True
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    flag = (data.get("autoPush") or {}).get("enabled")
    if flag is False:
        return False
    return True
```

Inside `main`, after resolving the workspace and before calling
`_run_prepare`, add:

```python
if not _is_auto_push_enabled(workspace):
    return 0
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_trigger_detect.AutoPushOptOutTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/trigger_detect.py tests/test_trigger_detect.py
git commit -m "feat: honour workspace.json autoPush.enabled=false opt-out"
```

---

### Task 19: Update `docs/schema.json` For `markers.notionDir`

**Files:**
- Modify: `docs/schema.json`
- Test: `tests/test_core.py` (extend)

- [ ] **Step 1: Add the failing schema test**

Append to `tests/test_core.py`:

```python
class SchemaNotionDirTests(unittest.TestCase):
    SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "schema.json"

    def test_notion_dir_is_optional_string_marker(self):
        data = json.loads(self.SCHEMA_PATH.read_text(encoding="utf-8"))
        markers = data.get("markers") or {}
        notion_dir = markers.get("notionDir")
        self.assertIsNotNone(notion_dir)
        self.assertEqual(notion_dir.get("type"), "string")
        self.assertFalse(notion_dir.get("required", False))
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_core.SchemaNotionDirTests
```

Expected: failure.

- [ ] **Step 3: Edit `docs/schema.json`**

Find the `"markers"` object and add:

```json
"notionDir": {
  "type": "string",
  "required": false,
  "description": "Optional manual override pinning a record to a specific Notion dir page (e.g. 'admin/'). Empty / absent uses auto-routing."
}
```

- [ ] **Step 4: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_core.SchemaNotionDirTests
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/schema.json tests/test_core.py
git commit -m "feat(schema): add markers.notionDir override field"
```

---

### Task 20: Update README For Auto-Push Behaviour

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read the current pull-vs-push section**

```bash
grep -n "Pull vs push" /Users/maksnalyvaiko/context-graph/README.md
```

Expected: a single line. Read 30 lines starting at that line.

- [ ] **Step 2: Replace the section with the new auto-push semantics**

In `README.md`, replace the entire `## Pull vs push — important`
section through to the next `---` separator with:

```markdown
## How notes get to Notion (auto-push)

Once `/cg-bootstrap` runs and the Notion side is connected:

- **You write notes locally.** Curator captures rules, decisions,
  gotchas, module boundaries, conventions, tasks, and bug fixes during
  your session and stores them in the local graph.
- **Captures queue up.** They land in `.context-graph/notion_push.json`
  under `pending`.
- **A trigger fires.** Any of these counts as a logical session-end:
  - a phrase like `готово`, `ship it`, `merged`, `done`, `закоммитим`
  - a `git commit`, `git push`, `git merge`, `git tag` command
  - completion of `/commit`, `/create-pr`, `/ship`, `/pr-review`
- **The plugin pushes silently.** Pending records are batch-pushed to
  Notion under their matching dir page. A summary block prints in chat
  with the new page names and locations.

To opt out, set `workspace.json.autoPush.enabled = false` and the
trigger script becomes a no-op.

`/cg-bootstrap` itself populates the root and per-dir pages with a
generated paragraph — no more empty stubs. Re-run with
`/cg-bootstrap --refresh` after major repo changes.
```

- [ ] **Step 3: Update the slash-command table**

Find the table starting with `## Slash commands`. Adjust the rows for
`/cg-sync-notion` and `/cg-bootstrap`:

| Old row | New row |
|---|---|
| `/cg-sync-notion` — Pull Notion pages into the graph (default), or `push` promoted records back. | `/cg-sync-notion` — Pull Notion pages into the graph. The plugin's auto-push path uses an `auto` mode internally; you do not run it manually. |
| `/cg-bootstrap` — Create the Notion skeleton (root page + per-dir pages) for this workspace. | `/cg-bootstrap` — Create the Notion skeleton AND fill each page with a generated paragraph. Re-run with `--refresh` to regenerate. |

- [ ] **Step 4: Smoke-check the README still parses**

Run:

```bash
python3 -c "from pathlib import Path; print(len(Path('/Users/maksnalyvaiko/context-graph/README.md').read_text(encoding='utf-8')))"
```

Expected: a positive integer (no exception).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: replace pull-vs-push section with auto-push behaviour"
```

---

### Task 21: End-To-End Integration Smoke Test

**Files:**
- Create: `tests/test_auto_push_e2e.py`

- [ ] **Step 1: Write the smoke test**

This test seeds a workspace with a captured record, manually fires the
trigger entry point, and asserts that the prepared plan contains a
single `create` and the queue has not yet been drained (the
slash-command layer would do that):

```python
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import enqueue_push, index_records  # noqa: E402
from trigger_detect import main as trigger_main  # noqa: E402


def _make_workspace(tmp: str) -> Path:
    root = Path(tmp).resolve()
    (root / ".context-graph").mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "1",
        "id": "ws-e2e",
        "rootPath": str(root),
        "notion": {
            "rootPageId": "root-page",
            "dirPageIds": {"core/": "core-page"},
        },
    }
    (root / ".context-graph" / "workspace.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return root


class AutoPushEndToEndTests(unittest.TestCase):
    def test_keyword_trigger_writes_plan_with_one_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(tmp)
            graph_path = str(ws / ".context-graph" / "graph.json")
            index_records({
                "graphPath": graph_path,
                "workspaceRoot": str(ws),
                "records": [{
                    "id": "notion:rule-e2e",
                    "title": "E2E rule",
                    "content": "Body",
                    "markers": {"type": "rule", "status": "done"},
                    "source": {
                        "system": "notion",
                        "metadata": {"parent": "kenmore > core/"},
                    },
                }],
            })
            enqueue_push("notion:rule-e2e", workspace_root=ws)
            payload = {"source": "keyword", "text": "готово"}
            buf = StringIO()
            with mock.patch.object(sys, "stdin", StringIO(json.dumps(payload))), \
                 mock.patch.object(sys, "stdout", buf):
                exit_code = trigger_main(["--source", "keyword"], cwd=str(ws))
            self.assertEqual(exit_code, 0)
            self.assertIn("Run /cg-sync-notion auto", buf.getvalue())
            plan_path = ws / ".context-graph" / "auto_push_plan.json"
            self.assertTrue(plan_path.exists(),
                "trigger_detect must have called prepare-auto-push")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertEqual(len(plan["creates"]), 1)
            self.assertEqual(plan["creates"][0]["recordId"], "notion:rule-e2e")
            self.assertEqual(plan["creates"][0]["parentPageId"], "core-page")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_auto_push_e2e
```

Expected: PASS (all upstream tasks already implemented).

- [ ] **Step 3: Run the entire test suite**

```bash
cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
```

Expected: every test passes. If any pre-existing test breaks because of
the schema change, fix it inline (the most common case is a test that
reads the old flat `notion_push.json` shape).

- [ ] **Step 4: Commit**

```bash
git add tests/test_auto_push_e2e.py
git commit -m "test: end-to-end smoke for keyword-trigger auto-push plan"
```

---

## Self-Review

After implementing all tasks above, run this checklist before declaring done:

1. **Spec coverage.** For each section in
   `docs/superpowers/specs/2026-04-26-auto-notion-push-design.md`, point
   to the task that implements it:
   - Architecture / push queue → Tasks 1, 2, 4
   - Trigger detection → Tasks 11, 18
   - Push orchestrator (planner + slash exec) → Tasks 8, 9, 10, 12
   - Bootstrap content → Tasks 13, 14
   - Curator skill update → Task 15
   - Notion structure (dir + index pages) → Tasks 8, 12, 14
   - Update semantics → Tasks 3, 8
   - Hooks setup → Tasks 16, 17, 18
   - Manual `/cg-sync-notion push` continues to work → covered by
     Task 3 (existing tests preserved)
   - Documentation → Tasks 19, 20

2. **Type-index pages.** The spec describes `Indexes/<Type>s` pages and
   per-record links. Tasks 8 and 12 specify the planner output and the
   slash-command exec, but the lazy creation of index pages is currently
   deferred to runtime in `commands/cg-sync-notion.md` (Task 12 step 3
   instructs the slash command to upsert index rows after each successful
   create/update). If implementation reveals the index logic needs its
   own helper, add it as a follow-up commit; the planner data structure
   already carries the type marker so no schema change is needed.

3. **Placeholder scan.** Each step contains real test code, real
   implementation code, real shell commands. No `TODO`, `TBD`,
   `implement later`. The only intentional templates are
   `<projectTitle>` and `<topLevelDirs JSON>` in Task 14, which are
   slash-command template arguments substituted by Claude at runtime.

4. **Type consistency.**
   - `enqueue_push` / `dequeue_push` / `list_pending_pushes` keep their
     names from Task 4 through Tasks 6, 7, 11, 15, 21.
   - `apply_push_result` keeps its signature `(record_id,
     notion_page_id, state, *, revision, pushed_at)` from Task 3
     onwards.
   - `build_plan` is called the same way in Tasks 8, 9, 11, 21.
   - `auto_push_plan.json` filename used in Tasks 9, 11, 12, 21.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-04-26-auto-notion-push.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
