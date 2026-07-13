#!/usr/bin/env python3
"""AC-2: the dispatch-identity guards are fail-CLOSED in practice.

With GAIA_DISPATCH_AGENT now wired at dispatch (AC-1), the three DB-side guards
must enforce the per-agent model: a dispatched agent WITHOUT authority that
tries to write memory, write evidence, or advance a brief/plan state is BLOCKED,
while authorized identities and a genuine human CLI call (no dispatch identity)
still pass.

These tests exercise the guard functions directly (the enforcement point) plus
one integration through ``insert_evidence`` (whose guard runs before any DB
access, so no DB fixture is needed).
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from gaia.store.writer import _assert_dispatch_can_write_memory, MemoryWriteForbidden
from gaia.evidence.store import (
    _assert_dispatch_can_write_evidence,
    EvidenceWriteForbidden,
    insert_evidence,
)
from gaia.state.permissions import (
    _assert_dispatch_can_advance_state,
    StateTransitionForbidden,
)

ENV = "GAIA_DISPATCH_AGENT"


# ---------------------------------------------------------------------------
# Memory guard
# ---------------------------------------------------------------------------

def test_memory_unset_is_allowed(monkeypatch):
    """No dispatch identity => human CLI => allowed (fail-open only here)."""
    monkeypatch.delenv(ENV, raising=False)
    _assert_dispatch_can_write_memory()  # no raise


def test_memory_empty_is_allowed(monkeypatch):
    """An empty-string value is treated as unset (documented contract). Note the
    memory guard checks ``not raw`` (no .strip()), so a whitespace-only value
    would fail CLOSED -- which is safe, and unreachable via the dispatch wiring,
    which strips and never emits a whitespace-only identity."""
    monkeypatch.setenv(ENV, "")
    _assert_dispatch_can_write_memory()  # no raise


@pytest.mark.parametrize("agent", ["developer", "platform-architect", "gaia-system", "gaia-planner"])
def test_memory_non_curator_blocked(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(MemoryWriteForbidden):
        _assert_dispatch_can_write_memory()


@pytest.mark.parametrize("agent", ["orchestrator", "operator", "gaia-orchestrator", "gaia-operator"])
def test_memory_curator_allowed(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    _assert_dispatch_can_write_memory()  # no raise


# ---------------------------------------------------------------------------
# Evidence guard (function + guard-first integration through insert_evidence)
# ---------------------------------------------------------------------------

def test_evidence_unset_is_allowed(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _assert_dispatch_can_write_evidence()  # no raise


@pytest.mark.parametrize("agent", ["developer", "gitops-operator", "cloud-troubleshooter"])
def test_evidence_non_curator_blocked(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(EvidenceWriteForbidden):
        _assert_dispatch_can_write_evidence()


@pytest.mark.parametrize("agent", ["orchestrator", "gaia-operator"])
def test_evidence_curator_allowed(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    _assert_dispatch_can_write_evidence()  # no raise


def test_insert_evidence_blocked_before_db(monkeypatch):
    """insert_evidence consults the guard BEFORE touching the DB, so a blocked
    subagent never reaches persistence (no db_path needed)."""
    monkeypatch.setenv(ENV, "developer")
    with pytest.raises(EvidenceWriteForbidden):
        insert_evidence(
            "me", 1, "AC-1", type="text", text="unauthorized",
            db_path=Path("/nonexistent/should-not-be-touched.db"),
        )


def test_insert_evidence_bypass_flag_skips_guard(monkeypatch):
    """The trusted hook-layer bypass path is unaffected by dispatch identity --
    it fails later on the missing DB, NOT on the guard (proves the guard was
    skipped)."""
    monkeypatch.setenv(ENV, "developer")
    with pytest.raises(Exception) as exc:
        insert_evidence(
            "me", 1, "AC-1", type="text", text="trusted",
            bypass_dispatch_guard=True,
            db_path=Path("/nonexistent/dir/db.sqlite"),
        )
    assert not isinstance(exc.value, EvidenceWriteForbidden)


# ---------------------------------------------------------------------------
# State-transition guard
# ---------------------------------------------------------------------------

def test_state_unset_is_allowed(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _assert_dispatch_can_advance_state("briefs")  # no raise


@pytest.mark.parametrize("table", ["briefs", "plans", "milestones"])
def test_state_curator_only_table_blocked_for_subagent(monkeypatch, table):
    monkeypatch.setenv(ENV, "developer")
    with pytest.raises(StateTransitionForbidden):
        _assert_dispatch_can_advance_state(table)


@pytest.mark.parametrize("table", ["tasks", "acceptance_criteria"])
def test_state_non_curator_table_allowed_for_subagent(monkeypatch, table):
    """Subagents may advance tasks / acceptance_criteria (curator_only=False)."""
    monkeypatch.setenv(ENV, "developer")
    _assert_dispatch_can_advance_state(table)  # no raise


@pytest.mark.parametrize("table", ["briefs", "plans", "milestones"])
def test_state_curator_allowed(monkeypatch, table):
    monkeypatch.setenv(ENV, "gaia-orchestrator")
    _assert_dispatch_can_advance_state(table)  # no raise
