from __future__ import annotations

import json
import math
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from classifier_idf import compute_idf_from_records, load_idf_stats, save_idf_stats
from classifier_learning import run_full_pass
from classifier_regions import extract_regions
from classifier_schema import load_merged_schema
from classifier_scorer import arbitrate, score_field


class WorkspaceNotInitializedError(RuntimeError):
    """Raised when a context-graph operation needs a workspace but none is found."""


WORKSPACE_MARKER = ".context-graph/workspace.json"


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (defaults to CWD) looking for .context-graph/workspace.json.

    Returns the directory that contains .context-graph/, or None if no marker is
    found up to the filesystem root.
    """
    current = Path(start).resolve() if start is not None else Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / WORKSPACE_MARKER).exists():
            return candidate
    return None


def require_workspace(start: Path | None = None) -> Path:
    """Like `find_workspace_root` but raises if none is found."""
    root = find_workspace_root(start)
    if root is None:
        raise WorkspaceNotInitializedError(
            "No Context Graph workspace found. Run /cg-init to initialize."
        )
    return root


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


def _legacy_mode() -> bool:
    return os.environ.get("CONTEXT_GRAPH_LEGACY_PLUGIN_DATA") == "1"


def _resolve_workspace_file(filename: str, start: Path | None = None) -> Path:
    if _legacy_mode():
        plugin_data = project_root() / "data"
        return plugin_data / filename
    root = require_workspace(start)
    return root / ".context-graph" / filename


def default_graph_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("graph.json", start)


def schema_learned_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("schema.learned.json", start)


def schema_overlay_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("schema.overlay.json", start)


def schema_feedback_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("schema.feedback.json", start)


def idf_stats_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("idf_stats.json", start)


def notion_cursor_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("notion_cursor.json", start)


def load_notion_cursor(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Read the per-page Notion cursor for a workspace.

    Returns `{}` when the file is missing, unparseable, or not a dict. The
    cursor maps `page_id -> last_edited_time` (ISO-8601 strings); callers should
    treat an absent key as "never seen".
    """
    start = Path(workspace_root) if workspace_root is not None else None
    path = notion_cursor_path(start)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_notion_cursor(
    cursor: dict[str, Any], workspace_root: Path | str | None = None
) -> None:
    """Persist the per-page Notion cursor for a workspace.

    Overwrites the file; parent directories are created as needed.
    """
    start = Path(workspace_root) if workspace_root is not None else None
    path = notion_cursor_path(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cursor, f, ensure_ascii=True, indent=2)
        f.write("\n")


def cursor_is_fresh(page: dict[str, Any], cursor: dict[str, Any]) -> bool:
    """Return True when `page` has been edited since the cursor last saw it.

    Pure function. ISO-8601 zulu timestamps sort correctly as strings, so we
    avoid datetime parsing here. A page missing either `id` or
    `last_edited_time` is treated as fresh (we err toward refetching).
    """
    page_id = page.get("id") if isinstance(page, dict) else None
    last_edited = page.get("last_edited_time") if isinstance(page, dict) else None
    if not page_id or not last_edited:
        return True
    stored = cursor.get(str(page_id), "") if isinstance(cursor, dict) else ""
    return str(last_edited) > str(stored or "")


