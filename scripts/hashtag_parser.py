"""Translate ``#word`` tokens in a query string into a ``markers`` payload
keyed by the schema's axis owning the word's value.

The parser is pure stdlib and side-effect free. Unknown tags are left in
the query (so the user notices the typo) and the function does not
raise on them.
"""

from __future__ import annotations

import re
from typing import Any

_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_-]+)")


def _index_schema_values(schema: dict[str, Any]) -> dict[str, str]:
    """Build a {value_lower: axis} index from schema['markers']."""
    markers = (schema or {}).get("markers") or {}
    index: dict[str, str] = {}
    for axis, values in markers.items():
        if not isinstance(values, list):
            continue
        for value in values:
            key = str(value).lower()
            # If the same value appears under two axes (does not currently
            # happen), the first axis encountered wins.
            index.setdefault(key, axis)
    return index


def parse_hashtags(query: str, schema: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Extract ``#word`` tokens that match a schema marker value.

    Returns ``(remaining_query, markers)``. ``remaining_query`` is the
    original query with matched ``#word`` tokens removed (and whitespace
    cleaned). ``markers`` maps the resolved axis to the canonical
    (lowercase) value. Unknown tags are kept in the query verbatim.

    Repeated tags targeting the same axis: the last one wins.
    """
    index = _index_schema_values(schema)
    markers: dict[str, str] = {}

    def _replace(match: re.Match) -> str:
        word = match.group(1).lower()
        axis = index.get(word)
        if axis is None:
            return match.group(0)  # keep unknown tag in the query
        markers[axis] = word
        return ""

    new_query = _HASHTAG_RE.sub(_replace, query or "")
    new_query = re.sub(r"\s+", " ", new_query).strip()
    return new_query, markers
