"""A request-set signature unlocks every lane that asks before the verb scan, and leaves no orphan request.

Each lane is driven through ``BashValidator.validate`` against a real store with
no lookup patched: denied, requested as the denial's own line spells it,
approved, then retried by the same command and ``tool_use_id``.
"""

from __future__ import annotations

import argparse
import io
import json
import shlex
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.store import writer  # noqa: E402

SESSION = "ses-guard-lane"
AGENT = "developer"
HARNESS_ID = "a" + "5" * 16
TOOL_USE_ID = "toolu_guard_lane"

LANES = {
    "install-root-change": "rm -rf {install}",
    "system-path-write": "echo x > /etc/gaia-guard-lane-roundtrip",
    "stranded-creation": "mkdir {home}/deliverable",
    "account-path-write": "echo x >> {home}/.bashrc",
    "unprovable-write-target": "echo x > $GAIA_UNSET_Q/x.txt",
    "sqlite-reach": "sqlite3 {tmp}/other.db 'SELECT 1'",
    "egress": "scp {tmp}/f.txt backup-host:/tmp/f.txt",
}

PHRASES = {
    "--what": "Run the guarded command.",
    "--question": "Run it?",
    "--does": "Runs the guarded command.",
    "--impact": "The command's effect lands.",
    "--rollback": "Undo it by hand.",
    "--verification": "Read the target back.",
    "--shared-state": "No shared state.",
}


@pytest.fixture
def lane_env(tmp_path, monkeypatch):
    """A scratch store, a home folder, an installed Gaia root and the dispatched shell's identity."""
    from modules.core.paths import clear_path_cache
    from modules.security import mutative_verbs, protected_paths, tiers

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    con = sqlite3.connect(data_dir / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.close()
    clear_path_cache()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GAIA_UNSET_Q", raising=False)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", AGENT)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    install = tmp_path / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.7.2"
    (install / "bin").mkdir(parents=True)
    (install / "package.json").write_text('{"name": "@jaguilar87/gaia"}', encoding="utf-8")
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    protected_paths.reset_caches()
    mutative_verbs.detect_mutative_command.cache_clear()
    mutative_verbs._detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    yield {"db": data_dir / "gaia.db", "home": home, "install": install, "tmp": tmp_path, "work": str(work)}
    protected_paths.reset_caches()


def _command(lane: str, env: dict) -> str:
    return LANES[lane].format(install=env["install"], home=env["home"], tmp=env["tmp"])


def _validate(command: str, cwd: str, tool_use_id: str = TOOL_USE_ID):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=True, session_id=SESSION, agent_type=AGENT, cwd=cwd,
        hook_payload={"cwd": cwd, "tool_use_id": tool_use_id, "session_id": SESSION,
                      "agent_id": HARNESS_ID, "agent_type": AGENT},
    )


def _denial_text(result) -> str:
    from modules.tools.hook_response import read_permission_reason

    return read_permission_reason(result.block_response) if result.block_response else result.reason


def _request_as_spelled(denial: str) -> str:
    """Run the request-set line the denial carries, its placeholders filled; return the approval_id."""
    from bin.cli import approvals

    line = next(text for text in denial.splitlines() if "gaia approvals request-set" in text)
    words = shlex.split(line[line.index("gaia approvals request-set"):])[2:]
    words = [PHRASES[words[i - 1]] if word.startswith("<") else word for i, word in enumerate(words)]
    parser = argparse.ArgumentParser()
    approvals.register(parser.add_subparsers(dest="group"))
    args = parser.parse_args(["approvals", *words, "--json"])
    out = io.StringIO()
    with redirect_stdout(out):
        code = approvals.cmd_request_set(args)
    assert code == 0, out.getvalue()
    return json.loads(out.getvalue().strip().splitlines()[-1])["approval_id"]


def _approve(approval_id: str) -> None:
    from gaia.approvals import core

    core.record_presentation(
        approval_id, native_ref="toolu_question", session_id="ses-orchestrator",
        agent_id="gaia-orchestrator",
    )
    assert core.decide(
        native_ref="toolu_question", option_key="approve", session_id="ses-orchestrator",
    ).status == "activated"


def _rows(db: Path, sql: str, *params):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _signed_lane(lane: str, env: dict) -> tuple[str, str]:
    """Deny the lane's command, request it as the denial spells it and approve; return (command, approval_id)."""
    command = _command(lane, env)
    denied = _validate(command, env["work"])
    assert not denied.allowed and denied.approval_id, denied.reason
    approval_id = _request_as_spelled(_denial_text(denied))
    _approve(approval_id)
    return command, approval_id


@pytest.mark.parametrize("lane", sorted(LANES))
def test_a_request_set_signature_unlocks_the_guard_lane(lane_env, lane):
    command, approval_id = _signed_lane(lane, lane_env)
    rows_before = _rows(lane_env["db"], "SELECT COUNT(*) FROM approvals")

    retried = _validate(command, lane_env["work"])

    assert retried.allowed, _denial_text(retried)
    assert retried.consumed_approval_id == approval_id
    assert _rows(
        lane_env["db"],
        "SELECT reservation_index, reservation_tool_use_id FROM approval_grants WHERE approval_id = ?",
        approval_id,
    ) == [(0, TOOL_USE_ID)]
    assert _rows(lane_env["db"], "SELECT COUNT(*) FROM approvals") == rows_before
    assert _rows(lane_env["db"], "SELECT id FROM approvals WHERE status = 'pending'") == []


@pytest.mark.parametrize("lane", sorted(LANES))
def test_a_denial_names_the_approved_signature_instead_of_sealing_another(lane_env, lane):
    command, approval_id = _signed_lane(lane, lane_env)
    rows_before = _rows(lane_env["db"], "SELECT COUNT(*) FROM approvals")

    elsewhere = _validate(command, str(lane_env["tmp"]), tool_use_id="toolu_elsewhere")

    assert not elsewhere.allowed
    assert approval_id in _denial_text(elsewhere)
    assert f"sealed for {lane_env['work']} and this call runs in {lane_env['tmp']}" in _denial_text(elsewhere)
    assert _rows(lane_env["db"], "SELECT COUNT(*) FROM approvals") == rows_before
