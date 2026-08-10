#!/usr/bin/env python3
"""The evidence producer lane is DERIVED from the fleet, not enumerated.

The property under test is not "these named agents may deposit evidence" --
that is the defect this file exists to prevent. It is: *every* declared agent
that is not a curator is admitted to insert, whatever its name, and no test
edit is required when a new agent .md lands. So the universe is read from
``agents/`` at run time and each member asserted against the live guard; a
specialist added tomorrow is covered by this file the day it appears.

The complementary half is asserted too, because a derivation that admits
everything is worthless: an identity outside the fleet is still refused, and
no producer -- derived or not -- may delete.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from gaia.evidence.store import (
    _EVIDENCE_CURATOR_AGENTS,
    _assert_dispatch_can_write_evidence,
    _evidence_producer_agents,
    EvidenceWriteForbidden,
)
from gaia.state.permissions import agent_fleet

ENV = "GAIA_DISPATCH_AGENT"


def _declared_non_curator_agents() -> list[str]:
    """Every agent under ``agents/`` that is not a curator, read at run time."""
    return sorted(agent_fleet() - _EVIDENCE_CURATOR_AGENTS)


def test_fleet_resolves_from_the_agents_directory():
    """Guards the premise of every other test here: if ``agents/`` stopped
    resolving, the parametrized cases below would silently degrade to the
    fallback floor (or to nothing) and assert far less than they appear to."""
    fleet = agent_fleet()
    names_on_disk = {
        md.stem for md in (_REPO_ROOT / "agents").glob("*.md")
        if md.name.lower() != "readme.md"
    }
    assert names_on_disk, "no agent .md files found under agents/"
    assert names_on_disk <= fleet, (
        f"agents present on disk but absent from the derived fleet: "
        f"{sorted(names_on_disk - fleet)}"
    )


def test_every_non_curator_agent_is_a_producer():
    """The derived producer set IS fleet-minus-curators, with nothing dropped."""
    assert _evidence_producer_agents() == frozenset(_declared_non_curator_agents())


@pytest.mark.parametrize("agent", _declared_non_curator_agents())
def test_declared_non_curator_agent_may_insert(monkeypatch, agent):
    """Every non-curator agent in the fleet is admitted to deposit evidence.

    Parametrized from the directory, so a new specialist joins this test by
    existing -- which is the whole point: an enumerated list would pass today
    and start excluding real agents the moment one is added.
    """
    monkeypatch.setenv(ENV, agent)
    _assert_dispatch_can_write_evidence()  # no raise


@pytest.mark.parametrize("agent", _declared_non_curator_agents())
def test_declared_non_curator_agent_may_not_delete(monkeypatch, agent):
    """Opening insert to the whole fleet must not leak into delete: deletion
    stays curator-only for every derived producer without exception."""
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(EvidenceWriteForbidden):
        _assert_dispatch_can_write_evidence(allow_producers=False)


@pytest.mark.parametrize("agent", sorted(_EVIDENCE_CURATOR_AGENTS))
def test_curator_may_insert_and_delete(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    _assert_dispatch_can_write_evidence()  # no raise
    _assert_dispatch_can_write_evidence(allow_producers=False)  # no raise


@pytest.mark.parametrize(
    "agent",
    ["not-an-agent", "developerr", "Developer", " ", "kubectl", "gaia"],
)
def test_identity_outside_the_fleet_is_refused(monkeypatch, agent):
    """The derivation bounds admission by the fleet -- it does not dissolve it.

    Without this arm, "derive instead of enumerate" could be satisfied by
    admitting everyone, which is not a guard at all.
    """
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(EvidenceWriteForbidden):
        _assert_dispatch_can_write_evidence()
