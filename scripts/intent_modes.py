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
