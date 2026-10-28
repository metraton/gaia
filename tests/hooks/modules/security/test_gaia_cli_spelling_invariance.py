"""A trusted gaia CLI call gets one verdict whether spelled bare or by the installed dispatcher's absolute path."""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
for entry in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import mutative_verbs, tiers  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

PUSH = "git -C {repo} push origin feature-x:release/1.x"
CASES = {
    "approvals request-set --command {push} --cwd {repo} --what Push": "FREE",
    "approvals request-file-write --path {repo}/f --what Write": "FREE",
    "approvals question P-1 --text why": "FREE",
    "approvals show P-1": "FREE",
    "task gate set-status 1 --status pass": "FREE",
    "approvals approve P-1": "SIGNED",
    "approvals revoke P-1": "FREE",
    "approvals reject P-1": "FREE",
    "approvals replay P-1": "SIGNED",
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    from gaia.paths import ensure_layout

    ensure_layout()
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "init"), ("branch", "feature-x"),
                 ("remote", "add", "origin", "https://example.invalid/o/r.git")):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    shutil.copy(_REPO_ROOT / "bin" / "gaia", install / "bin" / "gaia")
    monkeypatch.setattr(mutative_verbs, "_HOOK_ROOT", install)
    monkeypatch.setattr(mutative_verbs, "_hook_root_is_install", lambda: True)
    monkeypatch.chdir(repo)
    return {"repo": str(repo), "gaia": str(install / "bin" / "gaia")}


def _verdict(command: str, cwd: str) -> tuple:
    mutative_verbs.detect_mutative_command.cache_clear()
    tiers._classify_command_tier_cached.cache_clear()
    payload = {"cwd": cwd, "tool_use_id": "t-spelling", "session_id": "s-spelling",
               "agent_id": "aspelling00000001", "agent_type": "gaia-system"}
    result = BashValidator().validate(command, is_subagent=True, session_id="s-spelling", agent_type="gaia-system",
                                      hook_payload=payload)
    if result.allowed:
        return "FREE", ""
    return ("SIGNED" if result.approval_id else "BLOCKED"), result.reason or ""


@pytest.mark.parametrize("spelling", ["bare", "installed-path"])
@pytest.mark.parametrize("template,expected", CASES.items())
def test_a_gaia_verb_keeps_its_verdict_in_every_spelling(template, expected, spelling, world):
    program = "gaia" if spelling == "bare" else world["gaia"]
    args = template.format(push=shlex.quote(PUSH.format(repo=world["repo"])), repo=world["repo"])
    verdict, reason = _verdict(f"{program} {args}", world["repo"])
    assert verdict == expected, reason


def test_the_stored_push_still_needs_its_own_signature(world):
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('spelling')")
        con.execute("INSERT OR REPLACE INTO projects (workspace, name, path, status, project_identity) "
                    "VALUES ('spelling', 'repo', ?, 'active', 'spelling/repo')", (world["repo"],))
        con.execute("INSERT OR REPLACE INTO project_context (project_identity, contract_name, payload) "
                    "VALUES ('spelling/repo', 'targets', ?)", ('{"protected_branches": ["release/1.x"]}',))
        con.commit()
    finally:
        con.close()
    assert _verdict(PUSH.format(repo=world["repo"]), world["repo"])[0] == "SIGNED"
