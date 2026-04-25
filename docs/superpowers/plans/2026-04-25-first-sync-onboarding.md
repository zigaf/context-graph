# First Sync Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/cg-start`, a single hybrid onboarding command that initializes a workspace and guides the first Notion or markdown sync.

**Architecture:** Implement the first version as slash-command orchestration over existing MCP tools. Do not add `workspace_status` yet; the command can discover workspace state by checking `.context-graph/workspace.json` and then reuse `init_workspace`, Notion cursor tools, classification, indexing, and markdown ingestion. Add smoke coverage that keeps the command discoverable and documents the required branches.

**Tech Stack:** Markdown slash commands, Python 3 stdlib `unittest`, existing Context Graph MCP tools, official Notion MCP/OAuth tools when available.

---

## File Structure

- Create: `commands/cg-start.md`
  - Owns the user-facing first-run wizard.
  - Orchestrates workspace initialization, Notion first sync, markdown ingest, and skip path.
- Create: `tests/test_start_command.py`
  - Smoke-tests command existence, frontmatter, and key flow instructions.
- Modify: `README.md`
  - Adds `/cg-start` as the recommended first command.
- Modify: `docs/roadmap.md`
  - Tracks first-sync onboarding under the Claude/Codex integration layer.

No Python runtime helper is needed in this first pass.

---

### Task 1: Add Smoke Tests for `/cg-start`

**Files:**
- Create: `tests/test_start_command.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_start_command.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StartCommandSmokeTests(unittest.TestCase):
    COMMAND_PATH = ROOT / "commands" / "cg-start.md"

    def test_command_file_exists(self):
        self.assertTrue(self.COMMAND_PATH.exists(), f"Missing command at {self.COMMAND_PATH}")

    def test_frontmatter_declares_command(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "Command must start with YAML frontmatter")
        end = text.find("\n---\n", 4)
        self.assertGreater(end, 0, "Frontmatter has no closing delimiter")
        front = text[4:end]
        self.assertIn("description:", front)
        self.assertIn("argument-hint:", front)

    def test_hybrid_sources_are_documented(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "Notion",
            "Local markdown",
            "Skip",
            "init_workspace",
            "ingest_markdown",
            "load_notion_cursor",
            "filter_pages_by_cursor",
            "save_notion_cursor",
        ):
            self.assertIn(phrase, text)

    def test_user_facing_summary_is_required(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        for phrase in (
            "Context Graph is ready",
            "pages pulled",
            "pages skipped",
            "files processed",
            "/cg-search",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run:

```bash
python3 -B -m unittest tests.test_start_command
```

Expected: failure because `commands/cg-start.md` does not exist.

- [ ] **Step 3: Commit the failing test**

Run:

```bash
git add tests/test_start_command.py
git commit -m "test: cover cg-start command contract"
```

---

### Task 2: Add `/cg-start` Slash Command

**Files:**
- Create: `commands/cg-start.md`
- Test: `tests/test_start_command.py`

- [ ] **Step 1: Create the command file**

Create `commands/cg-start.md`:

```markdown
---
description: Guided first setup for Context Graph, including workspace init and first Notion or markdown sync
argument-hint: [notion|markdown|skip] [scope-or-path]
---

The user wants the simplest path from a fresh project to an indexed Context
Graph. Run this as a one-step-at-a-time onboarding wizard. Hide implementation
details unless an error requires them.

## Principles

- Prefer one question at a time.
- Do not mention `graphPath`, `cursor`, `index_records`, or `MCP` in normal
  user-facing text.
- Existing advanced commands remain available, but do not ask the user to run
  them during the happy path.
- Never ask for a Notion API key. The Notion path uses the official live Notion
  OAuth tools available in the session.
- Do not push promoted records back to Notion from this command.

## Step 1: Workspace

1. Determine the candidate root:
   - If `$ARGUMENTS` includes an absolute or relative path after a source mode,
     use that only for markdown source selection, not as the workspace root.
   - Otherwise use the current working directory as the workspace root.
