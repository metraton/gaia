"""Classify a batch of replay units with the Bash validator and truth-table harness of one checkout: worker.py ROOT IN OUT."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tarfile
from pathlib import Path

RULE = re.compile(r"Rule: (\S+)\.")
HARNESS = Path("tests/hooks/modules/security/test_effect_and_target_truth_table.py")


def _harness(root: Path):
    for entry in (str(root / "hooks"), str(root)):
        sys.path.insert(0, entry)
    spec = importlib.util.spec_from_file_location("replay_truth_harness", root / HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _world(harness, base: Path) -> dict:
    """The same contexts, scratch entry and worktrees root the truth-table fixture builds, under *base*."""
    import pytest
    from gaia.paths import ensure_layout, scratch_dir, worktrees_dir

    harness._isolate_targets(pytest.MonkeyPatch(), base)
    ensure_layout()
    managed = Path(os.path.realpath(str(worktrees_dir())))
    dirs = {name: harness._build_context(spec, base, name, managed) for name, spec in harness.HEADER["contexts"].items()}
    entry = os.path.join(os.path.realpath(str(scratch_dir())), "atruthtable01.token")
    os.makedirs(entry, exist_ok=True)
    with tarfile.open(os.path.join(entry, "a.tar"), "w") as archive:
        archive.addfile(tarfile.TarInfo("pkg/empty.txt"))
    return {"root": base, "dirs": dirs, "SCRATCH": entry, "WORKTREES": str(managed)}


def _classify(harness, world: dict, unit: dict) -> dict:
    import pytest

    patch = pytest.MonkeyPatch()
    try:
        if "row" in unit:
            verdict, reason = harness._observe_isolated(unit["row"], world, patch)
        else:
            patch.chdir(world["root"])
            verdict, reason = harness.observe(unit["command"], unit["context"], world)
    except Exception as exc:
        return {"verdict": "ERROR", "rule": type(exc).__name__}
    finally:
        patch.undo()
    found = RULE.search(reason or "")
    return {"verdict": verdict, "rule": found.group(1) if found else ""}


def main(root: str, source: str, target: str) -> None:
    base = Path(os.environ["GAIA_DATA_DIR"]).parent
    harness = _harness(Path(root))
    world = _world(harness, base)
    units = json.loads(Path(source).read_text(encoding="utf-8"))
    results = {}
    for index, (key, unit) in enumerate(units.items(), 1):
        results[key] = _classify(harness, world, unit)
        if index % 500 == 0 or index == len(units):
            Path(target).write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])
