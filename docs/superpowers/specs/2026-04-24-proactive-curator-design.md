# Proactive Curator Workflow — Design Spec

**Status:** design approved, pending implementation plan.
**Phase:** Phase 8 — proactive note management (new).
**Related:** builds on Phase 4a (Notion live sync, push-back), Phase 5 (intent modes), Phase 3.5 (skills + hooks). Sealed: `scripts/intent_modes.py`, all current schemas.

---

## 1. Problem

The plugin today is fully **user-driven**: the user must run `/cg-init`, `/cg-index`, `/cg-search`, `/cg-sync-notion` explicitly. The plugin's local graph and Notion stay in sync only when the user remembers to push. Project knowledge — rules, conventions, gotchas, module boundaries — is captured manually if at all.

The user's vision: when the plugin is installed in a project, every Claude session participates in **building a structured project knowledge base**. The plugin guides Claude (via skill instructions) on what to capture, how to tag it, and when to sync. The user retrieves accumulated knowledge by tag — `#rule #payments`, `#gotcha #auth`, `#intersection #api #webhook` — instead of re-explaining context every session.

The trigger model is **semantic, not technical.** When the user says "сделай ревью" or "запиши это правило", Claude (guided by the curator skill) recognizes the moment and acts — the plugin does NOT try to detect events from `git push` hooks or test runners.

## 2. Design decisions (brainstormed, locked)

1. **Single Notion opt-in per workspace** — no per-write confirmation. After `notionRootPageId` is set in `workspace.json`, the curator skill is free to create/update Notion pages without nagging.
2. **Digest content** = bug fixes + new modules touched + their logic + intersections with other components. NOT conversation summaries.
3. **Bootstrap is LIGHT** — README + dir tree (depth 2) + manifests, low token budget. Generate skeleton (titles + 1-2 lines per dir), not full content. Real content fills organically over sessions.
4. **Triggers are semantic, surfaced through a curator skill** — the plugin ships an agent-facing skill that teaches Claude when to capture, classify, and sync. NOT git/CI hooks. User's "сделай ревью" becomes a recognized trigger because the skill says so.
5. **No-Notion = clear instruction**, not silent fallback. The plugin surfaces "Run `/cg-sync-notion` to connect Notion (OAuth, no key)" when the workspace has no `notionRootPageId`.
6. **Reuse existing schema** — `type`, `scope`, `artifact` cover rules / conventions / gotchas / intersections without new axes. Schema versioning is unchanged.
7. **Hashtag retrieval syntax** is sugar over existing `markers: {axis: value}` payload — no new MCP tool, just slash-command parsing.

## 3. Architecture — 5 layers

### Layer 1 — Light bootstrap

When a session starts in a directory with `.context-graph/workspace.json` but no `notion.rootPageId`:

