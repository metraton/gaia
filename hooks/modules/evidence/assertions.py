"""
Declarative assertion DSL for evidence runner.

Contract:
    evaluate(assert_spec: dict, data: Any) -> bool

Ops (10 total):
    contains, equals, gte, lte, eq, ne,
    has_field, length_gte, length_eq, matches

Path resolution:
    - Dotted segments traverse dict keys ("a.b.c").
    - Root "" (empty path) targets `data` itself.
    - Missing segment -> evaluate returns False (KeyError swallowed).

Unknown ops raise ValueError -- fail fast rather than silently return False.
"""
from __future__ import annotations

import re
from typing import Any

_ALLOWED_OPS = frozenset({
    "contains",
    "equals",
    "eq",
    "ne",
    "gte",
    "lte",
    "has_field",
    "length_gte",
    "length_eq",
    "matches",
})


class _Missing:
    """Sentinel used by _resolve_path to distinguish 'absent' from 'None'."""

    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_MISSING = _Missing()


def _resolve_path(data: Any, path: str) -> Any:
    """Return the value at `path` or _MISSING if any segment is absent.

    Empty path -> data itself.
    """
    if path == "" or path is None:
        return data

    current: Any = data
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return _MISSING
    return current


def evaluate(assert_spec: dict, data: Any) -> bool:
    """Evaluate a single assert spec against `data` and return a boolean.

    assert_spec shape:
        { op: <str>, path: <dotted str>, value: <literal> }
        - value is optional for has_field.
    """
    op = assert_spec.get("op")
    if op not in _ALLOWED_OPS:
        raise ValueError(f"Unknown assert op: {op!r}")

    path = assert_spec.get("path", "")
    value = assert_spec.get("value")

    resolved = _resolve_path(data, path)

    # has_field: resolves entirely on presence/absence.
    if op == "has_field":
        return resolved is not _MISSING

    # Every other op requires a resolved value.
    if resolved is _MISSING:
        return False

    if op == "contains":
        try:
            return value in resolved
        except TypeError:
            return False

    if op in ("equals", "eq"):
        return resolved == value

    if op == "ne":
        return resolved != value

    if op == "gte":
        try:
            return resolved >= value
        except TypeError:
            return False

    if op == "lte":
        try:
            return resolved <= value
        except TypeError:
            return False

    if op == "length_gte":
        try:
            return len(resolved) >= value
        except TypeError:
            return False

    if op == "length_eq":
        try:
            return len(resolved) == value
        except TypeError:
            return False

    if op == "matches":
        if not isinstance(resolved, str) or not isinstance(value, str):
            return False
        try:
            return re.search(value, resolved) is not None
        except re.error:
            return False

    # Defensive; _ALLOWED_OPS was checked up front.
    raise ValueError(f"Unhandled op: {op!r}")