def update_cursor(cursor: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    """Return a new cursor dict advanced to `page`'s `last_edited_time`.

    Pure function — does not mutate `cursor`. A page whose `last_edited_time`
    is not strictly newer than the stored value is a no-op (the returned dict
    is a fresh copy but unchanged). Pages missing `id` or `last_edited_time`
    are ignored.
    """
    result = dict(cursor) if isinstance(cursor, dict) else {}
    if not isinstance(page, dict):
        return result
    page_id = page.get("id")
    last_edited = page.get("last_edited_time")
    if not page_id or not last_edited:
        return result
    existing = result.get(str(page_id), "")
    if str(last_edited) > str(existing or ""):
        result[str(page_id)] = str(last_edited)
    return result


def filter_pages_by_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    """Split a page list into fresh (needs fetch) and stale (skip) buckets.

    Pure function. Does not touch disk or mutate inputs. The `newCursorHint`
    is the max `last_edited_time` across the fresh pages, or None when no
    page is fresh — a convenience for callers that want to advance the
    cursor after a successful fetch cycle.
    """
    pages = payload.get("pages") if isinstance(payload, dict) else None
    cursor = payload.get("cursor") if isinstance(payload, dict) else None
    pages_list = list(pages) if isinstance(pages, list) else []
    cursor_dict = cursor if isinstance(cursor, dict) else {}

    fresh: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    newest: str | None = None

    for page in pages_list:
        if not isinstance(page, dict):
            continue
        if cursor_is_fresh(page, cursor_dict):
            fresh.append(page)
            last_edited = page.get("last_edited_time")
            if isinstance(last_edited, str) and (newest is None or last_edited > newest):
                newest = last_edited
        else:
            stale.append(page)

    return {
        "fresh": fresh,
        "stale": stale,
        "newCursorHint": newest,
    }


def push_state_path(start: Path | None = None) -> Path:
    """Path to the per-workspace Notion push-state mapping.

    Mirrors the other workspace path helpers. Content shape:
    ``{record_id: notion_page_id}``. Missing file is treated as an empty
    mapping by ``load_push_state``.
    """
    return _resolve_workspace_file("notion_push.json", start)


GITIGNORE_ENTRIES = [
    ".context-graph/graph.json",
    ".context-graph/schema.learned.json",
    ".context-graph/schema.feedback.json",
    ".context-graph/idf_stats.json",
    ".context-graph/notion_cursor.json",
    ".context-graph/notion_push.json",
]


def _ensure_gitignore(root: Path) -> None:
    gitignore_path = root / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    lines = existing.splitlines()
    missing_entries = [entry for entry in GITIGNORE_ENTRIES if entry not in lines]
    if not missing_entries:
        return

    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing:
        existing += "\n"
    existing += "# Context Graph (local workspace state)\n"
    existing += "\n".join(missing_entries) + "\n"
    gitignore_path.write_text(existing, encoding="utf-8")


def init_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    root_value = payload.get("rootPath") or str(Path.cwd().resolve())
    root = Path(str(root_value)).expanduser().resolve()
    context_dir = root / ".context-graph"
    manifest_path = context_dir / "workspace.json"
    if manifest_path.exists():
        raise ValueError(f"Workspace already initialized at {root}.")

    context_dir.mkdir(parents=True, exist_ok=True)
    created_at = now_iso()
    manifest: dict[str, Any] = {
        "version": "1",
        "id": f"ws-{uuid.uuid4().hex[:12]}",
        "rootPath": str(root),
        "createdAt": created_at,
        "updatedAt": created_at,
    }

    notion_page_id = payload.get("notionRootPageId")
    notion_page_url = payload.get("notionRootPageUrl")
    if notion_page_id:
        manifest["notion"] = {
            "rootPageId": str(notion_page_id),
            "rootPageUrl": str(notion_page_url) if notion_page_url else None,
            "createdAt": created_at,
        }

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=True, indent=2)
        f.write("\n")

    _ensure_gitignore(root)

    return {
        "rootPath": str(root),
        "workspaceId": manifest["id"],
        "manifestPath": str(manifest_path),
        "notion": manifest.get("notion"),
    }


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


def _workspace_from_payload(payload: dict[str, Any]) -> Path:
    if payload.get("workspaceRoot"):
        return Path(str(payload["workspaceRoot"])).expanduser().resolve()
    return require_workspace()


def _load_learned(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": "1",
            "proposals": {"pending": [], "rejected": []},
            "accepted": {},
        }
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("version", "1")
    data.setdefault("proposals", {})
    data["proposals"].setdefault("pending", [])
    data["proposals"].setdefault("rejected", [])
    data.setdefault("accepted", {})
    return data


