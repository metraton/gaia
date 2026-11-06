"""Materialise a revision and classify units with its validator in worker subprocesses, each on its own scratch store."""

from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

WORKER = Path(__file__).with_name("worker.py")
STARTUP_TIMEOUT_SECONDS = 300
COMMAND_TIMEOUT_SECONDS = 30
POLL_SECONDS = 0.2


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def merge_base(repo: Path, base: str) -> str:
    return _git(repo, "merge-base", "HEAD", base)


def checkout(repo: Path, revision: str, cache: Path) -> Path:
    """The tree of *revision* extracted under *cache*, reused across runs by commit id."""
    sha = _git(repo, "rev-parse", f"{revision}^{{commit}}")
    target = cache / sha
    if (target / ".complete").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    archive = cache / f"{sha}.tar"
    _git(repo, "archive", "--format=tar", "-o", str(archive), sha)
    with tarfile.open(archive) as tree:
        tree.extractall(target, filter="data")
    archive.unlink()
    (target / ".complete").touch()
    return target


def _lines(target: Path) -> List[dict]:
    lines = []
    for line in target.read_text(encoding="utf-8").splitlines() if target.exists() else []:
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return lines


def _attempt(root: Path, units: Dict[str, dict], work: Path, attempt: int, log) -> Tuple[List[dict], bool]:
    """Lines one worker process wrote for *units*, and whether it was killed for making no progress."""
    source, target = work / f"in{attempt}.json", work / f"out{attempt}.jsonl"
    source.write_text(json.dumps(units), encoding="utf-8")
    data = work / "gaia-data"
    env = dict(os.environ, GAIA_DATA_DIR=str(data), GAIA_DB=str(data / "gaia.db"), PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen([sys.executable, str(WORKER), str(root), str(source), str(target)], cwd=work, env=env,
                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    size, moved, limit = -1, time.monotonic(), STARTUP_TIMEOUT_SECONDS
    while process.poll() is None:
        time.sleep(POLL_SECONDS)
        now_size = target.stat().st_size if target.exists() else 0
        if now_size != size:
            size, moved = now_size, time.monotonic()
            limit = COMMAND_TIMEOUT_SECONDS if now_size else STARTUP_TIMEOUT_SECONDS
        elif time.monotonic() - moved > limit:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return _lines(target), True
    return _lines(target), False


def _batch(root: Path, units: Dict[str, dict], work: Path) -> Dict[str, dict]:
    """Classify *units* in one worker, restarting it past any unit it stalls or dies on, which is reported as an ERROR."""
    work.mkdir(parents=True, exist_ok=True)
    results: Dict[str, dict] = {}
    pending = list(units)
    with open(work / "worker.log", "w", encoding="utf-8") as log:
        for attempt in itertools.count():
            if not pending:
                break
            lines, killed = _attempt(root, {key: units[key] for key in pending}, work, attempt, log)
            for line in lines:
                if "key" in line:
                    results[line.pop("key")] = line
            pending = [key for key in pending if key not in results]
            if not pending:
                break
            if not lines:
                results.update({key: {"verdict": "ERROR", "rule": "worker-lost"} for key in pending})
                break
            results[pending.pop(0)] = {"verdict": "ERROR", "rule": "timeout" if killed else "worker-exit"}
    return results


def classify(root: Path, units: Dict[str, dict], work: Path, workers: int) -> Dict[str, dict]:
    """``{unit key: {verdict, rule}}`` from the validator of the tree at *root*, split over *workers* processes."""
    keys = list(units)
    count = max(1, min(workers, len(keys)))
    batches = [{key: units[key] for key in keys[index::count]} for index in range(count)]
    with ThreadPoolExecutor(max_workers=count) as pool:
        results = pool.map(lambda pair: _batch(root, pair[1], work / f"w{pair[0]}"), enumerate(batches))
    merged: Dict[str, dict] = {}
    for result in results:
        merged.update(result)
    return merged
