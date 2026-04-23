from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote


INFERRED_EDGE_TTL_DAYS = 30


# Module-level redactor registry. Redactors receive a shallow copy of a ranked
# context-pack item and may return a new dict. Underlying graph records must
# never be mutated by a redactor; callers in build_context_pack pass copies.
_REDACTORS: list[Callable[[dict[str, Any]], dict[str, Any]]] = []


def register_redactor(fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    _REDACTORS.append(fn)


def clear_redactors() -> None:
    _REDACTORS.clear()


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_TOKEN_RE = re.compile(r"\b(?:sk|pk|Bearer)[-_A-Za-z0-9]{16,}\b")


def strip_obvious_secrets(record: dict[str, Any]) -> dict[str, Any]:
    """Built-in redactor that scrubs emails and obvious API-key-looking tokens
    from a record's ``content`` field. Not auto-registered — users must call
    ``register_redactor(strip_obvious_secrets)`` explicitly to enable it.
    """
    redacted = dict(record)
    content = redacted.get("content")
    if isinstance(content, str) and content:
        cleaned = EMAIL_RE.sub("[redacted-email]", content)
        cleaned = SECRET_TOKEN_RE.sub("[redacted-secret]", cleaned)
        redacted["content"] = cleaned
    return redacted


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
NOTION_PAGE_ID_RE = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", re.IGNORECASE)
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "into",
    "after",
    "before",
    "from",
    "need",
    "this",
    "that",
    "issue",
    "bug",
    "task",
    "rule",
    "flow",
    "design",
    "record",
    "notes",
    "note",
    "callback",
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return project_root() / "data"


def default_graph_path() -> Path:
    return data_dir() / "graph.json"


def load_schema() -> dict[str, Any]:
    schema_path = project_root() / "docs" / "schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def tokenize(text: str) -> set[str]:
    return {match.group(0) for match in TOKEN_RE.finditer(text.lower())}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def recency_score(value: str | None) -> float:
    dt = parse_dt(value)
    if not dt:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 0.0)
    return math.exp(-age_days / 30.0)


def normalize_marker(value: str, allowed: list[str] | None = None) -> str:
    normalized = slugify(value)
    if not allowed:
        return normalized
    for item in allowed:
        if normalized == slugify(item):
            return item
    return normalized


def stable_record_id(record: dict[str, Any]) -> str:
    explicit_id = record.get("id")
    if explicit_id:
        return str(explicit_id)
    source = record.get("source", {})
    for preferred_key in ("path", "url"):
        preferred_value = source.get(preferred_key)
        if preferred_value:
            source_key = "::".join(str(part) for part in [source.get("system"), source.get("space"), preferred_value] if part)
            return f"src:{slugify(source_key)}"
    title = str(record.get("title") or "untitled")
    return f"record:{slugify(title)}"


def graph_template() -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "updatedAt": now_iso(),
        "records": {},
        "edges": [],
        "stats": {
            "recordCount": 0,
            "edgeCount": 0,
        },
    }


def load_graph(graph_path: str | None = None) -> dict[str, Any]:
    path = Path(graph_path) if graph_path else default_graph_path()
    if not path.exists():
        return graph_template()
    with path.open("r", encoding="utf-8") as f:
        graph = json.load(f)
    graph.setdefault("records", {})
    graph.setdefault("edges", [])
    graph.setdefault("stats", {})
    return graph


def write_graph(graph: dict[str, Any], graph_path: str | None = None) -> dict[str, Any]:
    path = Path(graph_path) if graph_path else default_graph_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    graph["updatedAt"] = now_iso()
    graph["stats"] = {
        "recordCount": len(graph.get("records", {})),
        "edgeCount": len(graph.get("edges", [])),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=True, indent=2)
        f.write("\n")
    return graph


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",")]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return value.strip("'\"")


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    block = match.group(1)
    metadata: dict[str, Any] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = parse_scalar(value)
    return metadata, text[match.end() :]


def extract_title_and_content(text: str, fallback_title: str) -> tuple[str, str]:
    match = HEADING_RE.search(text)
    if not match:
        return fallback_title, text.strip()
    title = match.group(1).strip()
    content = (text[: match.start()] + text[match.end() :]).strip()
    return title or fallback_title, content


def guess_title_from_filename(path: Path) -> str:
    stem = NOTION_PAGE_ID_RE.sub("", path.stem)
    stem = re.sub(r"\s{2,}", " ", stem).strip(" -_")
    cleaned = stem.replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else path.stem


def detect_notion_page_id(path: Path) -> str | None:
    match = NOTION_PAGE_ID_RE.search(path.stem)
    return match.group(1).lower() if match else None


