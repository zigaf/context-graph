# Auto Notion Push Design

Date: 2026-04-26

## Problem

The current Context Graph workflow expects the user to write Notion pages by
hand and run `/cg-sync-notion push` explicitly when they want local rules,
decisions, and other curated signals to land in Notion. In practice this means:

- `/cg-bootstrap` produces empty stub pages — the user still has to fill them.
- Curator captures (rules, gotchas, decisions, etc.) accumulate in the local
  graph and never reach Notion unless the user remembers to promote and push.
- When the user does push, they have to remember the right command, the right
  scope, and the right confirmation flow.

The user expectation is that **the plugin creates Notion pages itself**, both
when bootstrapping a new workspace and when capturing new findings during day-
to-day sessions. This design replaces the manual push flow with an automatic
one driven by detectable session boundaries.

## Goal

Two changes, working together:

1. **Auto-populated bootstrap.** `/cg-bootstrap` writes a one-paragraph
   description into each created Notion page (root + per-dir + lazy type-index
   pages), instead of leaving them empty.
2. **Auto-push on logical session end.** Curator captures stay in a local
   pending queue. When a recognised "session boundary" trigger fires (a
   keyword in the user's message, a `git` operation, or a completed slash
   command), the plugin batch-pushes pending records to Notion silently and
   reports a one-block summary in chat.

The user no longer types `/cg-sync-notion push`; the plugin does it on every
trigger.

## Non-goals

- Do not auto-detect repo structural changes (new dirs, removed dirs) for
  bootstrap. `--refresh` is a manual flag.
- Do not auto-promote raw curator signals into deeper "promoted patterns" via
  `promote-pattern`. That remains explicit.
- Do not invent a richer conflict model than last-write-wins. Manual edits to
  Notion pages may be overwritten on the next push (acknowledged trade-off).
- Do not introduce a new Notion API-token path. All Notion writes go through
  the official Notion MCP OAuth connection.
- Do not change the existing `/cg-sync-notion push` semantics; manual push
  stays available and shares state with auto-push.

## User Flow

### One-time setup

```text
/cg-init                # creates .context-graph/workspace.json
/cg-bootstrap           # creates Notion root + per-dir pages with paragraph content
```

### Day-to-day

The user works in Claude Code as usual. Curator captures rules, gotchas,
decisions, etc. into the local graph (existing behaviour). When the user:

- types a keyword phrase such as `готово`, `ship it`, `merged`, `done`;
- runs a `git commit`, `git push`, `git merge`, or `git tag` command;
- completes a slash command such as `/commit`, `/create-pr`, `/ship`,
  `/pr-review`,

…the plugin batch-pushes any pending captures to Notion and prints a summary:

```text
Auto-pushed to Notion
  + Rule: Always require Idempotency-Key on bl-api webhooks → bl-api/
  + Decision: Use Sequelize transactions for multi-step money ops → core/
  + Gotcha: room_oxtraders overrides bank_accounts schema → docs/
```

No confirmation prompt. If nothing is pending, the trigger is a no-op.

## Architecture

```text
[Curator skill captures signal]
        ↓
[Local graph: record indexed; recordId appended to notion_push.json#pending]
        ↓
[Trigger fires: keyword | git op | slash command]
        ↓
[Push orchestrator: lock → plan → push → unlock]
        ↓
[Notion API via official MCP: create or update page under target]
        ↓
[Push state recorded: notionPageId + lastPushedRevision]
        ↓
[Chat summary]
```

Components:

- **Curator skill** (existing) — extended to append the captured record's id
  to the push queue (`notion_push.json#pending`) on every captured signal of
  types `rule | gotcha | decision | module-boundary | convention | task |
  bug-fix`.
- **Trigger detector** (new) — three sources wired through Claude Code hooks:
  - `UserPromptSubmit` hook scanning user message for keyword patterns.
  - `PostToolUse` hook on Bash matching `git commit | git push | git merge | git tag`.
  - `PostToolUse` (or `SlashCommandComplete` if available) matching listed slash
    commands.
- **Push orchestrator** (new) — invoked by trigger detector. Uses the existing
  `plan_notion_push` → `record_to_notion_payload` → Notion MCP →
  `apply_notion_push_result` chain.
