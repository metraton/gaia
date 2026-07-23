"""
gaia.state.permissions -- Permission matrix for state-machine transitions.

Implements D1 (permission matrix) from the state-machine-completion brief:

* Subagents (developer, platform-architect, gitops-operator, gaia-system,
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

import functools
import os
import re
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Content-authorship permissions (M2 -- brief/plan content gating)
# ---------------------------------------------------------------------------
#
# DISTINCT axis from DISPATCH_PERMISSIONS above. That matrix gates state
# TRANSITIONS on curator-only tables; this one gates who may author the CONTENT
# of a brief or plan via ``upsert_brief`` / ``upsert_plan``. Under the (now
# live) planner protocol authorship is split: the ORCHESTRATOR authors brief
# content (the brief-spec flow), the PLANNER authors plan content. A dispatched
# agent that is neither may not mutate brief/plan content.
#
# Why this cannot reuse DISPATCH_PERMISSIONS: the planner is a NON-curator that
# MUST be allowed to author PLAN content, yet must remain FORBIDDEN from
# transitioning plan STATUS (``plans`` is curator_only in DISPATCH_PERMISSIONS).
# Content authorship and lifecycle transition therefore have different
# authorized sets and cannot share a single matrix.
#
# Why not the ``agent_permissions`` DB table (_is_authorized in store.writer):
# that table requires an explicit allow_write row per (table, agent) and has no
# unset/human-CLI escape hatch, so it would BLOCK the human ``gaia brief``
# terminal flow (upsert_brief takes no ``agent`` argument). The env-var
# dispatch-guard pattern -- fail-open when GAIA_DISPATCH_AGENT is unset -- is the
# model the sibling guards (memory/evidence/state) already use, and the only one
# that keeps the human/orchestrator-main-session flow working.

# Planner identities (bare + gaia- prefixed, mirroring the curator aliases).
_PLANNER_AGENTS: frozenset[str] = frozenset({"planner", "gaia-planner"})

CONTENT_AUTHOR_PERMISSIONS: dict[str, frozenset[str]] = {
    "briefs": _CURATOR_AGENTS,
    "plans": _CURATOR_AGENTS | _PLANNER_AGENTS,
}


class ContentWriteForbidden(PermissionError):
    """Raised when a dispatched agent lacks authority to author brief/plan content."""


def _assert_dispatch_can_write_content(table: str) -> None:
    """Block brief/plan CONTENT mutations from unauthorized subagent dispatches.

    Reads ``GAIA_DISPATCH_AGENT`` -- same contract as the sibling guards:

    * Unset / empty -> human CLI caller or orchestrator main session. Allowed.
    * Set to an author authorized for ``table`` -> allowed.
    * Set to any other identity -> raises ``ContentWriteForbidden``.
    * Unknown table (not in ``CONTENT_AUTHOR_PERMISSIONS``) -> allowed
      (fail-open for tables not yet registered).

    Args:
        table: Target table -- ``'briefs'`` or ``'plans'``.
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    agent = raw.strip()
    if not agent:
        return
    authors = CONTENT_AUTHOR_PERMISSIONS.get(table)
    if authors is None:
        return
    if agent in authors:
        return
    raise ContentWriteForbidden(
        f"Content authorship of '{table}' is restricted (current "
        f"GAIA_DISPATCH_AGENT={agent!r}). Brief content is authored by the "
        f"orchestrator; plan content by the planner. Authorized for '{table}': "
        f"{sorted(authors)}."
    )


