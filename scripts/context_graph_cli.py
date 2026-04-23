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


def main() -> int:
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