- **Bootstrap content writer** (new, integrated into `/cg-bootstrap`) — generates
  per-page paragraphs from cheap source materials (READMEs, package manifests,
  file listings).

## Trigger Detection

### Keyword phrases

Matched case-insensitive on the user's incoming message via `UserPromptSubmit`
hook.

Russian: `готово`, `закоммить`, `закоммитим`, `закругляемся`, `закрываем
задачу`, `закрыли`, `запушил`, `запуш`, `задеплоил`, `деплой`, `доделал`,
`доделали`, `закончил`, `закончили`, `мержим`, `замержил`, `завершил`,
`работа сделана`, `шипим`, `ок все`, `готово к мёрджу`.

English: `ship`, `ship it`, `shipped`, `merge`, `merging`, `merged`, `commit
this`, `committed`, `done`, `we're done`, `all done`, `task complete`,
`completed`, `wrap up`, `wrapped`, `closing this out`, `pushed`, `deployed`,
`pr is up`, `pr opened`, `lgtm`, `that's it`, `and we're done`, `all set`.

Matching is substring with word boundaries. False positives are tolerated over
missed triggers; if a phrase is mid-sentence ambiguous, the orchestrator still
fires (cost is one extra summary line, not data loss).

### Git operations

Matched via `PostToolUse` hook on Bash invocations whose command starts with
one of: `git commit`, `git push`, `git merge`, `git tag`. Matched on success
exit code only.

### Slash commands

Matched via `PostToolUse` hook (or `SlashCommandComplete` event if available
in the runtime) on completion of: `/commit`, `/create-pr`, `/ship`,
`/pr-review`.

### Debounce

Multiple triggers within 10 seconds are collapsed into a single push. The
orchestrator owns a debounce timer per workspace.

### Workspace gating

Triggers are no-ops outside a directory whose nearest ancestor contains
`.context-graph/workspace.json`. The detector resolves the workspace by
walk-up from the cwd of the triggering event.

## Push Orchestrator Flow

1. **Lock.** Acquire `.context-graph/.push.lock` (file lock with 30 s timeout).
   If lock is busy, the trigger is a no-op (the in-flight push will pick up
   any captures that landed while it was running on its next iteration).
2. **Collect.** Read `notion_push.json#pending`. Look up each id in the
   local graph. Filter to records whose
   `source.metadata.classifierNotes.arbiter != "pending-arbitration"`.
   Pending-arbitration records stay in the queue and surface in the summary.
3. **Empty check.** If no eligible records, release lock and exit.
4. **Workspace state check.** If `workspace.json.notion.rootPageId` is missing
   (user skipped `/cg-bootstrap`), emit one-line message: `Auto-push paused:
   run /cg-bootstrap first.` Captures stay pending, lock released.
5. **Plan.** Call `plan_notion_push` with `recordIds: <eligible>`. Returns
   `creates` and `updates` per the existing semantics.
6. **Resolve target.** For each record, in order:
   - If `record.markers.notionDir` is set explicitly, use
     `dirPageIds[notionDir]`.
   - Else parse `record.source.metadata.parent` (a `>`-separated breadcrumb
     such as `kenmore > Kenmore > kenmore — Context Graph > bl-api/`) and
     pick the rightmost segment that matches a key in `dirPageIds`.
   - Else (cross-cutting) use the workspace `rootPageId` and place the
     record under a `Cross-cutting` section header on the root page.
7. **Per-record push.**
   - For `creates`: call `record_to_notion_payload`, then `notion-create-pages`
     with the resolved parent. On success call `apply_notion_push_result`
     (writes `notionPageId`, `lastPushedRevision`, `lastPushedAt`) and remove
     the recordId from `notion_push.json#pending`.
   - For `updates`: call `record_to_notion_payload`, then `notion-update-page`
     with `command: replace_content`, `allow_deleting_content: true`. On
     success call `apply_notion_push_result` (preserves mapping, bumps
     `lastPushedRevision` and `lastPushedAt`) and remove the recordId from
     `notion_push.json#pending`.
   - On per-record API error, retry with exponential backoff (1 s, 3 s, 10 s).
     After 3 failures, leave the recordId in `pending` and continue with the
     next.
8. **Type-index pages.** For each pushed record's type, ensure the
   `Indexes/<Type>s` page exists (create lazily on first encounter), then
   upsert a row pointing to the primary page. Index pages are kept under root.