def _save_learned(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data.setdefault("version", "1")
    data["updatedAt"] = now_iso()
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
        f.write("\n")


def learn_schema(payload: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_payload(payload)
    graph_path = payload.get("graphPath") or str(default_graph_path(workspace))
    graph = load_graph(graph_path)
    records = list(graph.get("records", {}).values())
    analysis = run_full_pass(records)

    learned_path = schema_learned_path(workspace)
    learned = _load_learned(learned_path)
    accepted_values = {
        value
        for values in learned.get("accepted", {}).values()
        for value in values
    }
    rejected_values = {
        item.get("value")
        for item in learned["proposals"].get("rejected", [])
        if item.get("value")
    }
    pending = list(learned["proposals"].get("pending", []))
    seen_pending_values = {item.get("value") for item in pending}

    for strategy in ("hierarchy", "ngram", "codePath"):
        for proposal in analysis["proposals"].get(strategy, []):
            value = proposal.get("value")
            if not value or value in accepted_values or value in rejected_values or value in seen_pending_values:
                continue
            pending.append(proposal)
            seen_pending_values.add(value)

    learned["proposals"]["pending"] = pending
    learned["corpusSize"] = analysis["corpusSize"]
    learned["markerImportance"] = analysis["markerImportance"]
    _save_learned(learned_path, learned)

    return {
        "workspaceRoot": str(workspace),
        "proposals": analysis["proposals"],
        "pendingCount": len(pending),
        "markerImportance": analysis["markerImportance"],
        "corpusSize": analysis["corpusSize"],
    }


def list_proposals(payload: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_payload(payload)
    learned = _load_learned(schema_learned_path(workspace))
    return {
        "pending": learned["proposals"]["pending"],
        "accepted": learned["accepted"],
        "rejected": learned["proposals"]["rejected"],
    }


def apply_proposal_decision(payload: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_payload(payload)
    value = str(payload.get("value") or "")
    decision = str(payload.get("decision") or "")
    field = payload.get("field")
    if decision not in {"accept", "reject", "skip"}:
        raise ValueError("decision must be one of accept/reject/skip")
    if decision == "accept" and not field:
        raise ValueError("field is required when decision=accept")
    if not value:
        raise ValueError("value is required")

    learned_path = schema_learned_path(workspace)
    learned = _load_learned(learned_path)
    remaining = [
        proposal
        for proposal in learned["proposals"]["pending"]
        if proposal.get("value") != value
    ]

    if decision == "accept":
        field_name = str(field)
        learned["accepted"].setdefault(field_name, [])
        if value not in learned["accepted"][field_name]:
            learned["accepted"][field_name].append(value)
    elif decision == "reject":
        learned["proposals"]["rejected"].append(
            {
                "value": value,
                "field": field,
                "rejectedAt": now_iso(),
            }
        )

    learned["proposals"]["pending"] = remaining
    _save_learned(learned_path, learned)
    return {
        "value": value,
        "decision": decision,
        "field": field,
        "remainingPending": len(remaining),
    }


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


def _load_schema_for(workspace_start: Path | None) -> dict[str, Any]:
    try:
        overlay = schema_overlay_path(workspace_start)
    except WorkspaceNotInitializedError:
        overlay = None
    try:
        learned = schema_learned_path(workspace_start)
    except WorkspaceNotInitializedError:
        learned = None
    return load_merged_schema(overlay_path=overlay, learned_path=learned)


def _load_idf_for(workspace_start: Path | None) -> dict[str, Any]:
    try:
        path = idf_stats_path(workspace_start)
    except WorkspaceNotInitializedError:
        return {"corpusSize": 0, "tokenDocumentFrequency": {}}
    return load_idf_stats(path)


def _required_fields(schema: dict[str, Any]) -> list[str]:
    return list((schema.get("record", {}) or {}).get("requiredMarkers", []) or [])


def _merge_source(record: dict[str, Any], extra_metadata: dict[str, Any]) -> dict[str, Any]:
    source = dict(record.get("source") or {})
    metadata = dict(source.get("metadata") or {})
    metadata.update(extra_metadata)
    source["metadata"] = metadata
    return source


def _build_arbitration_request(
    record: dict[str, Any],
    regions: dict[str, str],
    scores_by_field: dict[str, list[dict[str, Any]]],
    arbitration_needed: list[dict[str, Any]],
    schema: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    field_names = [item["field"] for item in arbitration_needed]
    return {
        "recordId": stable_record_id(record),
        "record": {
            "title": str(record.get("title") or ""),
            "breadcrumb": regions.get("breadcrumb", ""),
            "frontmatter": regions.get("frontmatter", ""),
            "metadataBlock": regions.get("metadataBlock", ""),
            "bodyPreview": (regions.get("body") or "")[:2000],
        },
        "candidates": {field: scores_by_field.get(field, []) for field in field_names},
        "allowedValues": {
            field: list((schema.get("markers", {}) or {}).get(field, []))
            for field in field_names
        },
        "requiredFields": required,
        "instructions": (
            "Pick the single best value per field from allowedValues. Return null "
            "only if truly nothing fits. Required fields should not be null unless "
            "absolutely necessary."
        ),
    }


def classify_record(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    workspace_start = Path(str(payload["workspaceRoot"])).resolve() if payload.get("workspaceRoot") else None
    schema = schema or _load_schema_for(workspace_start)
    idf_stats = _load_idf_for(workspace_start)
    idf = dict(idf_stats.get("tokenDocumentFrequency", {}))
    record = normalize_record_input(dict(payload.get("record", payload)))
    title = str(record.get("title") or "")
    content = str(record.get("content") or "")
    regions = extract_regions(record)

    markers = normalize_markers(record.get("markers", {}), schema)
    scores_by_field: dict[str, list[dict[str, Any]]] = {}
    arbitration_needed: list[dict[str, Any]] = []

    for marker_name in (schema.get("markers", {}) or {}).keys():
        if markers.get(marker_name):
            continue
        scores = score_field(marker_name, regions, schema, idf)
        scores_by_field[marker_name] = scores[:5]
        decision = arbitrate(scores)
        if decision["value"]:
            markers[marker_name] = decision["value"]
        if decision["arbiter"] == "pending-arbitration":
            top = decision.get("top") or {}
            arbitration_needed.append(
                {
                    "field": marker_name,
                    "reason": decision.get("reason", "ambiguous"),
                    "topScore": top.get("score", 0.0),
                    "gap": decision.get("gap", 0.0),
                }
            )

    required = _required_fields(schema)
    missing_required = [marker_name for marker_name in required if not markers.get(marker_name)]
    tokens = sorted(
        {
            token
            for region_text in regions.values()
            for token in tokenize(markdown_to_text(region_text or ""))
        }
    )
    record_id = stable_record_id(record)
    regions_used = [name for name, text in regions.items() if text]
    if arbitration_needed:
        overall_arbiter = "pending-arbitration"
    elif markers:
        overall_arbiter = "deterministic"
    else:
        overall_arbiter = "fallback"

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
        "hierarchy": derive_hierarchy(markers, schema),
        "relations": record.get("relations", {"explicit": [], "inferred": []}),
        "source": _merge_source(
            record,
            {
                "classifierVersion": "2",
                "classifierNotes": {
                    "classifierVersion": "2",
                    "arbiter": overall_arbiter,
                    "regionsUsed": regions_used,
                    "scores": scores_by_field,
                    "reasoning": None,
                },
            },
        ),
        "revision": revision,
        "tokens": tokens,
        "classifiedAt": now_iso(),
    }
    if arbitration_needed:
        classified["arbitrationRequest"] = _build_arbitration_request(
            record,
            regions,
            scores_by_field,
            arbitration_needed,
            schema,
            required,
        )
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


def _load_importance(workspace_start: Path | None) -> dict[str, float]:
    try:
        learned = _load_learned(schema_learned_path(workspace_start))
    except WorkspaceNotInitializedError:
        return {}
    return dict(learned.get("markerImportance", {}) or {})


def _weighted_marker_score(
    matched_markers: list[str],
    query_markers: dict[str, str],
    importance: dict[str, float],
) -> float:
    if not query_markers:
        return 0.0
    weights = {field: float(importance.get(field, 0.5)) for field in query_markers}
    total_weight = sum(weights.values()) or 1.0
    matched_weight = sum(weights.get(field, 0.5) for field in matched_markers)
    return matched_weight / total_weight


def record_weight(
    record: dict[str, Any],
    query_markers: dict[str, str],
    query_tokens: set[str],
    importance: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    markers = record.get("markers", {})
    matched_markers = [key for key, value in query_markers.items() if markers.get(key) == value]
    exactness = _weighted_marker_score(matched_markers, query_markers, importance or {})
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
    workspace_start = Path(str(payload["workspaceRoot"])).resolve() if payload.get("workspaceRoot") else None
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
    importance = _load_importance(workspace_start)

    ranked: list[dict[str, Any]] = []
    for record in records:
        score, matched_markers = record_weight(record, query_markers, query_tokens, importance)
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


def rebuild_edges_for_neighbors(
    graph: dict[str, Any],
    dirty_record_ids: set[str],
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Partial edge rebuild restricted to a ``dirty`` subset of records.

    ``dirty_record_ids`` is the set of record ids whose edges may have
    changed (typically the former neighbors of a record that was just
    deleted). The output ``graph["edges"]`` is equivalent to what
    :func:`rebuild_edges` would produce over every surviving record,
    except that edges whose endpoints are both outside ``dirty_record_ids``
    are left untouched (same ``createdAt``, same ``updatedAt``) — a
    micro-optimization enabled by the per-pair nature of the inference
    engine: scores for (s, t) depend only on s and t, so removing a
    third record from the candidate pool cannot shift them.

    Explicit edges declared on dirty records are re-emitted, which
    reproduces the (historically allowed) behavior where a record's
    ``relations.explicit`` pointing at a now-missing target still
    surfaces as a dangling edge — matching :func:`rebuild_edges`.

    If ``dirty_record_ids`` is empty the graph is returned unchanged.
    """
    if not dirty_record_ids:
        return graph

    schema = schema or load_schema()
    records: dict[str, dict[str, Any]] = graph.get("records", {}) or {}
    alive_ids = set(records.keys())
    dirty_alive = {rid for rid in dirty_record_ids if rid in alive_ids}

    existing_edges: list[dict[str, Any]] = list(graph.get("edges", []) or [])

    # Preserve createdAt on any surviving inferred edge keyed by
    # (source, target, type). Explicit edges carry no createdAt.
    prior_inferred: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for edge in existing_edges:
        if edge.get("kind") == "inferred":
            key = (edge.get("source"), edge.get("target"), edge.get("type"))
            prior_inferred[key] = edge

    # Partition existing edges:
    # - stable: both endpoints alive and neither in dirty_record_ids. Keep
    #   bit-for-bit (including updatedAt).
    # - dangling: at least one endpoint no longer in ``records``. Drop.
    # - touching dirty (but both alive): drop and recompute.
    new_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in existing_edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in alive_ids or target not in alive_ids:
            continue
        if source in dirty_record_ids or target in dirty_record_ids:
            continue
        key = (source, target, edge.get("type"))
        new_edges[key] = edge

    # Explicit re-emission: any surviving record whose explicit relation
    # touches a dirty id. We walk all survivors for safety (cost is O(n))
    # so we pick up explicit edges regardless of whether they were in the
    # prior edge list.
    for record in records.values():
        source_id = record["id"]
        for relation in normalize_explicit_relations(record):
            target_id = relation["target"]
            if source_id not in dirty_record_ids and target_id not in dirty_record_ids:
                continue
            key = (source_id, target_id, relation["type"])
            new_edges[key] = {
                "source": source_id,
                "target": target_id,
                "type": relation["type"],
                "kind": "explicit",
                "confidence": relation.get("confidence", 1.0),
                "updatedAt": now_iso(),
            }

    # Inferred re-emission from dirty sources. For non-dirty sources with
    # dirty targets we carry the pre-existing edge forward below, since
    # the inference engine is per-pair and R's removal cannot change
    # those scores.
    record_list = list(records.values())
    for dirty_id in dirty_alive:
        source_record = records.get(dirty_id)
        if source_record is None:
            continue
        candidates = [item for item in record_list if item["id"] != dirty_id]
        inferred = infer_relations(
            {
                "record": source_record,
                "candidates": candidates,
                "minScore": 0.3,
            },
            schema,
        )
        for relation in inferred["inferredRelations"]:
            key = (dirty_id, relation["id"], relation["relationType"])
            existing = new_edges.get(key)
            if existing and existing.get("kind") == "explicit":
                continue
            now = now_iso()
            prior = prior_inferred.get(key)
            created_at = prior.get("createdAt") if prior and prior.get("createdAt") else now
            new_edges[key] = {
                "source": dirty_id,
                "target": relation["id"],
                "type": relation["relationType"],
                "kind": "inferred",
                "confidence": relation["confidence"],
                "matchedMarkers": relation.get("matchedMarkers", []),
                "sharedTokens": relation.get("sharedTokens", []),
                "createdAt": created_at,
                "updatedAt": now,
            }

    # Carry forward inferred edges from non-dirty sources pointing into
    # the dirty set. Their per-pair score is unchanged because neither
    # endpoint moved and inference is per-pair. Without this step we
    # would lose category-3 edges that ``rebuild_edges`` would re-emit.
    for (src, tgt, typ), edge in prior_inferred.items():
        if src not in alive_ids or tgt not in alive_ids:
            continue
        if src in dirty_record_ids:
            continue  # already handled above
        if tgt not in dirty_record_ids:
            continue  # not in our responsibility zone
        key = (src, tgt, typ)
        if key in new_edges and new_edges[key].get("kind") == "explicit":
            continue
        carried = dict(edge)
        carried["updatedAt"] = now_iso()
        new_edges[key] = carried

    graph["edges"] = sorted(
        new_edges.values(),
        key=lambda item: (item["source"], item["kind"], -item["confidence"], item["target"]),
    )
    return graph


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
    if not graph_path and payload.get("workspaceRoot"):
        graph_path = str(default_graph_path(Path(str(payload["workspaceRoot"])).expanduser().resolve()))
    graph = load_graph(graph_path)
    existing_records = dict(graph.get("records", {}))
    input_records = payload.get("records", [])

    upserted_ids: list[str] = []
    for item in input_records:
        classified = classify_record(
            {"record": item, "workspaceRoot": payload.get("workspaceRoot")},
            schema,
        )
        merged = merge_record(existing_records.get(classified["id"]), classified)
        existing_records[merged["id"]] = merged
        upserted_ids.append(merged["id"])

    prior_edges = list(graph.get("edges", []))
    graph["records"] = existing_records
    graph["edges"] = rebuild_edges(existing_records, schema, prior_edges)
    write_graph(graph, graph_path)

    workspace_for_side_effects: Path | None = None
    try:
        if payload.get("workspaceRoot"):
            workspace_for_side_effects = Path(str(payload["workspaceRoot"])).expanduser().resolve()
        else:
            workspace_for_side_effects = require_workspace()
    except WorkspaceNotInitializedError:
        workspace_for_side_effects = None

    if workspace_for_side_effects is not None:
        stats = compute_idf_from_records(list(graph["records"].values()))
        save_idf_stats(idf_stats_path(workspace_for_side_effects), stats)
        learn_schema(
            {
                "workspaceRoot": str(workspace_for_side_effects),
                "graphPath": graph_path,
            }
        )

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
    if not graph_path and payload.get("workspaceRoot"):
        graph_path = str(default_graph_path(Path(str(payload["workspaceRoot"])).expanduser().resolve()))
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
            "workspaceRoot": payload.get("workspaceRoot"),
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

    # Capture the dirty set (former neighbors of the deleted record) from
    # the pre-delete edge list. Only these records can possibly lose an
    # edge; all other pairwise scores are stable by construction because
    # the inference engine is per-pair.
    pre_edges: list[dict[str, Any]] = list(graph.get("edges", []) or [])
    dirty_ids: set[str] = set()
    for edge in pre_edges:
        if edge.get("source") == record_id:
            neighbor = edge.get("target")
            if neighbor and neighbor != record_id:
                dirty_ids.add(str(neighbor))
        elif edge.get("target") == record_id:
            neighbor = edge.get("source")
            if neighbor and neighbor != record_id:
                dirty_ids.add(str(neighbor))

    edges_before = len(pre_edges)
    records.pop(record_id, None)
    graph["records"] = records

    if dirty_ids:
        # Partial rebuild restricted to the neighbor set. Equivalent to a
        # full ``rebuild_edges`` over every survivor (see
        # ``rebuild_edges_for_neighbors`` docstring) but cheaper on large
        # graphs because inference is only re-run for dirty sources.
        rebuild_edges_for_neighbors(graph, dirty_ids, schema)
    else:
        # No neighbors — but a self-edge (source==target==record_id) is
        # possible in pathological graphs, so prune any edge that still
        # references the deleted id.
        graph["edges"] = [
            edge for edge in pre_edges
            if edge.get("source") != record_id and edge.get("target") != record_id
        ]

    # Observability seam: how much work did the partial rebuild save?
    # Local variables only — Phase 6 observability work will attach these
    # to a tracer; the names ``edges_dropped`` and ``dirty_neighbor_count``
    # are the stable hook.
    edges_after = len(graph.get("edges", []) or [])
    edges_dropped = max(edges_before - edges_after, 0)
    dirty_neighbor_count = len(dirty_ids)
    del edges_dropped, dirty_neighbor_count  # unused until observability lands

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


# ---------------------------------------------------------------------------
# Notion push (Phase 4a)
# ---------------------------------------------------------------------------
#
# The push side writes promoted rules/decisions back to Notion so the workspace
# becomes a real second memory. These helpers are deliberately pure: they do
# not touch the Notion API. The slash command and the Python fallback in
# ``scripts/notion_sync.py`` are responsible for the network calls.

PUSHABLE_MARKER_TYPES = frozenset({"rule", "decision"})


def list_pushable_records(
    graph_path: str | None = None,
    record_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return records eligible for push-back to Notion.

    When ``record_ids`` is omitted, returns every record whose
    ``markers.type`` is in ``PUSHABLE_MARKER_TYPES`` (rule or decision).
    When ``record_ids`` is supplied, the marker filter is bypassed so callers
    can push arbitrary records they have already picked. Missing ids are
    silently dropped rather than raising so batch callers do not stall on a
    single typo; the caller can diff requested ids against returned ids to
    detect skips.
    """
    graph = load_graph(graph_path)
    records = graph.get("records", {})
    if record_ids is not None:
        wanted = list(record_ids)
        return [records[rid] for rid in wanted if rid in records]
    out: list[dict[str, Any]] = []
    for record in records.values():
        markers = record.get("markers") or {}
        if markers.get("type") in PUSHABLE_MARKER_TYPES:
            out.append(record)
    return out


def load_push_state(workspace_root: Path | str | None = None) -> dict[str, str]:
    """Read ``.context-graph/notion_push.json`` as ``{record_id: page_id}``.

    Missing file returns ``{}``. Malformed JSON is treated as empty to keep
    the push path resilient; callers needing strict validation should read
    the file directly via ``push_state_path``.
    """
    start = Path(str(workspace_root)) if workspace_root else None
    try:
        path = push_state_path(start)
    except WorkspaceNotInitializedError:
        return {}
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value is not None}


def save_push_state(
    state: dict[str, str],
    workspace_root: Path | str | None = None,
) -> None:
    """Persist the push-state mapping to ``.context-graph/notion_push.json``."""
    start = Path(str(workspace_root)) if workspace_root else None
    path = push_state_path(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {str(key): str(value) for key, value in state.items() if value is not None}
    with path.open("w", encoding="utf-8") as f:
        json.dump(serialized, f, ensure_ascii=True, indent=2, sort_keys=True)
        f.write("\n")


def plan_push(
    records: list[dict[str, Any]],
    state: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Classify ``records`` into creates vs updates against the push ``state``.

    Pure function: does not touch the network or disk. Records whose id is
    already mapped in ``state`` become ``updates`` entries (paired with the
    existing Notion page id). Everything else becomes a ``create``.
    """
    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for record in records:
        record_id = record.get("id")
        if not record_id:
            continue
        mapped = state.get(str(record_id))
        if mapped:
            updates.append({"record": record, "notionPageId": mapped})
        else:
            creates.append(record)
    return {"creates": creates, "updates": updates}


def apply_push_result(
    record_id: str,
    notion_page_id: str,
    state: dict[str, str],
) -> dict[str, str]:
    """Return a new state dict with ``record_id -> notion_page_id`` added.

    Does not mutate the input mapping; callers must use the return value.
    Overwrites any prior mapping for the same ``record_id`` so re-creates
    after a Notion-side restore stay idempotent.
    """
    new_state = dict(state)
    new_state[str(record_id)] = str(notion_page_id)
    return new_state


def _rich_text(plain: str) -> list[dict[str, Any]]:
    """Wrap a plain string as a Notion rich_text run.

    The public Notion API accepts the richer ``{"type": "text", "text":
    {"content": ...}}`` shape. We keep the output minimal — no annotations,
    no link — because the push path is best-effort. Callers that need
    fidelity should hand the markdown body to the Notion MCP directly and
    let it parse annotations.
    """
    if not plain:
        return []
    return [
        {
            "type": "text",
            "text": {"content": plain, "link": None},
            "plain_text": plain,
            "annotations": {},
        }
    ]


def _flush_code_block(lines: list[str], language: str) -> dict[str, Any]:
    body = "\n".join(lines)
    return {
        "object": "block",
        "type": "code",
        "code": {
            "language": language or "plain text",
            "rich_text": _rich_text(body),
        },
    }


def _paragraph_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


_BULLETED_PREFIX = ("- ", "* ", "+ ")
_TODO_CHECKED_PREFIX = ("- [x] ", "- [X] ")
_TODO_UNCHECKED_PREFIX = ("- [ ] ",)


def _numbered_prefix_len(line: str) -> int:
    """Return the length of a leading ``\\d+\\. `` prefix, or 0."""
    idx = 0
    while idx < len(line) and line[idx].isdigit():
        idx += 1
    if idx == 0:
        return 0
    if idx < len(line) - 1 and line[idx] == "." and line[idx + 1] == " ":
        return idx + 2
    return 0


def record_to_notion_blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a local record to a list of Notion block payloads.

    Best-effort inverse of ``scripts/notion_markdown.py``. Covers the block
    types the pull path already round-trips: heading_1/2/3, paragraph,
    bulleted_list_item, numbered_list_item, to_do, code, quote, divider.
    Unknown markdown falls through as paragraphs; empty records return ``[]``.

    This is intended for the Python fallback path. The MCP path prefers the
    raw markdown string because ``notion-create-pages`` accepts markdown and
    parses annotations server-side.
    """
    content = str(record.get("content") or "")
    if not content.strip():
        return []

    blocks: list[dict[str, Any]] = []
    lines = content.splitlines()
    in_code = False
    code_language = ""
    code_buffer: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip()
        if in_code:
            if line.startswith("```"):
                blocks.append(_flush_code_block(code_buffer, code_language))
                code_buffer = []
                code_language = ""
                in_code = False
                continue
            code_buffer.append(raw_line)
            continue

        if line.startswith("```"):
            in_code = True
            code_language = line[3:].strip()
            code_buffer = []
            continue

        stripped = line.strip()
        if not stripped:
            # Blank lines are paragraph separators in markdown. We simply
            # skip them — adjacent non-blank blocks are kept distinct by
            # being separate entries in ``blocks``.
            continue

        if stripped == "---" or stripped == "***":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        if stripped.startswith("### "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": _rich_text(stripped[4:].strip())},
                }
            )
            continue
        if stripped.startswith("## "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": _rich_text(stripped[3:].strip())},
                }
            )
            continue
        if stripped.startswith("# "):
            blocks.append(
                {
                    "object": "block",
                    "type": "heading_1",
                    "heading_1": {"rich_text": _rich_text(stripped[2:].strip())},
                }
            )
            continue

        if stripped.startswith(_TODO_CHECKED_PREFIX):
            todo_text = stripped[len("- [x] "):].strip()
            blocks.append(
                {
                    "object": "block",
                    "type": "to_do",
                    "to_do": {"checked": True, "rich_text": _rich_text(todo_text)},
                }
            )
            continue
        if stripped.startswith(_TODO_UNCHECKED_PREFIX):
            todo_text = stripped[len("- [ ] "):].strip()
            blocks.append(
                {
                    "object": "block",
                    "type": "to_do",
                    "to_do": {"checked": False, "rich_text": _rich_text(todo_text)},
                }
            )
            continue

        if stripped.startswith(_BULLETED_PREFIX):
            text = stripped[2:].strip()
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _rich_text(text)},
                }
            )
            continue

        numbered_len = _numbered_prefix_len(stripped)
        if numbered_len:
            text = stripped[numbered_len:].strip()
            blocks.append(
                {
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {"rich_text": _rich_text(text)},
                }
            )
            continue

        if stripped.startswith(">"):
            quote_text = stripped[1:].lstrip()
            blocks.append(
                {
                    "object": "block",
                    "type": "quote",
                    "quote": {"rich_text": _rich_text(quote_text)},
                }
            )
            continue

        blocks.append(_paragraph_block(stripped))

    if in_code:
        # Unclosed fence — emit whatever we buffered so callers still see the
        # content. Notion's renderer will accept an open-ended code block.
        blocks.append(_flush_code_block(code_buffer, code_language))

    return blocks
