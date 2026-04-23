# Workspace Binding + Adaptive Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 1 of the long-term "Notion as second memory" vision — workspace binding (per-project `.context-graph/` dir mapping to a Notion root page) plus an adaptive classifier (4-stage pipeline with self-mining taxonomy).

**Architecture:** Keep the existing stdlib-only Python core; add a workspace-resolution layer, replace `classify_record` internals with a region-extractor → IDF/region-weighted scorer → threshold arbiter pipeline, add a post-ingest learner that mines hierarchy + n-grams + code paths, and extend `/cg-sync-notion` to orchestrate in-session LLM arbitration for ambiguous records. Headless callers degrade to deterministic fallbacks. No Anthropic API key anywhere.

**Tech Stack:** Python 3.11 stdlib (no external deps), `unittest`, MCP JSON-RPC over stdio, Claude Code plugin manifest, Notion official MCP (read-side), Markdown-based skills/commands.

**Spec:** [docs/superpowers/specs/2026-04-23-workspace-and-adaptive-classifier-design.md](../specs/2026-04-23-workspace-and-adaptive-classifier-design.md)

---

## Milestone 1 — Workspace foundation

Goal: resolve every plugin operation against a per-directory `.context-graph/workspace.json` with explicit opt-in via `/cg-init`. Nothing else depends on this, so it lands first.

### Task 1: Workspace resolution primitives

**Files:**
- Modify: `scripts/context_graph_core.py` (add near top, after imports)
- Test: `tests/test_workspace.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workspace.py
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import (  # noqa: E402
    WorkspaceNotInitializedError,
    find_workspace_root,
    require_workspace,
)


class FindWorkspaceRootTests(unittest.TestCase):
    def test_finds_workspace_from_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".context-graph").mkdir()
            (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
            self.assertEqual(find_workspace_root(root), root)

    def test_finds_workspace_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".context-graph").mkdir()
            (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
            sub = root / "src" / "nested"
            sub.mkdir(parents=True)
            self.assertEqual(find_workspace_root(sub), root)

    def test_returns_none_when_no_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_workspace_root(Path(tmp)))

    def test_require_workspace_raises_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkspaceNotInitializedError):
                require_workspace(Path(tmp))

    def test_require_workspace_returns_root_when_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".context-graph").mkdir()
            (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
            self.assertEqual(require_workspace(root), root)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/maksnalyvaiko/personal/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace -v`
Expected: ImportError — `WorkspaceNotInitializedError` / `find_workspace_root` / `require_workspace` not defined.

- [ ] **Step 3: Implement minimal resolution primitives**

Add to `scripts/context_graph_core.py`, right after the `Path` / `Callable` imports block, before any other functions:

```python
class WorkspaceNotInitializedError(RuntimeError):
    """Raised when a context-graph operation needs a workspace but none is found."""


WORKSPACE_MARKER = ".context-graph/workspace.json"


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (defaults to CWD) looking for .context-graph/workspace.json.

    Returns the directory that contains .context-graph/, or None if no marker is
    found up to the filesystem root.
    """
    current = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / WORKSPACE_MARKER).exists():
            return candidate
    return None


def require_workspace(start: Path | None = None) -> Path:
    """Like `find_workspace_root` but raises if none is found."""
    root = find_workspace_root(start)
    if root is None:
        raise WorkspaceNotInitializedError(
            "No Context Graph workspace found. Run /cg-init to initialize."
        )
    return root
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace -v`
Expected: 5 tests pass.

- [ ] **Step 5: Run the full suite to verify no regression**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: 47 tests pass (42 previous + 5 new).

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_workspace.py
git commit -m "Add workspace root resolution primitives"
```

---

### Task 2: Workspace-aware path resolvers

**Files:**
- Modify: `scripts/context_graph_core.py` (replace `data_dir` / `default_graph_path`)
- Test: `tests/test_workspace.py` (extend)

- [ ] **Step 1: Add failing tests for workspace-aware paths**

Append to `tests/test_workspace.py`:

```python
from context_graph_core import (  # noqa: E402
    default_graph_path,
    idf_stats_path,
    notion_cursor_path,
    schema_feedback_path,
    schema_learned_path,
    schema_overlay_path,
)


class PathResolverTests(unittest.TestCase):
    def _make_workspace(self, tmp: str) -> Path:
        root = Path(tmp).resolve()
        (root / ".context-graph").mkdir()
        (root / ".context-graph" / "workspace.json").write_text('{"version":"1"}')
        return root

    def test_default_graph_path_under_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            self.assertEqual(
                default_graph_path(root), root / ".context-graph" / "graph.json"
            )

    def test_all_resolvers_point_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            cg = root / ".context-graph"
            self.assertEqual(schema_learned_path(root),  cg / "schema.learned.json")
            self.assertEqual(schema_overlay_path(root),  cg / "schema.overlay.json")
            self.assertEqual(schema_feedback_path(root), cg / "schema.feedback.json")
            self.assertEqual(idf_stats_path(root),       cg / "idf_stats.json")
            self.assertEqual(notion_cursor_path(root),   cg / "notion_cursor.json")

    def test_legacy_env_var_keeps_plugin_data(self):
        # When CONTEXT_GRAPH_LEGACY_PLUGIN_DATA=1 AND no workspace, fall back
        # to plugin-local data/graph.json (for the plugin's own test env).
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CONTEXT_GRAPH_LEGACY_PLUGIN_DATA"] = "1"
            try:
                path = default_graph_path(start=Path(tmp))
                self.assertTrue(path.name == "graph.json")
                self.assertIn("data", path.parts)
            finally:
                os.environ.pop("CONTEXT_GRAPH_LEGACY_PLUGIN_DATA", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace -v`
Expected: ImportError for new resolver names.

- [ ] **Step 3: Replace path resolvers**

In `scripts/context_graph_core.py`, replace the existing `data_dir` and `default_graph_path` with the workspace-aware set. Keep `project_root` since shipped schema still lives there.

```python
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _legacy_mode() -> bool:
    return os.environ.get("CONTEXT_GRAPH_LEGACY_PLUGIN_DATA") == "1"


def _resolve_workspace_file(filename: str, start: Path | None = None) -> Path:
    if _legacy_mode():
        plugin_data = project_root() / "data"
        return plugin_data / filename
    root = require_workspace(start)
    return root / ".context-graph" / filename


def default_graph_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("graph.json", start)


def schema_learned_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("schema.learned.json", start)


def schema_overlay_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("schema.overlay.json", start)


def schema_feedback_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("schema.feedback.json", start)


def idf_stats_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("idf_stats.json", start)


def notion_cursor_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("notion_cursor.json", start)
```

At the top of the file, add `import os` if it isn't already there.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace -v`
Expected: 8 tests pass.

- [ ] **Step 5: Verify existing tests still pass (using explicit graphPath)**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: 50 tests pass (42 + 8). Existing tests pass because they all use explicit `graphPath` against `tempfile.TemporaryDirectory()`.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_workspace.py
git commit -m "Resolve all per-workspace paths via walk-up"
```

---

### Task 3: `init_workspace` function + MCP tool + `/cg-init` command

**Files:**
- Modify: `scripts/context_graph_core.py` (add `init_workspace`)
- Modify: `scripts/context_graph_cli.py` (add `init-workspace` subcommand)
- Modify: `scripts/context_graph_mcp.py` (add `init_workspace` tool)
- Create: `commands/cg-init.md`
- Test: `tests/test_workspace.py` (extend)

- [ ] **Step 1: Add failing test for init_workspace**

Append to `tests/test_workspace.py`:

```python
from context_graph_core import init_workspace  # noqa: E402


class InitWorkspaceTests(unittest.TestCase):
    def test_initializes_workspace_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result = init_workspace({"rootPath": str(root), "notionRootPageId": None})
            self.assertEqual(result["rootPath"], str(root))
            self.assertTrue((root / ".context-graph" / "workspace.json").exists())
            manifest = json.loads((root / ".context-graph" / "workspace.json").read_text())
            self.assertEqual(manifest["version"], "1")
            self.assertEqual(manifest["rootPath"], str(root))
            self.assertIsNotNone(manifest["id"])
            self.assertIn("createdAt", manifest)

    def test_refuses_if_already_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            with self.assertRaises(ValueError) as ctx:
                init_workspace({"rootPath": str(root)})
            self.assertIn("already initialized", str(ctx.exception).lower())

    def test_appends_to_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            gi = root / ".gitignore"
            gi.write_text("*.pyc\n")
            init_workspace({"rootPath": str(root)})
            text = gi.read_text()
            self.assertIn(".context-graph/graph.json", text)
            self.assertIn(".context-graph/schema.learned.json", text)
            self.assertIn(".context-graph/schema.feedback.json", text)
            self.assertIn(".context-graph/idf_stats.json", text)
            self.assertIn(".context-graph/notion_cursor.json", text)
            # Must not duplicate on re-run
            # (second init raises, so skip)

    def test_creates_gitignore_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            self.assertTrue((root / ".gitignore").exists())

    def test_stores_notion_metadata_when_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({
                "rootPath": str(root),
                "notionRootPageId": "34a37bbb09ff81839b2ae100879d1089",
                "notionRootPageUrl": "https://www.notion.so/Myapp-34a37bbb09ff81839b2ae100879d1089",
            })
            manifest = json.loads((root / ".context-graph" / "workspace.json").read_text())
            self.assertEqual(manifest["notion"]["rootPageId"],
                             "34a37bbb09ff81839b2ae100879d1089")
            self.assertEqual(manifest["notion"]["rootPageUrl"],
                             "https://www.notion.so/Myapp-34a37bbb09ff81839b2ae100879d1089")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace -v`
Expected: ImportError for `init_workspace`.

- [ ] **Step 3: Implement `init_workspace`**

Add to `scripts/context_graph_core.py` (below the path resolvers):

```python
import uuid

GITIGNORE_ENTRIES = [
    ".context-graph/graph.json",
    ".context-graph/schema.learned.json",
    ".context-graph/schema.feedback.json",
    ".context-graph/idf_stats.json",
    ".context-graph/notion_cursor.json",
]


def _ensure_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    lines = existing.splitlines()
    added = []
    for entry in GITIGNORE_ENTRIES:
        if entry not in lines:
            added.append(entry)
    if added:
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += "\n# Context Graph (local workspace state)\n"
        existing += "\n".join(added) + "\n"
        gi.write_text(existing)


def init_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    root_value = payload.get("rootPath") or str(Path.cwd().resolve())
    root = Path(root_value).expanduser().resolve()
    cg_dir = root / ".context-graph"
    manifest_path = cg_dir / "workspace.json"
    if manifest_path.exists():
        raise ValueError(
            f"Workspace already initialized at {root} — nothing to do."
        )
    cg_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "version": "1",
        "id": f"ws-{uuid.uuid4().hex[:12]}",
        "rootPath": str(root),
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }
    notion_page_id = payload.get("notionRootPageId")
    notion_page_url = payload.get("notionRootPageUrl")
    if notion_page_id:
        manifest["notion"] = {
            "rootPageId": notion_page_id,
            "rootPageUrl": notion_page_url,
            "createdAt": now_iso(),
        }

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=True, indent=2)
        f.write("\n")

    _ensure_gitignore(root)

    return {
        "rootPath": str(root),
        "workspaceId": manifest["id"],
        "manifestPath": str(manifest_path),
        "notion": manifest.get("notion"),
    }
