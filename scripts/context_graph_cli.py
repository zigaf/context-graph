from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from context_graph_core import (
    build_context_pack,
    classify_record,
    index_records,
    infer_relations,
    ingest_markdown,
    ingest_notion_export,
    promote_pattern,
    search_graph,
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
            "link-record",
            "build-context-pack",
            "index-records",
            "search-graph",
            "promote-pattern",
            "ingest-markdown",
            "ingest-notion-export",
        ],
        help="Command to execute",
    )
    args = parser.parse_args()
    payload = read_payload()

    if args.command == "classify-record":
        result = classify_record(payload)
    elif args.command == "link-record":
        result = infer_relations(payload)
    elif args.command == "index-records":
        result = index_records(payload)
    elif args.command == "search-graph":
        result = search_graph(payload)
    elif args.command == "promote-pattern":
        result = promote_pattern(payload)
    elif args.command == "ingest-markdown":
        result = ingest_markdown(payload)
    elif args.command == "ingest-notion-export":
        result = ingest_notion_export(payload)
    else:
        result = build_context_pack(payload)

    json.dump(result, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
