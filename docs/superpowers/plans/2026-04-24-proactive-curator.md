# Proactive Curator Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 8 — proactive curator workflow: light project bootstrap, a curator skill that teaches Claude when/how to capture rules/conventions/gotchas/decisions/intersections/tasks with deterministic markers, hashtag UX in slash commands, smart session priming, and a clear no-Notion message.

**Architecture:** Add a new `scripts/curator_bootstrap.py` module (workspace manifest helpers + project sniff + skeleton preview) and a new `scripts/session_start_prime.py` hook script. Add a new `skills/context-graph-curator/SKILL.md` that ships agent-facing instructions. Add a `parse_hashtags` helper consumed by `/cg-search`. Three new MCP tools (`bootstrap_preview`, `apply_bootstrap_decision`, `parse_hashtags`) make the orchestration callable. No sealed code is modified — all changes are additive.

**Tech Stack:** Python 3.11 stdlib (no new deps), `unittest`, MCP JSON-RPC over stdio, markdown for skills/commands/docs.

**Spec:** [docs/superpowers/specs/2026-04-24-proactive-curator-design.md](../specs/2026-04-24-proactive-curator-design.md)

---

## Milestone 1 — Workspace manifest helpers

Goal: extend the workspace manifest with the new `notion.dirPageIds` and `notion.bootstrapDeclined` fields without breaking the existing init flow. Three small helpers go into `scripts/context_graph_core.py` next to `init_workspace`.

### Task 1: `load_workspace_manifest` and `update_workspace_manifest`

**Files:**
- Modify: `scripts/context_graph_core.py` (add two helpers near `init_workspace`)
- Test: `tests/test_workspace_manifest.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workspace_manifest.py
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
    init_workspace,
    load_workspace_manifest,
    update_workspace_manifest,
)


class WorkspaceManifestHelperTests(unittest.TestCase):
    def test_load_returns_full_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            manifest = load_workspace_manifest(Path(tmp))
            self.assertEqual(manifest["version"], "1")
            self.assertIn("id", manifest)
            self.assertIn("createdAt", manifest)

    def test_load_raises_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                load_workspace_manifest(Path(tmp))

    def test_update_merges_top_level_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            update_workspace_manifest(
                Path(tmp),
                {"notion": {"rootPageId": "abc123", "dirPageIds": {"src/": "p1"}}},
            )
            manifest = load_workspace_manifest(Path(tmp))
            self.assertEqual(manifest["notion"]["rootPageId"], "abc123")
            self.assertEqual(manifest["notion"]["dirPageIds"], {"src/": "p1"})

    def test_update_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            original = load_workspace_manifest(Path(tmp))
            update_workspace_manifest(Path(tmp), {"notion": {"rootPageId": "x"}})
            after = load_workspace_manifest(Path(tmp))
            self.assertEqual(after["id"], original["id"])
            self.assertEqual(after["createdAt"], original["createdAt"])

    def test_update_bumps_updatedAt(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            before = load_workspace_manifest(Path(tmp))["updatedAt"]
            # Sleep just enough for ISO-8601 string to advance:
            import time; time.sleep(0.01)
            update_workspace_manifest(Path(tmp), {"notion": {"x": 1}})
            after = load_workspace_manifest(Path(tmp))["updatedAt"]
            self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/maksnalyvaiko/context-graph && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace_manifest -v`
Expected: ImportError — helpers not defined.

- [ ] **Step 3: Implement the helpers**

In `scripts/context_graph_core.py`, after `init_workspace` (around line 387), add:

```python
def load_workspace_manifest(workspace_root: Path | str) -> dict[str, Any]:
    """Read the workspace manifest from .context-graph/workspace.json.

    Raises FileNotFoundError when the manifest is missing.
    """
    root = Path(str(workspace_root)).resolve()
    manifest_path = root / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No workspace manifest at {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Workspace manifest at {manifest_path} is not an object")
    return data


def update_workspace_manifest(
    workspace_root: Path | str, updates: dict[str, Any]
) -> dict[str, Any]:
    """Merge ``updates`` into the manifest at top level (shallow), bump
    ``updatedAt``, write back atomically (write to .tmp, rename).

    Returns the new manifest. Top-level keys in ``updates`` fully replace
    existing values — this is shallow merge, not recursive.
    """
    root = Path(str(workspace_root)).resolve()
    manifest = load_workspace_manifest(root)
    for key, value in updates.items():
        manifest[key] = value
    manifest["updatedAt"] = now_iso()
    manifest_path = root / ".context-graph" / "workspace.json"
    tmp_path = manifest_path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=True, indent=2)
        f.write("\n")
    tmp_path.replace(manifest_path)
    return manifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_workspace_manifest -v`
Expected: 5 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: 323 prior + 5 new = 328 pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_core.py tests/test_workspace_manifest.py
git commit -m "Add load_workspace_manifest and update_workspace_manifest helpers"
```

---

## Milestone 2 — Bootstrap module (project sniff + skeleton preview)

Goal: a pure-helper module that scans README, manifests, and dir tree to produce a preview dict, plus state helpers for the bootstrap lifecycle. No I/O to Notion at this layer — Notion writes happen via Claude in the session via the official Notion MCP, then results land back via `apply_bootstrap_decision`.

### Task 2: `bootstrap_project_skeleton` — light project sniff

**Files:**
- Create: `scripts/curator_bootstrap.py`
- Test: `tests/test_curator_bootstrap.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_curator_bootstrap.py
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

from curator_bootstrap import bootstrap_project_skeleton  # noqa: E402