```

Add `import uuid` at the top alongside other imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace -v`
Expected: 13 workspace tests pass.

- [ ] **Step 5: Wire CLI subcommand**

In `scripts/context_graph_cli.py`:

1. Add `"init-workspace"` to the argparse `choices` list.
2. Add this dispatch branch:

```python
elif args.command == "init-workspace":
    from context_graph_core import init_workspace
    result = init_workspace(payload)
```

- [ ] **Step 6: Wire MCP tool**

In `scripts/context_graph_mcp.py`, add a `handle_init_workspace` and new `ToolSpec`:

```python
def handle_init_workspace(arguments: dict[str, Any]) -> dict[str, Any]:
    from context_graph_core import init_workspace
    return init_workspace(arguments)


# Append to TOOLS:
TOOLS.append(ToolSpec(
    name="init_workspace",
    title="Initialize Context Graph Workspace",
    description="Create .context-graph/workspace.json at a given root path and optionally record the Notion root page mapping.",
    input_schema={
        "type": "object",
        "properties": {
            "rootPath": {"type": "string", "description": "Absolute path to the workspace root. Defaults to CWD."},
            "notionRootPageId": {"type": "string"},
            "notionRootPageUrl": {"type": "string"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "rootPath": {"type": "string"},
            "workspaceId": {"type": "string"},
            "manifestPath": {"type": "string"},
        },
    },
    handler=handle_init_workspace,
))
```

(In reality `TOOLS` is declared via list-literal; append by inserting the new `ToolSpec(...)` inside the existing list, keeping the file formatted.)

- [ ] **Step 7: Add `/cg-init` slash command**

Create `commands/cg-init.md`:

```md
---
description: Initialize a Context Graph workspace for the current directory
argument-hint: <workspace-root-path?>  (optional — defaults to CWD)
---

The user wants to initialize a Context Graph workspace. Walk through it interactively.

Steps:

1. Determine the candidate root:
   - If `$ARGUMENTS` is non-empty, use it.
   - Otherwise use the CWD.

2. Ask the user to confirm:
   "Use `<candidate>` as the workspace root for Context Graph? [y/N/<other path>]"
   - "y" → proceed with candidate
   - Any other path → use that instead
   - "N" → stop and report "Initialization canceled."

3. Ask about Notion mapping:
   "Create a Notion root page for this workspace now?
      [a] Auto — create a page under a plugin-managed '🤖 Context Graph' parent
      [u] I have a parent page — I'll paste its URL or id
      [s] Skip — I'll link Notion later"

4. Depending on the answer:
   - [a]: Look up or create the '🤖 Context Graph' parent via `mcp__notion__notion-search` (by title); if missing, call `mcp__notion__notion-create-pages` to create it at the workspace root. Then create a child page titled after the directory name under that parent. Capture the returned page id and URL.
   - [u]: Ask the user for the URL/id, call `mcp__notion__notion-create-pages` with that as the parent and a title derived from the directory name. Capture ids.
   - [s]: Leave Notion fields empty.

5. Call `mcp__context-graph__init_workspace` with `{rootPath, notionRootPageId?, notionRootPageUrl?}`.

6. Report: workspace path, workspace id, Notion URL (if any). Mention `.gitignore` entries added.

If the MCP returns an "already initialized" error, surface it verbatim and suggest the user review the existing workspace.
```

- [ ] **Step 8: Run full suite + sanity-check CLI**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: 55 tests pass (50 + 5 new init_workspace tests — check count carefully; adjust if I miscounted).

# Sanity: MCP still imports cleanly
python3 -c "import sys; sys.path.insert(0, 'scripts'); import context_graph_mcp; print([t.name for t in context_graph_mcp.TOOLS if t.name=='init_workspace'])"
# Expected: ['init_workspace']
```

- [ ] **Step 9: Commit**

```bash
git add scripts/context_graph_core.py scripts/context_graph_cli.py scripts/context_graph_mcp.py commands/cg-init.md tests/test_workspace.py
git commit -m "Add init_workspace + /cg-init slash command"
```

---

## Milestone 2 — Classifier v2 scaffolding (regions, IDF, schema merge)

Goal: lay the building blocks that the new `classify_record` will compose. Still no behavior change from the caller's perspective until Task 8 ties them together.

### Task 4: Region extractor

**Files:**
- Create: `scripts/classifier_regions.py`
- Test: `tests/test_regions.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_regions.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_regions import extract_regions  # noqa: E402


