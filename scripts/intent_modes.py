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