9. **Unlock.** Release `.push.lock`.
10. **Chat summary.** One block listing pushed records with their primary
    location and counts of skipped/failed ones.

## Bootstrap Content

`/cg-bootstrap` writes generated paragraphs into each created Notion page.

### Root page

- Title: project name resolved from (in order) `package.json#name`, the
  python project's `pyproject.toml`, or the git remote basename.
- Body:
  - Tagline: first paragraph of repo-root `README.md`, or empty if absent.
  - Auto-generated list of dir-page links (existing behaviour).
  - Auto-generated `Indexes` section linking to type-index pages
    (created lazily, so initially empty).
  - Footer: `Maintained by Context Graph plugin. Auto-sections will be
    rewritten on /cg-bootstrap --refresh.`

### Per-dir page

- Title: `<dir>/`.
- Body, one paragraph generated from cheap source materials:
  - `Purpose:` one sentence describing what the dir does.
  - `Stack:` key dependencies extracted from `<dir>/package.json` (or
    nested `Cargo.toml` / `pyproject.toml` / `requirements.txt`).
  - `Entry points:` up to five top-level files, by name only (no body
    reading).
  - `Notes:` first paragraph of `<dir>/README.md` if present.
- Empty `Curated` section underneath, where auto-push will land records.

Source materials cap: per dir, the writer reads at most 1 README plus 1
manifest plus a directory listing. No deep code analysis. If nothing
matches, fall back to a heuristic from the dir name (`bl-api/` →
"API service for business logic").

### Type-index pages

Created lazily on the first auto-push of a record of that type. Children
of the root page (or under an `Indexes` parent if the user prefers; root
is the default).

Body: a markdown table sorted by `lastPushedAt` desc:

```markdown
# Rules

| Title | Dir | Updated |
|-------|-----|---------|
| Always require Idempotency-Key on bl-api webhooks | [bl-api/](link) | 2 days ago |
```

### Refresh

`/cg-bootstrap --refresh` re-runs the content generator over existing pages,
overwriting the auto-generated body sections only. Manually-edited content in
the `Curated` section of each dir page is preserved (it is owned by auto-push,
not by `--refresh`).

## Notion Structure

```text
📚 <ProjectName>                              ← root
├── 📂 admin/                                 ← dir page (paragraph + Curated section)
│   └── (Curated)
│       ├── Rule: …
│       └── Gotcha: …
├── 📂 bl-api/
├── 📂 core/
├── … (per-dir pages)
└── 📚 Indexes
    ├── 📋 Rules
    ├── ⚡ Decisions
    ├── ⚠️ Gotchas
    ├── 🧱 Module Boundaries
    ├── 📐 Conventions
    ├── ✅ Tasks
    └── 🐛 Bug Fixes
```

Each pushed record exists in two places: under its primary dir page (or root
under `Cross-cutting` if no matching dir) and as a row in its type-index page.

## Update Semantics

Conflict policy: **last-write-wins, plugin is source of truth.**

Each record carries `revision.version` (already implemented; bumped by
`index_records` on upsert). Push state stored in `.context-graph/notion_push.json`:

```json
{
  "notion:34d37bbb09ff8163a748d0a925b10435": {
    "notionPageId": "abc123",
    "lastPushedRevision": 3,
    "lastPushedAt": "2026-04-26T18:30:00Z"
  }
}
```

Decision per record on each trigger:

| State                                                        | Action |
|--------------------------------------------------------------|--------|
| Not in push state                                            | create |
| In push state, `revision.version > lastPushedRevision`       | update |
| In push state, `revision.version == lastPushedRevision`      | skip   |
| Local record archived                                        | update with `[ARCHIVED]` title prefix and remove from type-index |
| Local record deleted                                         | update with `[DELETED]` placeholder body and remove from type-index |

Manual edits to Notion are overwritten on update. The user's escape hatch is
to re-capture the change locally (so the next revision contains it).

## Edge Cases

- **Pending captures persist across sessions.** The push queue
  (`notion_push.json#pending`) lives on disk inside `.context-graph/`. If a
  session ends without a trigger, captures wait for the next session's
  first trigger. No data loss.
- **Concurrent triggers.** File lock prevents overlapping pushes. The losing
  trigger is a no-op; its captures will be picked up on the next cycle.
