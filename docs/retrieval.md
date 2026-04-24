# Retrieval Policy

Context Graph should not fetch all notes for every request.

It should build a compact context pack with this order:

1. Extract query signals:
   `type`, `project`, `domain`, `flow`, `goal`, `severity`, `status`
2. Fetch exact marker matches first.
3. Add one-hop explicit relations from those matches.
4. Add inferred relations only when the request is exploratory, architectural, or ambiguous.
5. Re-rank by:
   - exactness
   - relation distance
   - severity
   - freshness
   - status relevance
6. Return only the smallest set that explains the current task.

## Ranking guidance

- exact scope beats inferred scope
- confirmed relations beat probable relations
- critical and in-progress records beat done records
- rules and decisions decay slower than tasks and incidents

## Freshness decay

Every ranked record has its raw score multiplied by a type-specific decay
factor:

```
factor = 0.5 ** (age_days / half_life_days)
```

where ``age_days`` is computed from the first timestamp that parses, in order:
``revision.updatedAt`` → ``source.metadata.last_edited_time`` →
``classifiedAt``. Records with no parseable timestamp get a factor of 1.0
(no penalty) so fixtures and legacy records are not silently demoted.

Default half-lives (days), keyed by ``markers.type``:

| type | half-life |
|---|---|
| rule | 365 |
| decision | 180 |
| architecture | 180 |
| pattern | 180 |
| task | 30 |
| incident | 30 |
| bug | 30 |
| *(unknown)* | 60 |

Callers override by passing ``freshnessHalfLifeDays: {type: number}`` in the
``build_context_pack`` payload. Missing keys fall back to the defaults;
``null`` or missing means "use defaults entirely". The implementation is
``scripts/context_graph_core.py::type_freshness_factor`` and the default map
lives at ``FRESHNESS_HALF_LIFE_DAYS``.

## Distance penalties

Records reached via explicit-relation traversal carry a ``hopCount`` in the
returned pack:

- ``0`` — direct query match (marker or token overlap)
- ``1`` — one-hop neighbor of a direct match
- ``2+`` — multi-hop (only visible when the caller raises the traversal cap)

The penalty applied on top of the raw score is:

```
score *= HOP_PENALTY ** max(0, hop_count - 1)
```

so hop 0 and hop 1 are unpenalized and every hop after the first contributes
another factor of ``HOP_PENALTY`` (default ``0.5``). In addition, a neighbor
reached via traversal that has no query relevance of its own inherits a
decayed share of the seed score (one ``HOP_PENALTY`` step per edge), so the
"direct > one-hop > two-hop" ordering is observable even when neighbors share
no marker or token with the query.

Current traversal cap: ``maxHops = 1`` unless the caller passes
``hopTraversal: {maxHops: N}``. The scoring hook handles any depth; raising
the cap is a single config change. Payload overrides: ``hopPenalty`` and
``hopTraversal.maxHops``. Implementation: ``apply_hop_penalty`` and the
frontier loop inside ``build_context_pack``.

## Conflict-aware promotion

``promote_pattern`` detects two kinds of conflict in the source cohort:

- **Marker conflicts** — two records that disagree on a marker value
  (e.g. one is ``type: rule``, another ``type: decision``). Already surfaced
  as ``quality.conflicts`` and ``splitSuggestions``.
- **Content-negation conflicts** — two records that share a content token
  but one affirms it and the other negates it ("retry" vs "do not retry").
  Detection is deterministic: a negation word (``not``, ``never``, ``avoid``,
  ...) scopes only the next content word, with stopwords transparent. The
  detector is ``scripts/context_graph_core.py::detect_content_conflicts``.

When a content-negation conflict splits the cohort cleanly (at least one
record on each side), ``promote_pattern`` emits **one proposal per sub-
cohort** instead of a single self-contradicting promotion. Each proposal
carries a narrower scope in its title (e.g. ``... (when retry)`` vs ``...
(when not retry)``) and in its content body.

Returned payload shape:

```
{
  "promotedRecord": <first proposal, for backward compatibility>,
  "promotedRecords": [<proposal>, ...],
  "conflicts": [
    {"kind": "content-negation", "token": "retry",
     "affirmative": [<id>, ...], "negated": [<id>, ...],
     "recordIds": [<id>, ...]}
  ],
  ...
}
```

``conflicts`` is always present: it is an empty list when the cohort is
internally consistent. Callers that want "one promotion, split if needed"
should read ``promotedRecords``; callers that only need a single example can
keep reading ``promotedRecord``.

## Output shape

Each context pack should contain:

- direct matches
- supporting relations
- promoted rules
- unresolved risks
- omitted-but-nearby records count
