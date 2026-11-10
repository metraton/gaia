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
CLAIM_SECONDS = 2 * 3600
SWEEP_ENV = "GAIA_SESSION_SWEEP"
MODES = ("on", "list", "off")
DOCTOR_SIZE_BUDGET_SECONDS = 3.0


def mode() -> str:
    """The position in force: ``GAIA_SESSION_SWEEP`` on (default), list or off; an unknown value lists."""
    value = os.environ.get(SWEEP_ENV, "").strip().lower() or "on"
    return value if value in MODES else "list"


def report_path() -> Path:
    """Where the last sweep's report lives."""
    return data_dir() / "session-sweep.json"


def _claim_path() -> Path:
    return data_dir() / "session-sweep.claim"


def _size(path: Path, deadline: Optional[float] = None) -> Optional[int]:
    try:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        if deadline is not None and time.monotonic() > deadline:
            return None
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def measure_sizes(items: List[Dict[str, object]], *, budget_seconds: Optional[float] = None) -> None:
    """Fill each item's ``size`` in order; past *budget_seconds* the rest stay None (not measured)."""
    deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
    for item in items:
        if deadline is not None and time.monotonic() >= deadline:
            return
        item["size"] = _size(Path(str(item["path"])), deadline)


def _item(kind: str, path, verdict: str, reason: str, sizes: bool, **extra) -> Dict[str, object]:
    size = _size(Path(path)) if sizes and verdict != "keep" else None
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


def _turn_scoped(kind: str, root: Path, sizes: bool) -> List[Dict[str, object]]:
    from gaia.retention.fs_rules import judge_turn_scoped

    return [
        _item(kind, v["path"], v["verdict"], v["reason"], sizes)
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
    from gaia.retention.worktree_reclaim import _exempt_metadata_filename, _has_uncommitted_changes

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
        if _has_uncommitted_changes(path, exempt=_exempt_metadata_filename(path)):
            return "refuse", "uncommitted work, kept as evidence"
        branch = subprocess.run(
            ["git", "-C", str(path), "symbolic-ref", "-q", "HEAD"], capture_output=True, text=True,
        ).stdout.strip()
        others = [f"--exclude={branch[len('refs/heads/'):]}"] if branch else []
        unpushed = subprocess.run(
            ["git", "-C", str(path), "rev-list", "--count", "HEAD", "--not", "--remotes", *others, "--branches"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as exc:
        return "refuse", f"git could not read its state: {exc}"
    if unpushed != "0":
        return "refuse", f"{unpushed} commit(s) no other branch holds, kept as evidence"
    return "remove", reason


def _worktrees(repo: Path, held, sizes: bool) -> List[Dict[str, object]]:
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
        out.append(_item("worktree", path, verdict, reason, sizes, repo=str(repo), contract_id=entry["contract_id"]))
    return out


def plan(start: Path, *, repos: Optional[List[Path]] = None, sizes: bool = True) -> List[Dict[str, object]]:
    """Every leftover the sweep sees from *start*, each with its verdict, reason and (unless *sizes* is off) size."""
    from gaia.retention.fs_rules import _open_paths

    items = _turn_scoped("tmp", tmp_dir(), sizes) + _turn_scoped("scratch", scratch_dir(), sizes)
    held = _open_paths()
    for repo in sweep_repos(start) if repos is None else repos:
        items += _worktrees(Path(repo), held, sizes)
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
        from gaia.retention.worktree_reclaim import _exempt_metadata_filename, _remove_worktree

        try:
            _remove_worktree(Path(str(item["repo"])), path, exempt=_exempt_metadata_filename(path))
        except (subprocess.CalledProcessError, OSError) as exc:
            return str(getattr(exc, "stderr", "") or exc).strip()
        return None
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


def _store_report(report: Dict[str, object]) -> None:
    target = report_path()
    staging = target.with_name(f"{target.name}.{os.getpid()}")
    staging.write_text(json.dumps(report), encoding="utf-8")
    replace_retrying(staging, target)


def run(start: Path, *, repos: Optional[List[Path]] = None, mode: str = "on") -> Optional[Dict[str, object]]:
    """Judge without sizes, remove only in mode ``on`` (else record ``would_remove``), store the report; None if claimed."""
    began = time.time()
    if not _claim(began):
        return None
    try:
        removed, would_remove, refused, failed = [], [], [], []
        for item in plan(start, repos=repos, sizes=False):
            if item["verdict"] == "refuse":
                refused.append(item)
            elif item["verdict"] == "remove" and mode != "on":
                would_remove.append(item)
            elif item["verdict"] == "remove":
                error = _remove(item)
                (failed if error else removed).append({**item, **({"error": error} if error else {})})
        report = {
            "ran_at": began,
            "seconds": round(time.time() - began, 3),
            "mode": mode,
            "removed": removed,
            "would_remove": would_remove,
            "refused": refused,
            "failed": failed,
        }
        _store_report(report)
        return report
    finally:
        try:
            _claim_path().unlink()
        except OSError:
            pass


def spawn(start: Path) -> bool:
    """Start one detached sweep in the position in force (the first on a machine lists only); True when started."""
    position = mode()
    report = last_report()
    if position == "off" or (report.get("mode") == "first" and not report.get("announced")):
        return False
    if time.time() - float(report.get("ran_at") or 0) < MIN_INTERVAL_SECONDS or _claim_path().exists():
        return False
    how = "first" if not report and position == "on" else position
    package_root = Path(__file__).resolve().parents[2]
    subprocess.Popen(
        [sys.executable, "-m", "gaia.retention.session_sweep", str(start), "--mode", how],
        cwd=package_root, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return True


def first_run_notice() -> str:
    """The one session-start line after the first sweep listed what it would remove; "" otherwise, and once only."""
    report = last_report()
    if report.get("mode") != "first" or report.get("announced"):
        return ""
    _store_report({**report, "announced": time.time()})
    listed = report.get("would_remove") or []
    if not listed:
        return ""
    kinds = ", ".join(
        f"{sum(1 for i in listed if i['kind'] == kind)} {kind}"
        for kind in ("tmp", "scratch", "worktree") if any(i["kind"] == kind for i in listed)
    )
    return (
        f"Gaia's first leftover sweep only listed: it would remove {len(listed)} item(s) ({kinds}) whose owner ended. "
        f"`gaia cleanup --sweep --dry-run` shows them with sizes; the next session-start sweep removes them "
        f"unless {SWEEP_ENV}=list or off."
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]), mode=sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--mode" else mode())
