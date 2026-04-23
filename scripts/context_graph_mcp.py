from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable

from context_graph_core import (
    apply_proposal_decision,
    apply_push_result,
    archive_record,
    build_context_pack,
    classify_record,
    default_graph_path,
    delete_record,
    filter_pages_by_cursor,
    index_records,
    infer_relations,
    init_workspace,
    ingest_markdown,
    ingest_notion_export,
    learn_schema,
    list_proposals,
    list_pushable_records,
    load_graph,
    load_notion_cursor,
    load_push_state,
    plan_push,
    promote_pattern,
    record_to_notion_blocks,
    save_notion_cursor,
    save_push_state,
    search_graph,
    unarchive_record,
)
import eval_harness as _eval_harness


PROTOCOL_VERSION = "2025-03-26"
JSONRPC_VERSION = "2.0"


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: ToolHandler


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=True, indent=2),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def require_object(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return arguments


def handle_classify_record(arguments: dict[str, Any]) -> dict[str, Any]:
    return classify_record(arguments)


def handle_init_workspace(arguments: dict[str, Any]) -> dict[str, Any]:
    return init_workspace(arguments)


def handle_link_record(arguments: dict[str, Any]) -> dict[str, Any]:
    if "record" not in arguments:
        raise ValueError("Missing required field: record")
    return infer_relations(arguments)


def handle_build_context_pack(arguments: dict[str, Any]) -> dict[str, Any]:
    return build_context_pack(arguments)


def handle_index_records(arguments: dict[str, Any]) -> dict[str, Any]:
    if "records" not in arguments:
        raise ValueError("Missing required field: records")
    if not isinstance(arguments["records"], list):
        raise ValueError("records must be an array")
    return index_records(arguments)


def handle_search_graph(arguments: dict[str, Any]) -> dict[str, Any]:
    return search_graph(arguments)


def handle_promote_pattern(arguments: dict[str, Any]) -> dict[str, Any]:
    return promote_pattern(arguments)


def handle_learn_schema(arguments: dict[str, Any]) -> dict[str, Any]:
    return learn_schema(arguments)


def handle_list_proposals(arguments: dict[str, Any]) -> dict[str, Any]:
    return list_proposals(arguments)


def handle_apply_proposal_decision(arguments: dict[str, Any]) -> dict[str, Any]:
    return apply_proposal_decision(arguments)


def handle_ingest_markdown(arguments: dict[str, Any]) -> dict[str, Any]:
    return ingest_markdown(arguments)


def handle_ingest_notion_export(arguments: dict[str, Any]) -> dict[str, Any]:
    return ingest_notion_export(arguments)


def handle_sync_notion(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        from notion_sync import sync_notion
    except ImportError as exc:
        raise ValueError(
            "notion_sync module not available. Install the Notion sync dependencies "
            f"or run ingest_notion_export on a markdown export instead. Detail: {exc}"
        )
    return sync_notion(arguments)


def handle_delete_record(arguments: dict[str, Any]) -> dict[str, Any]:
    if "recordId" not in arguments:
        raise ValueError("Missing required field: recordId")
    return delete_record(arguments)


def handle_archive_record(arguments: dict[str, Any]) -> dict[str, Any]:
    if "recordId" not in arguments:
        raise ValueError("Missing required field: recordId")
    return archive_record(arguments)


def handle_unarchive_record(arguments: dict[str, Any]) -> dict[str, Any]:
    if "recordId" not in arguments:
        raise ValueError("Missing required field: recordId")
    return unarchive_record(arguments)


def handle_load_notion_cursor(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_root = arguments.get("workspaceRoot")
    cursor = load_notion_cursor(workspace_root)
    return {"cursor": cursor}


def handle_save_notion_cursor(arguments: dict[str, Any]) -> dict[str, Any]:
    if "cursor" not in arguments:
        raise ValueError("Missing required field: cursor")
    cursor = arguments["cursor"]
    if not isinstance(cursor, dict):
        raise ValueError("cursor must be an object")
    workspace_root = arguments.get("workspaceRoot")
    save_notion_cursor(cursor, workspace_root)
    return {"cursor": cursor, "saved": True}


def handle_filter_pages_by_cursor(arguments: dict[str, Any]) -> dict[str, Any]:
    return filter_pages_by_cursor(arguments)


def handle_retrieval_scoring(arguments: dict[str, Any]) -> dict[str, Any]:
    queries_path = arguments.get("queriesPath")
    graph_path = arguments.get("graphPath")
    if not queries_path:
        raise ValueError("Missing required field: queriesPath")
    if not graph_path:
        raise ValueError("Missing required field: graphPath")
    from pathlib import Path as _Path
    queries = _eval_harness.load_queries(_Path(str(queries_path)))
    results = _eval_harness.run_harness(queries, _Path(str(graph_path)), k=int(arguments.get("k") or 5))
    summary = _eval_harness.summarize(results)
    baseline_path = arguments.get("baselinePath")
    baseline_info: dict[str, Any] = {}
    if baseline_path:
        is_regression, reason = _eval_harness.compare_against_baseline(
            summary,
            _Path(str(baseline_path)),
            precision_tolerance=float(arguments.get("tolerance") or 0.0),
        )
        baseline_info = {"isRegression": is_regression, "reason": reason}
    return {
        "summary": summary,
        "perQuery": [_eval_harness.result_to_dict(r) for r in results],
        "baseline": baseline_info,
    }


def _workspace_from_args(arguments: dict[str, Any]):
    workspace = arguments.get("workspaceRoot")
    return workspace if workspace else None


def handle_plan_notion_push(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_args(arguments)
    graph_path_input = arguments.get("graphPath")
    graph_path = str(graph_path_input) if graph_path_input else str(default_graph_path(workspace))
    record_ids_input = arguments.get("recordIds")
    record_ids = [str(rid) for rid in record_ids_input] if record_ids_input else None
    records = list_pushable_records(graph_path, record_ids=record_ids)
    state = load_push_state(workspace)
    plan = plan_push(records, state)
    return {
        "graphPath": graph_path,
        "plan": {
            "creates": [
                {"id": record.get("id"), "title": record.get("title")}
                for record in plan["creates"]
            ],
            "updates": [
                {"id": item["record"].get("id"), "notionPageId": item["notionPageId"]}
                for item in plan["updates"]
            ],
        },
        "pushState": state,
    }


def handle_apply_notion_push_result(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    notion_page_id = arguments.get("notionPageId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    if not notion_page_id:
        raise ValueError("Missing required field: notionPageId")
    workspace = _workspace_from_args(arguments)
    state = load_push_state(workspace)
    new_state = apply_push_result(str(record_id), str(notion_page_id), state)
    save_push_state(new_state, workspace)
    return {
        "recordId": str(record_id),
        "notionPageId": str(notion_page_id),
        "pushState": new_state,
    }


def _resolve_notion_root_page_id_from_workspace(workspace) -> str | None:
    from pathlib import Path

    if not workspace:
        return None
    root = Path(str(workspace))
    manifest_path = root / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        return None
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    notion = manifest.get("notion") if isinstance(manifest, dict) else None
    if not isinstance(notion, dict):
        return None
    root_id = notion.get("rootPageId")
    return str(root_id) if root_id else None


def handle_record_to_notion_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    workspace = _workspace_from_args(arguments)
    graph_path_input = arguments.get("graphPath")
    graph_path = str(graph_path_input) if graph_path_input else str(default_graph_path(workspace))
    graph = load_graph(graph_path)
    record = (graph.get("records") or {}).get(str(record_id))
    if not record:
        raise ValueError(f"Record not found in graph: {record_id}")
    blocks = record_to_notion_blocks(record)
    parent_page_id = _resolve_notion_root_page_id_from_workspace(workspace)
    return {
        "recordId": str(record_id),
        "title": str(record.get("title") or "Untitled"),
        "blocks": blocks,
        "content": str(record.get("content") or ""),
        "parentPageId": parent_page_id,
    }


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="classify_record",
        title="Classify Record",
        description="Normalize markers, infer missing fields from note text, and build a hierarchy path.",
        input_schema={
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "description": "Record with title, content, markers, and optional source metadata.",
                }
            },
            "required": ["record"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "markers": {"type": "object"},
                "missingRequiredMarkers": {"type": "array"},
                "hierarchy": {"type": "object"},
            },
            "required": ["id", "title", "markers", "missingRequiredMarkers", "hierarchy"],
        },
        handler=handle_classify_record,
    ),
    ToolSpec(
        name="init_workspace",
        title="Initialize Context Graph Workspace",
        description="Create .context-graph/workspace.json at a root path and optionally record the Notion root page mapping.",
        input_schema={
            "type": "object",
            "properties": {
                "rootPath": {
                    "type": "string",
                    "description": "Absolute path to the workspace root. Defaults to the server CWD.",
                },
                "notionRootPageId": {"type": "string"},
                "notionRootPageUrl": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "workspaceId": {"type": "string"},
                "manifestPath": {"type": "string"},
                "notion": {"type": ["object", "null"]},
            },
            "required": ["rootPath", "workspaceId", "manifestPath"],
        },
        handler=handle_init_workspace,
    ),
    ToolSpec(
        name="link_record",
        title="Link Record",
        description="Infer likely relations between one source record and candidate records.",
        input_schema={
            "type": "object",
            "properties": {
                "record": {"type": "object"},
                "candidates": {"type": "array"},
                "minScore": {"type": "number"},
            },
            "required": ["record", "candidates"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "record": {"type": "object"},
                "inferredRelations": {"type": "array"},
            },
            "required": ["record", "inferredRelations"],
        },
        handler=handle_link_record,
    ),
    ToolSpec(
        name="build_context_pack",
        title="Build Context Pack",
        description="Rank note records for a request and return a compact context pack with rules and unresolved risks.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "markers": {"type": "object"},
                "records": {"type": "array"},
                "limit": {"type": "number"},
            },
            "required": ["records"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "queryMarkers": {"type": "object"},
                "directMatches": {"type": "array"},
                "supportingRelations": {"type": "array"},
                "promotedRules": {"type": "array"},
                "unresolvedRisks": {"type": "array"},
            },
            "required": [
                "queryMarkers",
                "directMatches",
                "supportingRelations",
                "promotedRules",
                "unresolvedRisks",
            ],
        },
        handler=handle_build_context_pack,
    ),
    ToolSpec(
        name="index_records",
        title="Index Records",
        description="Upsert normalized records into the local graph store and rebuild explicit and inferred edges.",
        input_schema={
            "type": "object",
            "properties": {
                "records": {"type": "array"},
                "graphPath": {"type": "string"},
            },
            "required": ["records"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "graphPath": {"type": "string"},
                "upsertedIds": {"type": "array"},
                "recordCount": {"type": "number"},
                "edgeCount": {"type": "number"},
            },
            "required": ["graphPath", "upsertedIds", "recordCount", "edgeCount"],
        },
        handler=handle_index_records,
    ),
    ToolSpec(
        name="search_graph",
        title="Search Graph",
        description="Search the persisted graph index and return direct matches plus nearby graph edges.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "markers": {"type": "object"},
                "graphPath": {"type": "string"},
                "limit": {"type": "number"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "directMatches": {"type": "array"},
                "supportingRelations": {"type": "array"},
                "graphStats": {"type": "object"},
                "graphPath": {"type": "string"},
            },
            "required": ["directMatches", "supportingRelations", "graphStats", "graphPath"],
        },
        handler=handle_search_graph,
    ),
    ToolSpec(
        name="promote_pattern",
        title="Promote Pattern",
        description="Promote a cluster of related records into a reusable rule or decision record with derived links.",
        input_schema={
            "type": "object",
            "properties": {
                "recordIds": {"type": "array"},
                "records": {"type": "array"},
                "graphPath": {"type": "string"},
                "title": {"type": "string"},
                "outputType": {"type": "string"},
                "writeToGraph": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "promotedRecord": {"type": "object"},
                "sourceRecords": {"type": "array"},
                "sharedKeywords": {"type": "array"},
                "commonMarkers": {"type": "object"},
                "quality": {"type": "object"},
                "splitSuggestions": {"type": "array"},
            },
            "required": ["promotedRecord", "sourceRecords", "sharedKeywords", "commonMarkers", "quality", "splitSuggestions"],
        },
        handler=handle_promote_pattern,
    ),
    ToolSpec(
        name="learn_schema",
        title="Run Schema Learner",
        description="Mine hierarchy, n-grams, and code paths from the workspace graph and write candidate proposals to schema.learned.json.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "graphPath": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=handle_learn_schema,
    ),
    ToolSpec(
        name="list_proposals",
        title="List Schema Proposals",
        description="Return pending, accepted, and rejected marker proposals for the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=handle_list_proposals,
    ),
    ToolSpec(
        name="apply_proposal_decision",
        title="Apply Schema Proposal Decision",
        description="Accept, reject, or skip a pending proposal. Accept requires a target field.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "value": {"type": "string"},
                "field": {"type": "string"},
                "decision": {"type": "string", "enum": ["accept", "reject", "skip"]},
            },
            "required": ["value", "decision"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=handle_apply_proposal_decision,
    ),
    ToolSpec(
        name="ingest_markdown",
        title="Ingest Markdown",
        description="Scan a markdown file or directory, classify records from front matter and headings, and optionally index them.",
        input_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "recursive": {"type": "boolean"},
                "index": {"type": "boolean"},
                "graphPath": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "fileCount": {"type": "number"},
                "recordIds": {"type": "array"},
            },
            "required": ["rootPath", "fileCount", "recordIds"],
        },
        handler=handle_ingest_markdown,
    ),
    ToolSpec(
        name="ingest_notion_export",
        title="Ingest Notion Export",
        description="Scan a Notion markdown export, preserve page ids from filenames when available, resolve local page links, and optionally index the result.",
        input_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "recursive": {"type": "boolean"},
                "index": {"type": "boolean"},
                "graphPath": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "fileCount": {"type": "number"},
                "recordIds": {"type": "array"},
            },
            "required": ["rootPath", "fileCount", "recordIds"],
        },
        handler=handle_ingest_notion_export,
    ),
    ToolSpec(
        name="sync_notion",
        title="Sync Notion",
        description="Pull pages from a Notion database or parent page via the Notion API, convert them into records, persist a cursor for delta sync, and optionally index the result into the graph.",
        input_schema={
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "databaseId": {"type": "string"},
                "parentPageId": {"type": "string"},
                "graphPath": {"type": "string"},
                "cursorPath": {"type": "string"},
                "since": {"type": "string"},
                "index": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "pagesPulled": {"type": "number"},
                "recordIds": {"type": "array", "items": {"type": "string"}},
                "newCursor": {"type": "string"},
                "indexResult": {"type": "object"},
                "noChangesSince": {"type": "boolean"},
            },
            "required": ["pagesPulled", "recordIds"],
        },
        handler=handle_sync_notion,
    ),
    ToolSpec(
        name="load_notion_cursor",
        title="Load Notion Cursor",
        description="Read the workspace's per-page Notion cursor mapping page_id to last-seen last_edited_time.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "cursor": {"type": "object"},
            },
            "required": ["cursor"],
        },
        handler=handle_load_notion_cursor,
    ),
    ToolSpec(
        name="save_notion_cursor",
        title="Save Notion Cursor",
        description="Persist a per-page Notion cursor (page_id to last_edited_time) to the workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "cursor": {"type": "object"},
            },
            "required": ["cursor"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "cursor": {"type": "object"},
                "saved": {"type": "boolean"},
            },
            "required": ["cursor", "saved"],
        },
        handler=handle_save_notion_cursor,
    ),
    ToolSpec(
        name="filter_pages_by_cursor",
        title="Filter Notion Pages by Cursor",
        description="Partition Notion page stubs into fresh pages (to fetch) and stale pages (to skip) using a per-page cursor.",
        input_schema={
            "type": "object",
            "properties": {
                "pages": {"type": "array"},
                "cursor": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "fresh": {"type": "array"},
                "stale": {"type": "array"},
                "newCursorHint": {"type": ["string", "null"]},
            },
            "required": ["fresh", "stale"],
        },
        handler=handle_filter_pages_by_cursor,
    ),
    ToolSpec(
        name="plan_notion_push",
        title="Plan Notion Push",
        description="Classify pushable records (rule/decision markers by default) against the workspace push state and return creates vs updates without touching Notion.",
        input_schema={
            "type": "object",
            "properties": {
                "graphPath": {"type": "string"},
                "workspaceRoot": {"type": "string"},
                "recordIds": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "graphPath": {"type": "string"},
                "plan": {
                    "type": "object",
                    "properties": {
                        "creates": {"type": "array"},
                        "updates": {"type": "array"},
                    },
                    "required": ["creates", "updates"],
                },
                "pushState": {"type": "object"},
            },
            "required": ["plan", "pushState"],
        },
        handler=handle_plan_notion_push,
    ),
    ToolSpec(
        name="apply_notion_push_result",
        title="Apply Notion Push Result",
        description="Record a {recordId -> notionPageId} mapping in .context-graph/notion_push.json so re-runs update instead of duplicating.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "notionPageId": {"type": "string"},
                "workspaceRoot": {"type": "string"},
            },
            "required": ["recordId", "notionPageId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "notionPageId": {"type": "string"},
                "pushState": {"type": "object"},
            },
            "required": ["recordId", "notionPageId", "pushState"],
        },
        handler=handle_apply_notion_push_result,
    ),
    ToolSpec(
        name="record_to_notion_payload",
        title="Record To Notion Payload",
        description="Return the title, markdown content, Notion blocks, and parent page id for a local record so the slash command can hand it to notion-create-pages or notion-update-page.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "graphPath": {"type": "string"},
                "workspaceRoot": {"type": "string"},
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "title": {"type": "string"},
                "blocks": {"type": "array"},
                "content": {"type": "string"},
                "parentPageId": {"type": ["string", "null"]},
            },
            "required": ["recordId", "title", "blocks", "content"],
        },
        handler=handle_record_to_notion_payload,
    ),
    ToolSpec(
        name="delete_record",
        title="Delete Record",
        description="Remove a record from the persisted graph and rebuild edges so no dangling references survive.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "graphPath": {"type": "string"},
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "deletedId": {"type": "string"},
                "notFound": {"type": "boolean"},
                "recordCount": {"type": "number"},
                "edgeCount": {"type": "number"},
                "graphPath": {"type": "string"},
                "updatedAt": {"type": "string"},
            },
            "required": ["deletedId", "graphPath", "updatedAt"],
        },
        handler=handle_delete_record,
    ),
    ToolSpec(
        name="archive_record",
        title="Archive Record",
        description="Flag a record as archived so it is hidden from context packs and graph search without modifying its edges.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "graphPath": {"type": "string"},
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "archived": {"type": "boolean"},
                "graphPath": {"type": "string"},
                "updatedAt": {"type": "string"},
            },
            "required": ["recordId", "archived", "graphPath", "updatedAt"],
        },
        handler=handle_archive_record,
    ),
    ToolSpec(
        name="unarchive_record",
        title="Unarchive Record",
        description="Clear the archived flag on a record so it becomes visible to context packs and graph search again.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "graphPath": {"type": "string"},
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "archived": {"type": "boolean"},
                "graphPath": {"type": "string"},
                "updatedAt": {"type": "string"},
            },
            "required": ["recordId", "archived", "graphPath", "updatedAt"],
        },
        handler=handle_unarchive_record,
    ),
    ToolSpec(
        name="eval_retrieval",
        title="Score Retrieval Quality",
        description=(
            "Run the retrieval evaluation harness against a curated query set and "
            "fixture graph; compute precision@k, recall@k, context-pack size vs "
            "full-dump size, and optionally compare against a stored baseline."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "queriesPath": {"type": "string"},
                "graphPath": {"type": "string"},
                "baselinePath": {"type": "string"},
                "tolerance": {"type": "number"},
                "k": {"type": "number"},
            },
            "required": ["queriesPath", "graphPath"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "object"},
                "perQuery": {"type": "array"},
                "baseline": {"type": "object"},
            },
            "required": ["summary", "perQuery", "baseline"],
        },
        handler=handle_retrieval_scoring,
    ),
]


