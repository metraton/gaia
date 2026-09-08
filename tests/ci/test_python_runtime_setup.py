"""The Python CI suite provisions the runtime its plugin tests execute."""

import re
from pathlib import Path

import yaml


def test_python_ci_provisions_pinned_bun_before_tests():
    """Bun setup precedes pytest and uses an exact stable version."""
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["test-python"]["steps"]
    bun_steps = [
        (index, step) for index, step in enumerate(steps)
        if step.get("uses", "").startswith("oven-sh/setup-bun@")
    ]
    assert len(bun_steps) == 1
    bun_index, bun_step = bun_steps[0]
    assert re.fullmatch(r"\d+\.\d+\.\d+", bun_step["with"]["bun-version"])
    test_indexes = [
        index for index, step in enumerate(steps)
        if "pytest" in step.get("run", "") and "-m pytest" in step["run"]
    ]
    assert test_indexes
    assert all(bun_index < index for index in test_indexes)