2. Walk upward from the current directory looking for
   `.context-graph/workspace.json`.
3. If a workspace exists, keep using it and continue to source selection.
4. If no workspace exists, ask:
   `Create a Context Graph workspace for <cwd>? [y/N/<other path>]`
   - `y`: call `mcp__context-graph__init_workspace` with
     `{"rootPath": "<cwd>"}`.
   - `<other path>`: resolve it to an absolute path and call
     `mcp__context-graph__init_workspace` with that root.
   - `N`: stop and say `Setup canceled.`
5. If `init_workspace` reports that the workspace already exists, continue
   with that existing workspace instead of treating it as fatal.

## Step 2: Source Selection

Parse `$ARGUMENTS` for an optional first token:

- `notion`: choose Notion without asking.
- `markdown`: choose Local markdown without asking.
- `skip`: initialize only and skip first sync.

If no source token is present, ask:

`What do you want to sync first? [n] Notion / [m] Local markdown / [s] Skip`

Interpret answers:

- `n`, `notion`, or `Notion` -> Notion path.
- `m`, `markdown`, `local`, or `Local markdown` -> markdown path.
- `s`, `skip`, or `Skip` -> skip path.
- Anything else -> ask once more with the same choices.

## Step 3A: Notion First Sync

Use this path when the user chose Notion.

1. Determine the search scope:
   - If `$ARGUMENTS` has text after `notion`, use that as the scope.
   - Otherwise ask:
     `Which Notion page, database, or keyword should I sync first?`
   - If the user gives an empty scope, stop and ask them to rerun `/cg-start notion <scope>`.
2. Load the stored page freshness state by calling
   `mcp__context-graph__load_notion_cursor` with `{}`.
3. Search Notion with the available official Notion search tool:
   - Query: the user's scope.
   - Query type: internal.
   - Page size: 10.
   - Filters: `{}`.
4. If no Notion search tool is available, tell the user:
   `Notion is not connected in this session. Connect the official Notion OAuth integration, then rerun /cg-start notion <scope>.`
   Stop without changing the graph.
5. If search returns no pages, report that no matching Notion pages were found
   and ask the user to rerun with a narrower or different scope.
6. If search returns more than 50 pages, summarize the count and ask:
   `This will inspect <N> Notion pages. Continue? [y/N]`
   Stop unless the user confirms.
7. Build page stubs from search results:
   `{"id": "<page-id>", "last_edited_time": "<timestamp>"}`
8. Call `mcp__context-graph__filter_pages_by_cursor` with:
   `{"pages": <stubs>, "cursor": <loaded cursor>}`.
9. If `fresh` is empty, report:
   `Context Graph is ready. No Notion changes found for <scope>. Try: /cg-search <scope>`
   Stop.
10. For each fresh page:
    - Fetch it with the available official Notion fetch tool.
    - Build a draft record with:
      - `id`: `notion:<32-hex page id>`
      - `title`: page title
      - `content`: markdown body from the fetched page
      - `source.system`: `notion`
      - `source.url`: Notion URL
      - `source.metadata.notionPageId`: raw page id
      - `source.metadata.last_edited_time`: search timestamp
      - `source.metadata.parent`: ancestor titles joined by ` > ` when available
    - Call `mcp__context-graph__classify_record` with `{"record": <draft>}`.
    - If the classifier asks for pending arbitration, choose marker values from
      the allowed values using the current session context. Do not invent
      values.
11. Call `mcp__context-graph__index_records` once with all finalized records.
12. Advance the loaded cursor for each successfully indexed fresh page and call
    `mcp__context-graph__save_notion_cursor` with `{"cursor": <advanced cursor>}`.
13. Report:
    `Context Graph is ready. Source: Notion. <N> pages pulled, <M> pages skipped, <R> records indexed. Try: /cg-search <scope>`

## Step 3B: Local Markdown First Sync

Use this path when the user chose Local markdown.