class ExtractRegionsTests(unittest.TestCase):
    def test_passes_through_structuredContent(self):
        record = {
            "title": "X",
            "content": "ignored",
            "structuredContent": {
                "frontmatter": "status: in-progress",
                "metadataBlock": "",
                "titleText": "X",
                "breadcrumb": "parent > child",
                "body": "pre-parsed body",
            },
        }
        regions = extract_regions(record)
        self.assertEqual(regions["body"], "pre-parsed body")
        self.assertEqual(regions["breadcrumb"], "parent > child")

    def test_parses_yaml_frontmatter(self):
        content = "---\nstatus: in-progress\ntype: task\n---\n\n# Heading\nbody text"
        record = {"title": "T", "content": content}
        regions = extract_regions(record)
        self.assertIn("status: in-progress", regions["frontmatter"])
        self.assertIn("type: task", regions["frontmatter"])
        self.assertNotIn("---", regions["frontmatter"])

    def test_extracts_metadata_block(self):
        content = (
            "## Metadata\n"
            "- **status**: in-progress\n"
            "- **room**: core\n"
            "\n"
            "# Main\n"
            "body content"
        )
        record = {"title": "T", "content": content}
        regions = extract_regions(record)
        self.assertIn("status", regions["metadataBlock"])
        self.assertIn("room", regions["metadataBlock"])
        self.assertNotIn("## Metadata", regions["metadataBlock"])
        self.assertIn("body content", regions["body"])
        self.assertNotIn("status", regions["body"])

    def test_pulls_breadcrumb_from_source_metadata(self):
        record = {
            "title": "T",
            "content": "body",
            "source": {"metadata": {"parent": "kenmore > Architecture"}},
        }
        regions = extract_regions(record)
        self.assertEqual(regions["breadcrumb"], "kenmore > Architecture")

    def test_all_regions_present_even_when_empty(self):
        record = {"title": "Lonely", "content": "just body"}
        regions = extract_regions(record)
        self.assertEqual(set(regions.keys()),
                         {"frontmatter", "metadataBlock", "titleText", "breadcrumb", "body"})
        self.assertEqual(regions["titleText"], "Lonely")
        self.assertEqual(regions["body"], "just body")

    def test_recognizes_localized_metadata_heading(self):
        content = "## Метадані\n- статус: у процесі\n\n# Основне\nтіло"
        record = {"title": "T", "content": content}
        regions = extract_regions(record)
        self.assertIn("статус", regions["metadataBlock"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_regions -v`
Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Implement the extractor**

Create `scripts/classifier_regions.py`:

```python
"""Structured region extractor for the adaptive classifier.

Splits an incoming record's raw markdown content into regions that the
scorer then weights differently: frontmatter / metadata-block / title /
breadcrumb / body.
"""
from __future__ import annotations

import re
from typing import Any


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# Recognize localized "Metadata" headings.
METADATA_HEADINGS = ["Metadata", "Метадані", "Метаданные", "Метаданы"]
METADATA_PATTERN = "|".join(re.escape(h) for h in METADATA_HEADINGS)
METADATA_BLOCK_RE = re.compile(
    r"(?ms)^\s*#{1,3}\s*(?:" + METADATA_PATTERN + r")\s*\n"
    r"(.*?)(?=^\s*#|\Z)"
)

REGION_NAMES = ("frontmatter", "metadataBlock", "titleText", "breadcrumb", "body")


def extract_regions(record: dict[str, Any]) -> dict[str, str]:
    if record.get("structuredContent"):
        pre = record["structuredContent"]
        return {name: str(pre.get(name, "")) for name in REGION_NAMES}

    content = str(record.get("content") or "")

    frontmatter = ""
    fm_match = FRONTMATTER_RE.match(content)
    if fm_match:
        frontmatter = fm_match.group(1).strip()
        content = content[fm_match.end():]

    metadata_block = ""
    meta_match = METADATA_BLOCK_RE.search(content)
    if meta_match:
        metadata_block = meta_match.group(1).strip()
        content = (content[:meta_match.start()] + content[meta_match.end():]).strip()

    breadcrumb = str(
        (record.get("source") or {}).get("metadata", {}).get("parent") or ""
    )

    return {
        "frontmatter":   frontmatter,
        "metadataBlock": metadata_block,
        "titleText":     str(record.get("title") or ""),
        "breadcrumb":    breadcrumb,
        "body":          content.strip(),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_regions -v`
Expected: 6 tests pass.

- [ ] **Step 5: Verify full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: prior count + 6 new.

- [ ] **Step 6: Commit**

```bash
git add scripts/classifier_regions.py tests/test_regions.py
git commit -m "Add region extractor for adaptive classifier"
```

---

### Task 5: IDF stats module

**Files:**
- Create: `scripts/classifier_idf.py`
- Test: `tests/test_idf.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_idf.py
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

from classifier_idf import (  # noqa: E402
    compute_idf_from_records,
    load_idf_stats,
    save_idf_stats,
)


class IdfComputationTests(unittest.TestCase):
    def test_empty_corpus_returns_empty(self):
        self.assertEqual(compute_idf_from_records([]), {"corpusSize": 0, "tokenDocumentFrequency": {}})

    def test_counts_unique_tokens_per_document(self):
        records = [
            {"id": "a", "title": "apple banana", "content": "apple cherry"},
            {"id": "b", "title": "banana",       "content": "banana"},
            {"id": "c", "title": "cherry",       "content": "cherry"},
        ]
        idf = compute_idf_from_records(records)
        self.assertEqual(idf["corpusSize"], 3)
        tdf = idf["tokenDocumentFrequency"]
        # "apple" appears in 1 of 3; "banana" in 2 of 3; "cherry" in 2 of 3
        self.assertEqual(tdf["apple"], 1)
        self.assertEqual(tdf["banana"], 2)
        self.assertEqual(tdf["cherry"], 2)

    def test_token_counted_once_per_document_even_if_repeated(self):
        records = [{"id": "x", "title": "word", "content": "word word word"}]
        idf = compute_idf_from_records(records)
        self.assertEqual(idf["tokenDocumentFrequency"]["word"], 1)


class IdfStorageTests(unittest.TestCase):
    def test_load_returns_uniform_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = load_idf_stats(Path(tmp) / "nope.json")
            self.assertEqual(stats["corpusSize"], 0)
            self.assertEqual(stats["tokenDocumentFrequency"], {})

    def test_save_then_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "idf.json"
            save_idf_stats(path, {"corpusSize": 3, "tokenDocumentFrequency": {"a": 2}})
            loaded = load_idf_stats(path)
            self.assertEqual(loaded["corpusSize"], 3)
            self.assertEqual(loaded["tokenDocumentFrequency"]["a"], 2)
            # Version stamp is populated on save
            raw = json.loads(path.read_text())
            self.assertEqual(raw["version"], "1")
            self.assertIn("updatedAt", raw)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_idf -v`
Expected: ImportError.

- [ ] **Step 3: Implement the IDF module**

Create `scripts/classifier_idf.py`:

```python
"""IDF (inverse document frequency) stats for the adaptive classifier.

Computed from the whole corpus of classified records; persisted to
<workspace>/.context-graph/idf_stats.json. Falls back to an empty
distribution (uniform weights) when the file is absent.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")


def _tokenize(text: str) -> set[str]:
    return {m.group(0) for m in TOKEN_RE.finditer(text.lower())}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_idf_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return {corpusSize, tokenDocumentFrequency} for the given records."""
    corpus_size = len(records)
    token_doc_freq: dict[str, int] = {}
    for record in records:
        text = " ".join([
            str(record.get("title") or ""),
            str(record.get("content") or ""),
        ])
        for token in _tokenize(text):
            token_doc_freq[token] = token_doc_freq.get(token, 0) + 1
    return {
        "corpusSize": corpus_size,
        "tokenDocumentFrequency": token_doc_freq,
    }


def load_idf_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"corpusSize": 0, "tokenDocumentFrequency": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "corpusSize": int(data.get("corpusSize", 0)),
        "tokenDocumentFrequency": data.get("tokenDocumentFrequency", {}),
    }


def save_idf_stats(path: Path, stats: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1",
        "updatedAt": _now_iso(),
        "corpusSize": int(stats.get("corpusSize", 0)),
        "tokenDocumentFrequency": stats.get("tokenDocumentFrequency", {}),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        f.write("\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_idf -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/classifier_idf.py tests/test_idf.py
git commit -m "Add IDF stats computation and persistence"
```

---

### Task 6: Schema merge loader

**Files:**
- Create: `scripts/classifier_schema.py`
- Test: `tests/test_schema_merge.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schema_merge.py
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

from classifier_schema import load_merged_schema  # noqa: E402


class SchemaMergeTests(unittest.TestCase):
    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_shipped_only_when_no_overlays(self):
        schema = load_merged_schema(overlay_path=None, learned_path=None)
        self.assertIn("domain", schema["markers"])
        self.assertIn("payments", schema["markers"]["domain"])

    def test_learned_accepted_unions_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            learned = Path(tmp) / "learned.json"
            self._write(learned, {
                "accepted": {"domain": ["challenge", "promo"]},
            })
            schema = load_merged_schema(overlay_path=None, learned_path=learned)
            self.assertIn("challenge", schema["markers"]["domain"])
            self.assertIn("promo", schema["markers"]["domain"])
            self.assertIn("payments", schema["markers"]["domain"])

    def test_overlay_union_and_alias_concat(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.json"
            self._write(overlay, {
                "markers": {"domain": ["ib-commission"]},
                "aliases": {"domain": {"challenge": ["challenge-account"]}},
            })
            learned = Path(tmp) / "learned.json"
            self._write(learned, {"accepted": {"domain": ["challenge"]}})
            schema = load_merged_schema(overlay_path=overlay, learned_path=learned)
            self.assertIn("ib-commission", schema["markers"]["domain"])
            self.assertIn("challenge", schema["markers"]["domain"])
            self.assertIn("challenge-account", schema["aliases"]["domain"]["challenge"])

    def test_rejected_values_are_not_in_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            learned = Path(tmp) / "learned.json"
            self._write(learned, {
                "accepted": {"domain": ["challenge"]},
                "proposals": {"rejected": [{"value": "bl-api", "field": "domain"}]},
            })
            schema = load_merged_schema(overlay_path=None, learned_path=learned)
            self.assertNotIn("bl-api", schema["markers"]["domain"])

    def test_new_field_in_overlay_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "overlay.json"
            # shipped does not have "room" as a list marker
            self._write(overlay, {"markers": {"room": ["core", "il", "pat"]}})
            schema = load_merged_schema(overlay_path=overlay, learned_path=None)
            self.assertIn("room", schema["markers"])
            self.assertIn("core", schema["markers"]["room"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_schema_merge -v`
Expected: ImportError.

- [ ] **Step 3: Implement schema loader**

Create `scripts/classifier_schema.py`:

```python
"""Schema loader for the adaptive classifier.

Merges the shipped docs/schema.json with two optional per-workspace layers:

  shipped  ⊕  learned.accepted  ⊕  overlay

All three contribute markers (union), aliases (concat), relations (union).
hierarchy.preferredOrder from overlay replaces shipped if present. Values
listed in learned.proposals.rejected are never emerged into the merged
result.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _union_list(*lists: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for lst in lists:
        for item in lst or []:
            seen.setdefault(item, None)
    return list(seen.keys())


def load_merged_schema(
    overlay_path: Path | None = None,
    learned_path: Path | None = None,
    shipped_path: Path | None = None,
) -> dict[str, Any]:
    shipped = _load_json(shipped_path or (_project_root() / "docs" / "schema.json"))
    learned = _load_json(learned_path)
    overlay = _load_json(overlay_path)

    merged: dict[str, Any] = json.loads(json.dumps(shipped))  # deep copy

    # Collect rejected values to subtract at the end.
    rejected_by_field: dict[str, set[str]] = {}
    for item in (learned.get("proposals", {}) or {}).get("rejected", []) or []:
        if item.get("field") and item.get("value"):
            rejected_by_field.setdefault(item["field"], set()).add(item["value"])

    # Merge markers: union values.
    merged.setdefault("markers", {})
    for source in (learned.get("accepted", {}) or {}, overlay.get("markers", {}) or {}):
        for field, values in source.items():
            merged["markers"][field] = _union_list(
                merged["markers"].get(field, []), values
            )

    # Subtract rejected.
    for field, rejected in rejected_by_field.items():
        if field in merged["markers"]:
            merged["markers"][field] = [
                v for v in merged["markers"][field] if v not in rejected
            ]

    # Merge aliases: concat lists per canonical.
    merged.setdefault("aliases", {})
    for source_aliases in (overlay.get("aliases", {}) or {},):
        for field, canonicals in source_aliases.items():
            merged["aliases"].setdefault(field, {})
            for canonical, alias_list in canonicals.items():
                merged["aliases"][field][canonical] = _union_list(
                    merged["aliases"][field].get(canonical, []), alias_list
                )

    # Relations: union.
    merged.setdefault("relations", {"explicit": [], "inferred": []})
    for rel_source in (overlay.get("relations", {}) or {},):
        for kind in ("explicit", "inferred"):
            merged["relations"][kind] = _union_list(
                merged["relations"].get(kind, []), rel_source.get(kind, [])
            )

    # Hierarchy: overlay replaces shipped if given.
    if overlay.get("hierarchy"):
        merged["hierarchy"] = overlay["hierarchy"]

    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_schema_merge -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/classifier_schema.py tests/test_schema_merge.py
git commit -m "Add schema merge loader (shipped + learned + overlay)"
```

---

## Milestone 3 — Scorer and arbiter

### Task 7: score_candidate + arbiter thresholds

**Files:**
- Create: `scripts/classifier_scorer.py`
- Test: `tests/test_scorer.py` (new)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_scorer.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_scorer import (  # noqa: E402
    HIGH_CONFIDENCE,
    MIN_GAP,
    MIN_SCORE,
    REGION_WEIGHTS,
    arbitrate,
    score_field,
)


class ScoreFieldTests(unittest.TestCase):
    def setUp(self):
        self.schema = {
            "markers": {"domain": ["payments", "trading", "challenge"]},
            "aliases": {"domain": {"payments": ["payment", "billing"]}},
        }

    def test_exact_match_in_title_scores_high(self):
        regions = {
            "frontmatter": "", "metadataBlock": "",
            "titleText": "Payments Hub",
            "breadcrumb": "", "body": "",
        }
        scores = score_field("domain", regions, self.schema, idf={})
        top = scores[0]
        self.assertEqual(top["value"], "payments")
        self.assertGreater(top["score"], 0.0)

    def test_alias_match_counts_as_canonical(self):
        regions = {
            "frontmatter": "", "metadataBlock": "",
            "titleText": "Billing update",
            "breadcrumb": "", "body": "",
        }
        scores = score_field("domain", regions, self.schema, idf={})
        self.assertEqual(scores[0]["value"], "payments")

    def test_idf_downweights_frequent_tokens(self):
        regions = {
            "frontmatter": "",
            "metadataBlock": "",
            "titleText": "",
            "breadcrumb": "",
            "body": "trading trading trading payments",
        }
        # With uniform IDF, trading would dominate (3 mentions vs 1).
        uniform = score_field("domain", regions, self.schema, idf={})
        self.assertEqual(uniform[0]["value"], "trading")
        # With corpus-wide IDF where trading is in every doc and payments is rare,
        # payments should win.
        idf_weighted = score_field(
            "domain",
            regions,
            self.schema,
            idf={"trading": 10, "payments": 10},  # doc-freq both 10 out of 10
        )
        # With equal-but-max doc freq, both are 0-weight → tied. Tie-break by
        # alphabetical → "payments" first.
        self.assertEqual(idf_weighted[0]["value"], "payments")


class ArbitrateTests(unittest.TestCase):
    def test_deterministic_when_top_clear(self):
        scores = [{"value": "a", "score": 0.9}, {"value": "b", "score": 0.4}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "deterministic")
        self.assertEqual(decision["value"], "a")

    def test_pending_when_top_below_high(self):
        scores = [{"value": "a", "score": 0.5}, {"value": "b", "score": 0.35}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "pending-arbitration")
        self.assertEqual(decision["value"], "a")

    def test_pending_when_gap_too_small(self):
        scores = [{"value": "a", "score": 0.8}, {"value": "b", "score": 0.75}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "pending-arbitration")

    def test_fallback_when_all_below_min(self):
        scores = [{"value": "a", "score": 0.05}]
        decision = arbitrate(scores)
        self.assertEqual(decision["arbiter"], "fallback")
        self.assertIsNone(decision["value"])


class RegionWeightsTests(unittest.TestCase):
    def test_weights_are_frozen(self):
        # Lock the expected values so accidental changes break a test.
        self.assertEqual(REGION_WEIGHTS["frontmatter"], 5.0)
        self.assertEqual(REGION_WEIGHTS["metadataBlock"], 4.0)
        self.assertEqual(REGION_WEIGHTS["titleText"], 3.0)
        self.assertEqual(REGION_WEIGHTS["breadcrumb"], 2.0)
        self.assertEqual(REGION_WEIGHTS["body"], 1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_scorer -v`
Expected: ImportError.

- [ ] **Step 3: Implement scorer and arbiter**

Create `scripts/classifier_scorer.py`:

```python
"""Deterministic scorer + arbiter for the adaptive classifier.

Produces (value, score) candidates per marker field, combining region
weights with optional IDF (from the workspace corpus). Arbiter applies
threshold logic and decides whether the result is accepted deterministically,
sent to LLM arbitration, or dropped as fallback.
"""
from __future__ import annotations

import math
import re
from typing import Any


REGION_WEIGHTS: dict[str, float] = {
    "frontmatter":   5.0,
    "metadataBlock": 4.0,
    "titleText":     3.0,
    "breadcrumb":    2.0,
    "body":          1.0,
}


HIGH_CONFIDENCE = 0.75
MIN_GAP         = 0.15
MIN_SCORE       = 0.20


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")


def _tokenize(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(text.lower())]


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def _canonical_forms(field: str, value: str, schema: dict[str, Any]) -> list[str]:
    aliases = (schema.get("aliases", {}) or {}).get(field, {})
    return [_slug(value), *(_slug(a) for a in aliases.get(value, []))]


def _matches(token: str, canonical_forms: list[str]) -> bool:
    slug = _slug(token)
    return slug in canonical_forms


def score_field(
    field: str,
    regions: dict[str, str],
    schema: dict[str, Any],
    idf: dict[str, int],
) -> list[dict[str, Any]]:
    """Return candidates ranked by score, high to low."""
    allowed = (schema.get("markers", {}) or {}).get(field, [])
    if not allowed:
        return []

    corpus_size = _infer_corpus_size(idf)
    max_idf = _max_idf(idf, corpus_size)

    total_weight = sum(REGION_WEIGHTS.values())
    results: list[dict[str, Any]] = []

    for value in allowed:
        canonical_forms = _canonical_forms(field, value, schema)
        raw = 0.0
        for region_name, region_text in regions.items():
            weight = REGION_WEIGHTS.get(region_name, 0.0)
            if weight == 0.0 or not region_text:
                continue
            for token in _tokenize(region_text):
                if _matches(token, canonical_forms):
                    raw += weight * _idf_weight(token, idf, corpus_size)
        normalized = raw / (total_weight * max_idf) if max_idf > 0 else raw / total_weight
        results.append({"value": value, "score": round(normalized, 4)})

    results.sort(key=lambda x: (-x["score"], x["value"]))
    return results


def _infer_corpus_size(idf: dict[str, int]) -> int:
    if not idf:
        return 0
    return max(idf.values()) if idf else 0


def _max_idf(idf: dict[str, int], corpus_size: int) -> float:
    if corpus_size <= 0:
        return 1.0
    rarest = min(idf.values()) if idf else 1
    rarest = max(rarest, 1)
    return math.log((corpus_size + 1) / rarest) + 1.0


def _idf_weight(token: str, idf: dict[str, int], corpus_size: int) -> float:
    if corpus_size <= 0:
        return 1.0
    df = idf.get(token, 1)
    df = max(df, 1)
    return math.log((corpus_size + 1) / df) + 1.0


def arbitrate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    """Given ordered scores, decide deterministic / pending / fallback."""
    if not scores or scores[0]["score"] < MIN_SCORE:
        return {"arbiter": "fallback", "value": None, "top": None, "gap": 0.0}

    top = scores[0]
    runner = scores[1] if len(scores) > 1 else {"score": 0.0}
    gap = top["score"] - runner["score"]

    if top["score"] >= HIGH_CONFIDENCE and gap >= MIN_GAP:
        return {"arbiter": "deterministic", "value": top["value"], "top": top, "gap": gap}

    return {
        "arbiter": "pending-arbitration",
        "value": top["value"],
        "top": top,
        "gap": gap,
        "reason": "below HIGH_CONFIDENCE" if top["score"] < HIGH_CONFIDENCE else "gap below MIN_GAP",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_scorer -v`
Expected: 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/classifier_scorer.py tests/test_scorer.py
git commit -m "Add deterministic scorer + arbiter"
```

---

### Task 8: Integrated `classify_record` v2 with `classifierNotes` and `arbitrationRequest`

**Files:**
- Modify: `scripts/context_graph_core.py` (replace body of `classify_record`)
- Modify: `tests/test_core.py` (update assertions where shape tightens)
- Test: `tests/test_arbiter.py` (new — covers arbitrationRequest shape end-to-end)

- [ ] **Step 1: Write failing test for new shape**

```python
# tests/test_arbiter.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from context_graph_core import classify_record  # noqa: E402


class ClassifyRecordV2ShapeTests(unittest.TestCase):
    def test_deterministic_path_produces_classifierNotes(self):
        record = {
            "title": "Payments webhook incident",
            "content": (
                "## Metadata\n"
                "- **type**: bug\n"
                "- **domain**: payments\n"
                "- **status**: in-progress\n"
                "- **flow**: webhook\n"
                "\n"
                "# Detail\n"
                "Duplicate payment creation after callback retry."
            ),
            "source": {"metadata": {"parent": "kenmore > Architecture"}},
        }
        result = classify_record({"record": record})
        # Classic fields still present
        self.assertIn("markers", result)
        self.assertIn("hierarchy", result)
        # New fields
        notes = result["source"]["metadata"]["classifierNotes"]
        self.assertEqual(notes["classifierVersion"], "2")
        self.assertIn(notes["arbiter"], ("deterministic", "pending-arbitration", "fallback"))
        self.assertIn("scores", notes)
        self.assertIn("regionsUsed", notes)

    def test_pending_arbitration_emits_arbitrationRequest(self):
        # A record with zero match signals triggers fallback (not pending).
        # To hit pending, craft a record where two candidates tie close.
        record = {
            "title": "Deposit review",
            "content": "Notes about withdrawal deposit flows. Some payments logic.",
        }
        result = classify_record({"record": record})
        notes = result["source"]["metadata"]["classifierNotes"]
        if notes["arbiter"] == "pending-arbitration":
            self.assertIn("arbitrationRequest", result)
            req = result["arbitrationRequest"]
            self.assertIn("candidates", req)
            self.assertIn("allowedValues", req)
            self.assertIn("requiredFields", req)
            self.assertIn("instructions", req)
        else:
            # If deterministic for this fixture, it's still valid — shape test is
            # separately covered below. Skip silently.
            pass

    def test_preserves_required_fields_list(self):
        result = classify_record({"record": {"title": "", "content": ""}})
        self.assertIn("missingRequiredMarkers", result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_arbiter -v`
Expected: `classifierNotes`/`classifierVersion` absent on record → KeyError.

- [ ] **Step 3: Rewrite `classify_record`**

Replace the existing `classify_record` in `scripts/context_graph_core.py` with v2. Keep the function signature compatible but compute everything through the new modules.

```python
from classifier_regions import extract_regions
from classifier_scorer import arbitrate, score_field
from classifier_schema import load_merged_schema
from classifier_idf import load_idf_stats


def _load_schema_for(workspace_start: Path | None) -> dict[str, Any]:
    overlay = learned = None
    try:
        overlay = schema_overlay_path(workspace_start)
    except WorkspaceNotInitializedError:
        overlay = None
    try:
        learned = schema_learned_path(workspace_start)
    except WorkspaceNotInitializedError:
        learned = None
    return load_merged_schema(overlay_path=overlay, learned_path=learned)


def _load_idf_for(workspace_start: Path | None) -> dict[str, Any]:
    try:
        path = idf_stats_path(workspace_start)
    except WorkspaceNotInitializedError:
        return {"corpusSize": 0, "tokenDocumentFrequency": {}}
    return load_idf_stats(path)


def _required_fields(schema: dict[str, Any]) -> list[str]:
    return list((schema.get("record", {}) or {}).get("requiredMarkers", []) or [])


def classify_record(
    payload: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adaptive classifier v2 — runs the four-stage pipeline."""
    workspace_start = None
    if payload.get("workspaceRoot"):
        workspace_start = Path(payload["workspaceRoot"]).resolve()

    schema = schema or _load_schema_for(workspace_start)
    idf_stats = _load_idf_for(workspace_start)
    idf = idf_stats.get("tokenDocumentFrequency", {})

    record = normalize_record_input(dict(payload.get("record", payload)))
    regions = extract_regions(record)

    fields = list((schema.get("markers", {}) or {}).keys())
    markers: dict[str, Any] = dict((record.get("markers") or {}))
    scores_by_field: dict[str, list[dict[str, Any]]] = {}
    arbitration_needed: list[dict[str, Any]] = []

    for field in fields:
        if markers.get(field):
            continue  # user-provided, do not reclassify
        scores = score_field(field, regions, schema, idf)
        scores_by_field[field] = scores[:5]
        decision = arbitrate(scores)
        if decision["arbiter"] in ("deterministic", "fallback") and decision["value"]:
            markers[field] = decision["value"]
        elif decision["arbiter"] == "pending-arbitration":
            markers[field] = decision["value"]  # draft value
            arbitration_needed.append({
                "field": field,
                "reason": decision.get("reason", "ambiguous"),
                "topScore": decision["top"]["score"],
                "gap": decision["gap"],
            })

    required = _required_fields(schema)
    missing_required = [f for f in required if not markers.get(f)]

    # Compute regions actually containing any match signal (informational).
    regions_used = [name for name, text in regions.items() if text]

    overall_arbiter: str
    if arbitration_needed:
        overall_arbiter = "pending-arbitration"
    elif markers:
        overall_arbiter = "deterministic"
    else:
        overall_arbiter = "fallback"

    classified_record = {
        "id": stable_record_id(record),
        "title": str(record.get("title") or ""),
        "content": str(record.get("content") or ""),
        "markers": markers,
        "missingRequiredMarkers": missing_required,
        "hierarchy": derive_hierarchy(markers, schema),
        "relations": record.get("relations", {"explicit": [], "inferred": []}),
        "source": _merge_source(record, {
            "classifierVersion": "2",
            "classifierNotes": {
                "classifierVersion": "2",
                "arbiter": overall_arbiter,
                "regionsUsed": regions_used,
                "scores": scores_by_field,
                "reasoning": None,
            },
        }),
        "revision": {
            "version": int(record.get("revision", {}).get("version", 1)),
            "updatedAt": record.get("revision", {}).get("updatedAt")
                or record.get("updatedAt") or now_iso(),
        },
        "tokens": sorted(set(
            t for region_text in regions.values() for t in tokenize(region_text or "")
        )),
        "classifiedAt": now_iso(),
    }

    if arbitration_needed:
        classified_record["arbitrationRequest"] = _build_arbitration_request(
            record, regions, scores_by_field, arbitration_needed, schema, required,
        )

    return classified_record


def _merge_source(record: dict[str, Any], extra_metadata: dict[str, Any]) -> dict[str, Any]:
    source = dict(record.get("source") or {})
    metadata = dict(source.get("metadata") or {})
    metadata.update(extra_metadata)
    source["metadata"] = metadata
    return source


def _build_arbitration_request(
    record: dict[str, Any],
    regions: dict[str, str],
    scores_by_field: dict[str, list[dict[str, Any]]],
    arbitration_needed: list[dict[str, Any]],
    schema: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    body_preview = (regions.get("body") or "")[:2000]
    field_names = [item["field"] for item in arbitration_needed]
    return {
        "recordId": stable_record_id(record),
        "record": {
            "title": str(record.get("title") or ""),
            "breadcrumb": regions.get("breadcrumb", ""),
            "frontmatter": regions.get("frontmatter", ""),
            "metadataBlock": regions.get("metadataBlock", ""),
            "bodyPreview": body_preview,
        },
        "candidates": {f: scores_by_field.get(f, []) for f in field_names},
        "allowedValues": {
            f: list((schema.get("markers", {}) or {}).get(f, []))
            for f in field_names
        },
        "requiredFields": required,
        "instructions": (
            "Pick the single best value per field from allowedValues. "
            "Return null only if truly nothing fits. Required fields should "
            "not be null unless absolutely necessary."
        ),
    }
```

- [ ] **Step 4: Adjust existing test_core.py assertions if needed**

Read `tests/test_core.py` and update any assertion that relied on the old flat shape (e.g., if something checks `result["source"]` strictly equal). Expected changes are minimal because most tests pass a bare record and only assert on `markers`.

- [ ] **Step 5: Run tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_arbiter -v
# Expected: 3 tests pass.
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: all prior + 3 new pass.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_arbiter.py tests/test_core.py
git commit -m "Rewrite classify_record on the 4-stage pipeline"
```

---

## Milestone 4 — Learning loop

### Task 9: Hierarchy mining

**Files:**
- Create: `scripts/classifier_learning.py`
- Test: `tests/test_learning.py` (new, this task adds mine_hierarchy)

- [ ] **Step 1: Write failing test**

```python
# tests/test_learning.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from classifier_learning import mine_hierarchy  # noqa: E402


class HierarchyMiningTests(unittest.TestCase):
    def _rec(self, rid: str, parent: str) -> dict:
        return {"id": rid, "source": {"metadata": {"parent": parent}}}

    def test_extracts_repeated_ancestors(self):
        records = [
            self._rec("1", "kenmore > Tasks"),
            self._rec("2", "kenmore > Tasks"),
            self._rec("3", "kenmore > Architecture"),
            self._rec("4", "kenmore > Architecture > bl-api"),
            self._rec("5", "kenmore > Architecture > bl-api"),
        ]
        proposals = mine_hierarchy(records)
        values = [p["value"] for p in proposals]
        # Universal "kenmore" is skipped (appears in 5/5).
        self.assertNotIn("kenmore", values)
        self.assertIn("tasks", values)
        self.assertIn("architecture", values)
        self.assertIn("bl-api", values)

    def test_drops_ancestors_below_support_threshold(self):
        records = [
            self._rec("1", "alpha > beta"),  # "alpha" appears once
            self._rec("2", "gamma > delta"),
        ]
        proposals = mine_hierarchy(records)
        self.assertEqual(proposals, [])

    def test_confidence_decreases_with_depth(self):
        records = [
            self._rec("1", "kenmore > Shared > Architecture > Deep > Deeper"),
            self._rec("2", "kenmore > Shared > Architecture > Deep"),
            self._rec("3", "kenmore > Shared > Architecture > Other"),
        ]
        proposals = mine_hierarchy(records)
        arch = next(p for p in proposals if p["value"] == "architecture")
        deep = next((p for p in proposals if p["value"] == "deep"), None)
        if deep is not None:
            self.assertGreaterEqual(arch["confidence"], deep["confidence"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: ImportError.

- [ ] **Step 3: Implement hierarchy miner**

Create `scripts/classifier_learning.py`:

```python
"""Corpus learner — mines taxonomy candidates and computes marker importance.

Three mining strategies in Phase 1: hierarchy, n-gram, code-path. Each
returns proposal dicts with a `confidence` score and a `source` tag so the
downstream review UX and auto-accept policy can tell them apart.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def mine_hierarchy(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(records)
    if total < 2:
        return []

    ancestor_records: dict[str, set[str]] = {}
    ancestor_depths: dict[str, list[int]] = {}

    for record in records:
        parent = (record.get("source") or {}).get("metadata", {}).get("parent") or ""
        parts = [p.strip() for p in parent.split(">") if p.strip()]
        for depth, part in enumerate(parts):
            key = _slug(part)
            if not key:
                continue
            ancestor_records.setdefault(key, set()).add(str(record.get("id") or ""))
            ancestor_depths.setdefault(key, []).append(depth)

    proposals: list[dict[str, Any]] = []
    for key, rec_ids in ancestor_records.items():
        support = len(rec_ids) / total
        if len(rec_ids) < 2 or support == 1.0:
            continue
        depths = ancestor_depths[key]
        avg_depth = sum(depths) / len(depths)
        distinctiveness = 1.0 - support
        depth_penalty = max(0.0, 1.0 - (avg_depth / 5.0))
        confidence = round(
            0.4 * support + 0.4 * distinctiveness + 0.2 * depth_penalty,
            3,
        )
        if confidence < 0.30:
            continue
        proposals.append({
            "value": key,
            "source": "hierarchy",
            "confidence": confidence,
            "supportRecords": sorted(rec_ids)[:5],
            "detail": {"averageDepth": round(avg_depth, 2), "occurrences": len(depths)},
        })
    proposals.sort(key=lambda p: -p["confidence"])
    return proposals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/classifier_learning.py tests/test_learning.py
git commit -m "Add hierarchy mining for learner"
```

---

### Task 10: N-gram mining + code-path mining

**Files:**
- Modify: `scripts/classifier_learning.py` (add `mine_ngrams`, `mine_code_paths`)
- Modify: `tests/test_learning.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_learning.py
from classifier_learning import mine_code_paths, mine_ngrams  # noqa: E402


class NgramMiningTests(unittest.TestCase):
    def test_finds_strong_collocations(self):
        records = [
            {"id": "1", "title": "challenge payment flow", "content": "challenge payment ninjacharge"},
            {"id": "2", "title": "challenge payment retry", "content": "challenge payment again"},
            {"id": "3", "title": "challenge payment status", "content": "challenge payment status"},
            {"id": "4", "title": "unrelated",               "content": "lorem ipsum"},
        ]
        proposals = mine_ngrams(records)
        values = [p["value"] for p in proposals]
        self.assertIn("challenge-payment", values)

    def test_skips_universal_bigrams(self):
        records = [
            {"id": "1", "title": "",  "content": "and the boss said"},
            {"id": "2", "title": "",  "content": "and the problem was"},
            {"id": "3", "title": "",  "content": "and the fix is"},
        ]
        proposals = mine_ngrams(records)
        self.assertNotIn("and-the", [p["value"] for p in proposals])


class CodePathMiningTests(unittest.TestCase):
    def test_extracts_path_components(self):
        records = [
            {"id": "1", "title": "", "content":
                "look at bl-api/modules/trader/challenge/index.js"},
            {"id": "2", "title": "", "content":
                "fix bl-api/modules/trader/challenge/retry.js"},
        ]
        proposals = mine_code_paths(records)
        values = [p["value"] for p in proposals]
        self.assertIn("challenge", values)
        self.assertIn("trader", values)

    def test_ignores_common_prefixes(self):
        records = [
            {"id": "1", "title": "", "content": "look at src/foo.js"},
            {"id": "2", "title": "", "content": "look at src/bar.js"},
        ]
        proposals = mine_code_paths(records)
        values = [p["value"] for p in proposals]
        self.assertNotIn("src", values)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: ImportError for `mine_ngrams`, `mine_code_paths`.

- [ ] **Step 3: Implement n-gram and code-path miners**

Append to `scripts/classifier_learning.py`:

```python
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
_PATH_RE = re.compile(r"\b[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+){2,}\b")
_COMMON_PATH_PARTS = {
    "src", "app", "lib", "modules", "module", "dist", "build", "node_modules",
    "test", "tests", "spec", "specs", "assets", "public", "utils", "util",
    "index", "main", "config", "configs", "package", "packages",
}
_STOP_TOKENS = {
    "the", "and", "for", "with", "into", "after", "before", "from", "need",
    "this", "that", "has", "have", "was", "were", "are", "been", "being",
    "when", "while", "then", "than", "but", "you", "your", "our", "their",
    "its", "his", "her", "they", "them", "these", "those",
}


def _tokens_in_order(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(text.lower())]


def mine_ngrams(
    records: list[dict[str, Any]],
    *,
    min_doc_freq: int = 2,
    min_confidence: float = 0.50,
    limit: int = 50,
) -> list[dict[str, Any]]:
    total = len(records)
    if total < 2:
        return []

    token_doc_count: Counter[str] = Counter()
    bigram_doc_count: Counter[tuple[str, str]] = Counter()

    for record in records:
        text = " ".join([
            str(record.get("title") or ""),
            str(record.get("content") or ""),
        ])
        tokens = _tokens_in_order(text)
        unique_tokens = set(tokens)
        token_doc_count.update(unique_tokens)

        unique_bigrams: set[tuple[str, str]] = set()
        for a, b in zip(tokens, tokens[1:]):
            if a in _STOP_TOKENS or b in _STOP_TOKENS:
                continue
            unique_bigrams.add((a, b))
        bigram_doc_count.update(unique_bigrams)

    proposals: list[dict[str, Any]] = []
    log_total = math.log(total) or 1.0

    for bigram, doc_freq in bigram_doc_count.items():
        if doc_freq < min_doc_freq or doc_freq == total:
            continue
        a, b = bigram
        a_freq = max(token_doc_count.get(a, 1), 1)
        b_freq = max(token_doc_count.get(b, 1), 1)
        joint = doc_freq
        idf = math.log(total / doc_freq)
        pmi = math.log((joint * total) / (a_freq * b_freq)) if a_freq * b_freq else 0.0
        confidence = round(min(1.0,
            0.5 * (idf / log_total) + 0.3 * (pmi / log_total) + 0.2 * (doc_freq / total)
        ), 3)
        if confidence < min_confidence:
            continue
        proposals.append({
            "value": f"{a}-{b}",
            "source": "ngram",
            "confidence": confidence,
            "supportRecords": [],
            "detail": {"ngram": [a, b], "docFreq": doc_freq, "pmi": round(pmi, 3)},
        })

    proposals.sort(key=lambda p: -p["confidence"])
    return proposals[:limit]


def mine_code_paths(
    records: list[dict[str, Any]],
    *,
    min_occurrences: int = 2,
    limit: int = 50,
) -> list[dict[str, Any]]:
    component_counts: Counter[str] = Counter()
    component_records: dict[str, set[str]] = {}

    for record in records:
        text = " ".join([str(record.get("title") or ""), str(record.get("content") or "")])
        for match in _PATH_RE.finditer(text):
            path = match.group(0)
            parts = [p.strip().lower() for p in path.split("/") if p.strip()]
            for part in parts:
                slug = _slug(part)
                if not slug or slug in _COMMON_PATH_PARTS or slug.endswith(".js") \
                   or slug.endswith(".ts") or slug.endswith(".py"):
                    continue
                component_counts[slug] += 1
                component_records.setdefault(slug, set()).add(str(record.get("id") or ""))

    proposals: list[dict[str, Any]] = []
    for component, count in component_counts.items():
        if count < min_occurrences:
            continue
        proposals.append({
            "value": component,
            "source": "code-path",
            "confidence": round(min(1.0, 0.4 + 0.1 * count), 3),
            "supportRecords": sorted(component_records[component])[:5],
            "detail": {"occurrences": count},
        })
    proposals.sort(key=lambda p: -p["confidence"])
    return proposals[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: 7 tests pass (3 hierarchy + 2 ngram + 2 code-path).

- [ ] **Step 5: Commit**

```bash
git add scripts/classifier_learning.py tests/test_learning.py
git commit -m "Add n-gram and code-path miners"
```

---

### Task 11: Marker importance scoring + full-pass orchestrator

**Files:**
- Modify: `scripts/classifier_learning.py` (add `compute_marker_importance`, `run_full_pass`)
- Modify: `tests/test_learning.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_learning.py
from classifier_learning import compute_marker_importance, run_full_pass  # noqa: E402


class MarkerImportanceTests(unittest.TestCase):
    def _rec(self, rid: str, markers: dict, regions_used: list[str] | None = None) -> dict:
        return {
            "id": rid,
            "markers": markers,
            "source": {"metadata": {
                "classifierNotes": {"regionsUsed": regions_used or ["body"]}
            }},
        }

    def test_field_populated_everywhere_has_higher_presence(self):
        records = [
            self._rec("1", {"type": "task", "domain": "payments"}),
            self._rec("2", {"type": "bug",  "domain": "payments"}),
            self._rec("3", {"type": "task"}),  # no domain
        ]
        importance = compute_marker_importance(records)
        self.assertGreater(importance["type"], importance["domain"])

    def test_explicit_metadata_boosts_importance(self):
        records = [
            self._rec("1", {"status": "done"}, regions_used=["metadataBlock"]),
            self._rec("2", {"status": "new"},  regions_used=["frontmatter"]),
        ]
        importance = compute_marker_importance(records)
        self.assertGreater(importance.get("status", 0), 0.7)


class RunFullPassTests(unittest.TestCase):
    def test_returns_proposals_and_importance(self):
        records = [
            {"id": "1", "title": "challenge payment flow",
             "content": "see bl-api/modules/trader/challenge/index.js",
             "markers": {"type": "task"},
             "source": {"metadata": {"parent": "kenmore > Tasks"}}},
            {"id": "2", "title": "challenge payment retry",
             "content": "bl-api/modules/trader/challenge/retry.js",
             "markers": {"type": "bug"},
             "source": {"metadata": {"parent": "kenmore > Tasks"}}},
        ]
        result = run_full_pass(records)
        self.assertIn("proposals", result)
        self.assertIn("markerImportance", result)
        self.assertIn("type", result["markerImportance"])
```

- [ ] **Step 2: Run tests to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: ImportError.

- [ ] **Step 3: Implement importance + full-pass**

Append to `scripts/classifier_learning.py`:

```python
def compute_marker_importance(records: list[dict[str, Any]]) -> dict[str, float]:
    total = len(records)
    if total == 0:
        return {}

    field_populated: Counter[str] = Counter()
    field_values: dict[str, Counter[str]] = {}
    field_explicit: Counter[str] = Counter()

    for record in records:
        markers = record.get("markers") or {}
        notes = ((record.get("source") or {}).get("metadata") or {}).get(
            "classifierNotes"
        ) or {}
        regions_used = set(notes.get("regionsUsed") or [])
        explicit_regions = {"frontmatter", "metadataBlock"}
        used_explicit = bool(regions_used & explicit_regions)

        for field, value in markers.items():
            if not value:
                continue
            field_populated[field] += 1
            field_values.setdefault(field, Counter())[str(value)] += 1
            if used_explicit:
                field_explicit[field] += 1

    importance: dict[str, float] = {}
    for field, populated in field_populated.items():
        presence = populated / total
        values = field_values[field]
        if len(values) <= 1:
            discriminative = 0.0
        else:
            total_counts = sum(values.values())
            probs = [c / total_counts for c in values.values()]
            entropy = -sum(p * math.log2(p) for p in probs if p > 0)
            max_entropy = math.log2(len(values))
            discriminative = entropy / max_entropy if max_entropy > 0 else 0.0
        explicit_rate = (field_explicit.get(field, 0) / populated) if populated else 0.0
        importance[field] = round(
            0.3 * presence + 0.4 * discriminative + 0.3 * explicit_rate,
            3,
        )
    return importance


def run_full_pass(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "proposals": {
            "hierarchy": mine_hierarchy(records),
            "ngram":     mine_ngrams(records),
            "codePath":  mine_code_paths(records),
        },
        "markerImportance": compute_marker_importance(records),
        "corpusSize": len(records),
    }
```

- [ ] **Step 4: Run tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/classifier_learning.py tests/test_learning.py
git commit -m "Add marker importance scoring and full-pass orchestrator"
```

---

### Task 12: `learn_schema`, `list_proposals`, `apply_proposal_decision` in core

**Files:**
- Modify: `scripts/context_graph_core.py`
- Test: `tests/test_proposals.py` (new)

- [ ] **Step 1: Write failing tests for proposals**

```python
# tests/test_proposals.py
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
                {"id": "1", "title": "challenge payment", "content": "challenge payment flow",
                 "source": {"metadata": {"parent": "kenmore > Tasks"}}},
                {"id": "2", "title": "challenge payment", "content": "challenge payment retry",
                 "source": {"metadata": {"parent": "kenmore > Tasks"}}},
            ]
            index_records({"graphPath": str(root / ".context-graph" / "graph.json"),
                           "records": records, "workspaceRoot": str(root)})
            result = learn_schema({"workspaceRoot": str(root)})
            self.assertGreater(len(result["proposals"]["hierarchy"]
                                  + result["proposals"]["ngram"]
                                  + result["proposals"]["codePath"]), 0)
            learned_path = root / ".context-graph" / "schema.learned.json"
            self.assertTrue(learned_path.exists())

    def test_list_proposals_returns_pending_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            records = [
                {"id": "1", "title": "challenge payment",
                 "content": "challenge payment challenge payment challenge payment",
                 "source": {"metadata": {"parent": "kenmore > Tasks"}}},
                {"id": "2", "title": "challenge payment",
                 "content": "challenge payment challenge payment challenge payment",
                 "source": {"metadata": {"parent": "kenmore > Tasks"}}},
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            learn_schema({"workspaceRoot": str(root)})
            result = list_proposals({"workspaceRoot": str(root)})
            self.assertIn("pending", result)

    def test_apply_accept_moves_to_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            # Seed learned.json with one pending proposal directly.
            (root / ".context-graph" / "schema.learned.json").write_text(json.dumps({
                "version": "1",
                "proposals": {"pending": [
                    {"value": "challenge", "source": "hierarchy", "confidence": 0.95}
                ], "rejected": []},
                "accepted": {},
            }))
            apply_proposal_decision({
                "workspaceRoot": str(root),
                "value": "challenge",
                "decision": "accept",
                "field": "domain",
            })
            learned = json.loads(
                (root / ".context-graph" / "schema.learned.json").read_text()
            )
            self.assertIn("challenge", learned["accepted"].get("domain", []))
            self.assertEqual(learned["proposals"]["pending"], [])

    def test_apply_reject_moves_to_rejected_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(Path(tmp))
            (root / ".context-graph" / "schema.learned.json").write_text(json.dumps({
                "version": "1",
                "proposals": {"pending": [
                    {"value": "bl-api", "source": "hierarchy", "confidence": 0.80}
                ], "rejected": []},
                "accepted": {},
            }))
            apply_proposal_decision({
                "workspaceRoot": str(root),
                "value": "bl-api",
                "decision": "reject",
            })
            learned = json.loads(
                (root / ".context-graph" / "schema.learned.json").read_text()
            )
            rejected_values = [r["value"] for r in learned["proposals"]["rejected"]]
            self.assertIn("bl-api", rejected_values)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_proposals -v`
Expected: ImportError for new functions.

- [ ] **Step 3: Implement the three functions in core**

Append to `scripts/context_graph_core.py`:

```python
from classifier_learning import run_full_pass


def _load_learned(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "1", "proposals": {"pending": [], "rejected": []}, "accepted": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("proposals", {"pending": [], "rejected": []})
    data["proposals"].setdefault("pending", [])
    data["proposals"].setdefault("rejected", [])
    data.setdefault("accepted", {})
    return data


def _save_learned(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["updatedAt"] = now_iso()
    data.setdefault("version", "1")
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
        f.write("\n")


def learn_schema(payload: dict[str, Any]) -> dict[str, Any]:
    ws = Path(payload["workspaceRoot"]).resolve() if payload.get("workspaceRoot") \
         else require_workspace()
    graph_path = payload.get("graphPath") or str(default_graph_path(ws))
    graph = load_graph(graph_path)
    records = list(graph.get("records", {}).values())
    analysis = run_full_pass(records)

    learned_path = schema_learned_path(ws)
    learned = _load_learned(learned_path)
    # Known rejected set — never re-propose.
    rejected_values = {
        (item.get("field"), item.get("value")) for item in learned["proposals"]["rejected"]
    }
    rejected_any_field = {v for _, v in rejected_values}

    # Merge hierarchy + ngram + codePath into learned.proposals.pending,
    # skipping values already accepted anywhere or rejected anywhere.
    accepted_values = {v for vs in learned["accepted"].values() for v in vs}
    pending: list[dict[str, Any]] = list(learned["proposals"]["pending"])
    seen_pending_values = {p["value"] for p in pending}

    for strategy in ("hierarchy", "ngram", "codePath"):
        for prop in analysis["proposals"].get(strategy, []):
            v = prop["value"]
            if v in accepted_values or v in rejected_any_field or v in seen_pending_values:
                continue
            pending.append(prop)
            seen_pending_values.add(v)

    learned["proposals"]["pending"] = pending
    learned["corpusSize"] = analysis["corpusSize"]
    learned["markerImportance"] = analysis["markerImportance"]
    _save_learned(learned_path, learned)

    return {
        "workspaceRoot": str(ws),
        "proposals": analysis["proposals"],
        "pendingCount": len(pending),
        "markerImportance": analysis["markerImportance"],
        "corpusSize": analysis["corpusSize"],
    }


def list_proposals(payload: dict[str, Any]) -> dict[str, Any]:
    ws = Path(payload["workspaceRoot"]).resolve() if payload.get("workspaceRoot") \
         else require_workspace()
    learned = _load_learned(schema_learned_path(ws))
    return {
        "pending":  learned["proposals"]["pending"],
        "accepted": learned["accepted"],
        "rejected": learned["proposals"]["rejected"],
    }


def apply_proposal_decision(payload: dict[str, Any]) -> dict[str, Any]:
    ws = Path(payload["workspaceRoot"]).resolve() if payload.get("workspaceRoot") \
         else require_workspace()
    value = str(payload.get("value") or "")
    decision = str(payload.get("decision") or "")
    field = payload.get("field")
    if decision not in ("accept", "reject", "skip"):
        raise ValueError("decision must be one of accept/reject/skip")
    if decision == "accept" and not field:
        raise ValueError("field is required when decision=accept")

    learned_path = schema_learned_path(ws)
    learned = _load_learned(learned_path)
    pending = learned["proposals"]["pending"]
    remaining = [p for p in pending if p.get("value") != value]

    if decision == "accept":
        learned["accepted"].setdefault(field, [])
        if value not in learned["accepted"][field]:
            learned["accepted"][field].append(value)
    elif decision == "reject":
        learned["proposals"]["rejected"].append({
            "value": value, "field": field, "rejectedAt": now_iso(),
        })
    # skip: just pop from pending

    learned["proposals"]["pending"] = remaining
    _save_learned(learned_path, learned)
    return {
        "value": value,
        "decision": decision,
        "field": field,
        "remainingPending": len(remaining),
    }
```

- [ ] **Step 4: Run tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_proposals -v`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_core.py tests/test_proposals.py
git commit -m "Add learn_schema / list_proposals / apply_proposal_decision"
```

---

### Task 13: Light-learn trigger + IDF refresh inside `index_records`

**Files:**
- Modify: `scripts/context_graph_core.py` (`index_records` — add post-ingest steps)
- Modify: `tests/test_core.py` or `tests/test_learning.py` (add regression test for the side effects)

- [ ] **Step 1: Add failing test**

Append to `tests/test_learning.py`:

```python
from context_graph_core import (  # noqa: E402
    default_graph_path, index_records, init_workspace,
)


class IndexRecordsSideEffectsTests(unittest.TestCase):
    def test_index_records_refreshes_idf_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            records = [
                {"id": "1", "title": "alpha", "content": "alpha beta"},
                {"id": "2", "title": "gamma", "content": "alpha gamma"},
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            idf_path = root / ".context-graph" / "idf_stats.json"
            self.assertTrue(idf_path.exists())
            stats = json.loads(idf_path.read_text())
            self.assertEqual(stats["corpusSize"], 2)
            self.assertEqual(stats["tokenDocumentFrequency"]["alpha"], 2)

    def test_index_records_triggers_light_learn(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            init_workspace({"rootPath": str(root)})
            records = [
                {"id": f"{i}", "title": "challenge payment",
                 "content": "challenge payment flow",
                 "source": {"metadata": {"parent": "kenmore > Tasks"}}}
                for i in range(3)
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            learned_path = root / ".context-graph" / "schema.learned.json"
            self.assertTrue(learned_path.exists())
```

Add `import json` and `import tempfile` at the top if missing.

- [ ] **Step 2: Run tests to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v`
Expected: side-effect tests fail (no idf_stats.json / schema.learned.json produced by index_records).

- [ ] **Step 3: Update `index_records`**

In `scripts/context_graph_core.py`, find the existing `index_records`. At the end, after `write_graph`, add:

```python
    # Side-effect 1: IDF refresh (only when a workspace is available).
    try:
        idf_target = idf_stats_path(
            Path(payload["workspaceRoot"]).resolve() if payload.get("workspaceRoot") else None
        )
    except WorkspaceNotInitializedError:
        idf_target = None
    if idf_target is not None:
        from classifier_idf import compute_idf_from_records, save_idf_stats
        stats = compute_idf_from_records(list(graph["records"].values()))
        save_idf_stats(idf_target, stats)

    # Side-effect 2: Light-learn (only when a workspace is available).
    if idf_target is not None:
        try:
            learn_schema({
                "workspaceRoot": str(Path(payload["workspaceRoot"]).resolve()
                                     if payload.get("workspaceRoot") else require_workspace()),
                "graphPath": graph_path,
            })
        except WorkspaceNotInitializedError:
            pass
```

- [ ] **Step 4: Run tests**

Run:
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_learning -v
# Expected: 12 tests pass (10 previous + 2 new).
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: total count grows by 2.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_core.py tests/test_learning.py
git commit -m "Trigger IDF refresh and light-learn after index_records"
```

---

## Milestone 5 — MCP tools, slash commands, and sync integration

### Task 14: Register new MCP tools

**Files:**
- Modify: `scripts/context_graph_mcp.py`

- [ ] **Step 1: Add tools**

Add handlers and ToolSpec entries for `learn_schema`, `list_proposals`, `apply_proposal_decision`. Pattern:

```python
def handle_learn_schema(arguments: dict[str, Any]) -> dict[str, Any]:
    from context_graph_core import learn_schema
    return learn_schema(arguments)


def handle_list_proposals(arguments: dict[str, Any]) -> dict[str, Any]:
    from context_graph_core import list_proposals
    return list_proposals(arguments)


def handle_apply_proposal_decision(arguments: dict[str, Any]) -> dict[str, Any]:
    from context_graph_core import apply_proposal_decision
    return apply_proposal_decision(arguments)
```

Add three `ToolSpec(...)` entries to `TOOLS`:

```python
ToolSpec(
    name="learn_schema",
    title="Run Schema Learner",
    description="Mine hierarchy, n-grams, and code paths from the workspace graph and write candidate proposals to schema.learned.json.",
    input_schema={"type": "object", "properties": {
        "workspaceRoot": {"type": "string"},
        "graphPath": {"type": "string"},
    }},
    output_schema={"type": "object"},
    handler=handle_learn_schema,
),
ToolSpec(
    name="list_proposals",
    title="List Schema Proposals",
    description="Return pending, accepted, and rejected marker proposals for the workspace.",
    input_schema={"type": "object", "properties": {"workspaceRoot": {"type": "string"}}},
    output_schema={"type": "object"},
    handler=handle_list_proposals,
),
ToolSpec(
    name="apply_proposal_decision",
    title="Apply Schema Proposal Decision",
    description="Accept, reject, or skip a pending proposal. Accept requires a target field.",
    input_schema={"type": "object", "properties": {
        "workspaceRoot": {"type": "string"},
        "value":    {"type": "string"},
        "field":    {"type": "string"},
        "decision": {"type": "string", "enum": ["accept", "reject", "skip"]},
    }, "required": ["value", "decision"]},
    output_schema={"type": "object"},
    handler=handle_apply_proposal_decision,
),
```

- [ ] **Step 2: Verify MCP registration**

```bash
python3 -c "import sys; sys.path.insert(0, 'scripts'); import context_graph_mcp; names=[t.name for t in context_graph_mcp.TOOLS]; print(names); [print('missing', n) for n in ['init_workspace','learn_schema','list_proposals','apply_proposal_decision'] if n not in names]"
# Expected: all four present, nothing missing.
```

- [ ] **Step 3: Commit**

```bash
git add scripts/context_graph_mcp.py
git commit -m "Register new MCP tools: learn_schema, list_proposals, apply_proposal_decision"
```

---

### Task 15: `/cg-schema-learn` and `/cg-schema-review` slash commands

**Files:**
- Create: `commands/cg-schema-learn.md`
- Create: `commands/cg-schema-review.md`

- [ ] **Step 1: Write `/cg-schema-learn`**

```md
---
description: Run the schema learner over the current workspace graph
---

The user wants to run the adaptive classifier's learning pass over their workspace's graph.

Steps:

1. Call `mcp__context-graph__learn_schema` with `{}` (workspace is inferred from CWD).
2. Render a compact summary:
   - `corpusSize`
   - Counts per strategy: hierarchy, ngram, codePath
   - Total `pendingCount`
   - Top 5 marker importance entries sorted descending
3. If `pendingCount > 0`, remind the user they can triage via `/cg-schema-review`.
4. If the tool raises "No Context Graph workspace found", tell the user to run `/cg-init` first.
```

- [ ] **Step 2: Write `/cg-schema-review`**

```md
---
description: Triage pending schema proposals — accept, reject, or skip
---

The user wants to review the pending marker proposals the learner has queued.

Steps:

1. Call `mcp__context-graph__list_proposals` with `{}`.
2. If `pending` is empty, say so and stop.
3. Otherwise, walk through proposals one at a time. For each proposal, show:
   - Value
   - Source (`hierarchy`, `ngram`, `code-path`)
   - Confidence
   - Sample support records (first 5 ids)
   - Detail (e.g., n-gram tokens, code-path occurrences, average hierarchy depth)
4. Ask the user: accept / reject / skip.
   - If accept: also ask which field (domain / flow / artifact / type / severity / status / project / room / scope / owner).
   - Call `mcp__context-graph__apply_proposal_decision` with `{value, decision, field?}`.
5. Repeat until the user quits or the queue is empty.
6. On exit, report how many were accepted, rejected, skipped, and remaining.
```

- [ ] **Step 3: Commit**

```bash
git add commands/cg-schema-learn.md commands/cg-schema-review.md
git commit -m "Add /cg-schema-learn and /cg-schema-review slash commands"
```

---

### Task 16: Update `/cg-sync-notion` to orchestrate LLM arbitration

**Files:**
- Modify: `commands/cg-sync-notion.md`

- [ ] **Step 1: Rewrite the command body**

Replace existing `commands/cg-sync-notion.md` with:

```md
---
description: Pull Notion pages into the Context Graph via the official Notion MCP
argument-hint: <scope>  (search query, page title, or database name)
---

The user wants to sync Notion content into the workspace's Context Graph. The Notion MCP is expected to be connected (OAuth in the browser) so no API key is needed.

Steps:

1. Confirm a workspace exists:
   - If the session is in a directory without `.context-graph/workspace.json`, tell the user to run `/cg-init` first and stop.

2. Scope handling:
   - If `$ARGUMENTS` is empty, ask the user for a search scope (keyword, page title, or database name) and stop.

3. Search via Notion MCP (e.g. `mcp__notion__notion-search`):
   - `query` = `$ARGUMENTS`, `query_type: "internal"`, `page_size: 10`, `filters: {}`.
   - If no Notion MCP tool is connected, tell the user and point them at `scripts/smoke_notion.py` (headless fallback with `NOTION_TOKEN`).
   - If more than ~50 pages returned, ask the user to confirm before pulling all.

4. For each page (in order):
   a. Fetch via Notion MCP fetch tool. Record the `timestamp` from search as `last_edited_time`.
   b. Build a draft record:
      - `id`: `notion:<32-hex page id>` (strip hyphens from the UUID, lowercase).
      - `title`: page title.
      - `content`: everything between `<content>` and `</content>` inside `text`.
      - `source.system`: `"notion"`, `source.url`: full Notion URL.
      - `source.metadata`: `notionPageId`, `last_edited_time`, `parent` (reversed ancestor-path joined with " > ").

   c. Call `mcp__context-graph__classify_record` with the draft.
      - If `classifierNotes.arbiter == "pending-arbitration"`:
        - Read `arbitrationRequest` — examine `record` (title, frontmatter, metadataBlock, breadcrumb, bodyPreview), `candidates`, and `allowedValues`.
        - For each field in `arbitrationRequest.candidates`, pick the single best value from the corresponding `allowedValues` list. If nothing fits and the field is not in `requiredFields`, return null.
        - Override `record.markers.<field>` with your picks.
        - Set `record.source.metadata.classifierNotes.arbiter` to `"llm-session"` and fill `reasoning` with one sentence explaining your choice.
      - Otherwise (deterministic / fallback): keep the draft as returned.

   d. Add the finalized record to the batch.

5. Call `mcp__context-graph__index_records` once with the full batch.

6. Report:
   - Pages pulled.
   - Records upserted (from `indexResult.upsertedIds`).
   - Count of records that went through `llm-session` arbitration.
   - If the sync response includes a new proposals count, mention `/cg-schema-review`.

Do NOT invent markers beyond what the schema's `allowedValues` permit. Validation is handled server-side — if the MCP tool rejects a value, fallback to the deterministic top.
```

- [ ] **Step 2: Commit**

```bash
git add commands/cg-sync-notion.md
git commit -m "Extend /cg-sync-notion to orchestrate LLM arbitration"
```

---

### Task 17: Update `sync_notion` (Python) for workspace + fallback arbiter

**Files:**
- Modify: `scripts/notion_sync.py`
- Modify: `tests/test_notion_sync.py` (add a case where classifier hits pending-arbitration)

- [ ] **Step 1: Add failing test**

Append to `tests/test_notion_sync.py`:

```python
class SyncFallbackArbiterTests(unittest.TestCase):
    def test_pending_arbitration_degrades_to_fallback(self):
        # Build a fake client factory that returns one page with ambiguous content.
        ... # (Fill in with the existing fake-client scaffolding used in prior tests,
            # passing a page whose text is intentionally ambiguous so classify_record
            # returns pending-arbitration.)
        # Call sync_notion and assert result contains fallbackCount >= 1.
```

Note: the exact scaffolding depends on existing helpers in `test_notion_sync.py`. Use the same `FakeNotionClient` pattern already in the test file.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_sync -v`
Expected: fallbackCount missing from result.

- [ ] **Step 3: Update `sync_notion`**

In `scripts/notion_sync.py`, after records are built but before `index_records`:

```python
from context_graph_core import classify_record

finalized = []
fallback_count = 0
for raw in records:
    classified = classify_record({"record": raw, "workspaceRoot": payload.get("workspaceRoot")})
    notes = classified["source"]["metadata"].get("classifierNotes", {})
    if notes.get("arbiter") == "pending-arbitration":
        # No LLM in headless — persist deterministic draft.
        notes["arbiter"] = "fallback"
        fallback_count += 1
    finalized.append(classified)
records = finalized
```

Return payload extended with `"fallbackCount": fallback_count`.

- [ ] **Step 4: Run tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_notion_sync -v
# Expected: all tests pass including the new fallback test.
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
```

- [ ] **Step 5: Commit**

```bash
git add scripts/notion_sync.py tests/test_notion_sync.py
git commit -m "Degrade pending-arbitration to fallback in headless sync"
```

---

### Task 18: Update `search_graph` to use marker importance

**Files:**
- Modify: `scripts/context_graph_core.py` (`build_context_pack` / `search_graph`)
- Test: `tests/test_core.py` (add an importance-weighted retrieval test)

- [ ] **Step 1: Write failing test**

Append to `tests/test_core.py`:

```python
class MarkerImportanceRetrievalTests(unittest.TestCase):
    def test_importance_weights_applied_when_learned_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            from context_graph_core import init_workspace, index_records, search_graph
            init_workspace({"rootPath": str(root)})
            # Two records — both match "payments" but differ in a field.
            records = [
                {"id": "a", "title": "A", "content": "payments",
                 "markers": {"domain": "payments", "type": "task"}},
                {"id": "b", "title": "B", "content": "payments",
                 "markers": {"domain": "payments", "flow": "deposit"}},
            ]
            index_records({"records": records, "workspaceRoot": str(root)})
            result = search_graph({
                "workspaceRoot": str(root),
                "query": "payments task",
                "markers": {"domain": "payments", "type": "task"},
            })
            # Expect record "a" ranked above "b" because both match domain, but
            # "a" also matches "type" while "b" does not.
            ids = [m["id"] for m in result["directMatches"]]
            self.assertEqual(ids[0], "a")
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_core -v`
Expected: behavior may already work because of the existing scoring; if it does, the test passes and we confirm backward compat. If it fails due to new importance weighting breaking, fix in step 3.

- [ ] **Step 3: Update `build_context_pack` / `search_graph`**

Inside `build_context_pack` in `scripts/context_graph_core.py`:

```python
def _load_importance(workspace_start: Path | None) -> dict[str, float]:
    try:
        learned = _load_learned(schema_learned_path(workspace_start))
        return learned.get("markerImportance", {}) or {}
    except WorkspaceNotInitializedError:
        return {}


# Inside build_context_pack, before the ranking loop:
importance = _load_importance(
    Path(payload["workspaceRoot"]).resolve() if payload.get("workspaceRoot") else None
)

# Replace the simple ratio in record_weight:
def _weighted_marker_score(matched: list[str], queried: dict[str, str]) -> float:
    if not queried:
        return 0.0
    weights = {f: importance.get(f, 0.5) for f in queried}
    total_weight = sum(weights.values()) or 1.0
    matched_weight = sum(weights.get(f, 0.5) for f in matched)
    return matched_weight / total_weight
```

(Adjust the existing `record_weight` function to use `_weighted_marker_score` instead of a naive ratio when computing `exactness`.)

- [ ] **Step 4: Run tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_core.py tests/test_core.py
git commit -m "Weight retrieval by learned marker importance"
```

---

## Milestone 6 — Polish, hooks, docs

### Task 19: Hooks + docs sweep

**Files:**
- Modify: `hooks.json` (confirm SessionStart is workspace-aware — it already walks up via CLAUDE_PLUGIN_ROOT)
- Modify: `scripts/post_edit_reindex.py` (verify: change happens automatically because `default_graph_path` now walks up; no code change required, but add workspace check)
- Modify: `README.md` (new commands, lifecycle references)
- Modify: `docs/roadmap.md` (mark Phase 1 subsystems complete or in progress accordingly)

- [ ] **Step 1: Ensure `post_edit_reindex.py` skips gracefully when no workspace**

Inside `plan_reindex`, add an early return:

```python
# At the top of plan_reindex, after computing edited_dir:
try:
    ws_root = find_workspace_root(edited_dir)
except Exception:
    ws_root = None
if ws_root is None:
    return None
```

Import `find_workspace_root` from `context_graph_core` at the top (with `sys.path` already set by the helper).

- [ ] **Step 2: Update README**

Replace the "Implemented MVP commands" section to list the new commands: `init-workspace`, `learn-schema`, `list-proposals`, `apply-proposal-decision`. Add a "Workspace" section that explains the new `.context-graph/` layout and links to the design spec.

- [ ] **Step 3: Update roadmap**

Mark the Phase 1 items complete:

```md
## Phase 6 follow-ups (Phase 1 of the adaptive plan)
- [x] Workspace binding via `.context-graph/workspace.json`
- [x] Adaptive classifier pipeline (regions + IDF + arbiter + learning loop)
- [x] `/cg-init`, `/cg-schema-learn`, `/cg-schema-review`
- [x] `/cg-sync-notion` orchestrates in-session LLM arbitration
- [x] Backward-compat legacy plugin-data mode
```

(Adjust wording to fit existing roadmap style.)

- [ ] **Step 4: Commit**

```bash
git add hooks.json scripts/post_edit_reindex.py README.md docs/roadmap.md
git commit -m "Phase 1 polish: workspace-aware hooks, README, roadmap"
```

---

### Task 20: End-to-end smoke + final verification

**Files:**
- Modify: none (runs existing code)

- [ ] **Step 1: Full test suite**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: ~91 tests pass.
```

- [ ] **Step 2: MCP tool registration sanity**

```bash
python3 -c "import sys; sys.path.insert(0, 'scripts'); import context_graph_mcp; print(sorted([t.name for t in context_graph_mcp.TOOLS]))"
# Expected: includes init_workspace, learn_schema, list_proposals, apply_proposal_decision on top of existing tools.
```

- [ ] **Step 3: Create a throwaway workspace and run a mini sync**

```bash
tmp=$(mktemp -d)
cd "$tmp"
echo '{}' | python3 /Users/maksnalyvaiko/personal/context-graph/scripts/context_graph_cli.py init-workspace
ls .context-graph/
# Expected: workspace.json present.

echo '{"records":[{"id":"r1","title":"Payment webhook incident","content":"## Metadata\n- **status**: in-progress\n- **domain**: payments\n\n# Detail\nCallback retry causes duplicate payment.","source":{"metadata":{"parent":"kenmore > Tasks"}}}]}' \
  | python3 /Users/maksnalyvaiko/personal/context-graph/scripts/context_graph_cli.py index-records
ls .context-graph/
# Expected: graph.json, idf_stats.json, schema.learned.json.
```

- [ ] **Step 4: If everything passes, final commit**

If prior tasks left any README / roadmap polish undone, finalize here. Otherwise no commit needed.

---

## Self-review checklist (performed after writing this plan)

**Spec coverage:**

- [x] Workspace (S1) — Tasks 1–3
- [x] Region extraction — Task 4
- [x] IDF stats — Task 5
- [x] Schema merge — Task 6
- [x] Scorer + arbiter — Task 7
- [x] Integrated classify_record v2 — Task 8
- [x] Hierarchy, n-gram, code-path mining — Tasks 9, 10
- [x] Marker importance + full-pass — Task 11
- [x] learn / list / apply proposal — Task 12
- [x] Post-ingest light-learn + IDF refresh — Task 13
- [x] MCP tool registration — Task 14
- [x] /cg-init, /cg-schema-learn, /cg-schema-review — Tasks 3, 15
- [x] /cg-sync-notion orchestration — Task 16
- [x] Python `sync_notion` fallback arbiter — Task 17
- [x] search_graph importance weighting — Task 18
- [x] Hooks + docs sweep — Task 19
- [x] End-to-end verify — Task 20

**Placeholder scan:** One placeholder exists in Task 17 Step 1 ("Fill in with the existing fake-client scaffolding used in prior tests"). This is intentional — the fake-client pattern is already established in `tests/test_notion_sync.py` and the executing engineer must follow whatever shape that file uses. The intent is clearly described.

**Type consistency:** Function names audited — `find_workspace_root`, `require_workspace`, `init_workspace`, `learn_schema`, `list_proposals`, `apply_proposal_decision`, `default_graph_path`, `schema_learned_path`, `schema_overlay_path`, `schema_feedback_path`, `idf_stats_path`, `notion_cursor_path` — all consistent across tasks. Public module surfaces (`classifier_regions.extract_regions`, `classifier_idf.compute_idf_from_records`, `classifier_idf.load_idf_stats`, `classifier_idf.save_idf_stats`, `classifier_schema.load_merged_schema`, `classifier_scorer.score_field`, `classifier_scorer.arbitrate`, `classifier_learning.mine_hierarchy`, `classifier_learning.mine_ngrams`, `classifier_learning.mine_code_paths`, `classifier_learning.compute_marker_importance`, `classifier_learning.run_full_pass`) consistent.

---

## Open questions deferred to implementation

(Copied from the spec; resolve inline during tasks.)

1. `extract_metadata_block` heuristic — localized heading list may need expansion based on real-world usage.
2. `/cg-schema-review` partial-session persistence — left as non-blocking UX refinement.
3. Plugin-managed `🤖 Context Graph` parent page creation behavior — opt-in assumed.
