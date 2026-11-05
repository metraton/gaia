"""Materialise a revision and classify units with its validator in worker subprocesses, each on its own scratch store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict

WORKER = Path(__file__).with_name("worker.py")
BATCH_TIMEOUT_SECONDS = 3600


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


def _batch(root: Path, units: Dict[str, dict], work: Path) -> Dict[str, dict]:
    work.mkdir(parents=True, exist_ok=True)
    source, target = work / "in.json", work / "out.json"
    source.write_text(json.dumps(units), encoding="utf-8")
    data = work / "gaia-data"
    env = dict(os.environ, GAIA_DATA_DIR=str(data), GAIA_DB=str(data / "gaia.db"), PYTHONDONTWRITEBYTECODE="1")
    with open(work / "worker.log", "w", encoding="utf-8") as log:
        try:
            subprocess.run([sys.executable, str(WORKER), str(root), str(source), str(target)], cwd=work, env=env,
                           stdout=log, stderr=subprocess.STDOUT, timeout=BATCH_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            pass
    found = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    return {key: found.get(key, {"verdict": "ERROR", "rule": "worker-lost"}) for key in units}


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
