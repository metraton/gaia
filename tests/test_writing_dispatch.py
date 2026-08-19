"""Real-git gate for the host-independent writing dispatch."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.dispatch import dispatch_writing_agent


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "writing-dispatch-test")
    (path / "README.md").write_text("main\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def test_real_writing_dispatch_uses_cwd_and_returns_ac2_identity(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _repo(workspace / "project-checkout")
    main_before = {
        "pwd": str(repo), "status": _git(repo, "status", "--porcelain"),
        "branch": _git(repo, "branch", "--show-current"),
        "commit": _git(repo, "rev-parse", "HEAD"),
    }
    script = (
        "import json, os, subprocess; "
        "p=os.getcwd(); open('specialist.txt','w').write(p+'\\n'); "
        "out={'pwd':p,'status':subprocess.run(['git','status','--porcelain'],capture_output=True,text=True).stdout.strip(),"
        "'branch':subprocess.run(['git','branch','--show-current'],capture_output=True,text=True).stdout.strip(),"
        "'commit':subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()}; "
        "print(json.dumps(out,sort_keys=True)); "
        "subprocess.run(['git','add','specialist.txt'],check=True); "
        "subprocess.run(['git','commit','-q','-m','specialist'],check=True)"
    )
    result = dispatch_writing_agent(
        repo, workspace, "project-checkout", "contract-62", "agent-62",
        [sys.executable, "-c", script], branch="task-62",
    )
    specialist = json.loads(result.stdout)
    printed = {"main": main_before, "worktree": {**specialist, **result.as_contract()}}
    print(json.dumps(printed, sort_keys=True))
    assert specialist["pwd"] == result.path
    assert specialist["branch"] == result.branch == "task-62"
    assert specialist["status"].splitlines() == ["?? .gaia-worktree.json", "?? specialist.txt"]
    assert result.commit != main_before["commit"]
    assert result.as_contract() == {
        "path": result.path, "branch": "task-62", "commit": result.commit,
        "contract_id": "contract-62", "agent_id": "agent-62",
    }
    assert {"pwd": str(repo), "status": _git(repo, "status", "--porcelain"),
            "branch": _git(repo, "branch", "--show-current"),
            "commit": _git(repo, "rev-parse", "HEAD")} == main_before
    assert (Path(result.path) / "specialist.txt").read_text() == result.path + "\n"
    assert capsys.readouterr().out


def test_failed_writing_dispatch_does_not_leave_worktree(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _repo(workspace / "project-checkout")
    with pytest.raises(subprocess.CalledProcessError):
        dispatch_writing_agent(
            repo, workspace, "project-checkout", "contract-fail", "agent-fail",
            [sys.executable, "-c", "open('half-owned.txt','w').write('x'); raise SystemExit(7)"],
        )
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not list((workspace / ".project-worktrees").rglob("half-owned.txt"))
