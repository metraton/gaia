"""Gap 2/4 (2026-08-03 CLI-gap fix): orchestrator read-lane widening.

Covers exactly the diff to ``gaia_cli_only_guard.py``:

  * ``("task", "show")`` joins ``ALLOWED_READ_PHRASES`` -- the single-task
    complement of the already-allowed ``("task", "list")``.
  * A bare ``gaia`` invocation and any stage carrying ``-h``/``--help``
    (including an abbreviation argparse itself would accept, and including on
    an otherwise-denied mutative verb) is allowed unconditionally -- verified
    empirically (see gaia_cli_only_guard.py's own docstring) that argparse
    exits during parsing before any subcommand handler runs, so no write ever
    executes either way.
  * Every previously-denied WRITE verb stays denied exactly as before when
    invoked WITHOUT a help flag -- this file exists specifically to prove
    that widening the reads did not touch the writes.

``check(command, hook_payload)`` is exercised directly with an orchestrator
payload (``{}`` -- empty dict classifies as ORCHESTRATOR per
``classify_session_role``) and ``is_trusted_gaia_binary`` monkeypatched to
accept the fake absolute path used throughout, isolating this file from the
unrelated package-provenance mechanism.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import gaia_cli_only_guard as guard

_GAIA = "/abs/path/bin/gaia"
_ORCHESTRATOR_PAYLOAD: dict = {}


@pytest.fixture(autouse=True)
def _trust_fake_binary(monkeypatch):
    monkeypatch.setattr(
        guard, "is_trusted_gaia_binary", lambda token: token == _GAIA
    )


def _check(command: str):
    return guard.check(command, _ORCHESTRATOR_PAYLOAD)


# ---------------------------------------------------------------------------
# Gap 2: `task show` joins the read allowlist
# ---------------------------------------------------------------------------

def test_task_show_is_allowed():
    allowed, reason = _check(f"{_GAIA} task show my-brief 1")
    assert allowed is True
    assert reason is None


def test_task_show_with_workspace_flag_still_allowed():
    allowed, reason = _check(f"{_GAIA} task show my-brief 1 --workspace=me")
    assert allowed is True


def test_task_list_unaffected_by_the_new_entry():
    allowed, reason = _check(f"{_GAIA} task list my-brief")
    assert allowed is True


# ---------------------------------------------------------------------------
# Gap 4: bare invocation and -h/--help are unconditionally readable
# ---------------------------------------------------------------------------

def test_bare_gaia_with_no_subcommand_is_allowed():
    allowed, reason = _check(_GAIA)
    assert allowed is True
    assert reason is None


def test_gaia_dash_dash_help_is_allowed():
    allowed, reason = _check(f"{_GAIA} --help")
    assert allowed is True


def test_gaia_dash_h_is_allowed():
    allowed, reason = _check(f"{_GAIA} -h")
    assert allowed is True


def test_task_dash_dash_help_is_allowed():
    """The exact case from the gap report: `gaia task --help` used to deny."""
    allowed, reason = _check(f"{_GAIA} task --help")
    assert allowed is True


def test_help_abbreviation_is_allowed():
    """argparse's allow_abbrev accepts --hel/--he for --help; the guard must
    recognize the same abbreviations it would actually reach at runtime."""
    allowed, _ = _check(f"{_GAIA} task --hel")
    assert allowed is True
    allowed, _ = _check(f"{_GAIA} install --he")
    assert allowed is True


def test_help_on_an_otherwise_denied_mutative_verb_is_allowed():
    """`gaia install --help` never executes install: argparse's help action
    exits during parsing before cmd_install() is ever reached."""
    allowed, reason = _check(f"{_GAIA} install --help")
    assert allowed is True


def test_help_on_an_explicitly_denied_task_verb_is_allowed():
    allowed, reason = _check(f"{_GAIA} task add --help")
    assert allowed is True


def test_help_flag_value_does_not_falsely_trigger_the_carve_out():
    """`--description=--help` is a VALUE, never the help flag itself (verified
    empirically against argparse: the `=` form does not re-parse its RHS as
    an option). The write-shape validator should still see this as an
    ordinary (denied) `task add` invocation, not a free pass."""
    allowed, reason = _check(
        f"{_GAIA} task add my-brief --goal=x --description=--help"
    )
    assert allowed is False
    # task,add is EXPLICITLY_DENIED_PHRASES -- confirm it denies as such, not
    # as an accidental help allow.
    assert "explicitly excluded" in reason


# ---------------------------------------------------------------------------
# Write verbs stay exactly as restricted as before (no help flag present)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "command",
    [
        f"{_GAIA} task add my-brief --order=3 --goal=x",
        f"{_GAIA} task remove my-brief 3",
        f"{_GAIA} task reorder my-brief --from=1 --to=2",
        f"{_GAIA} task gate add my-brief 1 --type=command",
        f"{_GAIA} task gate remove my-brief 1 3",
        f"{_GAIA} task gate set-status my-brief 1 3 pass",
        f"{_GAIA} brief delete my-brief",
        f"{_GAIA} plan save my-brief",
        f"{_GAIA} plan delete my-brief",
        f"{_GAIA} approvals approve P-xyz",
        f"{_GAIA} approvals replay P-xyz",
        f"{_GAIA} approvals revoke P-xyz",
        f"{_GAIA} approvals reject P-xyz",
        f"{_GAIA} approvals reject-all",
        f"{_GAIA} approvals clean",
        f"{_GAIA} memory edit --name=foo --field=body --content=x",
        f"{_GAIA} memory delete foo",
        f"{_GAIA} contract set foo bar",
        f"{_GAIA} contract finalize --draft-id=x",
        f"{_GAIA} install",
        f"{_GAIA} update",
        f"{_GAIA} uninstall",
        f"{_GAIA} cleanup",
        f"{_GAIA} dev",
    ],
)
def test_every_previously_denied_write_verb_still_denied(command):
    allowed, reason = _check(command)
    assert allowed is False
    assert reason is not None


def test_approval_verbs_stay_categorically_denied_not_approvable():
    """Approval verbs specifically: no approval_id, no T3-style escape --
    reason text must say denied outright, matching the module's own
    categorical-deny contract."""
    for verb in ("approve", "revoke", "reject", "reject-all", "clean", "replay"):
        allowed, reason = _check(f"{_GAIA} approvals {verb} P-xyz")
        assert allowed is False
        assert "not approvable" in reason or "excluded" in reason


def test_coordinator_owned_write_shapes_unaffected():
    """Sanity check that the ALREADY-allowed coordinator writes (unrelated to
    this change) still validate exactly as before."""
    allowed, reason = _check(
        f"{_GAIA} task set-status my-brief 1 done"
    )
    assert allowed is True
    allowed, reason = _check(
        f"{_GAIA} task set-status my-brief 1 done --override --reason=x"
    )
    assert allowed is False  # override/reason still refused for the orchestrator


def test_non_orchestrator_role_bypasses_this_guard_entirely():
    allowed, reason = guard.check(
        f"{_GAIA} task add my-brief --order=1 --goal=x",
        {"agent_id": "a1234567890abcdef"},
    )
    assert allowed is True
    assert reason is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
