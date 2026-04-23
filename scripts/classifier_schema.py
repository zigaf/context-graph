"""Schema loader for the adaptive classifier.

The adaptive classifier reads a merged schema: shipped defaults, accepted
learned values, then a user-curated overlay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _union_list(*lists: list[Any]) -> list[Any]:
    seen: dict[Any, None] = {}
    for values in lists:
        for item in values or []:
            seen.setdefault(item, None)
    return list(seen.keys())


def load_merged_schema(
    overlay_path: Path | None = None,
    learned_path: Path | None = None,
    shipped_path: Path | None = None,
) -> dict[str, Any]:
    shipped = _load_json(shipped_path or (_project_root() / "docs" / "schema.json"))
    learned = _load_json(learned_path)
    overlay = _load_json(overlay_path)
    merged: dict[str, Any] = json.loads(json.dumps(shipped))

    rejected_by_field: dict[str, set[Any]] = {}
    rejected = (learned.get("proposals", {}) or {}).get("rejected", []) or []
    for item in rejected:
        if item.get("field") and item.get("value"):
            rejected_by_field.setdefault(item["field"], set()).add(item["value"])

    merged.setdefault("markers", {})
    for source_markers in (learned.get("accepted", {}) or {}, overlay.get("markers", {}) or {}):
        for field, values in source_markers.items():
            merged["markers"][field] = _union_list(merged["markers"].get(field, []), values)

    for field, rejected_values in rejected_by_field.items():
        if field in merged["markers"]:
            merged["markers"][field] = [
                value for value in merged["markers"][field] if value not in rejected_values
            ]

    merged.setdefault("aliases", {})
    for source_aliases in (overlay.get("aliases", {}) or {},):
        for field, canonicals in source_aliases.items():
            merged["aliases"].setdefault(field, {})
            for canonical, aliases in canonicals.items():
                merged["aliases"][field][canonical] = _union_list(
                    merged["aliases"][field].get(canonical, []),
                    aliases,
                )

    merged.setdefault("relations", {"explicit": [], "inferred": []})
    for source_relations in (overlay.get("relations", {}) or {},):
        for kind in ("explicit", "inferred"):
            merged["relations"][kind] = _union_list(
                merged["relations"].get(kind, []),
                source_relations.get(kind, []),
            )

    if overlay.get("hierarchy"):
        merged["hierarchy"] = overlay["hierarchy"]

    return merged
