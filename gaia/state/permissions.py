"""
gaia.state.permissions -- Permission matrix for state-machine transitions.

Implements D1 (permission matrix) from the state-machine-completion brief:

* Subagents (developer, terraform-architect, gitops-operator, gaia-system,
  and any agent outside the orchestrator/operator group) may transition
  ``tasks`` and ``acceptance_criteria`` status.
* Only orchestrator/operator may transition ``milestones``, ``briefs``,
  and ``plans`` status.

The guard function mirrors the pattern of ``_assert_dispatch_can_write_memory``
in ``gaia.store.writer``:

* ``GAIA_DISPATCH_AGENT`` unset / empty -> human CLI caller -> always allowed.
* Set to a curator identity -> allowed.
* Set to any other value on a curator-only table -> raises
  ``StateTransitionForbidden``.

Note: ``_assert_dispatch_can_advance_state`` is intentionally generic enough
to serve both status transitions (T2.1/T2.2) and CRUD mutations on
curator-only tables (T5.2 milestones). The table name is the discriminator.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Permission matrix (D1)
# ---------------------------------------------------------------------------

DISPATCH_PERMISSIONS: dict[str, dict[str, bool]] = {
    "tasks":               {"curator_only": False},
    "acceptance_criteria": {"curator_only": False},
    "milestones":          {"curator_only": True},
    "briefs":              {"curator_only": True},
    "plans":               {"curator_only": True},
}

# Curator identities (mirroring _MEMORY_CURATOR_AGENTS in writer.py)
_CURATOR_AGENTS: frozenset[str] = frozenset({
    "orchestrator",
    "operator",
    "gaia-orchestrator",
    "gaia-operator",
})


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class StateTransitionForbidden(PermissionError):
    """Raised when a non-curator agent attempts to transition a curator-only
    state-machine table.

    ``PermissionError`` as the base class enables callers to distinguish
    permission failures from value/validation errors without importing this
    exception explicitly.
    """


# ---------------------------------------------------------------------------
# Guard function
# ---------------------------------------------------------------------------

def _assert_dispatch_can_advance_state(table: str) -> None:
    """Block state transitions on curator-only tables from non-curator dispatches.

    Reads ``GAIA_DISPATCH_AGENT`` from the environment. Contract:

    * Unset -> human CLI caller. Allowed.
    * Empty string -> treated as unset. Allowed.
    * Curator identity -> allowed on all tables.
    * Non-curator on ``curator_only=True`` table -> raises
      ``StateTransitionForbidden``.
    * Non-curator on ``curator_only=False`` table -> allowed.
    * Unknown table (not in DISPATCH_PERMISSIONS) -> allowed by default
      (fail-open for tables not yet registered; add them to
      ``DISPATCH_PERMISSIONS`` to enforce).

    Args:
        table: Name of the DB table being mutated (e.g. ``'tasks'``,
               ``'milestones'``).
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        # Human CLI caller or env var not set: always allowed.
        return

    agent = raw.strip()
    if not agent:
        return

    if agent in _CURATOR_AGENTS:
        return

    perm = DISPATCH_PERMISSIONS.get(table)
    if perm is None:
        # Table not in matrix: fail-open (unknown tables are not yet guarded).
        return

    if perm["curator_only"]:
        raise StateTransitionForbidden(
            f"State transitions on '{table}' are restricted to curator agents "
            f"(orchestrator/operator). Current GAIA_DISPATCH_AGENT={agent!r}. "
            f"Only orchestrator/operator may transition briefs, plans, and "
            f"milestones. Subagents may transition tasks and acceptance_criteria."
        )


__all__ = [
    "DISPATCH_PERMISSIONS",
    "StateTransitionForbidden",
    "_assert_dispatch_can_advance_state",
]
