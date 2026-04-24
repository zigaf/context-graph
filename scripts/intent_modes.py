"""Query-time intent modes for retrieval scoring.

Intent modes are pure, immutable presets that tune per-axis marker
weights, record-type boosts, status bias, freshness multiplier, hop
penalty, hop cap, and the allowed set of relation types during
traversal. The presets are applied in ``_score_record_detailed`` and in
the ``build_context_pack`` traversal loop; helpers return neutral values
when ``intent`` is ``None`` so the non-intent path is a strict no-op.

See ``docs/superpowers/specs/2026-04-24-intent-modes-design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntentMode:
    name: str
    marker_weights: dict[str, float] = field(default_factory=dict)
    type_boost: dict[str, float] = field(default_factory=dict)
    status_bias: dict[str, float] = field(default_factory=dict)
    freshness_multiplier: float = 1.0
    hop_penalty: float | None = None
    hop_cap: int = 1
    allowed_relations: frozenset[str] | None = None
    include_archived: bool | None = None


# The neutral baseline mirrors the non-intent defaults: unit multipliers
# everywhere, ``hop_penalty=None`` (fall back to the global
# ``HOP_PENALTY``), ``hop_cap=1``, and ``allowed_relations=None`` (all
# types permitted). Used internally by ``resolve_intent`` when the
# caller passes an ``override`` without a preset name.
NEUTRAL_INTENT = IntentMode(name="")


PRESETS: dict[str, IntentMode] = {
    "debug": IntentMode(
        name="debug",
        marker_weights={"severity": 2.5, "artifact": 2.5, "flow": 1.5, "type": 2.0},
        type_boost={"bug": 1.5, "incident": 1.5, "debug": 1.5},
        status_bias={
            "in-progress": 1.5, "known-risk": 1.3, "new": 1.2,
            "fixed": 0.6, "done": 0.6,
        },
        freshness_multiplier=1.5,
        hop_penalty=0.7,
        hop_cap=2,
        allowed_relations=frozenset({"might_affect", "same_pattern_as"}),
        include_archived=False,
    ),
    "implementation": IntentMode(
        name="implementation",
        marker_weights={"flow": 2.0, "artifact": 2.0, "goal": 1.5, "type": 2.0},
        type_boost={"rule": 1.5, "spec": 1.5, "pattern": 1.5, "decision": 1.3},
        status_bias={"done": 1.3, "fixed": 1.1},
        freshness_multiplier=1.0,
        hop_penalty=0.3,
        hop_cap=1,
        allowed_relations=frozenset({"same_pattern_as", "derived_from"}),
        include_archived=False,
    ),
    "architecture": IntentMode(
        name="architecture",
        marker_weights={"domain": 2.5, "scope": 2.5, "project": 1.5, "type": 2.0},
        type_boost={"architecture": 1.8, "decision": 1.5, "rule": 1.3},
        status_bias={},
        freshness_multiplier=0.3,
        hop_penalty=0.6,
        hop_cap=3,
        allowed_relations=frozenset({"derived_from", "related_pattern"}),
        include_archived=False,
    ),
    "product": IntentMode(
        name="product",
        marker_weights={"goal": 2.5, "project": 2.0, "room": 2.0, "type": 2.0},
        type_boost={"spec": 1.5, "research": 1.5, "decision": 1.3},
        status_bias={"new": 1.3, "in-progress": 1.2, "known-risk": 1.2},
        freshness_multiplier=1.2,
        hop_penalty=0.5,
        hop_cap=2,
        allowed_relations=frozenset({"related_pattern", "derived_from"}),
        include_archived=False,
    ),
}


# Mapping from camelCase override field names (JSON payload) to
# snake_case IntentMode attributes.
_OVERRIDE_FIELD_MAP = {
    "markerWeights": "marker_weights",
    "typeBoost": "type_boost",
    "statusBias": "status_bias",
    "freshnessMultiplier": "freshness_multiplier",
    "hopPenalty": "hop_penalty",
    "hopCap": "hop_cap",
    "allowedRelations": "allowed_relations",
    "includeArchived": "include_archived",
}


def _merge_override(base: IntentMode, override: dict) -> IntentMode:
    """Return a new IntentMode with fields replaced by override values.

    Only keys in ``_OVERRIDE_FIELD_MAP`` are consulted; unknown keys are
    silently ignored (lenient per spec §9 Q2). Dict/list fields in the
    override fully replace the corresponding preset field — they do NOT
    recursively merge.
    """
    updates: dict[str, object] = {}
    for src_key, dst_attr in _OVERRIDE_FIELD_MAP.items():
        if src_key not in override:
            continue
        value = override[src_key]
        if dst_attr == "allowed_relations":
            updates[dst_attr] = frozenset(value) if value is not None else None
        else:
            updates[dst_attr] = value
    if not updates:
        return base
    from dataclasses import replace
    return replace(base, **updates)


def resolve_intent(
    mode_name: str | None,
    override: dict | None,
) -> IntentMode | None:
    """Resolve an intent mode + optional partial override to an IntentMode.

    Returns ``None`` when both inputs are ``None`` so the non-intent path
    remains a strict no-op. An override alone (no mode) yields a mode
    derived from the neutral baseline — the power-user / eval-harness
    path. An unknown mode name raises ValueError with the list of known
    preset names.
    """
    if mode_name is None and override is None:
        return None
    if mode_name is not None:
        base = PRESETS.get(mode_name)
        if base is None:
            known = sorted(PRESETS.keys())
            raise ValueError(
                f"Unknown intentMode: {mode_name!r}. Expected one of {known}."
            )
    else:
        base = NEUTRAL_INTENT
    if override:
        return _merge_override(base, override)
    return base
