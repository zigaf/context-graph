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