def explicit_id_for_markdown_file(path: Path, root_path: Path, system: str) -> str:
    if system == "notion-export":
        notion_id = detect_notion_page_id(path)
        if notion_id:
            return f"notion:{notion_id}"
    relative_path = str(path.relative_to(root_path))
    return f"src:{slugify(f'{system}::{root_path.name}::{relative_path}')}"


def extract_markdown_link_targets(text: str) -> list[str]:
    targets: list[str] = []
    for match in MARKDOWN_LINK_RE.finditer(text):
        target = unquote(match.group(1).strip())
        if not target or target.startswith("#"):
            continue
        lowered = target.lower()
        if lowered.startswith(("http://", "https://", "mailto:", "notion://")):
            continue
        if ".md" not in lowered:
            continue
        targets.append(target.split("#", 1)[0])
    return targets


def markdown_to_text(text: str) -> str:
    text = MARKDOWN_LINK_RE.sub(lambda match: match.group(0).split("](", 1)[0].lstrip("["), text)
    text = re.sub(r"`{1,3}([^`]+)`{1,3}", r"\1", text)
    return text


def normalize_record_input(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    source = dict(normalized.get("source", {}))
    normalized["source"] = source
    normalized.setdefault("relations", {"explicit": [], "inferred": []})
    return normalized


def build_alias_index(schema: dict[str, Any]) -> dict[str, dict[str, str]]:
    aliases = schema.get("aliases", {})
    result: dict[str, dict[str, str]] = {}
    for marker_name, values in aliases.items():
        marker_map: dict[str, str] = {}
        for canonical, alias_list in values.items():
            marker_map[slugify(canonical)] = canonical
            for alias in alias_list:
                marker_map[slugify(alias)] = canonical
        result[marker_name] = marker_map
    return result


def normalize_markers(markers: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    alias_index = build_alias_index(schema)
    marker_schema = schema.get("markers", {})
    for key, value in markers.items():
        if value is None or value == "":
            continue
        if isinstance(value, list):
            normalized[key] = [normalize_markers({key: item}, schema)[key] for item in value]
            continue
        if not isinstance(value, str):
            normalized[key] = value
            continue
        allowed = marker_schema.get(key)
        slug = slugify(value)
        alias_match = alias_index.get(key, {}).get(slug)
        normalized[key] = normalize_marker(alias_match or value, allowed)
    return normalized


def infer_marker_from_text(
    marker_name: str,
    text: str,
    schema: dict[str, Any],
    markers: dict[str, Any],
) -> str | None:
    if markers.get(marker_name):
        return None
    haystack = slugify(text)
    alias_index = build_alias_index(schema).get(marker_name, {})
    candidates = schema.get("markers", {}).get(marker_name, [])
    for candidate in candidates:
        canonical = alias_index.get(slugify(candidate), candidate)
        if re.search(rf"(^|-){re.escape(slugify(canonical))}($|-)", haystack):
            return canonical
    for alias_slug, canonical in alias_index.items():
        if re.search(rf"(^|-){re.escape(alias_slug)}($|-)", haystack):
            return canonical
    return None


def derive_hierarchy(markers: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    order = schema.get("hierarchy", {}).get("preferredOrder", [])
    separator = schema.get("hierarchy", {}).get("pathSeparator", " > ")
    segments = [str(markers[name]) for name in order if markers.get(name)]
    return {
        "segments": segments,
        "path": separator.join(segments),
    }


def classify_record(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    record = normalize_record_input(dict(payload.get("record", payload)))
    title = str(record.get("title") or "")
    content = str(record.get("content") or "")
    text = " ".join(part for part in [title, markdown_to_text(content)] if part).strip()

    markers = normalize_markers(record.get("markers", {}), schema)
    for marker_name in schema.get("record", {}).get("requiredMarkers", []):
        inferred = infer_marker_from_text(marker_name, text, schema, markers)
        if inferred:
            markers[marker_name] = inferred
    for marker_name in schema.get("record", {}).get("optionalMarkers", []):
        inferred = infer_marker_from_text(marker_name, text, schema, markers)
        if inferred:
            markers[marker_name] = inferred

    missing_required = [
        marker_name
        for marker_name in schema.get("record", {}).get("requiredMarkers", [])
        if not markers.get(marker_name)
    ]

    tokens = sorted(tokenize(text))
    hierarchy = derive_hierarchy(markers, schema)
    record_id = stable_record_id(record)
    source = dict(record.get("source", {}))
    revision = {
        "version": int(record.get("revision", {}).get("version", 1)),
        "updatedAt": record.get("revision", {}).get("updatedAt") or record.get("updatedAt") or now_iso(),
    }
    classified = {
        "id": record_id,
        "title": title,
        "content": content,
        "markers": markers,
        "missingRequiredMarkers": missing_required,
        "hierarchy": hierarchy,
        "relations": record.get("relations", {"explicit": [], "inferred": []}),
        "source": source,
        "revision": revision,
        "tokens": tokens,
        "classifiedAt": now_iso(),
    }
    return classified


def marker_overlap(left: dict[str, Any], right: dict[str, Any]) -> tuple[int, list[str]]:
    matched: list[str] = []
    for key, value in left.items():
        if key in right and value and right.get(key) == value:
            matched.append(key)
    return len(matched), matched


@dataclass
class CandidateScore:
    candidate: dict[str, Any]
    score: float
    relation_type: str
    matched_markers: list[str]
    shared_tokens: list[str]


def infer_relations(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    source = classify_record({"record": payload["record"]}, schema)
    candidates = [classify_record({"record": item}, schema) for item in payload.get("candidates", [])]
    min_score = float(payload.get("minScore", 0.25))

    results: list[dict[str, Any]] = []
    source_tokens = set(source["tokens"])

    for candidate in candidates:
        overlap_count, matched_markers = marker_overlap(source["markers"], candidate["markers"])
        shared_tokens = sorted(source_tokens & set(candidate["tokens"]))
        shared_token_score = min(len(shared_tokens) / 8.0, 1.0)
        marker_score = min(overlap_count / 4.0, 1.0)
        structural_score = 0.0
        source_flow = source["markers"].get("flow")
        source_artifact = source["markers"].get("artifact")
        candidate_flow = candidate["markers"].get("flow")
        candidate_artifact = candidate["markers"].get("artifact")
        if source_flow and candidate_artifact and source_flow == candidate_artifact:
            structural_score += 0.5
        if source_artifact and candidate_flow and source_artifact == candidate_flow:
            structural_score += 0.5
        score = round(
            (marker_score * 0.6) + (shared_token_score * 0.25) + (min(structural_score, 1.0) * 0.15),
            3,
        )
        if score < min_score:
            continue
        relation_type = "related_pattern"
        if {"domain", "flow"} <= set(matched_markers):
            relation_type = "same_pattern_as"
        elif "flow" in matched_markers or "artifact" in matched_markers:
            relation_type = "might_affect"
        results.append(
            {
                "id": candidate.get("id"),
                "title": candidate.get("title"),
                "relationType": relation_type,
                "confidence": score,
                "matchedMarkers": matched_markers,
                "sharedTokens": shared_tokens[:12],
            }
        )

    return {
        "record": source,
        "inferredRelations": sorted(results, key=lambda item: item["confidence"], reverse=True),
    }


def extract_query_markers(query: str, schema: dict[str, Any]) -> dict[str, str]:
    markers: dict[str, str] = {}
    for marker_name in schema.get("markers", {}).keys():
        inferred = infer_marker_from_text(marker_name, query, schema, markers)
        if inferred:
            markers[marker_name] = inferred
    return markers


def record_weight(record: dict[str, Any], query_markers: dict[str, str], query_tokens: set[str]) -> tuple[float, list[str]]:
    markers = record.get("markers", {})
    matched_markers = [key for key, value in query_markers.items() if markers.get(key) == value]
    exactness = len(matched_markers) / max(len(query_markers), 1)
    token_overlap = len(query_tokens & set(record.get("tokens", []))) / max(len(query_tokens), 1)

    severity_weight = {
        "critical": 1.0,
        "high": 0.7,
        "medium": 0.4,
        "low": 0.2,
    }.get(markers.get("severity"), 0.0)
    status_weight = {
        "in-progress": 1.0,
        "known-risk": 0.85,
        "new": 0.6,
        "fixed": 0.45,
        "done": 0.35,
        "archived": 0.1,
    }.get(markers.get("status"), 0.25)
    freshness = recency_score(record.get("updatedAt") or record.get("classifiedAt"))

    total = (
        exactness * 0.45
        + token_overlap * 0.2
        + severity_weight * 0.15
        + status_weight * 0.1
        + freshness * 0.1
    )
    return round(total, 3), matched_markers


def build_context_pack(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    query = str(payload.get("query") or "")
    include_archived = bool(payload.get("includeArchived", False))
    raw_records = payload.get("records", [])
    records = [
        classify_record({"record": item}, schema)
        for item in raw_records
        if include_archived or not item.get("archived")
    ]
    query_markers = normalize_markers(payload.get("markers", {}), schema)
    if query:
        query_markers = {**extract_query_markers(query, schema), **query_markers}
    query_tokens = tokenize(query)
    limit = int(payload.get("limit", 8))

    ranked: list[dict[str, Any]] = []
    for record in records:
        score, matched_markers = record_weight(record, query_markers, query_tokens)
        if score <= 0:
            continue
        item: dict[str, Any] = {
            "id": record.get("id"),
            "title": record.get("title"),
            "score": score,
            "matchedMarkers": matched_markers,
            "hierarchyPath": record.get("hierarchy", {}).get("path", ""),
            "markers": record.get("markers", {}),
            "relations": record.get("relations", {}),
        }
        if _REDACTORS:
            # Redactors operate on content, so we surface it only when needed.
            item["content"] = record.get("content")
        ranked.append(item)

    ranked.sort(key=lambda item: item["score"], reverse=True)
    top = ranked[:limit]
    supporting = [item for item in ranked[limit : limit + 5] if item["score"] >= 0.3]

    if _REDACTORS:
        # Apply each registered redactor in order to a shallow copy of each
        # ranked item. The original graph records stay untouched because the
        # ranked items above are freshly built dicts; each redactor is also
        # handed a shallow copy to keep the registry contract explicit.
        def _apply_redactors(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for item in items:
                current = dict(item)
                for redactor in _REDACTORS:
                    current = redactor(dict(current))
                out.append(current)
            return out

        top = _apply_redactors(top)
        supporting = _apply_redactors(supporting)

    return {
        "query": query,
        "queryMarkers": query_markers,
        "directMatches": top,
        "supportingRelations": supporting,
        "promotedRules": [item for item in top if item["markers"].get("type") in {"rule", "decision"}],
        "unresolvedRisks": [
            item
            for item in top
            if item["markers"].get("status") in {"in-progress", "known-risk"}
            and item["markers"].get("severity") in {"critical", "high"}
        ],
        "omittedNearbyCount": max(len(ranked) - len(top) - len(supporting), 0),
        "generatedAt": now_iso(),
    }


def normalize_explicit_relations(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_relations = record.get("relations", {}).get("explicit", [])
    normalized: list[dict[str, Any]] = []
    for item in raw_relations:
        if isinstance(item, str):
            normalized.append({"type": "related_to", "target": item})
            continue
        if isinstance(item, dict) and item.get("target"):
            normalized.append(
                {
                    "type": str(item.get("type") or "related_to"),
                    "target": str(item["target"]),
                    "confidence": item.get("confidence", 1.0),
                }
            )
    return normalized


def strongest_severity(records: list[dict[str, Any]]) -> str | None:
    priority = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    best: tuple[int, str] | None = None
    for record in records:
        severity = record.get("markers", {}).get("severity")
        if severity not in priority:
            continue
        candidate = (priority[severity], severity)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else None


def marker_conflicts(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    conflicts: dict[str, list[str]] = {}
    keys = {key for record in records for key in record.get("markers", {}).keys()}
    for key in sorted(keys):
        values = sorted({str(record.get("markers", {}).get(key)) for record in records if record.get("markers", {}).get(key)})
        if len(values) > 1:
            conflicts[key] = values
    return conflicts


def promotion_quality(common: dict[str, Any], conflicts: dict[str, list[str]], records: list[dict[str, Any]]) -> dict[str, Any]:
    considered_keys = sorted(
        {
            key
            for record in records
            for key, value in record.get("markers", {}).items()
            if value and key in {"type", "goal", "status", "severity", "domain", "flow", "artifact", "project", "room", "scope"}
        }
    )
    consensus_count = len([key for key in considered_keys if key in common])
    conflict_count = len(conflicts)
    considered_count = len(considered_keys)
    score = round((consensus_count + max(considered_count - consensus_count - conflict_count, 0) * 0.5) / max(considered_count, 1), 3)
    if conflict_count == 0:
        recommendation = "safe"
    elif conflict_count <= 2:
        recommendation = "review"
    else:
        recommendation = "split"
    return {
        "score": score,
        "recommendation": recommendation,
        "consensusMarkerCount": consensus_count,
        "conflictMarkerCount": conflict_count,
        "consideredMarkerCount": considered_count,
        "conflicts": conflicts,
    }


def split_suggestions(records: list[dict[str, Any]], conflicts: dict[str, list[str]], limit: int = 3) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    preferred_markers = ["type", "goal", "artifact", "status", "severity", "flow", "domain"]
    ordered_markers = preferred_markers + [key for key in conflicts.keys() if key not in preferred_markers]
    seen = set()

    for marker in ordered_markers:
        if marker not in conflicts or marker in seen:
            continue
        seen.add(marker)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            value = record.get("markers", {}).get(marker)
            if value:
                grouped.setdefault(str(value), []).append(record)
        if len(grouped) <= 1:
            continue
        groups = []
        for value, group_records in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
            shared = common_markers(group_records)
            groups.append(
                {
                    "value": value,
                    "recordIds": [record["id"] for record in group_records],
                    "recordCount": len(group_records),
                    "sharedMarkers": shared,
                    "suggestedTitle": generate_promoted_title(shared, len(group_records), shared.get("type", "rule")),
                }
            )
        suggestions.append(
            {
                "marker": marker,
                "groupCount": len(groups),
                "groups": groups,
            }
        )
        if len(suggestions) >= limit:
            break

    return suggestions


def common_markers(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {}
    keys = set(records[0].get("markers", {}).keys())
    for record in records[1:]:
        keys &= set(record.get("markers", {}).keys())
    result: dict[str, Any] = {}
    for key in sorted(keys):
        values = {record.get("markers", {}).get(key) for record in records}
        if len(values) == 1:
            result[key] = values.pop()
    return result


def majority_marker(records: list[dict[str, Any]], key: str) -> str | None:
    values = [record.get("markers", {}).get(key) for record in records if record.get("markers", {}).get(key)]
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def shared_keywords(records: list[dict[str, Any]], limit: int = 8) -> list[str]:
    if not records:
        return []
    token_sets = []
    for record in records:
        filtered = {token for token in record.get("tokens", []) if token not in STOPWORDS and len(token) > 2}
        token_sets.append(filtered)
    shared = set.intersection(*token_sets) if token_sets else set()
    if shared:
        return sorted(shared)[:limit]
    counts = Counter()
    for token_set in token_sets:
        counts.update(token_set)
    return [token for token, _ in counts.most_common(limit)]


def merge_record(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return current
    prev_edited = previous.get("source", {}).get("metadata", {}).get("last_edited_time")
    curr_edited = current.get("source", {}).get("metadata", {}).get("last_edited_time")
    if prev_edited and curr_edited:
        prev_dt = parse_dt(prev_edited)
        curr_dt = parse_dt(curr_edited)
        if prev_dt and curr_dt and curr_dt < prev_dt:
            return previous
    revision_version = int(previous.get("revision", {}).get("version", 1)) + 1
    merged = dict(current)
    merged["revision"] = {
        "version": revision_version,
        "updatedAt": now_iso(),
    }
    return merged


def generate_promoted_title(common: dict[str, Any], record_count: int, output_type: str) -> str:
    parts = []
    if common.get("domain"):
        parts.append(str(common["domain"]).replace("-", " "))
    if common.get("flow"):
        parts.append(str(common["flow"]).replace("-", " "))
    if output_type == "decision":
        parts.append("decision")
    else:
        parts.append("rule")
    if not parts:
        return f"Promoted pattern from {record_count} records"
    return " ".join(word.capitalize() for word in parts)


def promote_pattern(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    graph_path = payload.get("graphPath")
    graph = load_graph(graph_path) if graph_path else graph_template()
    graph_records = graph.get("records", {})
    input_records = [classify_record({"record": item}, schema) for item in payload.get("records", [])]

    requested_ids = [str(item) for item in payload.get("recordIds", [])]
    selected_graph_records = [graph_records[item] for item in requested_ids if item in graph_records]
    source_records = selected_graph_records + input_records
    if not source_records:
        raise ValueError("promote_pattern requires recordIds or records.")

    common = common_markers(source_records)
    output_type = str(payload.get("outputType") or "rule")
    title = str(payload.get("title") or generate_promoted_title(common, len(source_records), output_type))
    record_id = str(payload.get("id") or f"promoted:{slugify(title)}")
    keywords = shared_keywords(source_records)
    highest_severity = strongest_severity(source_records)
    goal = str(payload.get("goal") or majority_marker(source_records, "goal") or "prevent-regression")
    conflicts = marker_conflicts(source_records)
    quality = promotion_quality(common, conflicts, source_records)
    suggestions = split_suggestions(source_records, conflicts)

    promoted_markers = {
        "type": output_type,
        "goal": goal,
        "status": "done",
    }
    for key in ("domain", "flow", "artifact", "project", "room", "scope"):
        if common.get(key):
            promoted_markers[key] = common[key]
    if highest_severity:
        promoted_markers["severity"] = highest_severity

    summary_lines = [
        f"Promoted from {len(source_records)} related records.",
        f"Promotion quality: {quality['recommendation']} ({quality['score']}).",
        "",
        "Common markers:",
    ]
    for key, value in sorted(common.items()):
        summary_lines.append(f"- {key}: {value}")
    if conflicts:
        summary_lines.extend(["", "Conflicts:"])
        for key, values in sorted(conflicts.items()):
            summary_lines.append(f"- {key}: {', '.join(values)}")
    if suggestions:
        summary_lines.extend(["", "Split suggestions:"])
        for suggestion in suggestions:
            summary_lines.append(f"- by {suggestion['marker']}: {suggestion['groupCount']} groups")
    if keywords:
        summary_lines.extend(["", "Shared keywords:", f"- {', '.join(keywords)}"])
    summary_lines.extend(
        [
            "",
            "Derived from:",
            *[f"- {record['id']}: {record.get('title', '')}" for record in source_records],
        ]
    )

    promoted_record = classify_record(
        {
            "record": {
                "id": record_id,
                "title": title,
                "content": "\n".join(summary_lines).strip(),
                "markers": promoted_markers,
                "relations": {
                    "explicit": [{"type": "derived_from", "target": record["id"]} for record in source_records],
                    "inferred": [],
                },
                "source": {
                    "system": "context-graph",
                    "path": f"promoted/{slugify(title)}.json",
                    "generatedBy": "promote_pattern",
                    "metadata": {
                        "promotionQuality": quality,
                        "splitSuggestions": suggestions,
                    },
                },
            }
        },
        schema,
    )
    for marker_name in ("artifact", "project", "room", "scope"):
        if marker_name not in promoted_markers and marker_name in promoted_record["markers"]:
            promoted_record["markers"].pop(marker_name, None)
    promoted_record["hierarchy"] = derive_hierarchy(promoted_record["markers"], schema)

    result = {
        "promotedRecord": promoted_record,
        "sourceRecords": [{"id": record["id"], "title": record.get("title", "")} for record in source_records],
        "sharedKeywords": keywords,
        "commonMarkers": common,
        "quality": quality,
        "splitSuggestions": suggestions,
    }

    if payload.get("writeToGraph"):
        index_result = index_records(
            {
                "graphPath": graph_path,
                "records": [promoted_record],
            },
            schema,
        )
        result["indexResult"] = index_result

    return result


def rebuild_edges(
    records: dict[str, dict[str, Any]],
    schema: dict[str, Any],
    existing_edges: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    record_list = list(records.values())

    # Build a lookup of prior inferred edges so we preserve their ``createdAt``
    # stamp when the same (source, target, type) edge still applies. This keeps
    # TTL age stable across rebuilds of unchanged edges. Explicit edges do not
    # carry ``createdAt`` because they are never filtered by TTL.
    prior_inferred: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    if existing_edges:
        for edge in existing_edges:
            if edge.get("kind") == "inferred":
                key = (edge.get("source"), edge.get("target"), edge.get("type"))
                prior_inferred[key] = edge

    for record in record_list:
        source_id = record["id"]
        for relation in normalize_explicit_relations(record):
            key = (source_id, relation["target"], relation["type"])
            edges[key] = {
                "source": source_id,
                "target": relation["target"],
                "type": relation["type"],
                "kind": "explicit",
                "confidence": relation.get("confidence", 1.0),
                "updatedAt": now_iso(),
            }

    for record in record_list:
        source_id = record["id"]
        candidates = [item for item in record_list if item["id"] != source_id]
        inferred = infer_relations(
            {
                "record": record,
                "candidates": candidates,
                "minScore": 0.3,
            },
            schema,
        )
        for relation in inferred["inferredRelations"]:
            key = (source_id, relation["id"], relation["relationType"])
            existing = edges.get(key)
            if existing and existing["kind"] == "explicit":
                continue
            now = now_iso()
            prior = prior_inferred.get(key)
            created_at = prior.get("createdAt") if prior and prior.get("createdAt") else now
            edges[key] = {
                "source": source_id,
                "target": relation["id"],
                "type": relation["relationType"],
                "kind": "inferred",
                "confidence": relation["confidence"],
                "matchedMarkers": relation.get("matchedMarkers", []),
                "sharedTokens": relation.get("sharedTokens", []),
                "createdAt": created_at,
                "updatedAt": now,
            }

    return sorted(edges.values(), key=lambda item: (item["source"], item["kind"], -item["confidence"], item["target"]))


def markdown_record_from_file(
    path: Path,
    root_path: Path,
    schema: dict[str, Any],
    *,
    system: str = "markdown",
    known_targets: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(raw_text)
    fallback_title = guess_title_from_filename(path)
    title, content = extract_title_and_content(body, fallback_title)
    record_id = explicit_id_for_markdown_file(path, root_path, system)

    markers: dict[str, Any] = {}
    for key in list(schema.get("markers", {}).keys()) + ["project", "room", "scope", "owner"]:
        if key in front_matter:
            markers[key] = front_matter.pop(key)

    explicit_relations = []
    relates_to = front_matter.pop("relates_to", None)
    if isinstance(relates_to, list):
        explicit_relations = [{"type": "related_to", "target": str(item)} for item in relates_to]
    if known_targets is not None:
        for raw_target in extract_markdown_link_targets(raw_text):
            candidate_path = (path.parent / raw_target).resolve()
            try:
                relative_target = str(candidate_path.relative_to(root_path.resolve()))
            except ValueError:
                continue
            target_id = known_targets.get(relative_target)
            if target_id and target_id != record_id:
                explicit_relations.append({"type": "related_to", "target": target_id})

    record = {
        "id": record_id,
        "title": str(front_matter.pop("title", title)),
        "content": content,
        "markers": markers,
        "relations": {"explicit": explicit_relations, "inferred": []},
        "source": {
            "system": system,
            "space": root_path.name,
            "path": str(path.relative_to(root_path)),
            "url": str(path),
        },
    }
    if system == "notion-export":
        notion_id = detect_notion_page_id(path)
        if notion_id:
            record["source"].setdefault("metadata", {})
            record["source"]["metadata"]["notionPageId"] = notion_id
    if front_matter:
        record["source"].setdefault("metadata", {})
        record["source"]["metadata"].update(front_matter)
    return classify_record({"record": record}, schema)


def collect_markdown_records(
    root_path: Path,
    schema: dict[str, Any],
    *,
    system: str,
    pattern: str,
    recursive: bool,
) -> tuple[list[Path], Path, list[dict[str, Any]]]:
    if root_path.is_file():
        files = [root_path]
        scan_root = root_path.parent
    else:
        scan_root = root_path
        iterator = scan_root.rglob(pattern) if recursive else scan_root.glob(pattern)
        files = sorted(path for path in iterator if path.is_file())

    known_targets = {
        str(path.relative_to(scan_root)): explicit_id_for_markdown_file(path, scan_root, system)
        for path in files
    }
    records = [
        markdown_record_from_file(path, scan_root, schema, system=system, known_targets=known_targets)
        for path in files
    ]
    return files, scan_root, records


def index_records(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    graph_path = payload.get("graphPath")
    graph = load_graph(graph_path)
    existing_records = dict(graph.get("records", {}))
    input_records = payload.get("records", [])

    upserted_ids: list[str] = []
    for item in input_records:
        classified = classify_record({"record": item}, schema)
        merged = merge_record(existing_records.get(classified["id"]), classified)
        existing_records[merged["id"]] = merged
        upserted_ids.append(merged["id"])

    prior_edges = list(graph.get("edges", []))
    graph["records"] = existing_records
    graph["edges"] = rebuild_edges(existing_records, schema, prior_edges)
    write_graph(graph, graph_path)

    return {
        "graphPath": str(Path(graph_path) if graph_path else default_graph_path()),
        "upsertedIds": upserted_ids,
        "recordCount": len(graph["records"]),
        "edgeCount": len(graph["edges"]),
        "updatedAt": graph["updatedAt"],
    }


def ingest_markdown(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    root_value = payload.get("rootPath") or payload.get("path")
    if not root_value:
        raise ValueError("ingest_markdown requires rootPath.")

    root_path = Path(str(root_value)).expanduser()
    if not root_path.exists():
        raise ValueError(f"Path does not exist: {root_path}")
    recursive = bool(payload.get("recursive", True))
    pattern = str(payload.get("pattern", "*.md"))
    files, scan_root, records = collect_markdown_records(
        root_path,
        schema,
        system="markdown",
        pattern=pattern,
        recursive=recursive,
    )
    result = {
        "rootPath": str(scan_root),
        "fileCount": len(files),
        "records": records,
        "recordIds": [record["id"] for record in records],
    }

    if payload.get("index", True):
        index_result = index_records(
            {
                "graphPath": payload.get("graphPath"),
                "records": records,
            },
            schema,
        )
        result["indexResult"] = index_result

    return result


def ingest_notion_export(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    root_value = payload.get("rootPath") or payload.get("path")
    if not root_value:
        raise ValueError("ingest_notion_export requires rootPath.")

    root_path = Path(str(root_value)).expanduser()
    if not root_path.exists():
        raise ValueError(f"Path does not exist: {root_path}")

    recursive = bool(payload.get("recursive", True))
    pattern = str(payload.get("pattern", "*.md"))
    files, scan_root, records = collect_markdown_records(
        root_path,
        schema,
        system="notion-export",
        pattern=pattern,
        recursive=recursive,
    )
    result = {
        "rootPath": str(scan_root),
        "fileCount": len(files),
        "records": records,
        "recordIds": [record["id"] for record in records],
    }

    if payload.get("index", True):
        index_result = index_records(
            {
                "graphPath": payload.get("graphPath"),
                "records": records,
            },
            schema,
        )
        result["indexResult"] = index_result

    return result


def _edge_survives_ttl(edge: dict[str, Any], ttl_days: float, now: datetime) -> bool:
    """Return True if the edge should remain visible at read time. Explicit
    edges are never filtered by TTL. Inferred edges without a parseable
    ``createdAt`` are kept as well — callers should not silently discard data
    just because a stamp is missing.
    """
    if edge.get("kind") != "inferred":
        return True
    if ttl_days is None or ttl_days <= 0:
        return True
    created = parse_dt(edge.get("createdAt"))
    if not created:
        return True
    age_days = (now - created).total_seconds() / 86400.0
    return age_days <= ttl_days


def search_graph(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    graph_path = payload.get("graphPath")
    graph = load_graph(graph_path)
    include_archived = bool(payload.get("includeArchived", False))
    all_records = list(graph.get("records", {}).values())
    visible_records = [
        record for record in all_records if include_archived or not record.get("archived")
    ]
    ttl_days_raw = payload.get("inferredEdgeTtlDays", INFERRED_EDGE_TTL_DAYS)
    try:
        ttl_days = float(ttl_days_raw) if ttl_days_raw is not None else float(INFERRED_EDGE_TTL_DAYS)
    except (TypeError, ValueError):
        ttl_days = float(INFERRED_EDGE_TTL_DAYS)

    context_pack = build_context_pack(
        {
            "query": payload.get("query", ""),
            "markers": payload.get("markers", {}),
            "records": visible_records,
            "limit": payload.get("limit", 8),
            "includeArchived": include_archived,
        },
        schema,
    )

    visible_ids = {record.get("id") for record in visible_records}
    now = datetime.now(timezone.utc)
    edge_lookup: dict[str, list[dict[str, Any]]] = {}
    for edge in graph.get("edges", []):
        if not include_archived and (edge.get("source") not in visible_ids or edge.get("target") not in visible_ids):
            # Drop edges that point at or from archived records so they never
            # leak into supporting-relations lookups either.
            continue
        if not _edge_survives_ttl(edge, ttl_days, now):
            continue
        edge_lookup.setdefault(edge["source"], []).append(edge)

    for item in context_pack["directMatches"]:
        item["outgoingEdges"] = edge_lookup.get(item["id"], [])[:10]

    context_pack["graphStats"] = graph.get("stats", {})
    context_pack["graphPath"] = str(Path(graph_path) if graph_path else default_graph_path())
    return context_pack


def delete_record(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    record_id = payload.get("recordId")
    if not record_id:
        raise ValueError("delete_record requires recordId.")
    record_id = str(record_id)
    graph_path = payload.get("graphPath")
    graph = load_graph(graph_path)
    records = dict(graph.get("records", {}))
    resolved_path = str(Path(graph_path) if graph_path else default_graph_path())

    if record_id not in records:
        return {
            "deletedId": record_id,
            "notFound": True,
            "recordCount": len(records),
            "edgeCount": len(graph.get("edges", [])),
            "graphPath": resolved_path,
            "updatedAt": graph.get("updatedAt", now_iso()),
        }

    records.pop(record_id, None)

    # Simplified partial rebuild: drop every edge touching the deleted id and
    # re-run rebuild_edges over the remaining records. rebuild_edges is
    # idempotent, so any inferred edges between the surviving records are
    # preserved along with their createdAt stamps via existing_edges.
    remaining_edges = [
        edge for edge in graph.get("edges", [])
        if edge.get("source") != record_id and edge.get("target") != record_id
    ]
    graph["records"] = records
    graph["edges"] = rebuild_edges(records, schema, remaining_edges)
    write_graph(graph, graph_path)

    return {
        "deletedId": record_id,
        "notFound": False,
        "recordCount": len(graph["records"]),
        "edgeCount": len(graph["edges"]),
        "graphPath": resolved_path,
        "updatedAt": graph["updatedAt"],
    }


def _set_archived(payload: dict[str, Any], archived: bool) -> dict[str, Any]:
    record_id = payload.get("recordId")
    if not record_id:
        raise ValueError("archive_record/unarchive_record requires recordId.")
    record_id = str(record_id)
    graph_path = payload.get("graphPath")
    graph = load_graph(graph_path)
    records = graph.get("records", {})
    resolved_path = str(Path(graph_path) if graph_path else default_graph_path())

    record = records.get(record_id)
    if not record:
        return {
            "recordId": record_id,
            "archived": archived,
            "notFound": True,
            "graphPath": resolved_path,
            "updatedAt": graph.get("updatedAt", now_iso()),
        }

    if archived:
        record["archived"] = True
    else:
        record.pop("archived", None)
    records[record_id] = record

    # Edges intentionally untouched: archiving hides the record at read time
    # without destroying its relationships.
    graph["records"] = records
    write_graph(graph, graph_path)

    return {
        "recordId": record_id,
        "archived": archived,
        "graphPath": resolved_path,
        "updatedAt": graph["updatedAt"],
    }


def archive_record(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return _set_archived(payload, True)


def unarchive_record(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return _set_archived(payload, False)