1. Determine the notes path:
   - If `$ARGUMENTS` has text after `markdown`, use that as the path.
   - Otherwise ask:
     `Which folder of markdown notes should I index?`
2. Resolve the path to an absolute path.
3. Call `mcp__context-graph__ingest_markdown` with:
   ```json
   {
     "rootPath": "<absolute notes path>",
     "recursive": true,
     "index": true
   }
   ```
4. If `fileCount` is zero, report:
   `No markdown files found under <path>. Check the folder and rerun /cg-start markdown <path>.`
5. Otherwise report:
   `Context Graph is ready. Source: Local markdown. <N> files processed, <R> records indexed. Try: /cg-search <folder name or topic>`

## Step 3C: Skip

Use this path when the user chose Skip.

Report:

`Context Graph workspace is ready. First sync skipped. Later, run /cg-start notion <scope> or /cg-start markdown <path>.`

## Completion Summary Requirements

Every successful path must include the phrase `Context Graph is ready`.

Notion summaries must include:

- `pages pulled`
- `pages skipped`
- indexed record count
- one `/cg-search` example

Markdown summaries must include:

- `files processed`
- indexed record count
- one `/cg-search` example
```

- [ ] **Step 2: Run the targeted test**

Run:

```bash
python3 -B -m unittest tests.test_start_command
```

Expected: all tests pass.

- [ ] **Step 3: Commit the command**

Run:

```bash
git add commands/cg-start.md tests/test_start_command.py
git commit -m "Add cg-start onboarding command"
```

---

### Task 3: Document `/cg-start` as the Recommended Entry Point

**Files:**
- Modify: `README.md`
- Modify: `docs/roadmap.md`

- [ ] **Step 1: Update README**

In `README.md`, after the workspace layout section and before "Implemented MVP commands", insert:

```markdown
## First setup

For a new project, start with:

```bash
/cg-start
```

The command creates the local workspace if needed, asks whether your first
source is Notion or a local markdown folder, runs the first sync, and finishes
with a suggested `/cg-search` query. The lower-level commands (`/cg-init`,
`/cg-sync-notion`, `/cg-index`) remain available for manual workflows.
```

- [ ] **Step 2: Update roadmap**

In `docs/roadmap.md`, under "Phase 3.5 - Claude Code integration layer", add a checked item after the existing slash command bullets:

```markdown
- [x] Add `/cg-start` as a hybrid first-run wizard that initializes the workspace and guides the first Notion or local markdown sync.
```

- [ ] **Step 3: Run a docs grep sanity check**

Run:

```bash
rg -n "/cg-start|First setup|hybrid first-run" README.md docs/roadmap.md commands/cg-start.md
```

Expected: matches in all three files.

- [ ] **Step 4: Commit docs**

Run:

```bash
git add README.md docs/roadmap.md
git commit -m "Document cg-start first setup flow"
```

---

### Task 4: Final Verification

**Files:**
- No file changes expected.

- [ ] **Step 1: Run targeted test**

Run:

```bash
python3 -B -m unittest tests.test_start_command
```

Expected: pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass.

- [ ] **Step 3: Run retrieval eval**

Run:

```bash
python3 scripts/context_graph_cli.py eval
```

Expected: no precision regression against `data/eval/baseline.json`.

- [ ] **Step 4: Check git status**

Run:

```bash
git status --short
```

Expected: clean worktree.

---

## Self-Review

**Spec coverage:** The plan implements the approved hybrid `/cg-start` path, supports Notion, markdown, and skip, keeps existing commands as advanced tools, avoids new token auth, and preserves idempotent repeat behavior through existing cursor and ingest flows.

**Placeholder scan:** No placeholder markers. Each task includes concrete files, command content, tests, commands, and expected results.

**Type consistency:** Tool names match the existing MCP surface: `init_workspace`, `load_notion_cursor`, `filter_pages_by_cursor`, `classify_record`, `index_records`, `save_notion_cursor`, and `ingest_markdown`. User-facing slash command name is consistently `/cg-start`.
