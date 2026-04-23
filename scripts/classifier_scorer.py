"""Deterministic scorer and arbiter for the adaptive classifier."""

from __future__ import annotations

import math
import re
from typing import Any


REGION_WEIGHTS: dict[str, float] = {
    "frontmatter": 5.0,
    "metadataBlock": 4.0,
    "titleText": 3.0,
    "breadcrumb": 2.0,
    "body": 1.0,
}

HIGH_CONFIDENCE = 0.75
MIN_GAP = 0.15
MIN_SCORE = 0.20

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")


def _tokenize(text: str) -> list[str]:
    return [match.group(0) for match in _TOKEN_RE.finditer(text.lower())]


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def _canonical_forms(field: str, value: str, schema: dict[str, Any]) -> list[str]:
    aliases = (schema.get("aliases", {}) or {}).get(field, {})
    return [_slug(value), *[_slug(alias) for alias in aliases.get(value, [])]]


def _matches(token: str, canonical_forms: list[str]) -> bool:
    return _slug(token) in canonical_forms


def _infer_corpus_size(document_frequency: dict[str, int]) -> int:
    if not document_frequency:
        return 0
    return max(document_frequency.values())


def _idf_weight(token: str, document_frequency: dict[str, int], corpus_size: int) -> float:
    if corpus_size <= 0:
        return 1.0
    df = max(int(document_frequency.get(token, 1)), 1)
    return math.log(corpus_size / df)


def _max_idf(document_frequency: dict[str, int], corpus_size: int) -> float:
    if corpus_size <= 0 or not document_frequency:
        return 1.0
    rarest = max(min(document_frequency.values()), 1)
    return max(math.log(corpus_size / rarest), 1.0)


def score_field(
    field: str,
    regions: dict[str, str],
    schema: dict[str, Any],
    idf: dict[str, int],
) -> list[dict[str, Any]]:
    """Return allowed marker values ranked by region-weighted score."""
    allowed_values = (schema.get("markers", {}) or {}).get(field, [])
    if not allowed_values:
        return []

    corpus_size = _infer_corpus_size(idf)
    max_idf = _max_idf(idf, corpus_size)
    total_weight = sum(REGION_WEIGHTS.values())
    results: list[dict[str, Any]] = []

    for value in allowed_values:
        canonical_forms = _canonical_forms(field, value, schema)
        raw_score = 0.0
        matched = False
        for region_name, region_text in regions.items():
            weight = REGION_WEIGHTS.get(region_name, 0.0)
            if weight == 0.0 or not region_text:
                continue
            for token in _tokenize(region_text):
                if _matches(token, canonical_forms):
                    matched = True
                    raw_score += weight * _idf_weight(token, idf, corpus_size)

        normalized = raw_score / (total_weight * max_idf)
        results.append({"value": value, "score": round(normalized, 4), "matched": matched})

    results.sort(key=lambda item: (-item["score"], not item["matched"], item["value"]))
    return results


def arbitrate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not scores or scores[0]["score"] < MIN_SCORE:
        return {"arbiter": "fallback", "value": None, "top": None, "gap": 0.0}

    top = scores[0]
    runner = scores[1] if len(scores) > 1 else {"score": 0.0}
    gap = round(float(top["score"]) - float(runner["score"]), 4)

    if top["score"] >= HIGH_CONFIDENCE and gap >= MIN_GAP:
        return {"arbiter": "deterministic", "value": top["value"], "top": top, "gap": gap}

    reason = "below HIGH_CONFIDENCE" if top["score"] < HIGH_CONFIDENCE else "gap below MIN_GAP"
    return {
        "arbiter": "pending-arbitration",
        "value": top["value"],
        "top": top,
        "gap": gap,
        "reason": reason,
    }