class BootstrapSnifferTests(unittest.TestCase):
    def _seed(self, tmp: str, *, readme: str | None, dirs: list[str]) -> Path:
        root = Path(tmp).resolve()
        if readme is not None:
            (root / "README.md").write_text(readme)
        for d in dirs:
            (root / d).mkdir(parents=True, exist_ok=True)
            (root / d / ".keep").write_text("")
        return root

    def test_returns_minimal_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme="# My Project\n\nA short tagline.\n", dirs=["src", "tests", "docs"])
            preview = bootstrap_project_skeleton(root)
            self.assertEqual(preview["projectTitle"], "My Project")
            self.assertIn("A short tagline", preview["tagline"])
            paths = sorted(d["path"] for d in preview["topLevelDirs"])
            self.assertEqual(paths, ["docs/", "src/", "tests/"])

    def test_uses_directory_name_when_no_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme=None, dirs=["src"])
            preview = bootstrap_project_skeleton(root)
            self.assertEqual(preview["projectTitle"], root.name)
            self.assertEqual(preview["tagline"], "")

    def test_excludes_known_noise_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(
                tmp, readme="# x\n",
                dirs=[".git", "node_modules", "dist", "build", "__pycache__", "src"],
            )
            preview = bootstrap_project_skeleton(root)
            paths = [d["path"] for d in preview["topLevelDirs"]]
            self.assertEqual(paths, ["src/"])

    def test_caps_dir_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = [f"d{i}" for i in range(50)]
            root = self._seed(tmp, readme="# x\n", dirs=dirs)
            preview = bootstrap_project_skeleton(root)
            self.assertLessEqual(len(preview["topLevelDirs"]), 30)

    def test_reads_package_json_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme=None, dirs=["src"])
            (root / "package.json").write_text(json.dumps(
                {"name": "my-pkg", "description": "JS lib for X"}
            ))
            preview = bootstrap_project_skeleton(root)
            # README absent — manifest provides title and tagline.
            self.assertEqual(preview["projectTitle"], "my-pkg")
            self.assertEqual(preview["tagline"], "JS lib for X")

    def test_readme_overrides_manifest_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._seed(tmp, readme="# README Title\n", dirs=["src"])
            (root / "package.json").write_text(json.dumps(
                {"name": "manifest-name", "description": "ignored"}
            ))
            preview = bootstrap_project_skeleton(root)
            self.assertEqual(preview["projectTitle"], "README Title")

    def test_readme_truncated_to_200_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            long_readme = "# Project\n\n" + "filler line\n" * 5000
            root = self._seed(tmp, readme=long_readme, dirs=["src"])
            preview = bootstrap_project_skeleton(root)
            # Tagline is the first non-empty paragraph after the heading;
            # we just confirm the sniffer didn't choke on the size.
            self.assertEqual(preview["projectTitle"], "Project")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_bootstrap -v`
Expected: ModuleNotFoundError — `curator_bootstrap` does not exist.

- [ ] **Step 3: Implement the sniffer**

Create `scripts/curator_bootstrap.py`:

```python
"""Light project sniff for the curator bootstrap flow.

Reads README + manifests + top-level dir tree (depth 2, capped) to
produce a preview dict. No I/O to Notion happens here — that lives in
the slash command and the MCP orchestration. This module is pure
stdlib and side-effect free except for the file reads listed above.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_NOISE_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env",
    "dist", "build", "out", "target",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".idea", ".vscode",
    ".next", ".nuxt",
}

_README_LINE_LIMIT = 200
_DIR_LIMIT = 30


def _read_readme(root: Path) -> tuple[str, str]:
    """Return ``(title, tagline)`` from the project README, or ``("", "")``
    when none is found."""
    for name in ("README.md", "README.MD", "Readme.md", "readme.md"):
        path = root / name
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()[:_README_LINE_LIMIT]
            title = ""
            tagline = ""
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not title and stripped.startswith("# "):
                    title = stripped[2:].strip()
                    # First non-empty non-heading paragraph is the tagline:
                    for next_line in lines[i + 1:]:
                        nxt = next_line.strip()
                        if not nxt:
                            continue
                        if nxt.startswith("#"):
                            break
                        tagline = nxt
                        break
                    break
            return title, tagline
    return "", ""


def _read_manifest(root: Path) -> tuple[str, str]:
    """Return ``(title, tagline)`` from a manifest if present.

    Tries ``package.json``, ``pyproject.toml``, ``Cargo.toml``, ``go.mod``
    in that order. Returns ``("", "")`` when nothing is found.
    """
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            return str(data.get("name") or ""), str(data.get("description") or "")
    py = root / "pyproject.toml"
    if py.exists():
        # Stdlib has tomllib in 3.11+. Use it lazily to keep the import
        # local — older interpreters in tests can still load this module.
        try:
            import tomllib
            data = tomllib.loads(py.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            project = data.get("project") or data.get("tool", {}).get("poetry") or {}
            if isinstance(project, dict):
                return str(project.get("name") or ""), str(project.get("description") or "")
    cargo = root / "Cargo.toml"
    if cargo.exists():
        # Cheap parse: scan for `name = "..."` and `description = "..."`
        # under [package]. Avoids a tomllib dependency if running on 3.10.
        text = cargo.read_text(encoding="utf-8", errors="replace")
        in_package = False
        title = ""
        tagline = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                in_package = (s == "[package]")
                continue
            if in_package and s.startswith("name = "):
                title = s.split("=", 1)[1].strip().strip('"')
            elif in_package and s.startswith("description = "):
                tagline = s.split("=", 1)[1].strip().strip('"')
        if title:
            return title, tagline
    return "", ""


def _list_top_level_dirs(root: Path) -> list[dict[str, str]]:
    """Return up to _DIR_LIMIT top-level dirs as ``{path, purpose}`` dicts.

    ``purpose`` is left empty in this layer — the curator skill / LLM
    fills it during the session-time orchestration. We only seed the
    structural part here.
    """
    if not root.is_dir():
        return []
    entries = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name in _NOISE_DIRS or name.startswith("."):
            continue
        entries.append({"path": f"{name}/", "purpose": ""})
        if len(entries) >= _DIR_LIMIT:
            break
    return entries


def bootstrap_project_skeleton(workspace_root: Path | str) -> dict[str, Any]:
    """Sniff project + return a preview dict for the bootstrap flow."""
    root = Path(str(workspace_root)).resolve()
    readme_title, readme_tagline = _read_readme(root)
    manifest_title, manifest_tagline = _read_manifest(root)
    title = readme_title or manifest_title or root.name
    tagline = readme_tagline or manifest_tagline or ""
    dirs = _list_top_level_dirs(root)
    return {
        "projectTitle": title,
        "tagline": tagline,
        "topLevelDirs": dirs,
        "rootPath": str(root),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_bootstrap -v`
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/curator_bootstrap.py tests/test_curator_bootstrap.py
git commit -m "Add bootstrap_project_skeleton: light README/manifest/dir sniff"
```

---

### Task 3: Bootstrap state helpers — `is_bootstrap_needed`, `mark_bootstrap_declined`, `record_bootstrap_result`

**Files:**
- Modify: `scripts/curator_bootstrap.py` (append three helpers)
- Test: `tests/test_curator_bootstrap.py` (extend)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_curator_bootstrap.py
from curator_bootstrap import (  # noqa: E402
    is_bootstrap_needed,
    mark_bootstrap_declined,
    record_bootstrap_result,
)


class BootstrapStateTests(unittest.TestCase):
    def _make_workspace(self, tmp: str) -> Path:
        from context_graph_core import init_workspace
        init_workspace({"rootPath": tmp})
        return Path(tmp).resolve()

    def test_is_bootstrap_needed_when_no_notion_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            self.assertTrue(is_bootstrap_needed(root))

    def test_not_needed_when_rootPageId_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            from context_graph_core import update_workspace_manifest
            root = self._make_workspace(tmp)
            update_workspace_manifest(root, {"notion": {"rootPageId": "abc"}})
            self.assertFalse(is_bootstrap_needed(root))

    def test_not_needed_when_declined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_workspace(tmp)
            mark_bootstrap_declined(root)
            self.assertFalse(is_bootstrap_needed(root))

    def test_not_needed_when_workspace_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_bootstrap_needed(Path(tmp)))

    def test_record_bootstrap_result_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            from context_graph_core import load_workspace_manifest
            root = self._make_workspace(tmp)
            record_bootstrap_result(
                root,
                root_page_id="rootP",
                root_page_url="https://notion/rootP",
                dir_page_ids={"src/": "p1", "tests/": "p2"},
            )
            manifest = load_workspace_manifest(root)
            self.assertEqual(manifest["notion"]["rootPageId"], "rootP")
            self.assertEqual(manifest["notion"]["rootPageUrl"], "https://notion/rootP")
            self.assertEqual(manifest["notion"]["dirPageIds"], {"src/": "p1", "tests/": "p2"})

    def test_mark_declined_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            from context_graph_core import load_workspace_manifest
            root = self._make_workspace(tmp)
            mark_bootstrap_declined(root)
            manifest = load_workspace_manifest(root)
            self.assertTrue(manifest["notion"]["bootstrapDeclined"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_bootstrap -v`
Expected: ImportError for the three new helpers.

- [ ] **Step 3: Implement the state helpers**

Append to `scripts/curator_bootstrap.py`:

```python
def is_bootstrap_needed(workspace_root: Path | str) -> bool:
    """True when the workspace exists, has no Notion root page id, and
    has not been declined.

    Returns False when the workspace manifest is missing entirely (no
    ``init-workspace`` has been run) — bootstrap is not the right action
    in that case; the user should run ``/cg-init`` first.
    """
    try:
        # Local import to avoid a circular import at module load.
        from context_graph_core import load_workspace_manifest
        manifest = load_workspace_manifest(workspace_root)
    except FileNotFoundError:
        return False
    notion = manifest.get("notion") or {}
    if notion.get("rootPageId"):
        return False
    if notion.get("bootstrapDeclined"):
        return False
    return True


def mark_bootstrap_declined(workspace_root: Path | str) -> None:
    """Persist ``bootstrapDeclined: True`` so SessionStart stops nagging."""
    from context_graph_core import update_workspace_manifest, load_workspace_manifest
    manifest = load_workspace_manifest(workspace_root)
    notion = dict(manifest.get("notion") or {})
    notion["bootstrapDeclined"] = True
    update_workspace_manifest(workspace_root, {"notion": notion})


def record_bootstrap_result(
    workspace_root: Path | str,
    *,
    root_page_id: str,
    root_page_url: str | None = None,
    dir_page_ids: dict[str, str] | None = None,
) -> None:
    """Persist the Notion page IDs returned from ``notion-create-pages``."""
    from context_graph_core import (
        update_workspace_manifest,
        load_workspace_manifest,
        now_iso,
    )
    manifest = load_workspace_manifest(workspace_root)
    notion = dict(manifest.get("notion") or {})
    notion["rootPageId"] = str(root_page_id)
    if root_page_url:
        notion["rootPageUrl"] = str(root_page_url)
    if dir_page_ids:
        notion["dirPageIds"] = {str(k): str(v) for k, v in dir_page_ids.items()}
    notion.setdefault("createdAt", now_iso())
    notion["updatedAt"] = now_iso()
    update_workspace_manifest(workspace_root, {"notion": notion})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_bootstrap -v`
Expected: 13 tests pass (7 + 6).

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: 328 prior + 6 new = 334 pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/curator_bootstrap.py tests/test_curator_bootstrap.py
git commit -m "Add bootstrap state helpers: is_needed, mark_declined, record_result"
```

---

## Milestone 3 — Hashtag UX

Goal: a pure helper that translates `#word` tokens in a query string into a `markers: {axis: word}` payload, plus the slash-command prose to use it.

### Task 4: `parse_hashtags` helper

**Files:**
- Create: `scripts/hashtag_parser.py`
- Test: `tests/test_hashtag_parser.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hashtag_parser.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from hashtag_parser import parse_hashtags  # noqa: E402


class HashtagParserTests(unittest.TestCase):
    SCHEMA = {
        "markers": {
            "type": ["task", "bug", "rule", "decision"],
            "domain": ["payments", "auth", "api"],
            "scope": ["convention", "gotcha", "intersection"],
            "artifact": ["webhook", "endpoint"],
        }
    }

    def test_no_hashtags_returns_query_unchanged(self):
        q, m = parse_hashtags("how do we handle webhooks", self.SCHEMA)
        self.assertEqual(q, "how do we handle webhooks")
        self.assertEqual(m, {})

    def test_single_hashtag_resolves_to_axis(self):
        q, m = parse_hashtags("#rule payments retries", self.SCHEMA)
        self.assertEqual(q, "payments retries")
        self.assertEqual(m, {"type": "rule"})

    def test_multiple_hashtags_anded(self):
        q, m = parse_hashtags("#rule #payments", self.SCHEMA)
        self.assertEqual(q, "")
        self.assertEqual(m, {"type": "rule", "domain": "payments"})

    def test_mixed_query_and_hashtags(self):
        q, m = parse_hashtags("how do #api #webhook services talk", self.SCHEMA)
        self.assertEqual(q, "how do services talk")
        self.assertEqual(m, {"domain": "api", "artifact": "webhook"})

    def test_unknown_hashtag_kept_in_query(self):
        q, m = parse_hashtags("#mystery #rule x", self.SCHEMA)
        # Unknown tag stays as a regular word; only the known one is moved.
        self.assertEqual(q, "#mystery x")
        self.assertEqual(m, {"type": "rule"})

    def test_empty_query(self):
        q, m = parse_hashtags("", self.SCHEMA)
        self.assertEqual(q, "")
        self.assertEqual(m, {})

    def test_only_hashtags(self):
        q, m = parse_hashtags("#rule #convention #payments", self.SCHEMA)
        self.assertEqual(q, "")
        self.assertEqual(m, {"type": "rule", "scope": "convention", "domain": "payments"})

    def test_repeated_hashtag_last_wins(self):
        q, m = parse_hashtags("#rule #decision", self.SCHEMA)
        # Both target axis ``type``; keep the last one to give the user a
        # predictable override semantics.
        self.assertEqual(m["type"], "decision")

    def test_case_insensitive_match(self):
        q, m = parse_hashtags("#Rule #PAYMENTS", self.SCHEMA)
        self.assertEqual(m, {"type": "rule", "domain": "payments"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_hashtag_parser -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the parser**

Create `scripts/hashtag_parser.py`:

```python
"""Translate ``#word`` tokens in a query string into a ``markers`` payload
keyed by the schema's axis owning the word's value.

