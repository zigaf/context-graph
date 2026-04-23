from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from context_graph_core import (
    apply_proposal_decision,
    archive_record,
    build_context_pack,
    classify_record,
    delete_record,
    index_records,
    infer_relations,
    init_workspace,
    ingest_markdown,
    ingest_notion_export,
    learn_schema,
    list_proposals,
    promote_pattern,
    search_graph,
    unarchive_record,
)


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def _run_push_notion(argv: list[str]) -> int:
    """Entry point for the ``push-notion`` subcommand.

    Defaults to ``--dry-run`` so accidental invocation cannot duplicate
    content in Notion. Pass ``--apply`` to actually write.
    """
    sub_parser = argparse.ArgumentParser(
        prog="context-graph push-notion",
        description="Push promoted rules/decisions back to Notion (Python fallback).",
    )
    sub_parser.add_argument("--graph", dest="graph", help="Path to graph.json")
    sub_parser.add_argument(
        "--record-ids",
        dest="record_ids",
        help="Comma-separated record ids to push instead of the default scope.",
    )
    group = sub_parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    group.add_argument("--apply", dest="dry_run", action="store_false")
    sub_parser.add_argument("--workspace-root", dest="workspace_root")
    sub_args = sub_parser.parse_args(argv)

    from notion_sync import push_to_notion  # type: ignore  # noqa: WPS433

    payload: dict[str, Any] = {"dryRun": bool(sub_args.dry_run)}
    if sub_args.graph:
        payload["graphPath"] = sub_args.graph
    if sub_args.workspace_root:
        payload["workspaceRoot"] = sub_args.workspace_root
    if sub_args.record_ids:
        payload["recordIds"] = [rid.strip() for rid in sub_args.record_ids.split(",") if rid.strip()]

    result = push_to_notion(payload)
    json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    # Intercept ``push-notion`` before the main parser so we can keep its own
    # flag surface distinct from the stdin-JSON convention used by the other
    # subcommands.
    if len(sys.argv) >= 2 and sys.argv[1] == "push-notion":
        return _run_push_notion(sys.argv[2:])

    parser = argparse.ArgumentParser(description="Context Graph CLI")
    parser.add_argument(
        "command",
        choices=[
            "classify-record",
            "init-workspace",
            "link-record",
            "build-context-pack",
            "index-records",
            "search-graph",
            "promote-pattern",
            "learn-schema",
            "list-proposals",
            "apply-proposal-decision",
            "ingest-markdown",
            "ingest-notion-export",
            "sync-notion",
            "push-notion",
            "delete-record",
            "archive-record",
            "unarchive-record",
        ],
        help="Command to execute",
    )
    args = parser.parse_args()
    payload = read_payload()

    if args.command == "classify-record":
        result = classify_record(payload)
    elif args.command == "init-workspace":
        result = init_workspace(payload)
    elif args.command == "link-record":
        result = infer_relations(payload)
    elif args.command == "index-records":
        result = index_records(payload)
    elif args.command == "search-graph":
        result = search_graph(payload)
    elif args.command == "promote-pattern":
        result = promote_pattern(payload)
    elif args.command == "learn-schema":
        result = learn_schema(payload)
    elif args.command == "list-proposals":
        result = list_proposals(payload)
    elif args.command == "apply-proposal-decision":
        result = apply_proposal_decision(payload)
    elif args.command == "ingest-markdown":
        result = ingest_markdown(payload)
    elif args.command == "ingest-notion-export":
        result = ingest_notion_export(payload)
    elif args.command == "sync-notion":
        try:
            from notion_sync import sync_notion
        except ImportError as exc:
            result = {
                "error": "notion_sync module not available",
                "detail": str(exc),
            }
        else:
            result = sync_notion(payload)
    elif args.command == "delete-record":
        result = delete_record(payload)
    elif args.command == "archive-record":
        result = archive_record(payload)
    elif args.command == "unarchive-record":
        result = unarchive_record(payload)
    else:
        result = build_context_pack(payload)

    json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