# ---------------------------------------------------------------------------
# Handoff-writer fleet seed (T8 -- brief contract-as-managed-data)
# ---------------------------------------------------------------------------
#
# INVERSION of the handoff write-guard.
#
# The original ``_assert_dispatch_can_write_handoff`` (gaia.store.writer) was
# curator-only: every subagent dispatch was FORBIDDEN and only the SubagentStop
# hook (running with GAIA_DISPATCH_AGENT unset) or a curator identity could
# write a handoff row. Under the contract-as-managed-data model the terminal
# row is finalized BY the agent itself (``gaia contract finalize`` builds the
# contract by-value and promotes it via ``finalize_agent_contract_handoff``),
# so the gate inverts: EVERY agent in the fleet may finalize its own handoff.
#
# The fleet is SEEDED by the agent definitions themselves. Each agent under
# ``agents/`` carries a frontmatter marker ``contract_handoff_writer: true``;
# the loader below enumerates ``agents/*.md`` (skipping README) and collects
# the ``name:`` of every agent that opts in. The seed is therefore
# self-describing -- adding a new agent with the marker enrolls it in the
# fleet with no code change here, and ``tests/contract/test_finalize_store.py``
# asserts that every agent under ``agents/`` is present in the loaded fleet
# (drift detection, the POSITIVE arm of AC-7).
#
# A hardcoded fallback fleet is the floor: when the ``agents/`` directory
# cannot be located (some installed layouts, minimal sandboxes) the loader
# returns the known shipped identities rather than an empty set, so a
# legitimate finalize is never blocked by an unreadable source tree. Note the
# guard NEVER fails open to "allow everyone" -- an identity absent from the
# resolved fleet is always rejected; the fallback only substitutes a known
# non-empty fleet for an unresolvable one.
# ---------------------------------------------------------------------------

# Frontmatter marker that opts an agent .md into the handoff-writer fleet.
_HANDOFF_WRITER_MARKER = "contract_handoff_writer"

# Bare curator aliases (no gaia- prefix) kept authorized for back-compat with
# callers that set GAIA_DISPATCH_AGENT to the short form. The gaia-prefixed
# curators (gaia-orchestrator / gaia-operator) are seeded from agents/ like any
# other agent, but are also listed here so the fallback fleet is complete.
_HANDOFF_CURATOR_ALIASES: frozenset[str] = frozenset({"orchestrator", "operator"})

# Known agents shipped under agents/ -- the fallback fleet floor (see above).
_FALLBACK_HANDOFF_WRITER_FLEET: frozenset[str] = frozenset({
    "cloud-troubleshooter",
    "developer",
    "gaia-operator",
    "gaia-orchestrator",
    "gaia-planner",
    "gaia-system",
    "gitops-operator",
    "platform-architect",
}) | _HANDOFF_CURATOR_ALIASES

# Top-level frontmatter key line (``key: value``, no leading indentation).
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z][\w-]*)\s*:\s*(.*?)\s*$")


def _agents_dir() -> Path | None:
    """Locate the ``agents/`` directory relative to this module.

    ``permissions.py`` lives at ``<root>/gaia/state/permissions.py`` in both the
    source tree and the installed plugin (the npm package root and
    ``node_modules/@jaguilar87/gaia`` both carry ``gaia/`` and ``agents/`` as
    siblings), so ``parents[2]`` is the root that contains ``agents/``.
    """
    candidate = Path(__file__).resolve().parents[2] / "agents"
    return candidate if candidate.is_dir() else None