The parser is pure stdlib and side-effect free. Unknown tags are left in
the query (so the user notices the typo) and the function does not
raise on them.
"""

from __future__ import annotations

import re
from typing import Any

_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_-]+)")


def _index_schema_values(schema: dict[str, Any]) -> dict[str, str]:
    """Build a {value_lower: axis} index from schema['markers']."""
    markers = (schema or {}).get("markers") or {}
    index: dict[str, str] = {}
    for axis, values in markers.items():
        if not isinstance(values, list):
            continue
        for value in values:
            key = str(value).lower()
            # If the same value appears under two axes (does not currently
            # happen), the first axis encountered wins.
            index.setdefault(key, axis)
    return index


def parse_hashtags(query: str, schema: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Extract ``#word`` tokens that match a schema marker value.

    Returns ``(remaining_query, markers)``. ``remaining_query`` is the
    original query with matched ``#word`` tokens removed (and whitespace
    cleaned). ``markers`` maps the resolved axis to the canonical
    (lowercase) value. Unknown tags are kept in the query verbatim.

    Repeated tags targeting the same axis: the last one wins.
    """
    index = _index_schema_values(schema)
    markers: dict[str, str] = {}

    def _replace(match: re.Match) -> str:
        word = match.group(1).lower()
        axis = index.get(word)
        if axis is None:
            return match.group(0)  # keep unknown tag in the query
        markers[axis] = word
        return ""

    new_query = _HASHTAG_RE.sub(_replace, query or "")
    new_query = re.sub(r"\s+", " ", new_query).strip()
    return new_query, markers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_hashtag_parser -v`
Expected: 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/hashtag_parser.py tests/test_hashtag_parser.py
git commit -m "Add parse_hashtags: #word → schema-resolved markers payload"
```

---

### Task 5: Update `/cg-search` slash command for hashtag syntax

**Files:**
- Modify: `commands/cg-search.md`

- [ ] **Step 1: Read the current file**

Read `commands/cg-search.md` to confirm the existing structure (Intent modes section and steps).

- [ ] **Step 2: Insert a Hashtag-syntax section before the existing Intent modes section**

In `commands/cg-search.md`, after the frontmatter block (`---` to `---`) and the first sentence, but BEFORE `## Intent modes (optional)`, insert:

````markdown
## Hashtag syntax (optional)

`$ARGUMENTS` may contain `#tag` tokens that resolve to marker filters.
Each tag's value is looked up in `docs/schema.json`; the matched axis is
filled into the `markers` payload that goes to `search_graph`.

