"""A single-command signature authorises the bytes it signed, in the directory it sealed, once."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.approvals import store  # noqa: E402
from gaia.approvals.core import grant_lookup_filter  # noqa: E402
from gaia.store import writer  # noqa: E402

SESSION = "ses-byte-binding"
AGENT = "developer"
HARNESS_ID = "a" + "6" * 16

#: (what was signed, what is attempted under that signature).
OTHER_BYTES = {
    "redirect-target": ("printf x > /etc/gaia-signed-a", "printf x > /etc/gaia-signed-b"),
    "redirect-added": ("git push origin feat/x", "git push origin feat/x > /etc/gaia-signed-a"),
    "redirect-dropped": ("git push origin feat/x 2>&1", "git push origin feat/x"),
    "flag-values-swapped": (
        "deployctl promote --from staging --to prod",
        "deployctl promote --to staging --from prod",
    ),
    "flag-order": (
        "git push --no-verify --force-with-lease origin feat/x",
        "git push --force-with-lease --no-verify origin feat/x",
    ),
    "flag-repeated": ("git push --force origin feat/x", "git push --force --force origin feat/x"),
}


@pytest.fixture
def signing_env(tmp_path, monkeypatch):
    """A scratch store, a home folder and a work directory; returns the work directory."""
    from modules.core.paths import clear_path_cache
    from modules.security import mutative_verbs, protected_paths, tiers

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.close()
    clear_path_cache()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    work = tmp_path / "work"
    work.mkdir()
    protected_paths.reset_caches()
    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    yield str(work)
    protected_paths.reset_caches()


def _sign(command: str, cwd: str) -> str:
    """Seal ``command`` as a blocked single command and approve it; return the approval_id."""
    from modules.tools.bash_validator import _build_sealed_payload

    payload = _build_sealed_payload(
        command, verb="push", category="MUTATIVE", agent_type=AGENT, session_id=SESSION, cwd=cwd,
    )
    approval_id = store.insert_requested(payload, agent_id=AGENT, session_id=SESSION)
    _approve(approval_id)
    return approval_id


def _approve(approval_id: str) -> None:
    activation = store.activate_approval_atomically(
        approval_id, approver_session="ses-approver", agent_id=AGENT,
    )
    assert activation.success, activation.reason


def _authorised_by(command: str, cwd: str) -> str | None:
    from modules.security.approval_grants import check_approval_grant

    grant = check_approval_grant(
        command, session_id=SESSION,
        requester=grant_lookup_filter(cwd=cwd, session_id=SESSION, agent_id=AGENT),
    )
    return getattr(grant, "_db_approval_id", None)


def _validate(command: str, cwd: str, tool_use_id: str):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=True, session_id=SESSION, agent_type=AGENT, cwd=cwd,
        hook_payload={"cwd": cwd, "tool_use_id": tool_use_id, "session_id": SESSION,
                      "agent_id": HARNESS_ID, "agent_type": AGENT},
    )


@pytest.mark.parametrize("case", sorted(OTHER_BYTES))
def test_a_single_command_signature_authorises_only_its_signed_bytes(signing_env, case):
    signed, attempted = OTHER_BYTES[case]
    approval_id = _sign(signed, signing_env)

    assert _authorised_by(attempted, signing_env) is None
    assert _authorised_by(signed, signing_env) == approval_id


def test_a_single_command_signature_authorises_only_in_its_sealed_directory(signing_env, tmp_path):
    signed = OTHER_BYTES["redirect-target"][0]
    approval_id = _sign(signed, signing_env)

    assert _authorised_by(signed, str(tmp_path)) is None
    assert _authorised_by(signed, signing_env) == approval_id


def test_a_signed_redirect_does_not_open_another_target_through_the_guard_lane(signing_env):
    signed, attempted = OTHER_BYTES["redirect-target"]
    blocked = _validate(signed, signing_env, "toolu_block")
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    _approve(blocked.approval_id)

    other_target = _validate(attempted, signing_env, "toolu_other")
    signed_bytes = _validate(signed, signing_env, "toolu_retry")
    again = _validate(signed, signing_env, "toolu_again")

    assert not other_target.allowed
    assert signed_bytes.allowed and signed_bytes.consumed_approval_id == blocked.approval_id
    assert not again.allowed


def test_a_denial_never_names_a_request_sealed_for_other_bytes(signing_env):
    signed, attempted = OTHER_BYTES["redirect-target"]
    first = _validate(signed, signing_env, "toolu_first")
    second = _validate(attempted, signing_env, "toolu_second")
    repeated = _validate(signed, signing_env, "toolu_repeated")

    assert first.approval_id and second.approval_id
    assert second.approval_id != first.approval_id
    assert repeated.approval_id == first.approval_id
