"""Structured region extractor for the adaptive classifier.

The adaptive scorer weights explicit metadata more heavily than incidental body
mentions, so records are first split into stable text regions.
"""

from __future__ import annotations

import re
from typing import Any


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
METADATA_HEADINGS = ["Metadata", "Метадані", "Метаданные", "Метаданы"]
METADATA_PATTERN = "|".join(re.escape(heading) for heading in METADATA_HEADINGS)
METADATA_BLOCK_RE = re.compile(
    r"(?ms)^\s*#{1,3}\s*(?:" + METADATA_PATTERN + r")\s*\n"
    r"(.*?)(?=^\s*#|\Z)"
)
REGION_NAMES = ("frontmatter", "metadataBlock", "titleText", "breadcrumb", "body")


def extract_regions(record: dict[str, Any]) -> dict[str, str]:
    structured = record.get("structuredContent")
    if isinstance(structured, dict):
        return {name: str(structured.get(name, "")) for name in REGION_NAMES}

    content = str(record.get("content") or "")
    frontmatter = ""
    frontmatter_match = FRONTMATTER_RE.match(content)
    if frontmatter_match:
        frontmatter = frontmatter_match.group(1).strip()
        content = content[frontmatter_match.end() :]

    metadata_block = ""
    metadata_match = METADATA_BLOCK_RE.search(content)
    if metadata_match:
        metadata_block = metadata_match.group(1).strip()
        content = (content[: metadata_match.start()] + content[metadata_match.end() :]).strip()

    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    breadcrumb = str(metadata.get("parent") or "")

    return {
        "frontmatter": frontmatter,
        "metadataBlock": metadata_block,
        "titleText": str(record.get("title") or ""),
        "breadcrumb": breadcrumb,
        "body": content.strip(),
    }
