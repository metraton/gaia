"""write_pending_approval_for_file() seals verification and impact too.

Before this change, ``sealed_payload`` only ever set ``rollback_hint`` from an
optional ``context`` dict; ``verification`` and ``impact`` were not keys the
payload builder knew at all, at any input -- so no producer, however willing,
could ever make either one appear on a FILE_WRITE/SCOPE_FILE_PATH approval's
rendered surface. ``consent_presentation.py``'s ``VISIBLE_FIELDS`` table
already rendered both (with their own pre-existing absence-text constants);
only the payload side was missing.

Three properties are under test:

  * AUTHORED CONTENT: a caller that supplies ``context`` gets all three
    fields sealed verbatim, and none of the three resolves to its
    declared-absence text.
  * BACKWARD COMPATIBILITY: a caller that supplies no ``context`` at all --
    today's only real caller, ``hooks/adapters/claude_code.py`` -- seals all
    three as ``None``, identical to this function's behavior before this
    change (which set only ``rollback_hint`` to ``None`` the same way).
  * END TO END: the new plan-first producer (``gaia approvals
    request-file-write``, exercised here via the same
    ``write_pending_approval_for_file`` call it makes) proactively mints a
    pending with real values; a SUBSEQUENT reactive block for the SAME file,
    routed through ``ClaudeCodeAdapter._adapt_write_edit`` exactly as a real
    Edit attempt would be, must REUSE that pending (proving the existing
    retry-dedup path serves this producer with no code change of its own)
    and its rendered surface must show the real values, not the three
    absence strings. This is the property unit tests alone cannot
    distinguish from a good intention.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[4] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[4])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.security.approval_grants import (  # noqa: E402
    find_pending_for_file,
    generate_nonce,
    write_pending_approval_for_file,
)
from adapters.consent_presentation import VISIBLE_FIELDS, sealed_field  # noqa: E402

SESSION = "sess-file-write-context-sealing"
AGENT_ID = "a" + "d" * 16


def _absence_text(field: str) -> str:
    """The text the presentation layer shows when no producer declared *field*."""
    for name, _label, _keys, absent in VISIBLE_FIELDS:
        if name == field:
            return absent
    raise AssertionError(f"{field} is not a visible consent field")


_ROLLBACK_ABSENT = _absence_text("rollback")
_VERIFICATION_ABSENT = _absence_text("verification")
_IMPACT_ABSENT = _absence_text("impact")


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    yield


def _sealed_payload(approval_id: str) -> dict:
    from gaia.approvals.store import get_by_id

    row = get_by_id(approval_id)
    assert row is not None, f"no approval row persisted for {approval_id}"
    return json.loads(row["payload_json"])


class TestAuthoredContentSealsVerbatim:
    def test_rollback_verification_impact_all_seal(self, tmp_path):
        target = tmp_path / "protected.py"
        target.write_text("x = 1\n", encoding="utf-8")
        nonce = generate_nonce()

        pending = write_pending_approval_for_file(
            nonce=nonce,
            file_path=str(target),
            session_id=SESSION,
            context={
                "rollback": "git checkout HEAD -- protected.py",
                "verification": "pytest tests/x.py -q",
                "impact": "protected.py starts validating input",
            },
        )
        assert pending is not None

        payload = _sealed_payload(f"P-{nonce}")
        assert payload["rollback_hint"] == "git checkout HEAD -- protected.py"
        assert payload["verification"] == "pytest tests/x.py -q"
        assert payload["impact"] == "protected.py starts validating input"

        # The render layer, not just the raw dict: none of the three must
        # resolve to their declared-absence text.
        assert sealed_field(payload, "rollback") != _ROLLBACK_ABSENT
        assert sealed_field(payload, "verification") != _VERIFICATION_ABSENT
        assert sealed_field(payload, "impact") != _IMPACT_ABSENT
        assert sealed_field(payload, "rollback") == "git checkout HEAD -- protected.py"
        assert sealed_field(payload, "verification") == "pytest tests/x.py -q"
        assert sealed_field(payload, "impact") == "protected.py starts validating input"


class TestNoContextIsUnchanged:
    def test_omitted_context_seals_all_three_as_none(self, tmp_path):
        """Today's only real caller (claude_code.py) passes no context at all."""
        target = tmp_path / "protected.py"
        target.write_text("x = 1\n", encoding="utf-8")
        nonce = generate_nonce()

        pending = write_pending_approval_for_file(
            nonce=nonce,
            file_path=str(target),
            session_id=SESSION,
        )
        assert pending is not None

        payload = _sealed_payload(f"P-{nonce}")
        assert payload["rollback_hint"] is None
        assert payload["verification"] is None
        assert payload["impact"] is None

        # And the render layer falls back to the same absence text as before
        # this change -- a producer declaring nothing must look identical to
        # how it looked pre-fix.
        assert sealed_field(payload, "rollback") == _ROLLBACK_ABSENT
        assert sealed_field(payload, "verification") == _VERIFICATION_ABSENT
        assert sealed_field(payload, "impact") == _IMPACT_ABSENT

    def test_empty_context_dict_behaves_like_no_context(self, tmp_path):
        target = tmp_path / "protected.py"
        target.write_text("x = 1\n", encoding="utf-8")
        nonce = generate_nonce()

        write_pending_approval_for_file(
            nonce=nonce, file_path=str(target), session_id=SESSION, context={},
        )

        payload = _sealed_payload(f"P-{nonce}")
        assert payload["rollback_hint"] is None
        assert payload["verification"] is None
        assert payload["impact"] is None