Examples:

    /cg-search #rule #payments                  # markers: {type: rule, domain: payments}
    /cg-search #gotcha #auth                    # markers: {scope: gotcha, domain: auth}
    /cg-search #intersection #api #webhook      # markers: {scope: intersection, domain: api, artifact: webhook}
    /cg-search how do we handle #idempotency    # query="how do we handle", markers: {scope: idempotency}

If a `#tag` does not match any schema value, leave it in the query
verbatim (the user may have made a typo). Repeated tags on the same axis
let the last one win.

To compute the split call the MCP tool
`mcp__context-graph__parse_hashtags` with `{"query": $ARGUMENTS}`. The
tool returns `{"query": "...", "markers": {...}}`. Pass both into
`search_graph`: `query` becomes the natural-language part, `markers`
becomes the marker-filter payload.

````

The existing "Intent modes (optional)" section stays as-is. The Steps section also stays — but extend step 2 to include `markers` in the call list (analogous to `intentMode` already added).

- [ ] **Step 3: Append `markers` to the step-2 call list**

Find the block:

```
2. Call the MCP tool `mcp__context-graph__search_graph` with:
   - `query`: `$ARGUMENTS` (with any leading `--mode <name>` already stripped per the Intent modes section above)
   - `graphPath`: `./data/graph.json` (default)
   - `limit`: omit unless the user specified one
   - `intentMode`: include only when the user passed `--mode <name>`; otherwise omit entirely so the call falls back to the no-mode default
```

Replace it with:

```
2. Call the MCP tool `mcp__context-graph__search_graph` with:
   - `query`: the natural-language part returned by `parse_hashtags` (with any leading `--mode <name>` already stripped per the Intent modes section above)
   - `markers`: the `markers` dict returned by `parse_hashtags`; omit when empty
   - `graphPath`: `./data/graph.json` (default)
   - `limit`: omit unless the user specified one
   - `intentMode`: include only when the user passed `--mode <name>`; otherwise omit entirely so the call falls back to the no-mode default
```

- [ ] **Step 4: Commit**

```bash
git add commands/cg-search.md
git commit -m "Document hashtag syntax (#tag → marker filter) in /cg-search"
```

---

## Milestone 4 — MCP tool registration

Goal: surface the three new entry points to Claude.

### Task 6: Register `bootstrap_preview`, `apply_bootstrap_decision`, `parse_hashtags` MCP tools

**Files:**
- Modify: `scripts/context_graph_mcp.py` (add imports + handlers + ToolSpec entries)
- Test: `tests/test_mcp_curator.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_curator.py
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

import context_graph_mcp as m  # noqa: E402


class MCPCuratorToolsTests(unittest.TestCase):
    def _names(self) -> list[str]:
        return [t.name for t in m.TOOLS]

    def test_three_curator_tools_registered(self):
        names = self._names()
        self.assertIn("bootstrap_preview", names)
        self.assertIn("apply_bootstrap_decision", names)
        self.assertIn("parse_hashtags", names)

    def test_bootstrap_preview_runs(self):
        from context_graph_core import init_workspace
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            (Path(tmp) / "README.md").write_text("# proj\n")
            (Path(tmp) / "src").mkdir()
            tool = next(t for t in m.TOOLS if t.name == "bootstrap_preview")
            result = tool.handler({"workspaceRoot": tmp})
            self.assertEqual(result["projectTitle"], "proj")
            self.assertTrue(result["bootstrapNeeded"])

    def test_bootstrap_preview_reports_not_needed_after_decline(self):
        from context_graph_core import init_workspace
        from curator_bootstrap import mark_bootstrap_declined
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            mark_bootstrap_declined(Path(tmp))
            tool = next(t for t in m.TOOLS if t.name == "bootstrap_preview")
            result = tool.handler({"workspaceRoot": tmp})
            self.assertFalse(result["bootstrapNeeded"])

    def test_apply_bootstrap_decision_accept(self):
        from context_graph_core import init_workspace, load_workspace_manifest
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            tool = next(t for t in m.TOOLS if t.name == "apply_bootstrap_decision")
            result = tool.handler({
                "workspaceRoot": tmp,
                "decision": "accept",
                "rootPageId": "root1",
                "rootPageUrl": "https://x/root1",
                "dirPageIds": {"src/": "p1"},
            })
            self.assertTrue(result["recorded"])
            manifest = load_workspace_manifest(Path(tmp))
            self.assertEqual(manifest["notion"]["rootPageId"], "root1")

    def test_apply_bootstrap_decision_decline(self):
        from context_graph_core import init_workspace, load_workspace_manifest
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            tool = next(t for t in m.TOOLS if t.name == "apply_bootstrap_decision")
            result = tool.handler({"workspaceRoot": tmp, "decision": "decline"})
            self.assertTrue(result["recorded"])
            manifest = load_workspace_manifest(Path(tmp))
            self.assertTrue(manifest["notion"]["bootstrapDeclined"])

    def test_apply_bootstrap_decision_unknown_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = next(t for t in m.TOOLS if t.name == "apply_bootstrap_decision")
            with self.assertRaises(ValueError):
                tool.handler({"workspaceRoot": tmp, "decision": "maybe"})

    def test_parse_hashtags_runs(self):
        tool = next(t for t in m.TOOLS if t.name == "parse_hashtags")
        result = tool.handler({"query": "#rule payments"})
        self.assertEqual(result["query"], "payments")
        self.assertEqual(result["markers"], {"type": "rule"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_mcp_curator -v`
Expected: failures — tools not registered.

- [ ] **Step 3: Add imports, handlers, and ToolSpec entries**

In `scripts/context_graph_mcp.py`:

Near the top imports block, add:

```python
from curator_bootstrap import (
    bootstrap_project_skeleton,
    is_bootstrap_needed,
    mark_bootstrap_declined,
    record_bootstrap_result,
)
from hashtag_parser import parse_hashtags as _parse_hashtags
```

After the existing handlers but before the `TOOLS: list[ToolSpec] = [` declaration, add:

```python
def handle_bootstrap_preview(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_root = arguments.get("workspaceRoot")
    if not workspace_root:
        raise ValueError("Missing required field: workspaceRoot")
    preview = bootstrap_project_skeleton(workspace_root)
    return {
        **preview,
        "bootstrapNeeded": is_bootstrap_needed(workspace_root),
    }


def handle_apply_bootstrap_decision(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_root = arguments.get("workspaceRoot")
    decision = arguments.get("decision")
    if not workspace_root:
        raise ValueError("Missing required field: workspaceRoot")
    if decision not in {"accept", "decline"}:
        raise ValueError("decision must be 'accept' or 'decline'")
    if decision == "decline":
        mark_bootstrap_declined(workspace_root)
        return {"recorded": True, "decision": "decline"}
    root_page_id = arguments.get("rootPageId")
    if not root_page_id:
        raise ValueError("Missing required field: rootPageId (required when decision=accept)")
    record_bootstrap_result(
        workspace_root,
        root_page_id=str(root_page_id),
        root_page_url=arguments.get("rootPageUrl"),
        dir_page_ids=arguments.get("dirPageIds") or {},
    )
    return {"recorded": True, "decision": "accept", "rootPageId": str(root_page_id)}


def handle_parse_hashtags(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "")
    from context_graph_core import load_schema
    schema = load_schema()
    new_query, markers = _parse_hashtags(query, schema)
    return {"query": new_query, "markers": markers}
```

In the `TOOLS: list[ToolSpec] = [...]` block, append three new `ToolSpec` entries (place them right before the closing `]`):