- **Pending-arbitration records are not pushed.** They remain in the local
  queue and surface in the summary as `N records skipped (run /cg-classify
  to resolve)`.
- **Cross-cutting override.** `record.markers.notionDir` set manually by the
  user (via `/cg-classify`) overrides the auto-routing rule.
- **Privacy / redaction.** The existing redactor registry runs on each
  payload before it leaves the workspace. Tokens and other secrets are
  replaced with `[REDACTED]` in Notion bodies; the local record is
  unchanged.
- **Notion rate limits.** Per-record exponential backoff handles transient
  rate-limit responses. Persistent rate-limit failures leave records
  pending until the next trigger.
- **Hooks setup.** `/cg-init` writes (or merges into) `hooks.json` workspace
  entries for `UserPromptSubmit` and `PostToolUse` so triggers fire without
  any extra configuration. If user already has custom hooks, the plugin
  appends rather than overwrites.
- **Manual `/cg-sync-notion push` continues to work.** It uses the same
  `plan_notion_push` → `apply_notion_push_result` chain and shares
  `notion_push.json`. Manual and automatic pushes are interchangeable.
- **`/cg-bootstrap --refresh` for major repo changes.** Plugin does not auto-
  detect new or removed top-level dirs. The user runs `--refresh` when
  structure changes meaningfully.

## Data Model Changes

- **Push queue.** A `pending: [recordId, ...]` list inside
  `.context-graph/notion_push.json`. Curator capture appends; the orchestrator
  drains. Keeping the queue here (instead of on each record) avoids polluting
  the taxonomy `markers` field and keeps push state in a single file.
- **Per-record push state.** Existing entries in `notion_push.json` are
  extended with `lastPushedRevision: number` and
  `lastPushedAt: ISO timestamp` alongside the existing `notionPageId`.
- **Index page mapping.** `workspace.json.notion.indexPageIds: { [type: string]: pageId }`
  is added on the first push of each type. Used to update the right index
  page on subsequent pushes.
- **Manual routing override.** `record.markers.notionDir: string` (optional)
  — placed in markers so it surfaces in classification output and persists
  through `index_records`. Empty / absent means use auto-routing.

## Hooks

The plugin extends its existing `hooks.json` (which already wires SessionStart
priming and PostToolUse reindex). The new entries follow the same shape as
the existing ones:

```json
{
  "hooks": {
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

Notes:

- `UserPromptSubmit` uses a permissive matcher; the keyword scan happens inside
  the script (cheaper than a regex matcher and easier to update the keyword
  list).
- `PostToolUse:Bash` fires on every Bash call; the script filters for the
  specific git verbs it cares about and exits silently otherwise.
- The exact hook event name for slash commands depends on the runtime. If
  `SlashCommand` is not available, the same script can be wired into
  `PostToolUse` with a Bash-side detection of slash-command exit signals.

`trigger_detect.py` is a new script (under `scripts/`) that:

1. Reads the hook event payload from stdin.
2. Decides whether the event is a real trigger (keyword present, git verb
   matched, or slash command matched).
3. Walks up from the event cwd to find a workspace; exits silently if none.
4. Acquires the push lock, runs the orchestrator (`scripts/auto_push.py`),
   releases the lock.
5. Emits the chat summary on stdout if any push happened.

Existing `hooks.json` entries (SessionStart prime, PostToolUse reindex) are
preserved unchanged — `/cg-init` merges new entries rather than overwriting.

## Rollout / Migration

- Existing workspaces continue to work without the new behaviour until they
  re-run `/cg-init` (which adds the hook entries) or manually edit
  `hooks.json`.
- Users who never want auto-push can opt out by removing the hook entries or
  setting `workspace.json.autoPush.enabled = false` (a new flag, default
  `true`).
- Existing `notion_push.json` is forward-compatible. New fields are added,
  no existing fields are renamed or repurposed.

## Open follow-ups

- Auto-detect new top-level dirs and offer a `--refresh` prompt at session
  start.
- Type-index page pagination once a single page exceeds Notion's block
  count comfort threshold (~200 entries).
- Per-record dry-run preview command (`/cg-sync-notion plan`) so users can
  inspect what auto-push would do before enabling it on a sensitive
  workspace.