1. The SessionStart hook detects the gap and calls a new helper `bootstrap_project_skeleton(workspace_root)`.
2. Helper performs a low-token scan:
   - Read `README.md` (first 200 lines)
   - Read top-level manifests if present (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`)
   - List top-level directories (depth 2, max 30 entries) — exclude `.git`, `node_modules`, `.venv`, `dist`, `build`, etc.
3. Helper builds a **preview** dict:
   ```json
   {
     "projectTitle": "context-graph",
     "tagline": "Plugin for classifying notes and assembling context packs",
     "topLevelDirs": [
       {"path": "scripts/", "purpose": "core, classifiers, MCP server, CLI"},
       {"path": "tests/", "purpose": "unittest fixtures and test files"},
       {"path": "docs/", "purpose": "design docs, schema, retrieval policy"}
     ]
   }
   ```
4. SessionStart prints the preview to the user and asks: "Создать skeleton-страницы в Notion для этого проекта? (y/n)" — single confirmation. If yes:
   - Use `notion-create-pages` (Notion MCP) to create a parent page named after `projectTitle`, then one child per top-level dir
   - Save the resulting `pageId` of the parent into `workspace.json` under `notion.rootPageId`
   - Save per-dir page IDs into a new field `notion.dirPageIds: {dirPath: pageId}`
5. From this point, the curator skill knows where to write each captured record.

If the user declines, the workspace gets `notion.bootstrapDeclined: true` so we don't ask again. The user can run `/cg-bootstrap` manually at any time.

### Layer 2 — Curator skill

A new skill at `skills/cg-curator/SKILL.md`. Loads automatically in every Claude Code session that has the plugin enabled. The skill teaches Claude:

- **What signals to capture** (the table in §4 below)
- **How to write records** (canonical marker layout)
- **When to sync to Notion** (after substantive capture; after explicit "запиши"; after a review session)
- **How to use existing infrastructure** (call `index_records` MCP tool; call `push_notion` for the push)

The skill body is a single markdown file (target: 200-400 lines). It is NOT a slash command — it's an always-loaded instruction set, like `cg-classify` and `cg-search` skills already are.

The skill MUST be deterministic in classification: when a signal matches one of the table rows in §4, the markers are fixed (no creative interpretation). Conflicts are resolved by checking the marker schema's accepted values.

### Layer 3 — Hashtag UX in slash commands

Extend `/cg-search` (and `/cg-classify`, `/cg-index` where it makes sense) to parse `#X` tokens as marker filters.

Parsing rule:
- `#word` → `markers: {<axis>: word}` where `<axis>` is auto-resolved from the word using `docs/schema.json` (the value's owning axis is unique by construction in the current schema).
- If the word is ambiguous (matches multiple axes' values — does not currently happen but defensive coverage), fall back to substring search and warn the user.
- Multiple `#X` tokens AND together: `#rule #payments` → `{type: rule, domain: payments}`.
- Words without `#` are still part of the natural query.

Examples:
```
/cg-search #rule #payments                  → markers: {type: rule, domain: payments}
/cg-search #gotcha #auth                    → markers: {scope: gotcha, domain: auth}
/cg-search #intersection #api #webhook      → markers: {scope: intersection, artifact: api, flow: webhook}
/cg-search how do we handle #idempotency    → markers: {scope: idempotency} + query="how do we handle"
```

The slash command file (`commands/cg-search.md`) gains a "Hashtag syntax" section telling Claude how to parse and where to send the resolved markers.

### Layer 4 — Smart session priming

The existing SessionStart hook primes a small context pack. Enhanced behavior:

1. Determine the **active scope** of the session:
   - Inspect `cwd` relative to workspace root
   - If cwd is `<workspace>/src/payments/`, derive scope `domain=payments` (from path-to-marker convention)
   - If derivation fails, default to project-level scope (no domain filter)
2. Pull from the local graph:
   - All records with `markers.type in {rule, decision, convention}` (rule book)
   - For the derived scope, all records with that domain/artifact (architecture context)
   - Limit total payload to 4000 tokens to avoid blowing the session budget
3. Inject as "Project rules and conventions for `<scope>`:" prefix into the priming message.

The session starts with the rule book in working context — Claude immediately knows "in this codebase webhooks are idempotent by design" without re-asking.

### Layer 5 — No-Notion guidance

When the curator skill detects that `workspace.notion.rootPageId` is missing AND `workspace.notion.bootstrapDeclined` is not set, it emits a one-time message:

> "Notion is not connected for this workspace. Run `/cg-sync-notion` once (OAuth, no API key) to enable proactive note management. Or run `/cg-init --offline` to keep notes only in the local graph."

It MUST NOT silently fall back to local-only — the user explicitly asked for clear guidance, not silent degradation.

## 4. Curator signal vocabulary

The curator skill ships this exact table. Claude consults it on every signal during a session.

| Trigger phrase / context | Markers | Title template | Stored where |
|---|---|---|---|
| "Always do X" / "Never Y" / "We use Z" | `type=rule, scope=convention, domain=<inferred>` | `<X>: <Y or Z>` | rules sub-page |
| "This looks wrong but is intentional because…" | `type=rule, scope=gotcha, domain=<inferred>` | `Gotcha: <subject>` | gotchas sub-page |
| "We chose X because Y" | `type=decision, domain=<inferred>` | `Decision: <X>` | decisions sub-page |
| "X talks to Y through Z" | `type=architecture, scope=intersection, domain=<X>, artifact=<Z>` | `Intersection: <X> ↔ <Y> via <Z>` | architecture sub-page |
| "Files live in `<path>`" / "Naming: `<rule>`" | `type=rule, scope=convention, artifact=<path or pattern>` | `Convention: <path>` | conventions sub-page |
| User asks for a feature/change | `type=task, status=in-progress, domain=<inferred>` | `Task: <verb phrase>` | tasks sub-page |
| Bug found and fixed | `type=bug, status=fixed, domain=<inferred>, severity=<inferred>` | `Bug: <symptom>` | bugs sub-page |
| Module summary (when first touching a new module) | `type=architecture, domain=<module>, artifact=<path>` | `Module: <name>` | architecture sub-page |

Sub-page mapping comes from `workspace.notion.dirPageIds` — if a record's `artifact` matches a known sub-page path prefix, that sub-page is the parent. Otherwise the record goes under the project root.

## 5. Records vs Notion pages — write semantics

- Each captured record creates a **local graph entry** via `index_records` (existing) and a **corresponding Notion page** via `push_notion` (existing). The push is idempotent through `.context-graph/notion_push.json` — no duplicates.
- The first call to `push_notion` for a record creates the page; subsequent calls update body. Update semantics are full body replace (current limitation, documented).
- Captured records are NOT deleted automatically. The user can run `/cg-archive r-id` if a rule becomes obsolete.

## 6. User-visible flow (one paragraph)

> The user installs the plugin and clones it into `~/.claude/plugins/context-graph`. They open Claude Code in a project directory. SessionStart detects no workspace and runs `init-workspace`. SessionStart then sees no `notion.rootPageId` and offers a 3-line preview of the project's structure with a Y/N prompt. The user says yes; SessionStart creates a Notion root page + per-dir sub-pages and stores the IDs. From session 2 onward, every session starts with `Project rules and conventions:` in the prime context. Mid-session, when the user says "не используй eval, всегда `int(x)`", the curator skill recognizes a rule, calls `index_records` with `markers={type: rule, scope: convention, domain: ...}`, and pushes the page to Notion under the conventions sub-page. The user later runs `/cg-search #rule #payments` and gets every payment-related rule the project has accumulated, regardless of which session created it.

## 7. Implementation shape

### 7.1 New module: `scripts/curator_bootstrap.py` (light, stdlib-only)

```python
def bootstrap_project_skeleton(workspace_root: Path) -> dict:
    """Scan README + manifests + dir tree; return a preview dict."""

def apply_bootstrap_to_notion(
    preview: dict, mcp_caller: Callable[[str, dict], dict],
    workspace_root: Path,
) -> dict:
    """Create the Notion pages via the official MCP tools and persist
    rootPageId + dirPageIds into workspace.json."""

def is_bootstrap_needed(workspace_root: Path) -> bool: ...
```

### 7.2 New skill: `skills/cg-curator/SKILL.md`

A static markdown file. Body sections:
- Purpose (one paragraph)
- The signal vocabulary table (§4 verbatim)
- Write protocol (which MCP tool to call, with example payloads)
- Push protocol (when to call `plan_notion_push` + `push_notion`)
- Failure modes (what to do if Notion is not connected — surface the no-Notion message and continue local-only)

### 7.3 New CLI subcommand: `bootstrap`

`scripts/context_graph_cli.py` gains a `bootstrap` subcommand that runs the same logic as the SessionStart hook, for users who want to re-trigger or debug the flow:

```bash
python3 scripts/context_graph_cli.py bootstrap [--workspace-root <path>] [--dry-run]
```

`--dry-run` returns the preview without creating Notion pages.

### 7.4 Hooks

`hooks.json` already has SessionStart. Update its target script (e.g. `scripts/session_start_prime.py` if it exists, or create one) to:

1. Run existing context-pack priming
2. Check `is_bootstrap_needed(workspace_root)` → if yes and not declined, emit preview + Y/N prompt
3. Pull rule book per scope as described in §3 Layer 4 — inject into prime payload

The Stop hook is NOT used. There is no auto-digest. Capture is conversational.

### 7.5 Slash command updates

- `commands/cg-search.md` gains a "Hashtag syntax" section.
- `commands/cg-init.md` gains a `--offline` flag note (skip Notion bootstrap).
- New: `commands/cg-bootstrap.md` for manual re-trigger.

### 7.6 Schema vocabulary additions (open enums, no migration needed)

`docs/schema.json` `scope` enum list (currently open) gains documented values:
- `convention`
- `gotcha`
- `intersection`

These are advisory — the schema is not strict on `scope`, but documenting them tightens the curator skill's deterministic mapping.

## 8. Testing strategy

### 8.1 Unit
- `tests/test_curator_bootstrap.py`:
  - `bootstrap_project_skeleton` returns the expected dict shape from a fixture project
  - Manifests are parsed when present, gracefully ignored when absent
  - Dir-tree exclusions (`.git`, `node_modules`, etc.) are honored
- `tests/test_hashtag_parsing.py`:
  - `#word` tokens resolve to correct axis using `docs/schema.json`
  - Multiple `#word` tokens AND together
  - Mixed `#word` + free text query
  - Unknown tag falls back to substring search and warns

### 8.2 Integration
- `tests/test_session_priming.py`:
  - When workspace has rules in graph, priming includes them in payload
  - cwd → scope derivation works for known module paths
  - Token budget cap (4000) is respected
- `tests/test_curator_skill_smoke.py`:
  - Skill file exists, parses as valid markdown, contains the signal table
  - All marker values used in the table are valid against `docs/schema.json`

### 8.3 Acceptance
- End-to-end: a fresh fixture project, run `bootstrap --dry-run`, assert preview shape; then a fake `mcp_caller` that records all `notion-create-pages` calls — assert root + per-dir pages would be created with correct titles and parent IDs.
- Session priming: seed graph with 5 rules across 2 domains, simulate session in cwd matching one domain, assert prime payload contains rules from that domain only (other domain filtered).

## 9. Out of scope (explicit)

- **Git-event-driven digest.** No hooks on `git push`, `pytest`, etc. All capture is conversational.
- **Auto-detection of "project type"** beyond reading manifests. We do not run `tree-sitter` or scan source code semantically.
- **Conversation summarization at session end.** No "auto-write a session log".
- **GitHub PR review webhooks.** Reviews are recognized via the curator skill responding to user requests (e.g. "сделай ревью"); no CI integration.
- **Auto-archival of old rules.** Manual via `/cg-archive`.
- **Cross-project rule sharing.** Each workspace has its own Notion root; rules are not cross-imported.
- **Bootstrap deep code analysis.** README + manifests + dir list only — no AST parsing, no LLM-driven module summaries during bootstrap (those grow organically through sessions).
- **Schema strictness changes.** `scope` stays open-enum; documented values are advisory.

## 10. Success criteria

1. Fresh install + Notion connection: SessionStart bootstrap creates a project root page + at least one sub-page, persists IDs, and confirms with the user before any write.
2. Curator skill loaded: when user says "we never use float for currency", Claude calls `index_records` with the correct marker shape (verified by checking the local graph after a scripted simulation).
3. `/cg-search #rule` returns all `type=rule` records; `/cg-search #rule #payments` filters further. Empty result for a non-existent tag is handled gracefully.
4. Session priming: opening a session in `<workspace>/src/payments/` injects payments-domain rules into the prime context.
5. No-Notion message: workspace with no `rootPageId` and not declined produces the documented prompt; declined workspace stays silent.
6. All 323 prior tests still pass; new tests (~25) added.

## 11. Open questions deferred to implementation

1. **Path → scope derivation.** A simple "first child dir under workspace root maps to `domain` or `artifact`" rule may need refinement based on real layouts. Default for MVP: take the immediate child of workspace root that contains the cwd, look it up in `notion.dirPageIds` — if matched, the parent's title is the scope value. Fallback: project-level (no scope filter).
2. **Token budget cap (4000) for prime.** Could be tuned per workspace. MVP: hardcode; expose via `workspace.json` field if it bites.
3. **Hashtag axis disambiguation.** Currently no value collisions in the schema. Defensive code (warn on ambiguous tag) is implemented but unused. Revisit if schema grows.
4. **Bootstrap when README is missing.** Fall back to dir-list-only preview. Document.
5. **Curator skill — granularity threshold.** When the user says "small thing", does the skill capture it? Default: capture anything matching the table; user can `/cg-archive` later. Revisit if signal-to-noise becomes a problem.

---

**Ready for implementation plan.** Next step: invoke the `writing-plans` skill to break this into bite-sized TDD tasks across the 4 deliverables (bootstrap module, curator skill, hashtag parsing, session priming enhancement).
