from __future__ import annotations

import hashlib
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
from intent_modes import (
    IntentMode,
    apply_marker_weight,
    apply_type_boost,
    apply_status_bias,
    apply_freshness_multiplier,
    hop_cap_for,
    hop_penalty_for,
    is_relation_allowed,
    resolve_intent,
)


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


# Per-type half-life (in days) used by ``type_freshness_factor`` when
# ``build_context_pack`` applies the freshness multiplier. The multiplier is
# ``0.5 ** (age_days / half_life)`` so larger half-lives decay more slowly.
# ``build_context_pack`` accepts a ``freshnessHalfLifeDays`` payload override
# that overlays these defaults. Missing keys fall back to ``default``.
#
# Age is measured from, in order of preference:
#   1. ``record["revision"]["updatedAt"]`` (stamped by ``merge_record``)
#   2. ``record["source"]["metadata"]["last_edited_time"]`` (upstream mtime)
#   3. ``record["classifiedAt"]`` (stamped by ``classify_record``)
# The first timestamp that parses wins; records without any timestamp get a
# factor of 1.0 (no decay) so fixtures without stamps are not penalized.
FRESHNESS_HALF_LIFE_DAYS: dict[str, float] = {
    "rule": 365.0,
    "decision": 180.0,
    "architecture": 180.0,
    "pattern": 180.0,
    "task": 30.0,
    "incident": 30.0,
    "bug": 30.0,
    "default": 60.0,
}


# Multiplicative penalty applied per hop beyond the first. Applied as
# ``score *= HOP_PENALTY ** max(0, hop_count - 1)`` where hop_count is:
#   0 = direct query match (no hop)
#   1 = one-hop neighbor of a direct match via an explicit relation
#   2+ = multi-hop
# Today's traversal cap in ``build_context_pack`` is 1 hop (one-hop neighbors
# are pulled in when the caller opts in via ``hopTraversal``). The hook is in
# place so later phases can raise the cap without rewriting scoring.
HOP_PENALTY: float = 0.5


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


def markdown_cursor_path(start: Path | None = None) -> Path:
    return _resolve_workspace_file("markdown_cursor.json", start)


