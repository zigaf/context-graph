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
    dequeue_push,
    enqueue_push,
    filter_pages_by_cursor,
    graph_diff,
    index_records,
    infer_relations,
    init_workspace,
    ingest_markdown,
    ingest_notion_export,
    inspect_record,
    learn_schema,
    list_pending_pushes,
    list_proposals,
    list_pushable_records,
    load_graph,
    load_markdown_cursor,
    load_notion_cursor,
    load_push_state,
    plan_push,
    promote_pattern,
    record_to_notion_blocks,
    save_markdown_cursor,
    save_notion_cursor,
    save_push_state,
    search_graph,
    unarchive_record,
)
from curator_bootstrap import (
    bootstrap_project_skeleton,
    is_bootstrap_needed,
    mark_bootstrap_declined,
    record_bootstrap_result,
)
from hashtag_parser import parse_hashtags as _parse_hashtags
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


def handle_load_markdown_cursor(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_root = arguments.get("workspaceRoot")
    cursor = load_markdown_cursor(workspace_root)
    return {"cursor": cursor}


def handle_save_markdown_cursor(arguments: dict[str, Any]) -> dict[str, Any]:
    if "cursor" not in arguments:
        raise ValueError("Missing required field: cursor")
    cursor = arguments["cursor"]
    if not isinstance(cursor, dict):
        raise ValueError("cursor must be an object")
    workspace_root = arguments.get("workspaceRoot")
    save_markdown_cursor(cursor, workspace_root)
    return {"cursor": cursor, "saved": True}


def handle_filter_pages_by_cursor(arguments: dict[str, Any]) -> dict[str, Any]:
    return filter_pages_by_cursor(arguments)


def handle_graph_diff(arguments: dict[str, Any]) -> dict[str, Any]:
    if not arguments.get("leftPath") and not arguments.get("left"):
        raise ValueError("Missing required field: leftPath (or inline 'left')")
    if not arguments.get("rightPath") and not arguments.get("right"):
        raise ValueError("Missing required field: rightPath (or inline 'right')")
    return graph_diff(arguments)


def handle_inspect_record(arguments: dict[str, Any]) -> dict[str, Any]:
    if not arguments.get("recordId"):
        raise ValueError("Missing required field: recordId")
    return inspect_record(arguments)


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
    revision_input = arguments.get("revision")
    pushed_at_input = arguments.get("pushedAt")
    workspace = _workspace_from_args(arguments)
    state = load_push_state(workspace)
    new_state = apply_push_result(
        str(record_id),
        str(notion_page_id),
        state,
        revision=int(revision_input) if revision_input is not None else None,
        pushed_at=str(pushed_at_input) if pushed_at_input else None,
    )
    save_push_state(new_state, workspace)
    return {
        "recordId": str(record_id),
        "notionPageId": str(notion_page_id),
        "pushState": new_state,
    }


def handle_enqueue_push(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    workspace = _workspace_from_args(arguments)
    pending = enqueue_push(str(record_id), workspace_root=workspace)
    return {"pending": pending}


def handle_dequeue_push(arguments: dict[str, Any]) -> dict[str, Any]:
    record_id = arguments.get("recordId")
    if not record_id:
        raise ValueError("Missing required field: recordId")
    workspace = _workspace_from_args(arguments)
    pending = dequeue_push(str(record_id), workspace_root=workspace)
    return {"pending": pending}


def handle_list_pending_pushes(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_args(arguments)
    return {"pending": list_pending_pushes(workspace_root=workspace)}


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


def handle_bootstrap_preview(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_root = arguments.get("workspaceRoot")
    if not workspace_root:
        raise ValueError("Missing required field: workspaceRoot")
    preview = bootstrap_project_skeleton(workspace_root)
    return {
        **preview,
        "bootstrapNeeded": is_bootstrap_needed(workspace_root),
    }


def handle_apply_bootstrap_decision(arguments: dict[str, Any]) -> dict[str, Any]:
    workspace_root = arguments.get("workspaceRoot")
    decision = arguments.get("decision")
    if not workspace_root:
        raise ValueError("Missing required field: workspaceRoot")
    if decision not in {"accept", "decline"}:
        raise ValueError("decision must be 'accept' or 'decline'")
    if decision == "decline":
        mark_bootstrap_declined(workspace_root)
        return {"recorded": True, "decision": "decline"}
    root_page_id = arguments.get("rootPageId")
    if not root_page_id:
        raise ValueError("Missing required field: rootPageId (required when decision=accept)")
    record_bootstrap_result(
        workspace_root,
        root_page_id=str(root_page_id),
        root_page_url=arguments.get("rootPageUrl"),
        dir_page_ids=arguments.get("dirPageIds") or {},
    )
    return {"recorded": True, "decision": "accept", "rootPageId": str(root_page_id)}


def handle_parse_hashtags(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "")
    from context_graph_core import load_schema
    schema = load_schema()
    new_query, markers = _parse_hashtags(query, schema)
    return {"query": new_query, "markers": markers}


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
                "includeArchived": {"type": "boolean"},
                "workspaceRoot": {"type": "string"},
                "freshnessHalfLifeDays": {
                    "type": ["object", "null"],
                    "description": "Optional override of per-type half-lives (days) used in freshness decay. Missing keys fall back to defaults.",
                },
                "hopPenalty": {
                    "type": "number",
                    "description": "Multiplicative factor per hop beyond the first (default 0.5).",
                },
                "hopTraversal": {
                    "type": "object",
                    "properties": {
                        "maxHops": {"type": "number"},
                    },
                    "description": "Traversal cap for explicit-relation expansion. Default maxHops=1.",
                },
                "intentMode": {
                    "type": ["string", "null"],
                    "enum": ["debug", "implementation", "architecture", "product", None],
                    "description": "Query intent preset. Optional.",
                },
                "intentOverride": {
                    "type": ["object", "null"],
                    "properties": {
                        "markerWeights": {"type": "object"},
                        "typeBoost": {"type": "object"},
                        "statusBias": {"type": "object"},
                        "freshnessMultiplier": {"type": "number"},
                        "hopPenalty": {"type": ["number", "null"]},
                        "hopCap": {"type": "integer"},
                        "allowedRelations": {"type": ["array", "null"], "items": {"type": "string"}},
                        "includeArchived": {"type": ["boolean", "null"]},
                    },
                    "additionalProperties": False,
                },
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
                "intentMode": {
                    "type": ["string", "null"],
                    "enum": ["debug", "implementation", "architecture", "product", None],
                    "description": "Query intent preset. Optional.",
                },
                "intentOverride": {
                    "type": ["object", "null"],
                    "properties": {
                        "markerWeights": {"type": "object"},
                        "typeBoost": {"type": "object"},
                        "statusBias": {"type": "object"},
                        "freshnessMultiplier": {"type": "number"},
                        "hopPenalty": {"type": ["number", "null"]},
                        "hopCap": {"type": "integer"},
                        "allowedRelations": {"type": ["array", "null"], "items": {"type": "string"}},
                        "includeArchived": {"type": ["boolean", "null"]},
                    },
                    "additionalProperties": False,
                },
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
                "promotedRecords": {"type": "array"},
                "sourceRecords": {"type": "array"},
                "sharedKeywords": {"type": "array"},
                "commonMarkers": {"type": "object"},
                "quality": {"type": "object"},
                "splitSuggestions": {"type": "array"},
                "conflicts": {"type": "array"},
            },
            "required": [
                "promotedRecord",
                "promotedRecords",
                "sourceRecords",
                "sharedKeywords",
                "commonMarkers",
                "quality",
                "splitSuggestions",
                "conflicts",
            ],
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
        description="Scan a markdown file or directory, classify records from front matter and headings, and optionally index them. When a per-file mtime cursor is provided, files unchanged since the cursor was recorded are skipped and the response carries an advanced cursor.",
        input_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "recursive": {"type": "boolean"},
                "index": {"type": "boolean"},
                "graphPath": {"type": "string"},
                "cursor": {
                    "type": "object",
                    "description": "Optional per-file mtime cursor (absolute path to epoch-second mtime). When present, unchanged files are skipped and the result includes an advanced cursor.",
                },
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "rootPath": {"type": "string"},
                "fileCount": {"type": "number"},
                "recordIds": {"type": "array"},
                "skippedFileCount": {"type": "number"},
                "skippedFiles": {"type": "array"},
                "cursor": {"type": "object"},
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
        name="load_markdown_cursor",
        title="Load Markdown Cursor",
        description="Read the workspace's per-file markdown ingest cursor mapping absolute file path to last-seen mtime.",
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
        handler=handle_load_markdown_cursor,
    ),
    ToolSpec(
        name="save_markdown_cursor",
        title="Save Markdown Cursor",
        description="Persist a per-file markdown ingest cursor (absolute path to mtime) to the workspace.",
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
        handler=handle_save_markdown_cursor,
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
        description="Record a recordId -> notionPageId mapping (with optional revision/pushedAt metadata) and drain the recordId from the pending queue.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "notionPageId": {"type": "string"},
                "revision": {"type": ["integer", "null"]},
                "pushedAt": {"type": ["string", "null"]},
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
        name="enqueue_push",
        title="Enqueue Notion Push",
        description="Append a record id to the local Notion auto-push queue (deduped).",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "workspaceRoot": {"type": "string"},
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "pending": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["pending"],
        },
        handler=handle_enqueue_push,
    ),
    ToolSpec(
        name="dequeue_push",
        title="Dequeue Notion Push",
        description="Remove a record id from the local Notion auto-push queue.",
        input_schema={
            "type": "object",
            "properties": {
                "recordId": {"type": "string"},
                "workspaceRoot": {"type": "string"},
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "pending": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["pending"],
        },
        handler=handle_dequeue_push,
    ),
    ToolSpec(
        name="list_pending_pushes",
        title="List Pending Notion Pushes",
        description="Return the record ids waiting in the local Notion auto-push queue.",
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
                "pending": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["pending"],
        },
        handler=handle_list_pending_pushes,
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
        name="graph_diff",
        title="Diff Two Graph Snapshots",
        description="Compare two graph.json files (or inline graphs) and return records/edges added, removed, or modified, plus a summary count.",
        input_schema={
            "type": "object",
            "properties": {
                "leftPath": {"type": "string"},
                "rightPath": {"type": "string"},
                "left": {"type": "object"},
                "right": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recordsAdded": {"type": "array"},
                "recordsRemoved": {"type": "array"},
                "recordsModified": {"type": "array"},
                "edgesAdded": {"type": "array"},
                "edgesRemoved": {"type": "array"},
                "summary": {"type": "object"},
            },
            "required": [
                "recordsAdded",
                "recordsRemoved",
                "recordsModified",
                "edgesAdded",
                "edgesRemoved",
                "summary",
            ],
        },
        handler=handle_graph_diff,
    ),
    ToolSpec(
        name="inspect_record",
        title="Inspect Record Score",
        description="Explain why a record would be ranked at its current score for a query, showing matched markers, matched tokens, per-factor contributions, and the record's rank in top-k.",
        input_schema={
            "type": "object",
            "properties": {
                "graphPath": {"type": "string"},
                "workspaceRoot": {"type": "string"},
                "recordId": {"type": "string"},
                "query": {"type": "string"},
                "markers": {"type": "object"},
                "limit": {"type": "number"},
                "includeArchived": {"type": "boolean"},
                "intentMode": {
                    "type": ["string", "null"],
                    "enum": ["debug", "implementation", "architecture", "product", None],
                    "description": "Query intent preset. Optional.",
                },
                "intentOverride": {
                    "type": ["object", "null"],
                    "properties": {
                        "markerWeights": {"type": "object"},
                        "typeBoost": {"type": "object"},
                        "statusBias": {"type": "object"},
                        "freshnessMultiplier": {"type": "number"},
                        "hopPenalty": {"type": ["number", "null"]},
                        "hopCap": {"type": "integer"},
                        "allowedRelations": {"type": ["array", "null"], "items": {"type": "string"}},
                        "includeArchived": {"type": ["boolean", "null"]},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["recordId"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "markers": {"type": "object"},
                "matchedMarkers": {"type": "array"},
                "matchedTokens": {"type": "array"},
                "factors": {"type": "object"},
                "score": {"type": "number"},
                "rank": {"type": ["number", "null"]},
                "inTopK": {"type": "boolean"},
                "outgoingEdges": {"type": "array"},
                "incomingEdges": {"type": "array"},
            },
            "required": [
                "id",
                "title",
                "markers",
                "matchedMarkers",
                "matchedTokens",
                "factors",
                "score",
            ],
        },
        handler=handle_inspect_record,
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
    ToolSpec(
        name="bootstrap_preview",
        title="Bootstrap Preview",
        description="Sniff the workspace's README, manifests, and top-level dirs to produce a skeleton preview for the curator bootstrap flow. Returns bootstrapNeeded so callers know whether to offer the bootstrap.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
            },
            "required": ["workspaceRoot"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "projectTitle": {"type": "string"},
                "tagline": {"type": "string"},
                "topLevelDirs": {"type": "array"},
                "rootPath": {"type": "string"},
                "bootstrapNeeded": {"type": "boolean"},
            },
            "required": ["projectTitle", "topLevelDirs", "bootstrapNeeded"],
        },
        handler=handle_bootstrap_preview,
    ),
    ToolSpec(
        name="apply_bootstrap_decision",
        title="Apply Bootstrap Decision",
        description="Persist the user's bootstrap decision into workspace.json. decision='accept' requires rootPageId (and optionally rootPageUrl + dirPageIds); decision='decline' sets notion.bootstrapDeclined=true so SessionStart stops nagging.",
        input_schema={
            "type": "object",
            "properties": {
                "workspaceRoot": {"type": "string"},
                "decision": {"type": "string", "enum": ["accept", "decline"]},
                "rootPageId": {"type": "string"},
                "rootPageUrl": {"type": "string"},
                "dirPageIds": {"type": "object"},
            },
            "required": ["workspaceRoot", "decision"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "recorded": {"type": "boolean"},
                "decision": {"type": "string"},
                "rootPageId": {"type": "string"},
            },
            "required": ["recorded", "decision"],
        },
        handler=handle_apply_bootstrap_decision,
    ),
    ToolSpec(
        name="parse_hashtags",
        title="Parse Hashtags",
        description="Translate #word tokens in a query into a markers payload keyed by the schema axis that owns each word. Unknown tags stay in the query verbatim.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "markers": {"type": "object"},
            },
            "required": ["query", "markers"],
        },
        handler=handle_parse_hashtags,
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
