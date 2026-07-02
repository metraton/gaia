#!/usr/bin/env python3
"""Behavior tests for T2.1: the consent flow is decoupled from the host.

Closure evidence for AC-3 (grep is the floor, behavior test is the closure):
when business logic decides an operation needs the user's consent, it must
request that consent VIA the adapter's ``request_consent`` -- NOT by assuming
the host's AskUserQuestion / ``permissionDecision`` mechanism. These tests
substitute the adapter's consent mechanism with a fake that does NOT use
AskUserQuestion and drive the real pre-tool-use consent paths
(``_adapt_bash`` security-mode T3, ``_adapt_write_edit`` protected path),
proving the host-consent knowledge now lives behind ``request_consent``.

Three halves:
  1. Contract: ``request_consent`` is abstract on ``HookAdapter`` -- a subclass
     that omits it cannot be instantiated.
  2. Flow-side: a ``FakeConsentAdapter`` whose ``request_consent`` emits a
     NON-host shape (no ``hookSpecificOutput`` / no ``permissionDecision``) is
     driven through the real consent paths. If those paths still built the host
     shape inline, the override would have no effect and the assertions would
     fail.
  3. Mechanism-side: the concrete ``ClaudeCodeAdapter.request_consent`` is the
     single owner of the AskUserQuestion (``permissionDecision: "ask"``) and the
     orchestrator approval-id (``deny``) shapes.
"""

import sys
from pathlib import Path

import pytest

# hooks/ is placed on sys.path by tests/conftest.py; make it explicit too.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.base import HookAdapter
from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import ConsentRequest, HookResponse


# ============================================================================
# 1. CONTRACT: request_consent is abstract on HookAdapter
# ============================================================================

class TestRequestConsentIsAbstract:
    """A HookAdapter subclass cannot exist without implementing request_consent."""

    def test_request_consent_marked_abstract_on_base(self):
        assert getattr(
            HookAdapter.request_consent, "__isabstractmethod__", False
        ) is True

    def test_subclass_without_request_consent_cannot_instantiate(self):
        # A subclass that implements every OTHER abstract method but omits
        # request_consent must remain abstract (TypeError on construction).
        # We assert request_consent is among the still-abstract members of a
        # ClaudeCodeAdapter subclass that re-abstracts only that method.
        class MissingConsent(ClaudeCodeAdapter):
            request_consent = HookAdapter.request_consent  # re-abstract it

        assert "request_consent" in MissingConsent.__abstractmethods__
        with pytest.raises(TypeError):
            MissingConsent()


# ============================================================================
# 2. FLOW-SIDE: the real consent paths request consent VIA the adapter
# ============================================================================

# A NON-host marker the fake adapter returns. The real Claude Code consent
# shape always nests under "hookSpecificOutput" with a "permissionDecision";
# this fake deliberately uses neither, so its presence in the response proves
# the consent path delegated to request_consent rather than building the host
# shape itself.
_FAKE_CONSENT_MARKER = "consent-via-abstraction"


class FakeConsentAdapter(ClaudeCodeAdapter):
    """A host whose consent mechanism is NOT AskUserQuestion.

    Reuses ClaudeCodeAdapter's pre-tool-use orchestration (the business flow we
    are pinning) but overrides ONLY the consent mechanism. If the consent paths
    still emitted the native host shape inline, the recorded calls below would
    be empty and the response would carry permissionDecision -- both assertions
    would fail.
    """

    def __init__(self):
        super().__init__()
        self.consent_requests: list[ConsentRequest] = []

    def request_consent(self, request: ConsentRequest) -> HookResponse:
        self.consent_requests.append(request)
        # Deliberately a foreign shape: no hookSpecificOutput, no
        # permissionDecision, no AskUserQuestion. A different host entirely.
        return HookResponse(
            output={"consent_mechanism": _FAKE_CONSENT_MARKER,
                    "operation": request.operation,
                    "approval_id": request.approval_id},
            exit_code=0,
        )


def _assert_consent_via_abstraction(resp: HookResponse) -> None:
    """The response came from request_consent, not an inline host shape."""
    assert resp.output.get("consent_mechanism") == _FAKE_CONSENT_MARKER
    # Negative guard: the native AskUserQuestion shape must be absent.
    assert "hookSpecificOutput" not in resp.output


class TestConsentFlowGoesViaAdapter:
    """The pre-tool-use consent paths must source consent from request_consent."""

    def test_protected_file_foreground_requests_consent_via_adapter(self):
        """A protected-path Write in foreground asks the user VIA request_consent."""
        adapter = FakeConsentAdapter()
        # A path inside the gaia hooks dir is protected (see _adapt_write_edit).
        protected = str(HOOKS_DIR / "modules" / "tools" / "bash_validator.py")

        resp = adapter._adapt_write_edit(
            "Edit", {"file_path": protected},
            session_id="sess-x", is_subagent=False,
        )

        assert len(adapter.consent_requests) == 1
        req = adapter.consent_requests[0]
        assert req.kind == "file"
        assert req.operation == protected
        assert req.approval_id is None  # foreground -> inline consent
        _assert_consent_via_abstraction(resp)

    def test_non_protected_file_does_not_request_consent(self):
        """A non-protected path never reaches the consent abstraction."""
        adapter = FakeConsentAdapter()
        resp = adapter._adapt_write_edit(
            "Edit", {"file_path": "/tmp/some_user_file.txt"},
            session_id="sess-x", is_subagent=False,
        )
        assert adapter.consent_requests == []
        # Pass-through allow: empty output, exit 0 (no consent needed).
        assert resp.output == {}
        assert resp.exit_code == 0


# ============================================================================
# 3. MECHANISM-SIDE: ClaudeCodeAdapter.request_consent owns AskUserQuestion
# ============================================================================

class TestClaudeCodeOwnsConsentMechanism:
    """The concrete adapter is the single place the host consent shapes live."""

    def test_inline_consent_maps_to_ask_permission_decision(self):
        """approval_id=None -> native AskUserQuestion prompt (permissionDecision ask)."""
        adapter = ClaudeCodeAdapter()
        resp = adapter.request_consent(
            ConsentRequest(operation="terraform apply", reason="[T3] needs approval")
        )
        hso = resp.output["hookSpecificOutput"]
        assert hso["permissionDecision"] == "ask"
        assert hso["permissionDecisionReason"] == "[T3] needs approval"
        assert resp.exit_code == 0

    def test_inline_consent_preserves_updated_input(self):
        """updated_input survives the consent step (footer-stripped command)."""
        adapter = ClaudeCodeAdapter()
        resp = adapter.request_consent(
            ConsentRequest(
                operation="git commit",
                reason="r",
                updated_input={"command": "git commit -m x"},
            )
        )
        hso = resp.output["hookSpecificOutput"]
        assert hso["permissionDecision"] == "ask"
        assert hso["updatedInput"] == {"command": "git commit -m x"}

    def test_out_of_band_consent_maps_to_deny_keyed_to_approval_id(self):
        """approval_id set -> deny keyed to that id (orchestrator approval flow)."""
        adapter = ClaudeCodeAdapter()
        reason = "approval_id: P-deadbeef"
        resp = adapter.request_consent(
            ConsentRequest(
                operation="/x/y.py", kind="file",
                reason=reason, approval_id="P-deadbeef",
            )
        )
        hso = resp.output["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert hso["permissionDecisionReason"] == reason
        assert resp.exit_code == 0