def load_markdown_cursor(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Read the per-file markdown ingest cursor for a workspace.

    Returns `{}` when the file is missing, unparseable, or not a dict. The
    cursor maps `absolute file path -> mtime float (epoch seconds)`; callers
    should treat an absent key as "never seen".
    """
    start = Path(workspace_root) if workspace_root is not None else None
    path = markdown_cursor_path(start)
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


def save_markdown_cursor(
    cursor: dict[str, Any], workspace_root: Path | str | None = None
) -> None:
    """Persist the per-file markdown ingest cursor for a workspace."""
    start = Path(workspace_root) if workspace_root is not None else None
    path = markdown_cursor_path(start)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cursor, f, ensure_ascii=True, indent=2)
        f.write("\n")


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
    ".context-graph/markdown_cursor.json",
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
        existing = load_workspace_manifest(root)
        return {
            "rootPath": str(root),
            "workspaceId": existing.get("id"),
            "manifestPath": str(manifest_path),
            "notion": existing.get("notion"),
            "alreadyExists": True,
        }

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


def load_workspace_manifest(workspace_root: Path | str) -> dict[str, Any]:
    """Read the workspace manifest from .context-graph/workspace.json.

    Raises FileNotFoundError when the manifest is missing.
    """
    root = Path(str(workspace_root)).resolve()
    manifest_path = root / ".context-graph" / "workspace.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No workspace manifest at {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Workspace manifest at {manifest_path} is not an object")
    return data


def update_workspace_manifest(
    workspace_root: Path | str, updates: dict[str, Any]
) -> dict[str, Any]:
    """Merge ``updates`` into the manifest at top level (shallow), bump
    ``updatedAt``, write back atomically (write to .tmp, rename).

    Returns the new manifest. Top-level keys in ``updates`` fully replace
    existing values — this is shallow merge, not recursive.
    """
    root = Path(str(workspace_root)).resolve()
    manifest = load_workspace_manifest(root)
    for key, value in updates.items():
        manifest[key] = value
    manifest["updatedAt"] = now_iso()
    manifest_path = root / ".context-graph" / "workspace.json"
    tmp_path = manifest_path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=True, indent=2)
        f.write("\n")
    tmp_path.replace(manifest_path)
    return manifest


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


def _record_age_timestamp(record: dict[str, Any]) -> str | None:
    """Return the ISO-8601 string used as the record's age anchor.

    Preference order, documented in ``FRESHNESS_HALF_LIFE_DAYS``:
      1. ``revision.updatedAt``
      2. ``source.metadata.last_edited_time``
      3. ``classifiedAt``
    Returns ``None`` when none of these parse. Callers that need an explicit
    anchor should prefer this helper so freshness decay and other time-based
    signals stay consistent.
    """
    candidates: list[str | None] = [
        record.get("revision", {}).get("updatedAt") if isinstance(record.get("revision"), dict) else None,
        (
            record.get("source", {}).get("metadata", {}).get("last_edited_time")
            if isinstance(record.get("source", {}).get("metadata"), dict)
            else None
        ),
        record.get("classifiedAt"),
        record.get("updatedAt"),
    ]
    for value in candidates:
        if value and parse_dt(value):
            return value
    return None


def type_freshness_factor(
    record: dict[str, Any],
    half_life_map: dict[str, float] | None = None,
) -> float:
    """Return a 0..1 decay multiplier for ``record`` based on its type-specific
    half-life. Returns 1.0 (no decay) when no timestamp is parseable.

    Formula: ``0.5 ** (age_days / half_life)``. Age is read via
    ``_record_age_timestamp``. The half-life comes from ``half_life_map`` keyed
    by ``markers.type`` and falls back to the ``default`` key.
    """
    stamp = _record_age_timestamp(record)
    if not stamp:
        return 1.0
    dt = parse_dt(stamp)
    if not dt:
        return 1.0
    age_days = max((datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 0.0)
    effective_map = dict(FRESHNESS_HALF_LIFE_DAYS)
    if half_life_map:
        for key, value in half_life_map.items():
            try:
                effective_map[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    markers = record.get("markers", {}) if isinstance(record.get("markers"), dict) else {}
    record_type = str(markers.get("type") or "")
    half_life = float(effective_map.get(record_type, effective_map.get("default", 60.0)))
    if half_life <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life)


def apply_hop_penalty(score: float, hop_count: int, penalty: float | None = None) -> float:
    """Multiply ``score`` by the hop penalty factor.

    ``hop_count`` convention: 0 = direct query match, 1 = one-hop neighbor,
    2+ = multi-hop. Penalty is applied as ``penalty ** max(0, hop_count - 1)``
    so hops 0 and 1 keep their score (no penalty for the first hop) and hops
    2+ are reduced by an additional factor per step.
    """
    effective = float(penalty) if penalty is not None else HOP_PENALTY
    if effective <= 0:
        return score
    hops_beyond_first = max(0, int(hop_count) - 1)
    return score * (effective ** hops_beyond_first)


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
    if payload.get("dryRun"):
        # classify_record is already a pure read — the marker is purely for
        # caller affordance so they can surface "dry-run" in UI without
        # branching on call sites.
        classified["dryRun"] = True
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


def _score_record_detailed(
    record: dict[str, Any],
    query_markers: dict[str, str],
    query_tokens: set[str],
    importance: dict[str, float] | None = None,
    intent: IntentMode | None = None,
) -> dict[str, Any]:
    """Return every number that feeds into a record's retrieval score.

    ``record_weight`` and the retrieval path use only ``score`` and
    ``matchedMarkers`` from this result. ``inspect_record`` surfaces the
    full breakdown. Splitting the computation this way keeps the two code
    paths honest — there is a single source of truth for how a score is
    built, so the ranker and the explainer can never drift.
    """
    markers = record.get("markers", {})
    matched_markers = [key for key, value in query_markers.items() if markers.get(key) == value]

    # Per-axis marker weight under intent is applied before the weighted
    # aggregate so the existing exactness pipeline stays one step.
    per_axis_intent: dict[str, float] = {
        a: apply_marker_weight(a, intent) for a in matched_markers
    }
    exactness = _weighted_marker_score(matched_markers, query_markers, importance or {})
    # Fold the per-axis intent multipliers into exactness by MEAN (not
    # product, not importance-weighted sum). Mean keeps each matched
    # axis's intent signal comparable regardless of learned
    # markerImportance weights: a 2x intent boost on a low-importance
    # axis should not be dominated by the importance weighting that
    # _weighted_marker_score already applies. See spec §5.2 and the
    # plan's Task 8 for the chosen contract.
    if per_axis_intent:
        exactness *= sum(per_axis_intent.values()) / len(per_axis_intent)

    record_tokens = set(record.get("tokens", []))
    matched_tokens = sorted(query_tokens & record_tokens)
    token_overlap = len(matched_tokens) / max(len(query_tokens), 1)

    severity_value = markers.get("severity")
    severity_weight = {
        "critical": 1.0,
        "high": 0.7,
        "medium": 0.4,
        "low": 0.2,
    }.get(severity_value, 0.0)
    status_value = markers.get("status")
    status_weight = {
        "in-progress": 1.0,
        "known-risk": 0.85,
        "new": 0.6,
        "fixed": 0.45,
        "done": 0.35,
        "archived": 0.1,
    }.get(status_value, 0.25)
    freshness = recency_score(record.get("updatedAt") or record.get("classifiedAt"))

    base_total = (
        exactness * 0.45
        + token_overlap * 0.2
        + severity_weight * 0.15
        + status_weight * 0.1
        + freshness * 0.1
    )

    # Intent post-multipliers, applied to the base_total in order:
    # markerWeights already folded into exactness above.
    type_boost_factor = apply_type_boost(markers.get("type"), intent)
    status_bias_factor = apply_status_bias(status_value, intent)
    freshness_mult_factor = apply_freshness_multiplier(1.0, intent)

    total = base_total * type_boost_factor * status_bias_factor * freshness_mult_factor
    score = round(total, 3)

    factors: dict[str, Any] = {
        "markerMatch": {
            "matched": matched_markers,
            "weight": 0.45,
            "value": exactness,
            "contribution": round(exactness * 0.45, 6),
        },
        "tokenMatch": {
            "matched": matched_tokens,
            "queryTokenCount": len(query_tokens),
            "recordTokenCount": len(record_tokens),
            "weight": 0.2,
            "value": token_overlap,
            "contribution": round(token_overlap * 0.2, 6),
        },
        "severity": {
            "value": severity_value,
            "weight": 0.15,
            "factor": severity_weight,
            "contribution": round(severity_weight * 0.15, 6),
        },
        "status": {
            "value": status_value,
            "weight": 0.1,
            "factor": status_weight,
            "contribution": round(status_weight * 0.1, 6),
        },
        "freshness": {
            "weight": 0.1,
            "factor": freshness,
            "contribution": round(freshness * 0.1, 6),
            "updatedAt": record.get("updatedAt") or record.get("classifiedAt"),
        },
    }
    if intent is not None:
        factors["intentMarkerMultiplier"] = per_axis_intent
        factors["intentTypeBoost"] = {"type": markers.get("type"), "value": type_boost_factor}
        factors["intentStatusBias"] = {"status": status_value, "value": status_bias_factor}
        factors["intentFreshnessMultiplier"] = {"value": freshness_mult_factor}

    return {
        "score": score,
        "matchedMarkers": matched_markers,
        "matchedTokens": matched_tokens,
        "factors": factors,
    }


def record_weight(
    record: dict[str, Any],
    query_markers: dict[str, str],
    query_tokens: set[str],
    importance: dict[str, float] | None = None,
    intent: IntentMode | None = None,
) -> tuple[float, list[str]]:
    detail = _score_record_detailed(record, query_markers, query_tokens, importance, intent)
    return detail["score"], detail["matchedMarkers"]


def build_context_pack(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    workspace_start = Path(str(payload["workspaceRoot"])).resolve() if payload.get("workspaceRoot") else None
    query = str(payload.get("query") or "")
    include_archived = bool(payload.get("includeArchived", False))
    intent_mode = payload.get("intentMode")
    intent_override = payload.get("intentOverride")
    intent = resolve_intent(intent_mode, intent_override)
    # include_archived falls back to the intent's preference when the
    # payload does not override it explicitly.
    if intent is not None and intent.include_archived is not None and "includeArchived" not in payload:
        include_archived = bool(intent.include_archived)
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

    # Freshness half-life overrides (``null`` or missing uses defaults).
    half_life_override_raw = payload.get("freshnessHalfLifeDays")
    half_life_override: dict[str, float] | None = None
    if isinstance(half_life_override_raw, dict):
        half_life_override = {}
        for key, value in half_life_override_raw.items():
            try:
                half_life_override[str(key)] = float(value)
            except (TypeError, ValueError):
                continue

    # Hop penalty override + traversal config. Traversal cap is 1 today (we
    # only reach one-hop neighbors of direct matches). The scoring hook for
    # hops 2+ is in place so later phases can raise the cap.
    hop_penalty_raw = payload.get("hopPenalty")
    hop_penalty: float | None = None
    if hop_penalty_raw is not None:
        try:
            hop_penalty = float(hop_penalty_raw)
        except (TypeError, ValueError):
            hop_penalty = None
    traversal_cfg = payload.get("hopTraversal") or {}
    try:
        max_hops = int(traversal_cfg.get("maxHops", 1)) if isinstance(traversal_cfg, dict) else 1
    except (TypeError, ValueError):
        max_hops = 1
    max_hops = max(0, max_hops)

    records_by_id = {record.get("id"): record for record in records if record.get("id")}

    def _score_record(record: dict[str, Any]) -> tuple[float, list[str]]:
        raw, matched = record_weight(record, query_markers, query_tokens, importance, intent)
        factor = type_freshness_factor(record, half_life_override)
        # Keep the internal score scale identical (3dp) so downstream
        # thresholds like the 0.3 supporting cutoff still work.
        return round(raw * factor, 3), matched

    def _has_query_relevance(record: dict[str, Any], matched: list[str]) -> bool:
        """A record is a hop-0 direct match only if it has some actual
        query signal: at least one matched marker or one overlapping token.
        The baseline score from status/freshness alone is not enough; without
        this gate, any record with any timestamp would be a direct match and
        the one-hop traversal hook could never fire.
        """
        if matched:
            return True
        record_tokens = set(record.get("tokens", []) or [])
        if query_tokens and record_tokens and (query_tokens & record_tokens):
            return True
        return False

    def _build_item(
        record: dict[str, Any],
        score: float,
        matched: list[str],
        hop_count: int,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": record.get("id"),
            "title": record.get("title"),
            "score": score,
            "matchedMarkers": matched,
            "hierarchyPath": record.get("hierarchy", {}).get("path", ""),
            "markers": record.get("markers", {}),
            "relations": record.get("relations", {}),
            "hopCount": hop_count,
        }
        if _REDACTORS:
            # Redactors operate on content, so we surface it only when needed.
            item["content"] = record.get("content")
        return item

    # Pass 1: score every candidate record against the query. Records with
    # actual query relevance (matched markers or shared tokens) become hop-0
    # direct matches. Records that score above zero only from the baseline
    # (status default, freshness, etc.) are held aside and may still be
    # pulled in via one-hop traversal below. Freshness is applied already by
    # ``_score_record``; the hop penalty is a no-op at hop 0.
    ranked_by_id: dict[str, dict[str, Any]] = {}
    # ``seed_scores`` tracks the final score of every record in the pack so
    # that one-hop neighbors can inherit a decayed share of it when their own
    # query relevance is zero. This is what keeps the "direct > one-hop >
    # two-hop" ordering observable even when the neighbors share no marker or
    # token with the query.
    seed_scores: dict[str, float] = {}
    direct_hit_ids: list[str] = []
    # Intent can override both the hop cap and the per-hop penalty. When
    # the payload also passes an explicit max_hops / hop_penalty, the
    # payload wins (explicit request > implicit preset).
    if "hopTraversal" not in payload and intent is not None:
        max_hops = hop_cap_for(intent, default=max_hops)
    if "hopPenalty" not in payload and intent is not None:
        override_penalty = hop_penalty_for(intent)
        if override_penalty is not None:
            hop_penalty = override_penalty
    effective_penalty = float(hop_penalty) if hop_penalty is not None else HOP_PENALTY
    for record in records:
        rid = record.get("id")
        if not rid:
            continue
        score, matched = _score_record(record)
        if score <= 0 or not _has_query_relevance(record, matched):
            continue
        item = _build_item(record, score, matched, hop_count=0)
        ranked_by_id[rid] = item
        seed_scores[rid] = score
        direct_hit_ids.append(rid)

    # Pass 2: expand neighbors of direct matches through explicit relations.
    # A neighbor's score is ``max(own_query_score_with_beyond_first_penalty,
    # inherited_seed_score * penalty ** hop_count)``. This means a neighbor
    # that happens to match the query keeps its higher raw score; a neighbor
    # that does not match the query at all still appears with a decayed
    # inherited score so downstream callers can see the relation chain.
    # The visible traversal cap is ``max_hops`` (default 1) today. The
    # scoring hook handles any depth so later phases can raise the cap.
    frontier: list[tuple[str, int]] = [(rid, 0) for rid in direct_hit_ids]
    while frontier and max_hops > 0:
        next_frontier: list[tuple[str, int]] = []
        for seed_id, seed_hop in frontier:
            if seed_hop >= max_hops:
                continue
            seed_record = records_by_id.get(seed_id)
            if not seed_record:
                continue
            seed_score_value = seed_scores.get(seed_id, 0.0)
            for rel in normalize_explicit_relations(seed_record):
                rel_type = str(rel.get("type") or "")
                if intent is not None and not is_relation_allowed(rel_type, intent):
                    continue
                target_id = rel.get("target")
                if not target_id or target_id in ranked_by_id:
                    continue
                target_record = records_by_id.get(target_id)
                if not target_record:
                    continue
                own_score, matched = _score_record(target_record)
                hop_count = seed_hop + 1
                own_penalized = apply_hop_penalty(own_score, hop_count=hop_count, penalty=hop_penalty)
                # Inheritance decays by one penalty step per edge traversed,
                # not by hop_count absolute: the seed score already reflects
                # its own hop-level decay.
                inherited = (
                    seed_score_value * effective_penalty
                    if effective_penalty > 0
                    else seed_score_value
                )
                combined = round(max(own_penalized, inherited), 3)
                if combined <= 0:
                    continue
                item = _build_item(target_record, combined, matched, hop_count=hop_count)
                ranked_by_id[target_id] = item
                seed_scores[target_id] = combined
                next_frontier.append((target_id, hop_count))
        frontier = next_frontier

    ranked = list(ranked_by_id.values())
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


# Tokens that, when they appear within a short window before a keyword, mark
# the keyword as negated in that record. Deliberately small and literal so
# the signal is deterministic and auditable.
_CONTENT_NEGATION_WORDS: frozenset[str] = frozenset({
    "not", "no", "never", "without", "avoid", "stop", "skip", "don't", "dont",
})


def _tokens_with_negation(text: str) -> list[tuple[str, bool]]:
    """Return a list of ``(token, is_negated)`` pairs for ``text``.

    Negation rule: a negation word ("not", "never", "avoid", ...) scopes
    only the **next content word** — stopwords like "the", "a", "for" are
    skipped and do not end the scope. The scope ends at the first non-
    stopword token, which is marked as negated. This keeps phrases like
    "do not retry the payment webhook" from negating every token after
    "not" — only "retry" is negated, "payment" and "webhook" are not.
    """
    cleaned = re.sub(r"[^a-z0-9\s']", " ", text.lower())
    tokens = [t for t in cleaned.split() if t]
    out: list[tuple[str, bool]] = []
    pending_negation = False
    for token in tokens:
        if token in _CONTENT_NEGATION_WORDS:
            pending_negation = True
            out.append((token, False))
            continue
        if pending_negation and token in STOPWORDS:
            # Don't consume the negation on a stopword; keep waiting.
            out.append((token, False))
            continue
        if pending_negation:
            out.append((token, True))
            pending_negation = False
        else:
            out.append((token, False))
    return out


def detect_content_conflicts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic content-level conflicts between records.

    Simplest implementation: scan each record for a verb-like token (length
    >= 4, not a stopword) that appears without negation in one record and
    with negation ("do not ...", "never ...", "avoid ...") in another. This
    surfaces pairs like "retry" vs "do not retry" without needing a parser.

    Output shape per conflict:
    ``{"kind": "content-negation", "token": "retry",
       "affirmative": ["<id>", ...], "negated": ["<id>", ...],
       "recordIds": [...]}``
    The ``recordIds`` field is a superset convenience for consumers that just
    want to know which records were involved regardless of stance.
    """
    if not records:
        return []
    affirmative_by_token: dict[str, list[str]] = {}
    negated_by_token: dict[str, list[str]] = {}
    for record in records:
        rid = record.get("id")
        if not rid:
            continue
        text = str(record.get("content") or "")
        if not text:
            text = str(record.get("title") or "")
        token_stances: dict[str, set[str]] = {}  # token -> {"affirmative"} / {"negated"} / both
        for token, is_negated in _tokens_with_negation(text):
            if len(token) < 4 or token in STOPWORDS:
                continue
            stance = "negated" if is_negated else "affirmative"
            token_stances.setdefault(token, set()).add(stance)
        for token, stances in token_stances.items():
            if "affirmative" in stances:
                affirmative_by_token.setdefault(token, []).append(str(rid))
            if "negated" in stances:
                negated_by_token.setdefault(token, []).append(str(rid))
    conflicts: list[dict[str, Any]] = []
    for token in sorted(set(affirmative_by_token.keys()) & set(negated_by_token.keys())):
        affirmative_ids = sorted(set(affirmative_by_token[token]))
        negated_ids = sorted(set(negated_by_token[token]))
        if not affirmative_ids or not negated_ids:
            continue
        # Drop cases where the same record appears on both sides — without a
        # cross-record disagreement there is no real conflict to surface.
        only_affirmative = sorted(set(affirmative_ids) - set(negated_ids))
        only_negated = sorted(set(negated_ids) - set(affirmative_ids))
        if not only_affirmative or not only_negated:
            continue
        conflicts.append(
            {
                "kind": "content-negation",
                "token": token,
                "affirmative": only_affirmative,
                "negated": only_negated,
                "recordIds": sorted(set(only_affirmative) | set(only_negated)),
            }
        )
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


def _build_promoted_record(
    cohort: list[dict[str, Any]],
    *,
    output_type: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    scope_suffix: str = "",
    scope_note: str = "",
) -> dict[str, Any]:
    """Build a single promoted record for ``cohort``.

    ``scope_suffix`` is appended to the title when the cohort is a narrower
    slice of a larger, conflicting cohort ("for webhook delivery path" vs
    "for background replay"). ``scope_note`` is inserted into the content
    body for the same reason. Both default to empty strings so the legacy
    single-cohort promotion path produces identical output.
    """
    common = common_markers(cohort)
    base_title = str(payload.get("title") or generate_promoted_title(common, len(cohort), output_type))
    title = f"{base_title} {scope_suffix}".strip() if scope_suffix else base_title
    record_id = (
        str(payload.get("id"))
        if payload.get("id") and not scope_suffix
        else f"promoted:{slugify(title)}"
    )
    keywords = shared_keywords(cohort)
    highest_severity = strongest_severity(cohort)
    goal = str(payload.get("goal") or majority_marker(cohort, "goal") or "prevent-regression")
    cohort_marker_conflicts = marker_conflicts(cohort)
    cohort_quality = promotion_quality(common, cohort_marker_conflicts, cohort)
    cohort_suggestions = split_suggestions(cohort, cohort_marker_conflicts)

    promoted_markers: dict[str, Any] = {
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
        f"Promoted from {len(cohort)} related records.",
        f"Promotion quality: {cohort_quality['recommendation']} ({cohort_quality['score']}).",
    ]
    if scope_note:
        summary_lines.extend(["", scope_note])
    summary_lines.extend(["", "Common markers:"])
    for key, value in sorted(common.items()):
        summary_lines.append(f"- {key}: {value}")
    if cohort_marker_conflicts:
        summary_lines.extend(["", "Conflicts:"])
        for key, values in sorted(cohort_marker_conflicts.items()):
            summary_lines.append(f"- {key}: {', '.join(values)}")
    if cohort_suggestions:
        summary_lines.extend(["", "Split suggestions:"])
        for suggestion in cohort_suggestions:
            summary_lines.append(f"- by {suggestion['marker']}: {suggestion['groupCount']} groups")
    if keywords:
        summary_lines.extend(["", "Shared keywords:", f"- {', '.join(keywords)}"])
    summary_lines.extend(
        [
            "",
            "Derived from:",
            *[f"- {record['id']}: {record.get('title', '')}" for record in cohort],
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
                    "explicit": [{"type": "derived_from", "target": record["id"]} for record in cohort],
                    "inferred": [],
                },
                "source": {
                    "system": "context-graph",
                    "path": f"promoted/{slugify(title)}.json",
                    "generatedBy": "promote_pattern",
                    "metadata": {
                        "promotionQuality": cohort_quality,
                        "splitSuggestions": cohort_suggestions,
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
    return promoted_record


def _split_cohort_by_content_conflict(
    records: list[dict[str, Any]],
    content_conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split ``records`` into sub-cohorts that agree on the dominant content
    conflict. Returns a list of ``{"stance", "token", "records"}`` dicts.

    Falls back to a single ``agnostic`` group when no usable split can be
    produced (e.g., every record sits on both sides of every conflict).
    """
    if not content_conflicts:
        return [{"stance": "all", "token": "", "records": records}]
    # Use the conflict that partitions the cohort most evenly — this is the
    # axis most worth splitting on. Ties break alphabetically on the token
    # for determinism.
    scored: list[tuple[float, dict[str, Any]]] = []
    for conflict in content_conflicts:
        a = len(conflict.get("affirmative", []))
        n = len(conflict.get("negated", []))
        if a == 0 or n == 0:
            continue
        balance = min(a, n) / max(a, n)
        scored.append((balance, conflict))
    if not scored:
        return [{"stance": "all", "token": "", "records": records}]
    scored.sort(key=lambda item: (-item[0], item[1]["token"]))
    primary = scored[0][1]
    token = primary["token"]
    affirmative_ids = set(primary.get("affirmative", []))
    negated_ids = set(primary.get("negated", []))
    affirmative_records = [r for r in records if str(r.get("id")) in affirmative_ids]
    negated_records = [r for r in records if str(r.get("id")) in negated_ids]
    result: list[dict[str, Any]] = []
    if affirmative_records:
        result.append({"stance": "affirmative", "token": token, "records": affirmative_records})
    if negated_records:
        result.append({"stance": "negated", "token": token, "records": negated_records})
    if not result:
        return [{"stance": "all", "token": "", "records": records}]
    return result


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
    keywords = shared_keywords(source_records)
    marker_conflict_map = marker_conflicts(source_records)
    quality = promotion_quality(common, marker_conflict_map, source_records)
    suggestions = split_suggestions(source_records, marker_conflict_map)
    content_conflicts = detect_content_conflicts(source_records)

    # Decide whether to split. We only split when we have a usable content
    # conflict, i.e. at least one record on each side of the same token.
    splittable_conflicts = [
        c for c in content_conflicts
        if c.get("affirmative") and c.get("negated")
    ]
    promoted_records: list[dict[str, Any]] = []
    if splittable_conflicts:
        sub_cohorts = _split_cohort_by_content_conflict(source_records, splittable_conflicts)
        for sub in sub_cohorts:
            stance = sub["stance"]
            token = sub["token"]
            if stance == "affirmative":
                scope_suffix = f"(when {token})"
                scope_note = f"Narrower scope: records that affirm '{token}'."
            elif stance == "negated":
                scope_suffix = f"(when not {token})"
                scope_note = f"Narrower scope: records that negate '{token}'."
            else:
                scope_suffix = ""
                scope_note = ""
            promoted = _build_promoted_record(
                sub["records"],
                output_type=output_type,
                payload=payload,
                schema=schema,
                scope_suffix=scope_suffix,
                scope_note=scope_note,
            )
            promoted_records.append(promoted)
    else:
        promoted_records.append(
            _build_promoted_record(
                source_records,
                output_type=output_type,
                payload=payload,
                schema=schema,
            )
        )

    result: dict[str, Any] = {
        # Keep ``promotedRecord`` (singular) for backward compatibility;
        # callers that want the full set should read ``promotedRecords``.
        "promotedRecord": promoted_records[0] if promoted_records else None,
        "promotedRecords": promoted_records,
        "sourceRecords": [{"id": record["id"], "title": record.get("title", "")} for record in source_records],
        "sharedKeywords": keywords,
        "commonMarkers": common,
        "quality": quality,
        "splitSuggestions": suggestions,
        # Conflicts surfaced to the caller so they can decide whether to
        # accept the (possibly split) proposals. Empty list when the cohort
        # is internally consistent.
        "conflicts": content_conflicts,
    }

    if payload.get("writeToGraph"):
        index_result = index_records(
            {
                "graphPath": graph_path,
                "records": promoted_records,
                "dryRun": bool(payload.get("dryRun")),
            },
            schema,
        )
        result["indexResult"] = index_result

    if payload.get("dryRun"):
        result["dryRun"] = True

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
    dry_run = bool(payload.get("dryRun"))
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
    if dry_run:
        # Do not persist. The summary below still reflects what would have
        # been written so callers can verify impact before committing.
        return {
            "graphPath": str(Path(graph_path) if graph_path else default_graph_path()),
            "upsertedIds": upserted_ids,
            "recordCount": len(graph["records"]),
            "edgeCount": len(graph["edges"]),
            "updatedAt": graph.get("updatedAt", now_iso()),
            "dryRun": True,
        }
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
    dry_run = bool(payload.get("dryRun"))
    root_value = payload.get("rootPath") or payload.get("path")
    if not root_value:
        raise ValueError("ingest_markdown requires rootPath.")

    root_path = Path(str(root_value)).expanduser()
    if not root_path.exists():
        raise ValueError(f"Path does not exist: {root_path}")
    recursive = bool(payload.get("recursive", True))
    pattern = str(payload.get("pattern", "*.md"))

    cursor_in = payload.get("cursor")
    if cursor_in is not None and not isinstance(cursor_in, dict):
        raise ValueError("cursor must be an object mapping absolute paths to mtimes.")
    fresh_files, scan_root, records, skipped_paths, cursor_out = _collect_fresh_markdown(
        root_path,
        schema,
        pattern=pattern,
        recursive=recursive,
        cursor=cursor_in,
    )
    result = {
        "rootPath": str(scan_root),
        "fileCount": len(fresh_files),
        "records": records,
        "recordIds": [record["id"] for record in records],
    }
    if cursor_in is not None:
        result["skippedFileCount"] = len(skipped_paths)
        result["skippedFiles"] = skipped_paths
        result["cursor"] = cursor_out

    if payload.get("index", True):
        index_result = index_records(
            {
                "graphPath": payload.get("graphPath"),
                "records": records,
                "dryRun": dry_run,
            },
            schema,
        )
        result["indexResult"] = index_result

    if dry_run:
        result["dryRun"] = True

    return result


def _collect_fresh_markdown(
    root_path: Path,
    schema: dict[str, Any],
    *,
    pattern: str,
    recursive: bool,
    cursor: dict[str, Any] | None,
) -> tuple[list[Path], Path, list[dict[str, Any]], list[str], dict[str, Any]]:
    """Wrap `collect_markdown_records` with optional per-file mtime filtering.

    When `cursor` is None, behaves like a normal full scan and the returned
    cursor mapping is `{}`. When `cursor` is provided, files whose absolute
    path is in the cursor with mtime <= the stored value are skipped; the
    returned cursor mapping advances skipped files (carrying the prior mtime)
    and processed files (using the current mtime).
    """
    files, scan_root, records = collect_markdown_records(
        root_path,
        schema,
        system="markdown",
        pattern=pattern,
        recursive=recursive,
    )
    if cursor is None:
        return files, scan_root, records, [], {}

    skipped_paths: list[str] = []
    fresh_files: list[Path] = []
    fresh_records: list[dict[str, Any]] = []
    new_cursor: dict[str, Any] = dict(cursor)
    for file_path, record in zip(files, records):
        abs_path = str(file_path.resolve())
        current_mtime = file_path.stat().st_mtime
        stored = cursor.get(abs_path)
        if isinstance(stored, (int, float)) and current_mtime <= float(stored):
            skipped_paths.append(abs_path)
            continue
        fresh_files.append(file_path)
        fresh_records.append(record)
        new_cursor[abs_path] = current_mtime
    return fresh_files, scan_root, fresh_records, skipped_paths, new_cursor


def ingest_notion_export(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or load_schema()
    dry_run = bool(payload.get("dryRun"))
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
                "dryRun": dry_run,
            },
            schema,
        )
        result["indexResult"] = index_result

    if dry_run:
        result["dryRun"] = True

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

    # search_graph delegates scoring and traversal to build_context_pack,
    # which is already intent-aware (resolves intent, applies marker /
    # type / status / freshness multipliers, filters relations, scales
    # hop penalties). Forward intentMode / intentOverride through the
    # nested payload so a mode requested at the search layer reaches
    # those scoring sites.
    nested_payload: dict[str, Any] = {
        "query": payload.get("query", ""),
        "markers": payload.get("markers", {}),
        "records": visible_records,
        "limit": payload.get("limit", 8),
        "includeArchived": include_archived,
        "workspaceRoot": payload.get("workspaceRoot"),
    }
    if payload.get("intentMode") is not None:
        nested_payload["intentMode"] = payload.get("intentMode")
    if payload.get("intentOverride") is not None:
        nested_payload["intentOverride"] = payload.get("intentOverride")
    context_pack = build_context_pack(nested_payload, schema)

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
    dry_run = bool(payload.get("dryRun"))
    record_id = payload.get("recordId")
    if not record_id:
        raise ValueError("delete_record requires recordId.")
    record_id = str(record_id)
    graph_path = payload.get("graphPath")
    graph = load_graph(graph_path)
    records = dict(graph.get("records", {}))
    resolved_path = str(Path(graph_path) if graph_path else default_graph_path())

    if record_id not in records:
        response = {
            "deletedId": record_id,
            "notFound": True,
            "recordCount": len(records),
            "edgeCount": len(graph.get("edges", [])),
            "graphPath": resolved_path,
            "updatedAt": graph.get("updatedAt", now_iso()),
        }
        if dry_run:
            response["dryRun"] = True
        return response

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

    if dry_run:
        return {
            "deletedId": record_id,
            "notFound": False,
            "recordCount": len(graph["records"]),
            "edgeCount": len(graph["edges"]),
            "graphPath": resolved_path,
            "updatedAt": graph.get("updatedAt", now_iso()),
            "dryRun": True,
        }

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
    dry_run = bool(payload.get("dryRun"))
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
        response = {
            "recordId": record_id,
            "archived": archived,
            "notFound": True,
            "graphPath": resolved_path,
            "updatedAt": graph.get("updatedAt", now_iso()),
        }
        if dry_run:
            response["dryRun"] = True
        return response

    if dry_run:
        # Don't mutate the in-memory record (load_graph returns a fresh dict
        # but we still avoid flipping the flag). Return the "would-be" shape.
        return {
            "recordId": record_id,
            "archived": archived,
            "graphPath": resolved_path,
            "updatedAt": graph.get("updatedAt", now_iso()),
            "dryRun": True,
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


def load_push_state(workspace_root: Path | str | None = None) -> dict[str, Any]:
    """Read ``.context-graph/notion_push.json`` as the auto-push state.

    The returned shape is::

        {
            "pending": [recordId, ...],
            "records": {
                recordId: {
                    "notionPageId": str,
                    "lastPushedRevision": int | None,
                    "lastPushedAt": str | None,
                },
                ...,
            },
        }

    Legacy ``{record_id: page_id}`` files are migrated on read into the
    new shape with ``lastPushedRevision = None`` and ``lastPushedAt = None``.
    Missing or malformed files return ``{"pending": [], "records": {}}``.
    """
    start = Path(str(workspace_root)) if workspace_root else None
    try:
        path = push_state_path(start)
    except WorkspaceNotInitializedError:
        return {"pending": [], "records": {}}
    if not path.exists():
        return {"pending": [], "records": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"pending": [], "records": {}}
    if not isinstance(data, dict):
        return {"pending": [], "records": {}}
    if "records" in data or "pending" in data:
        records = data.get("records") or {}
        pending = data.get("pending") or []
        normalised: dict[str, dict[str, Any]] = {}
        for key, value in records.items():
            if isinstance(value, dict) and value.get("notionPageId"):
                normalised[str(key)] = {
                    "notionPageId": str(value["notionPageId"]),
                    "lastPushedRevision": value.get("lastPushedRevision"),
                    "lastPushedAt": value.get("lastPushedAt"),
                }
        return {
            "pending": [str(item) for item in pending if item],
            "records": normalised,
        }
    # Legacy: flat {recordId: pageId} mapping.
    legacy: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if value is None:
            continue
        legacy[str(key)] = {
            "notionPageId": str(value),
            "lastPushedRevision": None,
            "lastPushedAt": None,
        }
    return {"pending": [], "records": legacy}


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


# ---------------------------------------------------------------------------
# Observability (Cross-cutting)
# ---------------------------------------------------------------------------


def _content_hash(value: str | None) -> str:
    """Short hex digest of ``value``. Used by ``graph_diff`` so we can flag a
    content change without dumping the raw body — critical when graphs
    contain secrets or long bodies.
    """
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _resolve_graph_arg(payload: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Return a graph dict from either ``<prefix>`` (inline) or ``<prefix>Path``.

    Used by ``graph_diff`` so callers can hand us either raw graphs (unit
    tests, MCP callers that already have the data) or a path (the CLI).
    """
    inline = payload.get(prefix)
    if isinstance(inline, dict):
        return inline
    path_key = f"{prefix}Path"
    path_value = payload.get(path_key)
    if path_value:
        return load_graph(str(path_value))
    raise ValueError(
        f"graph_diff requires either '{prefix}' or '{path_key}' in the payload."
    )


def _record_fingerprint(record: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields ``graph_diff`` compares on.

    We keep ``markers``, ``title``, ``last_edited_time``, ``revision``, and
    a short content hash — enough to tell "something meaningful moved"
    without dumping bodies into the diff output.
    """
    content = record.get("content") or ""
    return {
        "title": record.get("title"),
        "markers": record.get("markers") or {},
        "contentHash": _content_hash(content if isinstance(content, str) else str(content)),
        "lastEditedTime": (
            record.get("last_edited_time")
            or (record.get("source") or {}).get("metadata", {}).get("last_edited_time")
        ),
        "revision": record.get("revision"),
    }


def _diff_record_fingerprints(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return a per-field change map for two record fingerprints.

    Only fields that differ are surfaced; the caller decides whether the
    record counts as modified (any change => modified).
    """
    changes: dict[str, dict[str, Any]] = {}
    for key in ("title", "contentHash", "lastEditedTime", "revision", "markers"):
        left_val = left.get(key)
        right_val = right.get(key)
        if left_val != right_val:
            changes[key] = {"left": left_val, "right": right_val}
    return changes


def _edge_key(edge: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (edge.get("source"), edge.get("target"), edge.get("type"))


def graph_diff(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare two graph snapshots and return a structured diff.

    Accepts either inline graphs (``left`` / ``right`` keys, each a graph
    dict) or paths (``leftPath`` / ``rightPath``). The returned shape:

    ``{"recordsAdded": [...], "recordsRemoved": [...], "recordsModified":
    [...], "edgesAdded": [...], "edgesRemoved": [...], "summary": {...}}``

    Record entries carry ``id``, ``title``, and ``markers`` (enough to
    identify the record in a terminal) plus, for modified records, a
    ``changes`` map with per-field left/right pairs. Edges are
    ``(source, target, type)`` tuples. Stability matters more than speed
    here — the output is sorted by id so consecutive diffs of the same
    graphs produce identical text output (friendly for ``diff -u``).
    """
    left = _resolve_graph_arg(payload, "left")
    right = _resolve_graph_arg(payload, "right")

    left_records = left.get("records") or {}
    right_records = right.get("records") or {}

    left_ids = set(left_records.keys())
    right_ids = set(right_records.keys())

    added_ids = sorted(right_ids - left_ids)
    removed_ids = sorted(left_ids - right_ids)
    shared_ids = sorted(left_ids & right_ids)

    def _summary_entry(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record.get("id"),
            "title": record.get("title"),
            "markers": record.get("markers") or {},
        }

    records_added = [_summary_entry(right_records[rid]) for rid in added_ids]
    records_removed = [_summary_entry(left_records[rid]) for rid in removed_ids]

    records_modified: list[dict[str, Any]] = []
    for rid in shared_ids:
        left_fp = _record_fingerprint(left_records[rid])
        right_fp = _record_fingerprint(right_records[rid])
        changes = _diff_record_fingerprints(left_fp, right_fp)
        if not changes:
            continue
        records_modified.append(
            {
                "id": rid,
                "title": right_records[rid].get("title"),
                "changes": changes,
            }
        )

    left_edges = {_edge_key(edge) for edge in left.get("edges") or []}
    right_edges = {_edge_key(edge) for edge in right.get("edges") or []}

    edges_added_keys = sorted(right_edges - left_edges)
    edges_removed_keys = sorted(left_edges - right_edges)

    def _edge_entry(key: tuple[Any, Any, Any]) -> dict[str, Any]:
        return {"source": key[0], "target": key[1], "type": key[2]}

    edges_added = [_edge_entry(key) for key in edges_added_keys]
    edges_removed = [_edge_entry(key) for key in edges_removed_keys]

    summary = {
        "recordsAdded": len(records_added),
        "recordsRemoved": len(records_removed),
        "recordsModified": len(records_modified),
        "edgesAdded": len(edges_added),
        "edgesRemoved": len(edges_removed),
    }

    return {
        "recordsAdded": records_added,
        "recordsRemoved": records_removed,
        "recordsModified": records_modified,
        "edgesAdded": edges_added,
        "edgesRemoved": edges_removed,
        "summary": summary,
    }


def format_graph_diff(diff: dict[str, Any]) -> str:
    """Render a ``graph_diff`` payload as a human-readable block of text.

    Mirrors the JSON shape: records first (added / removed / modified),
    then edges, then a single summary line. Kept stdlib-only so the CLI
    does not need to depend on a formatting library.
    """
    lines: list[str] = []

    def _section(header: str, items: list[Any]) -> None:
        lines.append(f"{header} ({len(items)})")
        if not items:
            lines.append("  (none)")
            return
        for item in items:
            lines.append(_format_diff_item(item))

    _section("Records added", diff.get("recordsAdded") or [])
    lines.append("")
    _section("Records removed", diff.get("recordsRemoved") or [])
    lines.append("")
    _section("Records modified", diff.get("recordsModified") or [])
    lines.append("")
    _section("Edges added", diff.get("edgesAdded") or [])
    lines.append("")
    _section("Edges removed", diff.get("edgesRemoved") or [])
    lines.append("")
    summary = diff.get("summary") or {}
    lines.append(
        f"Summary: {summary.get('recordsAdded', 0)} records added, "
        f"{summary.get('recordsRemoved', 0)} removed, "
        f"{summary.get('recordsModified', 0)} modified; "
        f"{summary.get('edgesAdded', 0)} edges added, "
        f"{summary.get('edgesRemoved', 0)} removed."
    )
    return "\n".join(lines)


def _format_diff_item(item: Any) -> str:
    if not isinstance(item, dict):
        return f"  {item!r}"
    # Edge shape: source/target/type.
    if "source" in item and "target" in item and "type" in item:
        return f"  ({item['source']}, {item['target']}, {item['type']})"
    # Modified record: id + changes.
    if "changes" in item:
        rid = item.get("id")
        title = item.get("title") or ""
        changed_keys = ", ".join(sorted((item.get("changes") or {}).keys()))
        return f"  {rid}  [{changed_keys}]  {title}"
    # Added/removed record summary.
    rid = item.get("id") or ""
    title = item.get("title") or ""
    return f"  {rid}  {title}"


def inspect_record(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explain why a record scores what it does for ``query``.

    Loads the graph at ``graphPath``, finds ``recordId``, recomputes the
    retrieval score using the exact same helper that ``build_context_pack``
    uses, and returns the full per-factor breakdown plus the record's
    rank (1-based) in the top-k for that query. This is a read-only
    introspection tool — it never writes and it never tweaks the scoring
    math. If we ever want to change how scores are reported, we change
    ``_score_record_detailed`` and both the ranker and the explainer move
    together.
    """
    schema = schema or load_schema()
    record_id = payload.get("recordId")
    if not record_id:
        raise ValueError("inspect_record requires recordId.")
    record_id = str(record_id)
    query = str(payload.get("query") or "")
    graph_path_arg = payload.get("graphPath")
    if not graph_path_arg and payload.get("workspaceRoot"):
        graph_path_arg = str(
            default_graph_path(Path(str(payload["workspaceRoot"])).expanduser().resolve())
        )
    graph = load_graph(graph_path_arg)
    records = graph.get("records") or {}
    record = records.get(record_id)
    if record is None:
        raise ValueError(f"Record not found: {record_id}")

    workspace_start = (
        Path(str(payload["workspaceRoot"])).resolve() if payload.get("workspaceRoot") else None
    )
    query_markers = normalize_markers(payload.get("markers", {}) or {}, schema)
    if query:
        query_markers = {**extract_query_markers(query, schema), **query_markers}
    query_tokens = tokenize(query)
    importance = _load_importance(workspace_start)
    # Resolve the optional intent mode / override so the explainer
    # reproduces the exact same score a retrieval caller would see if
    # they passed the same intent to build_context_pack or search_graph.
    intent = resolve_intent(payload.get("intentMode"), payload.get("intentOverride"))

    detail = _score_record_detailed(
        record, query_markers, query_tokens, importance, intent=intent
    )

    # Compute rank by scoring every record against the same query. Ties are
    # broken by the same stable sort used in build_context_pack so the rank
    # reported here is the rank a user would see in a context pack.
    limit = int(payload.get("limit", 8))
    include_archived = bool(payload.get("includeArchived", False))
    scored_pairs: list[tuple[float, str]] = []
    for other_id, other_record in records.items():
        if not include_archived and other_record.get("archived"):
            continue
        other_score = _score_record_detailed(
            other_record, query_markers, query_tokens, importance, intent=intent
        )["score"]
        if other_score <= 0:
            continue
        scored_pairs.append((other_score, other_id))
    # Stable sort by descending score then by id — matches the ranker's
    # "sort by score desc" with Python's stable tie-break (insertion order).
    scored_pairs.sort(key=lambda pair: (-pair[0], pair[1]))
    rank = None
    for idx, (_score, rid) in enumerate(scored_pairs, start=1):
        if rid == record_id:
            rank = idx
            break
    in_top_k = rank is not None and rank <= limit

    # Edges that touch this record — surface only those the retrieval
    # layer would surface (outgoing) plus incoming for symmetry. TTL is
    # not applied here because inspect_record is debugging the score, not
    # the pack rendering.
    outgoing_edges = [
        edge for edge in graph.get("edges") or [] if edge.get("source") == record_id
    ]
    incoming_edges = [
        edge for edge in graph.get("edges") or [] if edge.get("target") == record_id
    ]

    return {
        "id": record_id,
        "title": record.get("title"),
        "markers": record.get("markers") or {},
        "query": query,
        "queryTokens": sorted(query_tokens),
        "queryMarkers": query_markers,
        "matchedMarkers": detail["matchedMarkers"],
        "matchedTokens": detail["matchedTokens"],
        "factors": detail["factors"],
        "score": detail["score"],
        "rank": rank,
        "inTopK": in_top_k,
        "limit": limit,
        "outgoingEdges": outgoing_edges,
        "incomingEdges": incoming_edges,
        "graphPath": str(Path(graph_path_arg) if graph_path_arg else default_graph_path()),
    }


def format_inspect_record(result: dict[str, Any]) -> str:
    """Render an ``inspect_record`` payload as plain text for the CLI."""
    lines: list[str] = []
    lines.append(f"Record:  {result.get('id')}  {result.get('title') or ''}")
    markers = result.get("markers") or {}
    if markers:
        lines.append(
            "Markers: "
            + ", ".join(f"{key}={value}" for key, value in sorted(markers.items()))
        )
    lines.append(f"Query:   {result.get('query') or ''}")
    lines.append("Query tokens: " + ", ".join(result.get("queryTokens") or []))
    qm = result.get("queryMarkers") or {}
    if qm:
        lines.append(
            "Query markers: "
            + ", ".join(f"{key}={value}" for key, value in sorted(qm.items()))
        )
    lines.append(
        "Matched markers: "
        + (", ".join(result.get("matchedMarkers") or []) or "(none)")
    )
    lines.append(
        "Matched tokens:  "
        + (", ".join(result.get("matchedTokens") or []) or "(none)")
    )
    factors = result.get("factors") or {}
    lines.append("Factors:")
    for key in ("markerMatch", "tokenMatch", "severity", "status", "freshness"):
        factor = factors.get(key) or {}
        contribution = factor.get("contribution", 0)
        weight = factor.get("weight", 0)
        lines.append(f"  {key:14s} weight={weight}  contribution={contribution}")
    # Intent sub-factors — only printed when an intent mode was applied
    # to the score. Keeps non-intent output byte-identical.
    if "intentMarkerMultiplier" in factors:
        lines.append("  intentMarkerMultiplier:")
        for axis, val in sorted(factors["intentMarkerMultiplier"].items()):
            lines.append(f"    {axis}: {val}")
    if "intentTypeBoost" in factors:
        tb = factors["intentTypeBoost"]
        lines.append(f"  intentTypeBoost: {tb.get('type')} -> {tb.get('value')}")
    if "intentStatusBias" in factors:
        sb = factors["intentStatusBias"]
        lines.append(f"  intentStatusBias: {sb.get('status')} -> {sb.get('value')}")
    if "intentFreshnessMultiplier" in factors:
        fm = factors["intentFreshnessMultiplier"]
        lines.append(f"  intentFreshnessMultiplier: {fm.get('value')}")
    lines.append(f"Final score: {result.get('score')}")
    rank = result.get("rank")
    limit = result.get("limit")
    if rank is None:
        lines.append("Rank:        not ranked (score=0 or filtered)")
    else:
        top_marker = " (in top-k)" if result.get("inTopK") else ""
        lines.append(f"Rank:        {rank} (limit {limit}){top_marker}")
    outgoing = result.get("outgoingEdges") or []
    incoming = result.get("incomingEdges") or []
    if outgoing:
        lines.append(f"Outgoing edges ({len(outgoing)}):")
        for edge in outgoing:
            lines.append(
                f"  -> {edge.get('target')}  {edge.get('type')} "
                f"[{edge.get('kind')}, conf={edge.get('confidence')}]"
            )
    if incoming:
        lines.append(f"Incoming edges ({len(incoming)}):")
        for edge in incoming:
            lines.append(
                f"  <- {edge.get('source')}  {edge.get('type')} "
                f"[{edge.get('kind')}, conf={edge.get('confidence')}]"
            )
    return "\n".join(lines)
