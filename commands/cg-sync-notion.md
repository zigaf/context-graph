---
description: Pull Notion pages into the Context Graph via the official Notion MCP
argument-hint: <scope>  (search query, page title, or database name)
---

The user wants to sync Notion content into the workspace's Context Graph. The Notion MCP is expected to be connected through the live session's OAuth connection, so no API key or Notion token is needed.

Steps:

1. Confirm a Context Graph workspace exists.
   - If the session is in a directory without `.context-graph/workspace.json`, tell the user to run `/cg-init` first and stop.

2. Handle scope.
   - If `$ARGUMENTS` is empty, ask the user for a search scope such as a keyword, page title, or database name, then stop.

3. Search via the available Notion MCP search tool.
   - Use whichever Notion search tool is registered in the session, such as `mcp__notion__notion-search`.
   - Pass `query: $ARGUMENTS`, `query_type: "internal"`, `page_size: 10`, and `filters: {}`.
   - If no Notion MCP tool is connected, tell the user to connect the official Notion MCP/OAuth integration for this session and stop.
   - If more than about 50 pages are returned, ask the user to confirm before pulling all of them.

4. For each page, in order:
   - Fetch it through the available Notion MCP fetch tool.
   - Record the search result `timestamp` as `last_edited_time`.
   - Build a draft record:
     - `id`: `notion:<32-hex page id>` after stripping UUID hyphens and lowercasing.
     - `title`: page title.
     - `content`: markdown body between `<content>` and `</content>` inside `text`.
     - `source.system`: `notion`.
     - `source.url`: full Notion URL.
     - `source.metadata`: `notionPageId`, `last_edited_time`, and `parent` as reversed ancestor-path titles joined with ` > `.

5. Classify each draft record.
   - Call `mcp__context-graph__classify_record` with the draft.
   - If `source.metadata.classifierNotes.arbiter == "pending-arbitration"`, resolve it in this live session using the current agent, not an external API.
   - Read `arbitrationRequest`: use `record`, `candidates`, `allowedValues`, and `requiredFields`.
   - For each pending field, pick one value from that field's `allowedValues`. Return null only when nothing fits and the field is not required.
   - Override `record.markers.<field>` with the chosen values.
   - Set `record.source.metadata.classifierNotes.arbiter` to `llm-session` and fill `reasoning` with one sentence.
   - If the classifier was deterministic or fallback, keep the returned record unchanged.

6. Index once.
   - Call `mcp__context-graph__index_records` once with the finalized batch.

7. Report:
   - Pages pulled.
   - Records upserted from `indexResult.upsertedIds`.
   - Count of records resolved by `llm-session` arbitration.
   - If new proposals were produced, mention `/cg-schema-review`.

Do not invent marker values beyond `allowedValues`. Do not ask for or use API keys. If validation rejects a marker, fall back to the classifier's deterministic top value.
