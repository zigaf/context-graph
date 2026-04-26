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

Use the helpers in `scripts/bootstrap_content.py` to generate page bodies.

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

6. If the user says yes:
   a. Call the Notion MCP `notion-create-pages` to create the parent page (title = `projectTitle`, body = `tagline` or empty).
   b. Capture the resulting page id (`rootPageId`) and page url (`rootPageUrl`).
   c. For each dir in `topLevelDirs`, call `notion-create-pages` with parent = `rootPageId` and title = `<path> — <purpose>` (purpose may be empty; that's fine). Collect the results into `dirPageIds: {path: pageId}`.
   d. Call `mcp__context-graph__apply_bootstrap_decision` with `{workspaceRoot, decision: "accept", rootPageId, rootPageUrl, dirPageIds}`.
   e. Confirm to the user with the new root page URL.

## Failure modes

- Notion MCP not connected: tell the user "Run `/cg-sync-notion` once first to authorize Notion, then re-run `/cg-bootstrap`." Do NOT call `apply_bootstrap_decision`.
- Notion API returns an error mid-bootstrap: stop, report what was created so far, and instruct the user to retry. Do not record a partial result.

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