class TestProactiveDeclarationReachesTheReactiveBlock:
    """The property that proves the fix WORKS, not merely that it was intended.

    Mints a pending exactly as ``gaia approvals request-file-write`` does
    (the same function, the same context shape), then drives the REAL
    PreToolUse path for a subsequent Edit attempt on that same file and reads
    back what it actually reused.
    """

    def test_reactive_block_reuses_the_proactively_sealed_pending(self, tmp_path):
        from adapters.claude_code import ClaudeCodeAdapter

        target = tmp_path / "install" / ".claude" / "settings.json"
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")

        import gaia.approvals.store as astore
        from gaia.approvals import core
        from modules.security.protected_paths import resolved_write_target

        # 1. Proactive declaration -- what `gaia approvals request-file-write`
        #    does under the hood, by the same session and agent that will write.
        declared_approval_id = core.request_file_write(
            resolved_write_target(str(target)),
            session_id=SESSION,
            agent_id="gaia-system",
            rollback="git checkout HEAD -- .claude/settings.json",
            verification="gaia doctor --json shows no settings drift",
            impact="settings.json gains a new hook matcher",
        )
        nonce = declared_approval_id[2:]

        # 2. The REAL reactive path, exactly as a real Edit attempt drives it.
        adapter = ClaudeCodeAdapter()
        response = adapter._adapt_write_edit(
            "Edit",
            {"file_path": str(target)},
            session_id=SESSION,
            is_subagent=True,
            agent_id=AGENT_ID,
            agent_type="gaia-system",
        )

        # 3. It must have found and reused the SAME pending -- not minted a
        #    fresh, field-empty one.
        assert len(astore.list_pending(all_sessions=True)) == 1, (
            "the reactive block must reuse the proactively-declared pending "
            "by file-path signature, not mint a new field-empty one"
        )

        reason = response.output.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        assert declared_approval_id in reason or nonce in reason, (
            f"the surfaced approval_id must be the proactively-declared one; "
            f"reason was:\n{reason}"
        )

        # 4. The surface actually shown carries the real values, not the
        #    three absence strings -- the property unit tests cannot see.
        payload = _sealed_payload(declared_approval_id)
        assert sealed_field(payload, "rollback") == (
            "git checkout HEAD -- .claude/settings.json"
        )
        assert sealed_field(payload, "verification") == (
            "gaia doctor --json shows no settings drift"
        )
        assert sealed_field(payload, "impact") == (
            "settings.json gains a new hook matcher"
        )
        assert _ROLLBACK_ABSENT not in (
            sealed_field(payload, "rollback"),
        )
        assert _VERIFICATION_ABSENT not in (
            sealed_field(payload, "verification"),
        )
        assert _IMPACT_ABSENT not in (
            sealed_field(payload, "impact"),
        )
