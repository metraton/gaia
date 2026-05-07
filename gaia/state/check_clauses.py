"""
gaia.state.check_clauses -- Build SQL CHECK clauses from Python tuples.

Helper used by the schema migration (and tests) to ensure the SQL CHECK
expression always derives from the same Python tuple that runtime code
imports. If the tuple changes, the generated clause changes; the diff
tool (``tools/state/diff_source_of_truth.py``) detects when DB and
Python disagree.
"""

from __future__ import annotations

from typing import Iterable


def _quote_value(value: str) -> str:
    """Single-quote a SQL string literal, escaping embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


def build_check_clause(
    column: str,
    valid_values: Iterable[str],
    *,
    allow_null: bool = False,
) -> str:
    """Build a ``CHECK`` clause body for ``column`` constrained to
    ``valid_values``.

    Args:
        column: Column name (e.g. ``"plan_status"`` or ``"status"``).
        valid_values: Ordered iterable of legal values. Order is preserved
            in the generated SQL to keep schema diffs deterministic.
        allow_null: If True, the clause accepts NULL via
            ``column IS NULL OR column IN (...)``. Used for ``episodes.
            plan_status`` where future episodes may legitimately omit a
            status (per the gaia-state-machines brief decision).

    Returns:
        A SQL fragment of the form ``CHECK(column IN ('a','b','c'))`` or,
        when ``allow_null=True``, ``CHECK(column IS NULL OR column IN
        ('a','b','c'))``. The returned string is the full ``CHECK(...)``
        wrapper, ready to drop into a ``CREATE TABLE`` body or a column
        definition.
    """
    values = list(valid_values)
    if not values:
        raise ValueError(
            f"valid_values for column {column!r} is empty -- "
            "an empty CHECK would reject every row"
        )

    in_list = ", ".join(_quote_value(v) for v in values)
    if allow_null:
        body = f"{column} IS NULL OR {column} IN ({in_list})"
    else:
        body = f"{column} IN ({in_list})"
    return f"CHECK ({body})"


__all__ = ["build_check_clause"]
