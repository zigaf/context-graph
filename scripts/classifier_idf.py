"""IDF stats for the adaptive classifier.

The scorer uses corpus-level token document frequency to reduce the weight of
common terms while keeping missing stats equivalent to uniform weights.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")


def _tokenize(text: str) -> set[str]:
    return {match.group(0) for match in TOKEN_RE.finditer(text.lower())}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_idf_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    token_document_frequency: dict[str, int] = {}
    for record in records:
        text = " ".join(
            [
                str(record.get("title") or ""),
                str(record.get("content") or ""),
            ]
        )
        for token in _tokenize(text):
            token_document_frequency[token] = token_document_frequency.get(token, 0) + 1

    return {
        "corpusSize": len(records),
        "tokenDocumentFrequency": token_document_frequency,
    }


def load_idf_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"corpusSize": 0, "tokenDocumentFrequency": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "corpusSize": int(data.get("corpusSize", 0)),
        "tokenDocumentFrequency": dict(data.get("tokenDocumentFrequency", {})),
    }


def save_idf_stats(path: Path, stats: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1",
        "updatedAt": _now_iso(),
        "corpusSize": int(stats.get("corpusSize", 0)),
        "tokenDocumentFrequency": dict(stats.get("tokenDocumentFrequency", {})),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        f.write("\n")
