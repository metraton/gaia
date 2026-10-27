"""A grant binds to the target its command landed on when it was approved, not only to the command text."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.approval_grants import activate_db_pending_by_id  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

COMMAND = "kubectl apply -f x"
SESSION, AGENT = "s-grant-target", "agranttarget01"


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia-data" / "gaia.db"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    from gaia.paths import ensure_layout

    ensure_layout()
    kubeconfig = tmp_path / "kubeconfig"
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    def switch(context: str) -> None:
        kubeconfig.write_text(f"apiVersion: v1\nkind: Config\ncurrent-context: {context}\n", encoding="utf-8")

    return {"switch": switch, "kubeconfig": kubeconfig, "cwd": str(work), "tmp": tmp_path}


def _run(cwd: str):
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    payload = {"cwd": cwd, "session_id": SESSION, "tool_use_id": "t-grant-target", "agent_id": AGENT, "agent_type": "developer"}
    return BashValidator(agent_names_scratch=True).validate(
        COMMAND, is_subagent=True, session_id=SESSION, agent_type="developer", hook_payload=payload,
    )


def _mint(cwd: str) -> str:
    blocked = _run(cwd)
    assert not blocked.allowed and blocked.approval_id, blocked.reason
    activation = activate_db_pending_by_id(blocked.approval_id, current_session_id="s-approver")
    assert activation.success, activation.reason
    return blocked.approval_id


def test_grant_target_scope_follows_the_resolved_context(cluster):
    cluster["switch"]("dev-a")
    _mint(cluster["cwd"])
    consumed = _run(cluster["cwd"])
    assert consumed.allowed, consumed.reason

    minted = _mint(cluster["cwd"])
    cluster["switch"]("prod-a")
    switched = _run(cluster["cwd"])
    assert not switched.allowed and switched.approval_id and switched.approval_id != minted, switched.reason

    cluster["switch"]("dev-a")
    assert _run(cluster["cwd"]).allowed


def test_grant_target_scope_refuses_an_unreadable_kubeconfig_at_lookup(cluster):
    cluster["switch"]("dev-a")
    minted = _mint(cluster["cwd"])
    cluster["kubeconfig"].unlink()
    cluster["kubeconfig"].mkdir()
    unreadable = _run(cluster["cwd"])
    assert not unreadable.allowed and unreadable.approval_id != minted, unreadable.reason