```python
    ToolSpec(
        name="bootstrap_preview",
        title="Bootstrap Preview",
        description="Sniff the workspace's README, manifests, and top-level dirs to produce a skeleton preview for the curator bootstrap flow. Returns bootstrapNeeded so callers know whether to offer the bootstrap.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
            },
            "required": ["workspaceRoot"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "projectTitle": {"type": "string"},
                "tagline": {"type": "string"},
                "topLevelDirs": {"type": "array"},
                "rootPath": {"type": "string"},
                "bootstrapNeeded": {"type": "boolean"},
            },
            "required": ["projectTitle", "topLevelDirs", "bootstrapNeeded"],
        },
        handler=handle_bootstrap_preview,
    ),
    ToolSpec(
        name="apply_bootstrap_decision",
        title="Apply Bootstrap Decision",
        description="Persist the user's bootstrap decision into workspace.json. decision='accept' requires rootPageId (and optionally rootPageUrl + dirPageIds); decision='decline' sets notion.bootstrapDeclined=true so SessionStart stops nagging.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "decision": {"type": "string", "enum": ["accept", "decline"]},
                "rootPageId": {"type": "string"},
                "rootPageUrl": {"type": "string"},
                "dirPageIds": {"type": "object"},
            },
            "required": ["workspaceRoot", "decision"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recorded": {"type": "boolean"},
                "decision": {"type": "string"},
                "rootPageId": {"type": "string"},
            },
            "required": ["recorded", "decision"],
        },
        handler=handle_apply_bootstrap_decision,
    ),
    ToolSpec(
        name="parse_hashtags",
        title="Parse Hashtags",
        description="Translate #word tokens in a query into a markers payload keyed by the schema axis that owns each word. Unknown tags stay in the query verbatim.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "markers": {"type": "object"},
            },
            "required": ["query", "markers"],
        },
        handler=handle_parse_hashtags,
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_mcp_curator -v`
Expected: 7 tests pass.

- [ ] **Step 5: Run the full suite + verify MCP tool count**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: 334 prior + 7 new = 341 pass.

