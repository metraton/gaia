#!/usr/bin/env python3
"""Behavior tests for T2.2: host capabilities + DECLARED degradation.

Closure evidence for AC-6 / AC-7. The brief's goal: business logic queries
*what a host can do* host-agnostically, and when a host does NOT offer a
capability it degrades in a *declared, safe* way -- never a crash, never an
implicit ``if host == "claude_code"`` branch.

Four halves:
  1. Contract: ``capabilities`` is abstract on ``HookAdapter`` -- a subclass
     that omits it cannot be instantiated.
  2. Declaration: ``ClaudeCodeAdapter`` DECLARES the capabilities Claude Code
     offers, and ``supports`` reflects that declaration.
  3. Degradation (the core evidence): a ``PartialHost`` that declares only a
     SUBSET of capabilities. Querying a missing capability returns a
     ``CapabilityDegradation`` with ``available=False`` carrying the caller's
     fallback -- the controlled alternative to a crash. The same call against
     the present capability returns ``available=True``. Degradation is a value,
     identical in shape for every host.
  4. Host-agnostic: the query/degradation path never inspects host identity --
     two different hosts run the SAME ``degrade_when_missing`` call and the
     outcome is decided only by each host's declaration.
"""

import sys
from pathlib import Path

import pytest

# hooks/ is placed on sys.path by tests/conftest.py; make it explicit too.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.base import HookAdapter
from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import CapabilityDegradation, HostCapability


# ============================================================================
# 1. CONTRACT: capabilities() is abstract on HookAdapter
# ============================================================================

class TestCapabilitiesIsAbstract:
    """A HookAdapter subclass cannot exist without declaring its capabilities."""

    def test_capabilities_marked_abstract_on_base(self):
        assert getattr(
            HookAdapter.capabilities, "__isabstractmethod__", False
        ) is True

    def test_subclass_without_capabilities_cannot_instantiate(self):
        # A ClaudeCodeAdapter subclass that re-abstracts only capabilities must
        # remain abstract (TypeError on construction).
        class MissingCapabilities(ClaudeCodeAdapter):
            capabilities = HookAdapter.capabilities  # re-abstract it

        assert "capabilities" in MissingCapabilities.__abstractmethods__
        with pytest.raises(TypeError):
            MissingCapabilities()

    def test_supports_and_degrade_are_not_abstract(self):
        """The query + degradation helpers are shared (concrete) on the base."""
        assert getattr(
            HookAdapter.supports, "__isabstractmethod__", False
        ) is False
        assert getattr(
            HookAdapter.degrade_when_missing, "__isabstractmethod__", False
        ) is False


# ============================================================================
# 2. DECLARATION: ClaudeCodeAdapter declares what Claude Code offers
# ============================================================================

class TestClaudeCodeDeclaresCapabilities:
    """The concrete adapter is the single place the host's capability set lives."""

    def test_declares_every_capability_claude_code_offers(self):
        adapter = ClaudeCodeAdapter()
        caps = adapter.capabilities()
        # Claude Code v2.1+ supports the full vocabulary the core asks about.
        assert caps == frozenset(HostCapability)

    def test_supports_reflects_the_declaration(self):
        adapter = ClaudeCodeAdapter()
        for cap in HostCapability:
            assert adapter.supports(cap) is True

    def test_capabilities_is_stable_per_instance(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.capabilities() == adapter.capabilities()

    def test_capabilities_is_immutable(self):
        """The declaration is a frozenset -- callers cannot mutate it."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(AttributeError):
            adapter.capabilities().add(HostCapability.INTERACTIVE_CONSENT)  # type: ignore[attr-defined]


# ============================================================================
# 3. DEGRADATION: a host missing a capability degrades in a DECLARED way
# ============================================================================

# A host that supports interactive consent but NOT the out-of-band approval
# cycle nor transcript access -- a plausible future minimal host (Codex /
# Antigravity stand-in). It declares a strict subset; everything else is absent.
class PartialHost(ClaudeCodeAdapter):
    """A host whose declaration omits some capabilities Claude Code has.

    Reuses ClaudeCodeAdapter for every mechanism but overrides ONLY the
    capability declaration. The query + degradation logic is inherited
    unchanged from HookAdapter -- proving the core degrades a host it has never
    heard of, purely from the host's own declaration.
    """

    def capabilities(self):
        return frozenset(
            {
                HostCapability.INTERACTIVE_CONSENT,
                HostCapability.STRUCTURED_PERMISSION_DECISION,
            }
        )


class TestDeclaredDegradationWhenCapabilityMissing:
    """Missing capability -> declared, observable degradation, never a crash."""

    def test_present_capability_yields_available_true_no_fallback(self):
        host = PartialHost()
        result = host.degrade_when_missing(
            HostCapability.INTERACTIVE_CONSENT, fallback="deny",
        )
        assert isinstance(result, CapabilityDegradation)
        assert result.available is True
        assert result.capability is HostCapability.INTERACTIVE_CONSENT
        # When the capability is present the caller's fallback is NOT taken.
        assert result.fallback == ""

    def test_missing_capability_yields_declared_fallback_not_a_crash(self):
        """The core evidence: querying an absent capability degrades safely.

        No exception is raised; instead a CapabilityDegradation carries
        available=False plus the caller's chosen fallback and a reason. This is
        the controlled alternative the brief requires.
        """
        host = PartialHost()
        assert host.supports(HostCapability.OUT_OF_BAND_APPROVAL) is False

        result = host.degrade_when_missing(
            HostCapability.OUT_OF_BAND_APPROVAL,
            fallback="deny",
            reason="no out-of-band approval cycle on this host",
        )

        assert isinstance(result, CapabilityDegradation)
        assert result.available is False
        assert result.capability is HostCapability.OUT_OF_BAND_APPROVAL
        # The degradation is DECLARED: the caller's fallback is echoed back.
        assert result.fallback == "deny"
        assert result.reason == "no out-of-band approval cycle on this host"

    def test_missing_capability_supplies_default_reason(self):
        """When the caller gives no reason, degradation still explains itself."""
        host = PartialHost()
        result = host.degrade_when_missing(
            HostCapability.TRANSCRIPT_ACCESS, fallback="skip",
        )
        assert result.available is False
        assert result.fallback == "skip"
        # A non-empty, capability-naming reason is generated for logs/denials.
        assert "transcript_access" in result.reason
        assert "skip" in result.reason

    def test_degradation_is_immutable(self):
        host = PartialHost()
        result = host.degrade_when_missing(
            HostCapability.CONTEXT_INJECTION, fallback="log_only",
        )
        with pytest.raises(Exception):  # FrozenInstanceError (a dataclasses error)
            result.fallback = "mutated"  # type: ignore[misc]


# ============================================================================
# 4. HOST-AGNOSTIC: the same call, two hosts, decided only by declaration
# ============================================================================

class TestDegradationIsHostAgnostic:
    """Identical query against two hosts; outcome follows each declaration only."""

    def test_same_capability_present_on_one_host_absent_on_another(self):
        full = ClaudeCodeAdapter()
        partial = PartialHost()
        cap = HostCapability.OUT_OF_BAND_APPROVAL

        # Business logic makes the EXACT same call against either host; it never
        # branches on host identity. The result differs only because the hosts
        # declared different capability sets.
        full_result = full.degrade_when_missing(cap, fallback="deny")
        partial_result = partial.degrade_when_missing(cap, fallback="deny")

        assert full_result.available is True
        assert partial_result.available is False
        assert partial_result.fallback == "deny"
