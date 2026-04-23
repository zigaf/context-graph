---
description: Classify a record into normalized markers and hierarchy
---

The user wants to classify a single record using the Context Graph classifier.

Steps:

1. Determine the record text:
   - If there is an active selection or highlighted content in the current context, use that as the record.
   - Otherwise, if the user pasted text in the same message that invoked the command, use that.
   - If neither is present, ask the user to paste the note they want classified and stop.
2. Call the MCP tool `mcp__context-graph__classify_record` with the record text as the `record` argument. Do not pre-normalize or summarize the text before sending it.
3. Render the classifier output in three sections:
   - Markers: the normalized markers returned by the classifier, grouped by type (tags, entities, status, etc.) if the tool provides types.
   - Hierarchy path: the inferred path from root to leaf (for example, `project > area > topic`).
   - Missing required markers: any required markers the classifier flagged as absent, with a one-line hint for each on what to add.
4. If the tool returns an error, surface the error message verbatim and stop.

Keep the output scannable. Do not call `index_records` or persist anything — classification is read-only here.