python3 -c "import sys; sys.path.insert(0,'scripts'); import context_graph_mcp as m; print(len(m.TOOLS), 'tools'); print(sorted(t.name for t in m.TOOLS))"
# Expected: 26 tools (was 23 + 3 new).
```

- [ ] **Step 6: Commit**

```bash
git add scripts/context_graph_mcp.py tests/test_mcp_curator.py
git commit -m "Register MCP tools: bootstrap_preview, apply_bootstrap_decision, parse_hashtags"
```

---

## Milestone 5 — Curator skill (markdown-only)

Goal: ship the agent-facing instruction set that turns Claude into a deterministic curator during sessions.

### Task 7: Create `skills/context-graph-curator/SKILL.md`

**Files:**
- Create: `skills/context-graph-curator/SKILL.md`
- Test: `tests/test_curator_skill.py` (new)

- [ ] **Step 1: Write a smoke test that asserts the skill exists and references the signal table**

```python
# tests/test_curator_skill.py
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class CuratorSkillSmokeTests(unittest.TestCase):
    SKILL_PATH = ROOT / "skills" / "context-graph-curator" / "SKILL.md"

    def test_skill_file_exists(self):
        self.assertTrue(self.SKILL_PATH.exists(), f"Missing skill at {self.SKILL_PATH}")

    def test_frontmatter_present(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "Skill must start with YAML frontmatter")
        # Frontmatter ends at the second '---'
        end = text.find("\n---\n", 4)
        self.assertGreater(end, 0, "Frontmatter has no closing delimiter")
        front = text[4:end]
        self.assertIn("name: context-graph-curator", front)
        self.assertIn("description:", front)

    def test_signal_table_present(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        # The signal table must mention each of the seven signal types so
        # Claude has an explicit decision tree.
        for signal in ("Rule", "Gotcha", "Decision", "Module boundary",
                       "Convention", "Task", "Bug fix"):
            self.assertIn(signal, text, f"Signal '{signal}' missing from skill table")

    def test_marker_axes_referenced(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        # Every axis the skill instructs Claude to set must be a real axis
        # in the schema. Spot-check the common ones.
        for axis in ("type", "scope", "domain", "artifact", "status"):
            self.assertIn(axis, text)

    def test_mcp_tool_calls_referenced(self):
        text = self.SKILL_PATH.read_text(encoding="utf-8")
        for tool in ("classify_record", "index_records", "plan_notion_push",
                     "apply_notion_push_result", "record_to_notion_payload"):
            self.assertIn(tool, text, f"Skill must mention {tool}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_skill -v`
Expected: file not found.

- [ ] **Step 3: Create the skill directory and file**

```bash
mkdir -p /Users/maksnalyvaiko/context-graph/skills/context-graph-curator
```

Create `skills/context-graph-curator/SKILL.md`:

```markdown
---
name: context-graph-curator
description: Use proactively in any session against a project that has a Context Graph workspace. The curator captures rules, conventions, gotchas, decisions, intersections, tasks, and bug fixes into the local graph and (when Notion is connected) pushes them as structured pages. Trigger phrases include "we always X", "never Y", "use Z", "this is intentional because", "we picked X because", "X talks to Y", "files live in", "запиши это правило", "сделай ревью", "записал ли ты".
---

# Context Graph — Proactive Curator

This skill teaches you (the assistant) how to turn project knowledge that surfaces during a session into structured records in the Context Graph and — when Notion is connected — Notion pages tagged for later retrieval. It is the active counterpart to the read-side `context-graph-search` skill.

## When to use this skill

Use it whenever the user reveals project knowledge that is worth keeping. The seven recognized signals are listed in the table below. You do not need to ask permission to capture each signal — the user opted in once when the workspace was bootstrapped. You DO need to confirm before pushing to Notion if `workspace.notion.rootPageId` is unset (see "No-Notion" at the end).

You should NOT use this skill to:

- Summarize the conversation at session end (out of scope).
- Capture transient debugging steps that do not represent a stable rule or decision.
- Auto-create records from unrelated content unrelated to the active project.

## The signal vocabulary

When the user (or the work you are doing) matches a row in this table, capture a record with the prescribed marker shape. The mapping is deterministic — do not invent your own marker layout.

| Signal | Trigger phrases / context | Markers |
|---|---|---|
| **Rule** | "always X", "never Y", "we use Z" | `type=rule, scope=convention, domain=<inferred>` |
| **Gotcha** | "this looks wrong but it's intentional because…", "do not refactor this" | `type=rule, scope=gotcha, domain=<inferred>` |
| **Decision** | "we chose X because Y", "we evaluated A vs B and went with B" | `type=decision, domain=<inferred>` |
| **Module boundary** | "X talks to Y through Z", "auth depends on payments via the event bus" | `type=architecture, scope=intersection, domain=<X>, artifact=<Z>` |
| **Convention** | "files live in `<path>`", "naming: `<rule>`", "tests in `tests/`" | `type=rule, scope=convention, artifact=<path or pattern>` |
| **Task** | user asks for a feature/change explicitly | `type=task, status=in-progress, domain=<inferred>` |
| **Bug fix** | a bug was found and fixed during the session | `type=bug, status=fixed, domain=<inferred>, severity=<inferred>` |

`<inferred>` means: pick the single best matching value from `docs/schema.json` for that axis, based on the conversation. If you cannot infer, leave the axis off — `classify_record` will surface `missingRequiredMarkers`.

## The capture protocol (per signal)

Follow this exact sequence:

1. **Build the record dict.** Title is a short noun phrase (e.g. "Always use idempotency keys for webhooks"). Content is 1–4 sentences explaining the rule / decision / gotcha and the why. Markers come from the table above.
2. **Call `mcp__context-graph__classify_record`** with `{"record": {...}}` to normalize markers, infer missing values, and compute the hierarchy path.
3. **Inspect `missingRequiredMarkers`.** If a required marker is missing AND the user's intent is clear, fill it from context. If still missing, ask the user one short question (e.g. "Domain: payments or auth?") rather than dropping the record.
4. **Call `mcp__context-graph__index_records`** with `{"records": [normalized_record]}` to upsert into the local graph and rebuild affected edges.
5. **If Notion is connected** (`workspace.notion.rootPageId` exists in `workspace.json`), push:
   a. Call `mcp__context-graph__plan_notion_push` with `{"recordIds": [record.id]}` to confirm it would be a create vs update.
   b. Call `mcp__context-graph__record_to_notion_payload` to get the title/blocks/parent for the page.
   c. Call the Notion MCP tool `notion-create-pages` (or `notion-update-page`) with the payload. The parent page is `workspace.notion.dirPageIds[<best matching dir>]` if the record's `artifact` matches a dir prefix; otherwise `workspace.notion.rootPageId`.
   d. Call `mcp__context-graph__apply_notion_push_result` with the resulting Notion page id.
6. **Acknowledge briefly.** A one-line confirmation back to the user (e.g. "Captured rule: idempotency keys for webhooks (`#rule #payments`)") — do not over-explain.

## Review request

When the user asks "сделай ревью", "review this", "проверь это", or similar:

1. Determine the scope (file path, module, or topic mentioned).
2. Call `mcp__context-graph__search_graph` with `intentMode="architecture"` and a query targeting the scope. Pull all `type=rule`, `type=decision`, `type=convention` records that apply.
3. Apply them to the review explicitly — cite which rule each finding comes from. If you find an issue not covered by an existing rule, capture it as a NEW rule per the protocol above.
4. After the review, if any new rules were captured, push them to Notion (step 5 of the capture protocol).

## Bootstrap awareness

If the SessionStart prime indicates that the workspace is not bootstrapped to Notion (`workspace.notion.rootPageId` is missing AND `workspace.notion.bootstrapDeclined` is not true), offer the bootstrap once at the start of your first substantive turn. Use `mcp__context-graph__bootstrap_preview` to fetch the preview, present it to the user, and either:

- Run `mcp__context-graph__apply_bootstrap_decision` with `decision="accept"` plus the Notion page IDs returned by `notion-create-pages`, OR
- Run `mcp__context-graph__apply_bootstrap_decision` with `decision="decline"` if the user opts out.

After either decision, do not ask again in the same workspace.

## No-Notion

If Notion is not connected (no `rootPageId`) and the user has not declined bootstrap, surface this once per session with:

> "Notion is not connected for this workspace. Run `/cg-sync-notion` once (OAuth, no API key) to enable proactive note management. Or run `/cg-init --offline` (or decline the bootstrap prompt) to keep notes only in the local graph."

If declined, continue capturing locally — every step of the capture protocol works against the local graph alone. Skip step 5 (Notion push).

## Failure modes

- **`classify_record` returns errors.** Show the error to the user and stop — do not silently skip.
- **`index_records` succeeds but `plan_notion_push` shows no creates and no updates.** That means the record's `markers.type` is not in the pushable set (`rule` / `decision` is the default). Either adjust the type or skip the push.
- **Notion MCP returns an error.** Report it. The local record is already saved, so no data is lost; the user can re-run a manual `/cg-sync-notion push` later.

## What NOT to do

- Do not capture every conversational exchange as a record — only the seven signals from the table.
- Do not invent marker values outside `docs/schema.json`.
- Do not push to Notion before the bootstrap decision has been made.
- Do not write conversation summaries or "session logs" — that is explicitly out of scope (spec §9).
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_skill -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add skills/context-graph-curator/SKILL.md tests/test_curator_skill.py
git commit -m "Add context-graph-curator skill with deterministic signal vocabulary"
```

---

## Milestone 6 — Session priming + hook wiring

Goal: enhance the SessionStart hook to inject project rules and conventions into the prime context.

### Task 8: `session_start_prime.py` — enhanced prime script

**Files:**
- Create: `scripts/session_start_prime.py`
- Test: `tests/test_session_start_prime.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_start_prime.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_session_start_prime -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the prime script**

Create `scripts/session_start_prime.py`:

```python
"""SessionStart hook entry point.

Enhances the previous tiny ``search_graph`` warmup with:
- Pulling rules/decisions/conventions from the local graph for the
  current scope (so Claude starts the session with the rule book in
  context).
- Reporting whether the workspace still needs Notion bootstrap.

The hook prints a small JSON payload to stdout. Claude Code's
SessionStart machinery surfaces hook stdout as system context for the
next turn.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Resolve ``scripts/`` on sys.path when invoked outside the plugin context.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def prime_session(workspace_root: Path | None = None) -> dict[str, Any]:
    """Return a prime payload for the current session.

    When ``workspace_root`` is None, walks up from CWD. When no workspace
    is found, returns ``{"workspace": None}`` and stops.
    """
    from context_graph_core import (
        find_workspace_root,
        load_workspace_manifest,
        default_graph_path,
        search_graph,
    )
    from curator_bootstrap import is_bootstrap_needed

    start = Path(workspace_root) if workspace_root else None
    root = find_workspace_root(start)
    if root is None:
        return {"workspace": None}

    try:
        manifest = load_workspace_manifest(root)
    except FileNotFoundError:
        return {"workspace": None}

    # Pull all type=rule, type=decision, type=convention records the local
    # graph has — these become the rule book Claude sees at session start.
    rules: list[dict[str, Any]] = []
    try:
        for marker_type in ("rule", "decision"):
            res = search_graph({
                "graphPath": str(default_graph_path(root)),
                "query": "",
                "markers": {"type": marker_type},
                "limit": 25,
            })
            for hit in res.get("directMatches", []) or []:
                rules.append({
                    "id": hit.get("id"),
                    "title": hit.get("title"),
                    "markers": hit.get("markers"),
                })
    except Exception:
        # Don't fail the hook on retrieval errors — a broken prime is
        # better than a broken session.
        rules = []

    return {
        "workspace": manifest.get("id"),
        "rootPath": str(root),
        "rules": rules,
        "bootstrapNeeded": is_bootstrap_needed(root),
        "notionConnected": bool((manifest.get("notion") or {}).get("rootPageId")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="session-start-prime")
    parser.add_argument("--workspace-root", dest="workspace_root", default=None)
    args = parser.parse_args(argv)

    start = Path(args.workspace_root) if args.workspace_root else None
    payload = prime_session(workspace_root=start)
    json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_session_start_prime -v`
Expected: 5 tests pass.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
Expected: 341 + 5 = 346 pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/session_start_prime.py tests/test_session_start_prime.py
git commit -m "Add session_start_prime: rule book + bootstrap hint payload"
```

---

### Task 9: Update `hooks.json` to call the new prime script

**Files:**
- Modify: `hooks.json`

- [ ] **Step 1: Read current `hooks.json` to confirm shape**

The current SessionStart hook runs a one-line `search-graph` warmup. We replace its `command` to call the new prime script.

- [ ] **Step 2: Replace the SessionStart command**

In `hooks.json`, locate:

```json
{
  "type": "command",
  "command": "echo '{\"query\":\"\",\"limit\":1}' | python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/context_graph_cli.py\" search-graph > /dev/null 2>&1 || true",
  "timeout": 5
}
```

Replace with:

```json
{
  "type": "command",
  "command": "python3 \"${CLAUDE_PLUGIN_ROOT:-.}/scripts/session_start_prime.py\" 2>/dev/null || true",
  "timeout": 8
}
```

The timeout is bumped from 5s to 8s because `prime_session` now does two `search_graph` calls instead of one. The `|| true` keeps a hook failure from breaking the session.

- [ ] **Step 3: Smoke-test the hook command**

```bash
cd /Users/maksnalyvaiko/context-graph
python3 scripts/session_start_prime.py
# Expected: a JSON object with "workspace", "rootPath", "rules", "bootstrapNeeded", "notionConnected".
# In the plugin's own working tree the workspace may not be initialized — output {"workspace": null} is fine.
```

- [ ] **Step 4: Commit**

```bash
git add hooks.json
git commit -m "Wire SessionStart hook to session_start_prime"
```

---

## Milestone 7 — Slash commands + CLI subcommand

Goal: add `/cg-bootstrap` for users to manually re-trigger or skip the bootstrap, and a CLI `bootstrap` subcommand for headless invocation.

### Task 10: `commands/cg-bootstrap.md`

**Files:**
- Create: `commands/cg-bootstrap.md`

- [ ] **Step 1: Create the slash command file**

Create `commands/cg-bootstrap.md`:

```markdown
---
description: Create a Notion skeleton (root + per-dir pages) for the current Context Graph workspace.
argument-hint: [--decline]  (no args = run the preview + create flow; --decline = mark workspace bootstrapDeclined and skip)
---

The user wants to bootstrap (or skip bootstrapping) Notion pages for this Context Graph workspace.

## Decline path

If `$ARGUMENTS` contains `--decline`:

1. Confirm there is a workspace: walk up from cwd looking for `.context-graph/workspace.json`. If none, say so and stop — bootstrap is not the right action.
2. Call `mcp__context-graph__apply_bootstrap_decision` with `{workspaceRoot: <root>, decision: "decline"}`.
3. Tell the user: "Bootstrap skipped. The curator will keep notes locally only. Run `/cg-sync-notion` later to enable Notion sync."

## Bootstrap path (default)

1. Confirm there is a workspace.
2. Call `mcp__context-graph__bootstrap_preview` with `{workspaceRoot: <root>}`. The result has `projectTitle`, `tagline`, `topLevelDirs` (a list of `{path, purpose}`), and `bootstrapNeeded`.
3. If `bootstrapNeeded` is false, tell the user the workspace is already bootstrapped (or has been declined). Stop.
4. Show the preview to the user. Ask: "Создать в Notion родительскую страницу `<projectTitle>` и подстраницы для `<list of dirs>`? (y/n)"
5. If the user says no, call `apply_bootstrap_decision` with `decision: "decline"` and stop.
6. If the user says yes:
   a. Call the Notion MCP `notion-create-pages` to create the parent page (title = `projectTitle`, body = `tagline` or empty).
   b. Capture the resulting page id (`rootPageId`) and page url (`rootPageUrl`).
   c. For each dir in `topLevelDirs`, call `notion-create-pages` with parent = `rootPageId` and title = `<path> — <purpose>` (purpose may be empty; that's fine). Collect the results into `dirPageIds: {path: pageId}`.
   d. Call `mcp__context-graph__apply_bootstrap_decision` with `{workspaceRoot, decision: "accept", rootPageId, rootPageUrl, dirPageIds}`.
   e. Confirm to the user with the new root page URL.

## Failure modes

- Notion MCP not connected: tell the user "Run `/cg-sync-notion` once first to authorize Notion, then re-run `/cg-bootstrap`." Do NOT call `apply_bootstrap_decision`.
- Notion API returns an error mid-bootstrap: stop, report what was created so far, and instruct the user to retry. Do not record a partial result.
```

- [ ] **Step 2: Commit**

```bash
git add commands/cg-bootstrap.md
git commit -m "Add /cg-bootstrap slash command"
```

---

### Task 11: CLI `bootstrap` subcommand (preview-only / headless)

**Files:**
- Modify: `scripts/context_graph_cli.py` (add `bootstrap` early-dispatch alongside `eval`/`push-notion`/`graph-diff`/`inspect-record`)
- Test: extend `tests/test_curator_bootstrap.py`

- [ ] **Step 1: Append failing test**

```python
# Append to tests/test_curator_bootstrap.py
class CLIBootstrapTests(unittest.TestCase):
    def test_dry_run_prints_preview(self):
        import io, sys, tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from context_graph_core import init_workspace
        # Re-import context_graph_cli main fresh:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from context_graph_cli import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            init_workspace({"rootPath": tmp})
            (Path(tmp) / "README.md").write_text("# Sample\n")
            (Path(tmp) / "src").mkdir()
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main(["bootstrap", "--dry-run", "--workspace-root", tmp])
            self.assertEqual(code, 0)
            output = json.loads(buf.getvalue())
            self.assertEqual(output["projectTitle"], "Sample")
            paths = [d["path"] for d in output["topLevelDirs"]]
            self.assertIn("src/", paths)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_bootstrap.CLIBootstrapTests -v`
Expected: argparse error or unknown command.

- [ ] **Step 3: Add the subcommand to the CLI**

In `scripts/context_graph_cli.py`, add `bootstrap` to the early-dispatch block (the same pattern used by `eval`, `push-notion`, `graph-diff`, `inspect-record`).

After the `if argv_list and argv_list[0] == "inspect-record":` line in `main`, add:

```python
    if argv_list and argv_list[0] == "bootstrap":
        return _run_bootstrap(argv_list[1:])
```

Add the helper function near the other `_run_*` helpers in the same file:

```python
def _run_bootstrap(argv: list[str]) -> int:
    """``context-graph bootstrap`` — preview the project sniff. With
    ``--dry-run`` (default), prints the preview JSON and exits 0. The
    accept/decline path is interactive and runs through the slash
    command (`/cg-bootstrap`); the CLI does not orchestrate Notion API
    calls itself.
    """
    sub_parser = argparse.ArgumentParser(
        prog="context-graph bootstrap",
        description="Show the bootstrap preview for the current workspace.",
    )
    sub_parser.add_argument("--workspace-root", dest="workspace_root", default=None)
    sub_parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    sub_args = sub_parser.parse_args(argv)

    from pathlib import Path
    from curator_bootstrap import bootstrap_project_skeleton, is_bootstrap_needed
    from context_graph_core import find_workspace_root

    start = Path(sub_args.workspace_root) if sub_args.workspace_root else None
    root = find_workspace_root(start)
    if root is None:
        sys.stderr.write("No workspace found. Run /cg-init first.\n")
        return 2

    preview = bootstrap_project_skeleton(root)
    preview["bootstrapNeeded"] = is_bootstrap_needed(root)
    json.dump(preview, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_curator_bootstrap -v`
Expected: 14 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_graph_cli.py tests/test_curator_bootstrap.py
git commit -m "Add `bootstrap` CLI subcommand (preview-only)"
```

---

## Milestone 8 — Schema doc + roadmap + final verification

Goal: document the new `scope` values, refresh the roadmap, run the whole feature once.

### Task 12: Document `scope` values + roadmap update + smoke

**Files:**
- Modify: `docs/schema.json` (add `scope` enum)
- Modify: `docs/roadmap.md` (add Phase 8 entry)
- Modify: `CHANGELOG.md` (extend Unreleased)
- (no code changes)

- [ ] **Step 1: Add `scope` documented values to `docs/schema.json`**

Locate the `markers` block in `docs/schema.json`. If `scope` is absent, add it. If present, extend its values list to include the curator vocabulary. Use this canonical list:

```json
"scope": ["convention", "gotcha", "intersection", "system", "module"]
```

(`system` and `module` may already be in use; preserve any existing values.)

- [ ] **Step 2: Update `docs/roadmap.md`**

Append a new section after Phase 7:

```markdown
## Phase 8 - Proactive curator

Status: in progress

Goal: turn every Claude session into an active note-curating agent. The plugin ships a curator skill that teaches Claude when and how to capture rules, conventions, gotchas, decisions, intersections, tasks, and bug fixes; a light bootstrap creates a Notion skeleton (root + per-dir pages); session priming injects the rule book at session start; hashtag UX surfaces accumulated knowledge by `#tag`.

- [x] `bootstrap_project_skeleton` (README + manifest + dir tree, light) — `scripts/curator_bootstrap.py`
- [x] Workspace manifest helpers + bootstrap state (`is_bootstrap_needed`, `mark_bootstrap_declined`, `record_bootstrap_result`)
- [x] Hashtag parser (`#word` → axis-resolved markers payload) — `scripts/hashtag_parser.py`
- [x] MCP tools: `bootstrap_preview`, `apply_bootstrap_decision`, `parse_hashtags`
- [x] Curator skill — `skills/context-graph-curator/SKILL.md` with deterministic 7-signal vocabulary
- [x] Session priming script — `scripts/session_start_prime.py` injects rule book + bootstrap hint
- [x] `/cg-bootstrap` slash command + CLI `bootstrap` subcommand
- [x] Documented `scope` values: `convention`, `gotcha`, `intersection`
- [ ] Live smoke test: install plugin, run a session, verify curator captures a rule end-to-end (user-driven)

Acceptance: in a session against a freshly-bootstrapped workspace, the user says "we always use Idempotency-Key for webhooks" and the curator skill captures it as `type=rule, scope=convention, domain=payments`, indexes it locally, and pushes a Notion page under the conventions sub-page.
```

- [ ] **Step 3: Update `CHANGELOG.md`**

In the `## [Unreleased]` `### Added` section, add:

```markdown
- Proactive curator workflow (Phase 8): light project bootstrap (README + manifests + dir tree); curator skill that teaches Claude a deterministic 7-signal vocabulary (Rule / Gotcha / Decision / Module boundary / Convention / Task / Bug fix); hashtag UX (`/cg-search #rule #payments` → marker filter); smart session priming injects rules/decisions into the prime context; `/cg-bootstrap` slash command + CLI subcommand. New MCP tools: `bootstrap_preview`, `apply_bootstrap_decision`, `parse_hashtags`. See `docs/superpowers/specs/2026-04-24-proactive-curator-design.md`.
```

- [ ] **Step 4: Final verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'
# Expected: 323 prior + ~30 new = ~353 tests, all green.

python3 scripts/context_graph_cli.py eval
# Expected: exit 0, no regression (curator does not change retrieval scoring).

python3 -c "import sys; sys.path.insert(0,'scripts'); import context_graph_mcp as m; print(len(m.TOOLS), 'tools')"
# Expected: 26.

python3 scripts/session_start_prime.py
# Expected: a JSON payload (likely workspace=null when run in plugin tree).

ls skills/context-graph-curator/SKILL.md commands/cg-bootstrap.md scripts/curator_bootstrap.py scripts/hashtag_parser.py scripts/session_start_prime.py
# Expected: all five files present.
```

- [ ] **Step 5: Commit**

```bash
git add docs/schema.json docs/roadmap.md CHANGELOG.md
git commit -m "Phase 8 curator: schema scope values, roadmap entry, CHANGELOG"
```

---

## Self-review checklist

**Spec coverage:**

- [x] §3 Layer 1 (Bootstrap) — Tasks 2, 3, 6, 10, 11
- [x] §3 Layer 2 (Curator skill) — Task 7
- [x] §3 Layer 3 (Hashtag UX) — Tasks 4, 5
- [x] §3 Layer 4 (Session priming) — Tasks 8, 9
- [x] §3 Layer 5 (No-Notion guidance) — covered by skill prose (Task 7) and bootstrap message text (Task 10)
- [x] §4 (Signal vocabulary) — Task 7's skill body lists all seven rows
- [x] §5 (Records vs Notion pages) — covered by curator skill protocol (Task 7) which references existing push tooling
- [x] §7.1 (Bootstrap module) — Tasks 2, 3
- [x] §7.2 (Curator skill) — Task 7
- [x] §7.3 (CLI subcommand) — Task 11
- [x] §7.4 (Hooks) — Tasks 8, 9
- [x] §7.5 (Slash commands) — Tasks 5, 10
- [x] §7.6 (Schema vocabulary) — Task 12
- [x] §8 (Testing) — every task ships its own tests; eval baseline untouched
- [x] §9 (Out of scope) — no task implements digest, summarization, GitHub webhooks, etc.
- [x] §10 (Success criteria) — verified by Task 12 final commands + the live smoke (user-driven)

**Placeholder scan:** none. Every task has the actual code, the actual test, the actual command. Task 12 Step 4's live smoke is described concretely (commands + expected outputs).

**Type consistency:**

- `IntentMode` — not touched (sealed).
- Workspace manifest schema: top-level `notion` dict with sub-keys `rootPageId`, `rootPageUrl`, `dirPageIds`, `bootstrapDeclined`, `createdAt`, `updatedAt`. Used consistently across Task 1 (helpers), Task 3 (state helpers), Task 6 (MCP), Task 10 (slash command).
- MCP tool naming: `bootstrap_preview`, `apply_bootstrap_decision`, `parse_hashtags`. Consistent in Task 6 (registration), Task 7 (skill body), Task 10 (slash command), Task 11 (CLI doesn't register them).
- Function naming snake_case: `bootstrap_project_skeleton`, `is_bootstrap_needed`, `mark_bootstrap_declined`, `record_bootstrap_result`, `parse_hashtags`, `prime_session`, `load_workspace_manifest`, `update_workspace_manifest`. Consistent across all tasks.
- camelCase payload fields where they cross the wire (MCP / slash command): `workspaceRoot`, `rootPageId`, `rootPageUrl`, `dirPageIds`, `decision`, `query`, `markers`. Consistent.

**Scope:** this plan produces working software on its own — Tranche-1 ships the bootstrap primitives, Tranche-2 ships the skill, Tranche-3 ships the hashtag UX, Tranche-4 ships priming, Tranche-5 ships slash command + CLI, Tranche-6 ships docs. Each milestone passes tests independently. No dependency on a future PR.

---

## Open questions deferred to implementation

1. **Path → scope derivation for session priming.** Spec §11 Q1 suggested "first child dir under workspace root maps to `domain` or `artifact`". The current `prime_session` implementation pulls type=rule and type=decision regardless of cwd. A future enhancement can scope by cwd; tracking as a follow-up rather than blocking Tranche 4.
2. **Token budget cap (4000) for prime.** Implemented as `limit: 25` per type in `search_graph` calls — a soft cap on count, not on token size. If the average rule body is 200 tokens, 50 rules ≈ 10k tokens, which exceeds the spec's 4000 target. Realistic call-site behavior: keep the cap as count for MVP; revisit if token budget bites.
3. **CLI `bootstrap` apply path.** The CLI is preview-only; the accept/decline orchestration lives in the slash command because it requires the Notion MCP (out of CLI scope). If a headless apply is needed later, add a separate subcommand that takes `--root-page-id` and `--dir-page-ids` directly.
4. **`docs/observability.md` does not get a curator section.** Curator does not change observability. Add documentation only if user feedback says it's needed.
5. **`docs/retrieval.md` does not get a hashtag section.** Hashtag is search-side syntactic sugar; the slash command file documents it. If users ask "where do I learn about hashtags," we add a cross-reference there.