def _parse_agent_frontmatter(md_text: str) -> tuple[str | None, bool]:
    """Extract ``(name, is_handoff_writer)`` from an agent .md frontmatter block.

    Dependency-free: agent frontmatter is a leading ``---`` ... ``---`` block of
    simple top-level ``key: value`` lines. Only top-level keys (no leading
    whitespace) are read, so nested blocks (``routing:``, ``project_context_
    contracts:``) never shadow the marker. Returns the ``name`` value and
    whether ``contract_handoff_writer`` is truthy.
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (None, False)
    name: str | None = None
    is_writer = False
    for raw_line in lines[1:]:
        if raw_line.strip() == "---":
            break
        # Only consider top-level keys (no indentation) -- ignore nested YAML.
        if raw_line[:1].isspace():
            continue
        m = _FRONTMATTER_KEY_RE.match(raw_line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "name":
            name = value or None
        elif key == _HANDOFF_WRITER_MARKER:
            is_writer = value.lower() in ("true", "yes", "1")
    return (name, is_writer)


@functools.lru_cache(maxsize=1)
def handoff_writer_fleet() -> frozenset[str]:
    """Return the set of agent identities authorized to finalize a handoff row.

    Seeded from ``agents/*.md`` frontmatter (marker ``contract_handoff_writer:
    true``), unioned with the bare curator aliases. Falls back to
    ``_FALLBACK_HANDOFF_WRITER_FLEET`` when ``agents/`` is unresolvable or no
    agent opts in. Cached: the fleet is a static property of the installed
    tree, not per-call state. Call ``handoff_writer_fleet.cache_clear()`` in a
    test that mutates the agent set.
    """
    agents_dir = _agents_dir()
    if agents_dir is None:
        return _FALLBACK_HANDOFF_WRITER_FLEET
    fleet: set[str] = set()
    for md in sorted(agents_dir.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        name, is_writer = _parse_agent_frontmatter(text)
        if name and is_writer:
            fleet.add(name)
    if not fleet:
        return _FALLBACK_HANDOFF_WRITER_FLEET
    return frozenset(fleet | _HANDOFF_CURATOR_ALIASES)


def is_handoff_writer(agent: str) -> bool:
    """True iff ``agent`` is a seeded fleet identity permitted to finalize.

    The caller (``_assert_dispatch_can_write_handoff``) handles the unset/empty
    dispatch case (CLI / human / hook) before reaching here; this function
    answers only the "is this named identity in the fleet?" question.
    """
    if not agent:
        return False
    return agent.strip() in handoff_writer_fleet()


__all__ = [
    "DISPATCH_PERMISSIONS",
    "StateTransitionForbidden",
    "_assert_dispatch_can_advance_state",
    "CONTENT_AUTHOR_PERMISSIONS",
    "ContentWriteForbidden",
    "_assert_dispatch_can_write_content",
    "handoff_writer_fleet",
    "is_handoff_writer",
    "verifier_fleet",
    "is_verifier",
]


# ---------------------------------------------------------------------------
# Verifier fleet seed (harness R2 -- NEEDS_VERIFICATION / verifier-gated
# COMPLETE)
# ---------------------------------------------------------------------------
#
# Mirrors ``handoff_writer_fleet()`` / ``is_handoff_writer()`` above byte-for-
# byte in MECHANISM: a frontmatter marker on ``agents/*.md`` (``verifier:
# true``), the same top-level-key-only parser, the same ``lru_cache``
# discipline, and the same fallback-floor idiom (an unresolvable ``agents/``
# directory falls back to a hardcoded constant rather than failing open).
#
# The ONE deliberate difference from the handoff-writer precedent: the
# fallback floor here is EMPTY (``_FALLBACK_VERIFIER_FLEET = frozenset()``),
# not a known non-empty set -- it is reached only when ``agents/`` itself is
# unresolvable, never as a description of the live fleet. The fleet IS
# populated today: ``agents/gaia-verifier.md`` ships ``verifier: true`` (Gaia
# harness B3), so ``verifier_fleet()`` resolves to a non-empty set on any
# installed tree, and the SubagentStop gate (hooks/adapters/claude_code.py,
# ``_verifier_role_violation``) enforces the ARMED branch: only a seeded
# verifier-role agent (``gaia-verifier``) may set ``plan_status: COMPLETE``;
# every other producer proposing done work must transition through
# ``NEEDS_VERIFICATION`` and wait for that agent's independent pass. The
# mechanism (marker + loader + cached fleet + is_X predicate) is what is
# load-bearing; the fallback floor above exists only for the unresolvable-path
# edge case, not as the steady-state expectation.
#
# Never-fails-open, exactly like the handoff-writer fleet: an identity absent
# from the resolved fleet (empty or not) is always rejected by ``is_verifier``
# -- there is no "fall back to allow everyone" path anywhere in this module.

# Frontmatter marker that opts an agent .md into the verifier fleet.
_VERIFIER_MARKER = "verifier"

# Fallback floor when ``agents/`` is unresolvable. Deliberately EMPTY -- see
# module comment above; this is the unresolvable-path floor, not a
# description of the populated, ARMED fleet that ships today
# (``agents/gaia-verifier.md``, ``verifier: true``). Mirrors
# ``_FALLBACK_HANDOFF_WRITER_FLEET``'s role for the handoff-writer fleet.
_FALLBACK_VERIFIER_FLEET: frozenset[str] = frozenset()


def _parse_agent_verifier_frontmatter(md_text: str) -> tuple[str | None, bool]:
    """Extract ``(name, is_verifier)`` from an agent .md frontmatter block.

    Identical parsing discipline to ``_parse_agent_frontmatter``: only
    top-level (non-indented) ``key: value`` lines are read, so a nested
    ``routing:`` or ``project_context_contracts:`` block can never shadow the
    ``verifier`` marker. Kept as a separate function (rather than teaching
    ``_parse_agent_frontmatter`` a second marker) so the two fleets stay
    independently auditable and neither loader's behavior can be perturbed by
    a change aimed at the other.
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return (None, False)
    name: str | None = None
    is_verifier = False
    for raw_line in lines[1:]:
        if raw_line.strip() == "---":
            break
        if raw_line[:1].isspace():
            continue
        m = _FRONTMATTER_KEY_RE.match(raw_line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key == "name":
            name = value or None
        elif key == _VERIFIER_MARKER:
            is_verifier = value.lower() in ("true", "yes", "1")
    return (name, is_verifier)


@functools.lru_cache(maxsize=1)
def verifier_fleet() -> frozenset[str]:
    """Return the set of agent identities authorized to set ``COMPLETE`` when
    the verifier gate is armed.

    Seeded from ``agents/*.md`` frontmatter (marker ``verifier: true``).
    Falls back to ``_FALLBACK_VERIFIER_FLEET`` (empty) only when ``agents/``
    is unresolvable. Cached exactly like ``handoff_writer_fleet``: the fleet
    is a static property of the installed tree, not per-call state. Call
    ``verifier_fleet.cache_clear()`` in a test that mutates the agent set.

    Populated today: ``agents/gaia-verifier.md`` ships ``verifier: true``, so
    this resolves to a non-empty set (``{"gaia-verifier"}``) on any installed
    tree. Callers that gate on "is the verifier role armed" still MUST check
    ``bool(verifier_fleet())`` first rather than calling ``is_verifier(agent)``
    alone -- that guard is what lets the same code degrade safely to
    unenforced on a tree where ``agents/`` cannot be resolved at all, instead
    of rejecting every producer outright. See hooks/adapters/claude_code.py,
    ``_verifier_role_violation``, for the ARMED enforcement branch.
    """
    agents_dir = _agents_dir()
    if agents_dir is None:
        return _FALLBACK_VERIFIER_FLEET
    fleet: set[str] = set()
    for md in sorted(agents_dir.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        name, is_verifier_agent = _parse_agent_verifier_frontmatter(text)
        if name and is_verifier_agent:
            fleet.add(name)
    if not fleet:
        return _FALLBACK_VERIFIER_FLEET
    return frozenset(fleet)


def is_verifier(agent: str) -> bool:
    """True iff ``agent`` is a seeded fleet identity permitted to verify.

    Never fails open: an identity absent from the (possibly empty) fleet
    always returns False. Callers deciding whether the verifier gate is
    ARMED must check ``bool(verifier_fleet())`` separately -- this function
    only answers "is this named identity in the fleet?", exactly mirroring
    ``is_handoff_writer``.
    """
    if not agent:
        return False
    return agent.strip() in verifier_fleet()