class MCPServer:
    def __init__(self) -> None:
        self.initialized = False
        self.log_level = "info"
        self.tools = {tool.name: tool for tool in TOOLS}

    def send(self, message: dict[str, Any]) -> None:
        sys.stdout.write(compact_json(message) + "\n")
        sys.stdout.flush()

    def send_error(self, request_id: Any, code: int, message: str, data: Any = None) -> None:
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        self.send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error})

    def send_result(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result})

    def initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {
                    "listChanged": False,
                }
            },
            "serverInfo": {
                "name": "context-graph",
                "version": "0.1.0",
            },
            "instructions": (
                "Use Context Graph tools to classify records, infer relations, persist a local graph, "
                "and retrieve compact context packs instead of loading full note collections."
            ),
        }

    def list_tools_result(self) -> dict[str, Any]:
        tools = []
        for tool in TOOLS:
            tools.append(
                {
                    "name": tool.name,
                    "title": tool.title,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                    "outputSchema": tool.output_schema,
                }
            )
        return {"tools": tools}

    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Missing or invalid tool name.")
        tool = self.tools.get(name)
        if not tool:
            raise KeyError(name)
        arguments = require_object(params.get("arguments"))
        return tool.handler(arguments)

    def handle_request(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("jsonrpc") != JSONRPC_VERSION:
            request_id = message.get("id")
            if request_id is not None:
                self.send_error(request_id, INVALID_REQUEST, "jsonrpc must be '2.0'.")
            return None

        method = message.get("method")
        is_request = "id" in message
        request_id = message.get("id")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if is_request:
                self.send_error(request_id, INVALID_PARAMS, "params must be an object when provided.")
            return None

        if not isinstance(method, str):
            if is_request:
                self.send_error(request_id, INVALID_REQUEST, "method must be a string.")
            return None

        if method == "initialize":
            if not is_request:
                return None
            self.send_result(request_id, self.initialize_result())
            return None

        if method == "notifications/initialized":
            self.initialized = True
            return None

        if method == "ping":
            if is_request:
                self.send_result(request_id, {})
            return None

        if method == "logging/setLevel":
            self.log_level = str(params.get("level") or "info")
            if is_request:
                self.send_result(request_id, {})
            return None

        if method == "notifications/cancelled":
            return None

        if method == "tools/list":
            if not is_request:
                return None
            self.send_result(request_id, self.list_tools_result())
            return None

        if method == "tools/call":
            if not is_request:
                return None
            try:
                payload = self.call_tool(params)
            except KeyError as exc:
                self.send_error(request_id, INVALID_PARAMS, f"Unknown tool: {exc.args[0]}")
                return None
            except ValueError as exc:
                self.send_result(request_id, tool_result({"error": str(exc)}, is_error=True))
                return None
            except Exception as exc:  # pragma: no cover - defensive boundary
                self.send_result(
                    request_id,
                    tool_result(
                        {
                            "error": "Tool execution failed.",
                            "details": str(exc),
                        },
                        is_error=True,
                    ),
                )
                return None
            self.send_result(request_id, tool_result(payload))
            return None

        if is_request:
            self.send_error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        return None

    def handle_message(self, raw: str) -> None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.send_error(None, PARSE_ERROR, f"Invalid JSON: {exc.msg}")
            return

        if isinstance(parsed, list):
            if not parsed:
                self.send_error(None, INVALID_REQUEST, "Batch request must not be empty.")
                return
            for item in parsed:
                if isinstance(item, dict):
                    self.handle_request(item)
                else:
                    self.send_error(None, INVALID_REQUEST, "Each batch item must be an object.")
            return

        if not isinstance(parsed, dict):
            self.send_error(None, INVALID_REQUEST, "Request must be a JSON object.")
            return

        self.handle_request(parsed)

    def serve(self) -> int:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue
            self.handle_message(raw)
        return 0


def main() -> int:
    server = MCPServer()
    return server.serve()


if __name__ == "__main__":
    raise SystemExit(main())
