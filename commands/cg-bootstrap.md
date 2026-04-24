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
