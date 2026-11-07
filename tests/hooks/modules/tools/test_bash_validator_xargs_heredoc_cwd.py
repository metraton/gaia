#!/usr/bin/env python3
"""An xargs heredoc line is judged in the caller's directory, and asks when that directory is unknown."""

import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

TEE_RELATIVE = "xargs tee <<'EOF'\nnotes.txt\nEOF"


def _validate(command, **kwargs):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(command, is_subagent=True, session_id="t", agent_type="developer", **kwargs)


def test_a_relative_xargs_line_with_no_known_cwd_asks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _validate(TEE_RELATIVE)
    assert not result.allowed
    assert result.rule == "unknown.dynamic-span"
    assert "relative xargs argument" in result.reason


def test_an_absolute_xargs_line_with_no_known_cwd_keeps_its_verdict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _validate("xargs cat <<'EOF'\n/etc/hostname\nEOF").allowed


def test_the_caller_cwd_places_a_relative_line_not_the_hook_cwd(tmp_path, monkeypatch):
    repo, elsewhere = tmp_path / "repo", tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(elsewhere)
    result = _validate(TEE_RELATIVE, cwd=str(repo))
    assert not result.allowed
    assert result.rule == "floor.shell-write"


def test_an_xargs_line_is_its_argument_while_a_shell_runs_each_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "absent" / "deploy.frob"
    assert _validate(f"xargs cat <<'EOF'\n{missing}\nEOF").allowed
    assert not _validate(f"bash <<'EOF'\n{missing}\nEOF").allowed
    assert not _validate(f"sh -s <<'EOF'\n{missing}\nEOF").allowed
