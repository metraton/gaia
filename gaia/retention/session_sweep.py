"""Session-start sweep of Gaia's tmp, scratch and worktrees: an ended owner's go, a live one's stay, the rest are listed."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from gaia.atomic_replace import replace_retrying
from gaia.paths import data_dir, scratch_dir, tmp_dir

MIN_INTERVAL_SECONDS = 15 * 60
CLAIM_SECONDS = 10 * 60


def report_path() -> Path:
    """Where the last sweep's report lives."""
    return data_dir() / "session-sweep.json"


def _claim_path() -> Path:
    return data_dir() / "session-sweep.claim"


def _size(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def _item(kind: str, path, verdict: str, reason: str, **extra) -> Dict[str, object]:
    size = _size(Path(path)) if verdict != "keep" else None
    return {"kind": kind, "path": str(path), "verdict": verdict, "reason": reason, "size": size, **extra}


def sweep_repos(start: Path) -> List[Path]:
    """Repos whose worktrees the sweep judges: the declared workspaces' around *start*, and *start* when it is one."""
    from gaia.retention.workspace_leftovers import workspace_repos

    try:
        repos = workspace_repos(start)
    except Exception:  # noqa: BLE001 -- no workspace store means no declared repos
        repos = []
    start = Path(start).resolve()
    if (start / ".git").exists() and start not in repos:
        repos.append(start)
    return repos


def _turn_scoped(kind: str, root: Path) -> List[Dict[str, object]]:
    from gaia.retention.fs_rules import judge_turn_scoped

    return [
        _item(kind, v["path"], v["verdict"], v["reason"])
        for v in judge_turn_scoped(root, spare_live_sessions=True)
    ]


def _worktree_verdict(path: Path, contract_id: str, held) -> tuple:
    from gaia.retention import worktree_collector
    from gaia.retention.fs_rules import _holds_open
    from gaia.retention.liveness import (
        SessionLiveness,
        session_ended_for_contract,
        session_liveness_for_contract,
    )
    from gaia.retention.worktree_reclaim import worktree_needs_capture

    if worktree_collector._contract_row(contract_id) is None:
        return "refuse", f"owning contract {contract_id} has no readable row"
    if held and _holds_open(path, held):
        return "keep", "a running process works inside"
    if session_liveness_for_contract(contract_id) == SessionLiveness.ALIVE:
        return "keep", "its session is live"
    reason = worktree_collector.worktree_collect_reason(contract_id, path.stat().st_mtime)
    if reason is None:
        if session_ended_for_contract(contract_id) is None:
            return "refuse", f"owning contract {contract_id}: its session cannot be read"
        return "keep", "its owner may still be running"
    try:
        if worktree_needs_capture(path):
            return "refuse", "uncommitted or unpushed work, kept as evidence"
    except subprocess.CalledProcessError as exc:
        return "refuse", f"git could not read its state: {exc}"
    return "remove", reason


def _worktrees(repo: Path, held) -> List[Dict[str, object]]:
    from gaia.retention.worktree_collector import list_managed_worktrees

    try:
        entries = list_managed_worktrees(repo)
    except (subprocess.CalledProcessError, OSError):
        return []
    out = []
    for entry in entries:
        path = Path(entry["path"])
        if not path.is_dir():
            continue
        try:
            verdict, reason = _worktree_verdict(path, entry["contract_id"], held)
        except OSError:
            continue
        out.append(_item("worktree", path, verdict, reason, repo=str(repo), contract_id=entry["contract_id"]))
    return out


def plan(start: Path, *, repos: Optional[List[Path]] = None) -> List[Dict[str, object]]:
    """Every leftover the sweep sees from *start*, each with its verdict and reason; changes nothing."""
    from gaia.retention.fs_rules import _open_paths

    items = _turn_scoped("tmp", tmp_dir()) + _turn_scoped("scratch", scratch_dir())
    held = _open_paths()
    for repo in sweep_repos(start) if repos is None else repos:
        items += _worktrees(Path(repo), held)
    return items


def _writable_retry(func, path, _exc) -> None:
    parent = os.path.dirname(path)
    os.chmod(parent, stat.S_IRWXU)
    if os.path.isdir(path) and not os.path.islink(path):
        os.chmod(path, stat.S_IRWXU)
    func(path)


def _remove(item: Dict[str, object]) -> Optional[str]:
    path = Path(str(item["path"]))
    if item["kind"] == "worktree":
        from gaia.retention.worktree_reclaim import reclaim_worktree

        result = reclaim_worktree(Path(str(item["repo"])), path)
        return None if result.get("recycled") else str(result.get("reason") or result.get("status"))
    try:
        if path.is_dir() and not path.is_symlink():
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_writable_retry)
            else:
                shutil.rmtree(path, onerror=_writable_retry)
        else:
            path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return str(exc)
    return None


def _claim(now: float) -> bool:
    path = _claim_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if now - path.stat().st_mtime < CLAIM_SECONDS:
                    return False
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return True
    return False


def last_report() -> Dict[str, object]:
    """The last run's report; {} when no sweep ran."""
    try:
        loaded = json.loads(report_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def run(start: Path, *, repos: Optional[List[Path]] = None) -> Optional[Dict[str, object]]:
    """Remove every ``remove`` item of ``plan`` and store the report; None when another sweep holds the claim."""
    began = time.time()
    if not _claim(began):
        return None
    try:
        removed, refused, failed = [], [], []
        for item in plan(start, repos=repos):
            if item["verdict"] == "refuse":
                refused.append(item)
            elif item["verdict"] == "remove":
                error = _remove(item)
                (failed if error else removed).append({**item, **({"error": error} if error else {})})
        report = {
            "ran_at": began,
            "seconds": round(time.time() - began, 3),
            "freed": sum(int(i["size"] or 0) for i in removed),
            "removed": removed,
            "refused": refused,
            "failed": failed,
        }
        target = report_path()
        staging = target.with_name(f"{target.name}.{os.getpid()}")
        staging.write_text(json.dumps(report), encoding="utf-8")
        replace_retrying(staging, target)
        return report
    finally:
        try:
            _claim_path().unlink()
        except OSError:
            pass


def spawn(start: Path) -> bool:
    """Start one detached sweep unless one ran within ``MIN_INTERVAL_SECONDS``; True when started."""
    if time.time() - float(last_report().get("ran_at") or 0) < MIN_INTERVAL_SECONDS:
        return False
    if _claim_path().exists():
        return False
    package_root = Path(__file__).resolve().parents[2]
    subprocess.Popen(
        [sys.executable, "-m", "gaia.retention.session_sweep", str(start)],
        cwd=package_root, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return True


if __name__ == "__main__":
    run(Path(sys.argv[1]))
