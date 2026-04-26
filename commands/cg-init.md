---
description: Initialize a Context Graph workspace for the current directory
argument-hint: <workspace-root-path?>
---

The user wants to initialize a Context Graph workspace. Walk through it interactively.

Steps:

1. Determine the candidate root:
   - If `$ARGUMENTS` is non-empty, use it.
   - Otherwise use the CWD.

2. Ask the user to confirm:
   `Use <candidate> as the workspace root for Context Graph? [y/N/<other path>]`
   - `y`: proceed with the candidate.
   - Any other path: use that instead.
   - `N`: stop and report `Initialization canceled.`

3. Ask about Notion mapping:
   `Create a Notion root page for this workspace now? [a] Auto / [u] I have a parent page / [s] Skip`
   - `a`: create a workspace page under a plugin-managed `Context Graph` parent.
   - `u`: ask for a parent page URL or id and create a child page for this workspace.
   - `s`: leave Notion fields empty.

4. Depending on the answer:
   - For `a`, look up a `Context Graph` parent with the available Notion MCP search tool. If it is missing and a Notion create tool is available, create it, then create a child page titled after the directory name.
   - For `u`, ask the user for the parent URL or id, then use the available Notion MCP create tool to create a child page titled after the directory name.
   - For `s`, skip Notion calls.

5. Call `mcp__context-graph__init_workspace` with:
   - `rootPath`: the confirmed root path.
   - `notionRootPageId`: the created or linked Notion page id, if available.
   - `notionRootPageUrl`: the created or linked Notion page URL, if available.

6. Report:
   - Workspace path.
   - Workspace id.
   - Manifest path.
   - Notion URL, if any.
   - That local `.context-graph` state entries were added to `.gitignore`.

If the Context Graph MCP returns an already-initialized error, surface it verbatim and suggest reviewing the existing `.context-graph/workspace.json`.

## Auto-push hooks

After the workspace is initialised, the auto-push hooks are inherited
from the plugin's repo-level `hooks.json`. New users do not need to
copy anything: the plugin's `hooks.json` is loaded by Claude Code
automatically when the plugin is enabled.

Claude Code merges plugin-level hooks with any user-level hooks the
user already has — there is nothing to copy or edit. To opt out of
auto-push, set `workspace.json.autoPush.enabled` to `false`; the
`scripts/trigger_detect.py` trigger script honours the flag and exits
silently.
