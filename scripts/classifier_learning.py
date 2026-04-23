"""Corpus learner for adaptive taxonomy proposals."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,}")
_PATH_RE = re.compile(r"\b[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+){1,}\b")
_COMMON_PATH_PARTS = {
    "src",
    "app",
    "lib",
    "modules",
    "module",
    "dist",
    "build",
    "node_modules",
    "test",
    "tests",
    "spec",
    "specs",
    "assets",
    "public",
    "utils",
    "util",
    "index",
    "main",
    "config",
    "configs",
    "package",
    "packages",
}
_STOP_TOKENS = {
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
    "has",
    "have",
    "was",
    "were",
    "are",
    "been",
    "being",
    "when",
    "while",
    "then",
    "than",
    "but",
    "you",
    "your",
    "our",
    "their",
    "its",
    "his",
    "her",
    "they",
    "them",
    "these",
    "those",
}


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


def _tokens_in_order(text: str) -> list[str]:
    return [match.group(0) for match in _TOKEN_RE.finditer(text.lower())]


def mine_hierarchy(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = len(records)
    if total < 2:
        return []

    ancestor_records: dict[str, set[str]] = {}
    ancestor_depths: dict[str, list[int]] = {}

    for record in records:
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        parent = str(metadata.get("parent") or "")
        parts = [part.strip() for part in parent.split(">") if part.strip()]
        for depth, part in enumerate(parts):
            key = _slug(part)
            if not key:
                continue
            record_id = str(record.get("id") or "")
            ancestor_records.setdefault(key, set()).add(record_id)
            ancestor_depths.setdefault(key, []).append(depth)

    proposals: list[dict[str, Any]] = []
    for key, record_ids in ancestor_records.items():
        support = len(record_ids) / total
        depths = ancestor_depths[key]
        average_depth = sum(depths) / len(depths)
        if len(record_ids) < 2 or (support == 1.0 and average_depth == 0.0):
            continue
        distinctiveness = 1.0 - support
        depth_penalty = max(0.0, 1.0 - (average_depth / 5.0))
        confidence = round(
            0.4 * support + 0.4 * distinctiveness + 0.2 * depth_penalty,
            3,
        )
        if confidence < 0.30:
            continue
        proposals.append(
            {
                "value": key,
                "source": "hierarchy",
                "confidence": confidence,
                "supportRecords": sorted(record_ids)[:5],
                "detail": {
                    "averageDepth": round(average_depth, 2),
                    "occurrences": len(depths),
                },
            }
        )

    proposals.sort(key=lambda proposal: (-proposal["confidence"], proposal["value"]))
    return proposals


def mine_ngrams(
    records: list[dict[str, Any]],
    *,
    min_doc_freq: int = 2,
    min_confidence: float = 0.50,
    limit: int = 50,
) -> list[dict[str, Any]]:
    total = len(records)
    if total < 2:
        return []

    token_doc_count: Counter[str] = Counter()
    bigram_doc_count: Counter[tuple[str, str]] = Counter()
    bigram_records: dict[tuple[str, str], set[str]] = {}

    for record in records:
        text = " ".join([str(record.get("title") or ""), str(record.get("content") or "")])
        tokens = _tokens_in_order(text)
        token_doc_count.update(set(tokens))

        unique_bigrams: set[tuple[str, str]] = set()
        for left, right in zip(tokens, tokens[1:]):
            if left in _STOP_TOKENS or right in _STOP_TOKENS:
                continue
            unique_bigrams.add((left, right))
        bigram_doc_count.update(unique_bigrams)
        for bigram in unique_bigrams:
            bigram_records.setdefault(bigram, set()).add(str(record.get("id") or ""))

    log_total = math.log(total) or 1.0
    proposals: list[dict[str, Any]] = []
    for bigram, doc_freq in bigram_doc_count.items():
        if doc_freq < min_doc_freq or doc_freq == total:
            continue
        left, right = bigram
        left_freq = max(token_doc_count.get(left, 1), 1)
        right_freq = max(token_doc_count.get(right, 1), 1)
        idf_component = math.log(total / doc_freq) / log_total
        pmi = math.log((doc_freq * total) / (left_freq * right_freq))
        pmi_component = max(pmi / log_total, 0.0)
        support = doc_freq / total
        confidence = round(min(1.0, 0.6 * support + 0.25 * idf_component + 0.15 * pmi_component), 3)
        if confidence < min_confidence:
            continue
        proposals.append(
            {
                "value": f"{left}-{right}",
                "source": "ngram",
                "confidence": confidence,
                "supportRecords": sorted(bigram_records.get(bigram, set()))[:5],
                "detail": {
                    "ngram": [left, right],
                    "docFreq": doc_freq,
                    "pmi": round(pmi, 3),
                },
            }
        )

    proposals.sort(key=lambda proposal: (-proposal["confidence"], proposal["value"]))
    return proposals[:limit]


def mine_code_paths(
    records: list[dict[str, Any]],
    *,
    min_occurrences: int = 2,
    limit: int = 50,
) -> list[dict[str, Any]]:
    component_counts: Counter[str] = Counter()
    component_records: dict[str, set[str]] = {}

    for record in records:
        text = " ".join([str(record.get("title") or ""), str(record.get("content") or "")])
        for match in _PATH_RE.finditer(text):
            parts = [part.strip().lower() for part in match.group(0).split("/") if part.strip()]
            for part in parts:
                slug = _slug(part)
                if not slug or slug in _COMMON_PATH_PARTS:
                    continue
                if "." in part:
                    stem = part.rsplit(".", 1)[0]
                    slug = _slug(stem)
                if not slug or slug in _COMMON_PATH_PARTS:
                    continue
                component_counts[slug] += 1
                component_records.setdefault(slug, set()).add(str(record.get("id") or ""))

    proposals: list[dict[str, Any]] = []
    for component, count in component_counts.items():
        if count < min_occurrences:
            continue
        proposals.append(
            {
                "value": component,
                "source": "code-path",
                "confidence": round(min(1.0, 0.4 + 0.1 * count), 3),
                "supportRecords": sorted(component_records.get(component, set()))[:5],
                "detail": {"occurrences": count},
            }
        )

    proposals.sort(key=lambda proposal: (-proposal["confidence"], proposal["value"]))
    return proposals[:limit]


def compute_marker_importance(records: list[dict[str, Any]]) -> dict[str, float]:
    total = len(records)
    if total == 0:
        return {}

    field_populated: Counter[str] = Counter()
    field_values: dict[str, Counter[str]] = {}
    field_explicit: Counter[str] = Counter()
    explicit_regions = {"frontmatter", "metadataBlock"}

    for record in records:
        markers = record.get("markers") if isinstance(record.get("markers"), dict) else {}
        source = record.get("source") if isinstance(record.get("source"), dict) else {}
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        notes = metadata.get("classifierNotes") if isinstance(metadata.get("classifierNotes"), dict) else {}
        regions_used = set(notes.get("regionsUsed") or [])
        used_explicit = bool(regions_used & explicit_regions)

        for field, value in markers.items():
            if value in (None, "", []):
                continue
            field_populated[field] += 1
            field_values.setdefault(field, Counter())[str(value)] += 1
            if used_explicit:
                field_explicit[field] += 1

    importance: dict[str, float] = {}
    for field, populated in field_populated.items():
        presence = populated / total
        values = field_values[field]
        if len(values) <= 1:
            discriminative = 0.0
        else:
            value_total = sum(values.values())
            probabilities = [count / value_total for count in values.values()]
            entropy = -sum(prob * math.log2(prob) for prob in probabilities if prob > 0)
            max_entropy = math.log2(len(values))
            discriminative = entropy / max_entropy if max_entropy > 0 else 0.0
        explicit_rate = field_explicit.get(field, 0) / populated if populated else 0.0
        importance[field] = round(
            0.3 * presence + 0.4 * discriminative + 0.3 * explicit_rate,
            3,
        )

    return importance


def run_full_pass(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "proposals": {
            "hierarchy": mine_hierarchy(records),
            "ngram": mine_ngrams(records),
            "codePath": mine_code_paths(records),
        },
        "markerImportance": compute_marker_importance(records),
        "corpusSize": len(records),
    }
