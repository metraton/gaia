"""Windows file-system behaviour, simulated so it runs on Linux CI.

R5: a replace target another process holds open raises PermissionError.
Lock: a waiter on a held lock gives up within its bound instead of hanging.
R7: git refuses paths beyond MAX_PATH unless core.longpaths is on.
R8: CRLF working copies must neither look modified nor change content hashes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gaia.subprocess_text import run_text

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "hooks"))

CONTRACT_ID = "a" + "1234567890abcdef" + ".deadbeef01"
AGENT_ID = "a" + "1234567890abcdef"


def _hold_target_for(monkeypatch, failures: int) -> list:
    real_replace = os.replace
    calls = []

    def replace(source, target):
        calls.append(target)
        if len(calls) <= failures:
            raise PermissionError(13, "The process cannot access the file", str(target))
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    return calls


def _git(repo: Path, *args: str) -> str:
    return run_text(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    ).stdout


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "core.autocrlf", "false")
    return path


def test_draft_save_retries_a_held_target(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.contract import drafts

    drafts.drafts_dir().mkdir(parents=True, exist_ok=True)
    calls = _hold_target_for(monkeypatch, failures=2)
    drafts.save_draft(CONTRACT_ID, {"k": "v"})

    assert len(calls) == 3
    assert json.loads(drafts.draft_path(CONTRACT_ID).read_text(encoding="utf-8")) == {"k": "v"}


def test_draft_save_gives_up_after_bounded_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.atomic_replace import REPLACE_ATTEMPTS
    from gaia.contract import drafts

    drafts.drafts_dir().mkdir(parents=True, exist_ok=True)
    drafts.save_draft(CONTRACT_ID, {"k": "before"})
    monkeypatch.setattr("gaia.atomic_replace.time.sleep", lambda _seconds: None)
    calls = _hold_target_for(monkeypatch, failures=REPLACE_ATTEMPTS)

    with pytest.raises(PermissionError):
        drafts.save_draft(CONTRACT_ID, {"k": "after"})
    assert len(calls) == REPLACE_ATTEMPTS
    assert json.loads(drafts.draft_path(CONTRACT_ID).read_text(encoding="utf-8")) == {"k": "before"}


def test_leftovers_cache_write_retries_a_held_target(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.retention import workspace_leftovers

    _hold_target_for(monkeypatch, failures=1)
    workspace_leftovers._write_entry("k", {"line": "x"})

    assert workspace_leftovers._read_cache() == {"k": {"line": "x"}}


def test_lock_wait_is_bounded_and_fails_closed(tmp_path):
    from modules.core.filelock import LockTimeout, exclusive_file_lock

    lock = tmp_path / "context.lock"
    held, release = threading.Event(), threading.Event()

    def hold():
        with exclusive_file_lock(lock):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(5)
    entered = []
    started = time.monotonic()
    try:
        with pytest.raises(LockTimeout):
            with exclusive_file_lock(lock, timeout=0.3):
                entered.append(True)
        waited = time.monotonic() - started
    finally:
        release.set()
        holder.join(5)

    assert entered == []
    assert 0.3 <= waited < 5
    with exclusive_file_lock(lock, timeout=1):
        entered.append(True)
    assert entered == [True]


def _record_runs(monkeypatch) -> list:
    real_run = subprocess.run
    seen = []

    def recording_run(args, *rest, **kwargs):
        seen.append(args if isinstance(args, str) else list(args))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    return seen


def test_every_git_run_on_windows_enables_long_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("gaia.subprocess_text._ON_WINDOWS", True)
    from gaia.subprocess_text import run_text as runner

    seen = _record_runs(monkeypatch)
    runner(["git", "--version"], check=True, capture_output=True)
    runner([sys.executable, "-c", "pass"], check=True)

    assert seen == [["git", "-c", "core.longpaths=true", "--version"], [sys.executable, "-c", "pass"]]


def test_worktree_add_on_windows_enables_long_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    monkeypatch.setattr("gaia.subprocess_text._ON_WINDOWS", True)
    from gaia import worktree

    seen = _record_runs(monkeypatch)
    target = worktree.create_agentic_worktree(repo, CONTRACT_ID, AGENT_ID)

    git_runs = [cmd for cmd in seen if cmd[0] == "git"]
    assert git_runs and all(cmd[1:3] == ["-c", "core.longpaths=true"] for cmd in git_runs)
    assert target.is_dir()


@pytest.mark.parametrize("configured, warned", [("", True), ("false", True), ("true", False)])
def test_long_paths_check_reads_user_config_and_never_writes_it(monkeypatch, configured, warned):
    monkeypatch.setattr("gaia.subprocess_text._ON_WINDOWS", True)
    from gaia import subprocess_text

    seen = []

    def fake_run(args, *rest, **kwargs):
        seen.append(list(args))
        return subprocess.CompletedProcess(args, 0 if configured else 1, stdout=configured + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    problem = subprocess_text.long_paths_disabled()

    assert (problem is not None) is warned
    assert seen == [["git", "config", "--type=bool", "--get", "core.longpaths"]]
    assert subprocess_text.ENABLE_LONG_PATHS_COMMAND == "git config --global core.longpaths true"


def test_windows_checkout_is_not_modified_for_a_linux_reader(tmp_path):
    attributes = REPO_ROOT / ".gitattributes"
    assert attributes.is_file()
    repo = _init_repo(tmp_path / "repo")
    shutil.copy(attributes, repo / ".gitattributes")
    (repo / "module.py").write_bytes(b"a = 1\nb = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    _git(repo, "config", "core.autocrlf", "true")
    (repo / "module.py").unlink()
    _git(repo, "checkout", "--", "module.py")
    _git(repo, "config", "core.autocrlf", "false")

    assert (repo / "module.py").read_bytes() == b"a = 1\nb = 2\n"
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "check-attr", "eol", "--", "module.py").strip() == "module.py: eol: lf"


def test_crlf_copy_matches_its_lf_package_in_content_hashes(tmp_path):
    from gaia import hooks_build, source_parity

    source, installed = tmp_path / "source", tmp_path / "installed"
    for root, eol in ((source, b"\r\n"), (installed, b"\n")):
        (root / "hooks" / "modules").mkdir(parents=True)
        (root / "hooks" / "modules" / "m.py").write_bytes(b"x = 1" + eol + b"y = 2" + eol)

    assert source_parity.compare(source, installed, ["hooks"])["divergent"] == []
    assert hooks_build.hooks_content_hash(source / "hooks") == hooks_build.hooks_content_hash(installed / "hooks")

    (source / "hooks" / "modules" / "m.py").write_bytes(b"x = 2\r\ny = 2\r\n")
    assert source_parity.compare(source, installed, ["hooks"])["divergent"] == [("hooks/modules/m.py", source_parity.DIFFERS)]
    assert hooks_build.hooks_content_hash(source / "hooks") != hooks_build.hooks_content_hash(installed / "hooks")
