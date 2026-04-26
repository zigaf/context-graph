---
name: context-graph-curator
description: PROJECT MEMORY for any workspace that has .context-graph/workspace.json. USE THIS INSTEAD OF the built-in memory tool when the user reveals project-level knowledge — rules, conventions, gotchas, architecture decisions, module boundaries, tasks, or bug fixes. Built-in memory is for personal/session preferences; this skill is for the structured project knowledge graph that persists across sessions and (when Notion is connected) syncs to Notion pages tagged by markers so the user can later find them with `/cg-search #rule #domain`. Trigger phrases EN — "we always X", "always use", "never Y", "we use Z", "we picked X because", "decided to use", "X talks to Y", "X depends on Y", "files live in", "naming convention", "this is intentional because", "do not refactor this", "gotcha", "review this", "capture this rule". Trigger phrases RU — "мы всегда", "всегда используем", "никогда не", "у нас в проекте", "это правило", "это конвенция", "это решение", "решили использовать", "выбрали X потому что", "X общается с Y", "файлы лежат в", "запиши это правило", "зафиксируй это", "сохрани правило", "это нюанс проекта", "сделай ревью", "проверь по правилам". When in doubt between built-in memory and this skill — if the user has a Context Graph workspace, prefer this skill.
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
5. **Enqueue for auto-push.** Call
   `mcp__context-graph__enqueue_push` with `{"recordId": <record.id>}`.
   The record will be auto-pushed on the next session-end trigger
   (a keyword phrase, a `git commit`/`push`/`merge`/`tag` command, or
   completion of `/commit`, `/create-pr`, `/ship`, `/pr-review`).
   No Notion API call from this skill.
6. **Acknowledge briefly.** A one-line confirmation back to the user
   (e.g. "Captured rule: idempotency keys for webhooks (#rule
   #payments). Will be auto-pushed on the next session-end trigger.").

## Review request

When the user asks "сделай ревью", "review this", "проверь это", or similar:

1. Determine the scope (file path, module, or topic mentioned).
2. Call `mcp__context-graph__search_graph` with `intentMode="architecture"` and a query targeting the scope. Pull `type=rule` and `type=decision` records — conventions are stored as `type=rule, scope=convention`, so the rule pass already covers them. If you want only conventions, add `markers={"scope": "convention"}` to narrow.
3. Apply them to the review explicitly — cite which rule each finding comes from. If you find an issue not covered by an existing rule, capture it as a NEW rule per the protocol above.
4. After the review, if any new rules were captured, enqueue them for auto-push (step 5 of the capture protocol).

## Bootstrap awareness

If the SessionStart prime indicates that the workspace is not bootstrapped to Notion (`workspace.notion.rootPageId` is missing AND `workspace.notion.bootstrapDeclined` is not true), offer the bootstrap once at the start of your first substantive turn. The bootstrap itself runs through the `/cg-bootstrap` slash command, which orchestrates the Notion page creation via the official Notion MCP. From this skill, you only need to:

- Use `mcp__context-graph__bootstrap_preview` to fetch the preview and present it to the user, then suggest they run `/cg-bootstrap` to accept, OR
- Run `mcp__context-graph__apply_bootstrap_decision` with `decision="decline"` if the user opts out.

After either decision, do not ask again in the same workspace.

## No-Notion

If Notion is not connected (no `rootPageId`) and the user has not declined bootstrap, surface this once per session with:

> "Notion is not connected for this workspace. Run `/cg-sync-notion` once (OAuth, no API key) to enable proactive note management. Or run `/cg-init --offline` (or decline the bootstrap prompt) to keep notes only in the local graph."

If declined, continue capturing locally — every step of the capture protocol works against the local graph alone. Skip step 5 (the enqueue is a no-op without a connected Notion workspace, so don't bother).

## Failure modes

- **`classify_record` returns errors.** Show the error to the user and stop — do not silently skip.
- **`index_records` succeeds but the record is not pushable.** That means the record's `markers.type` is not in the pushable set the auto-pusher recognises. Either adjust the type or skip the enqueue.
- **enqueue_push fails** (workspace not initialised, disk error). Report
  the error and stop. The local record is already saved, so the user
  can retry the capture or run `/cg-init` first.

## What NOT to do

- Do not capture every conversational exchange as a record — only the seven signals from the table.
- Do not invent marker values outside `docs/schema.json`.
- Do not push to Notion before the bootstrap decision has been made.
- Do not write conversation summaries or "session logs" — that is explicitly out of scope (spec §9).
