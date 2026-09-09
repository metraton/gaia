"""The Python CI suite provisions the runtime its plugin tests execute."""

import re
import subprocess
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


def test_python_ci_fetches_exact_baseline_before_tests(tmp_path):
    """The prerequisite repairs a shallow object DB without changing HEAD."""
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["test-python"]["steps"]
    baseline = "c407aadd643051d320a3fc90ffbcbe3fdf75cb5a"
    index = next(i for i, step in enumerate(steps)
                 if step.get("name") == "Fetch pinned plugin comparison baseline")
    script = steps[index]["run"]
    assert script.splitlines() == [
        f"git fetch --no-tags --depth=1 origin {baseline}",
        f"git cat-file -e '{baseline}^{{commit}}'",
        f"git show {baseline}:opencode/plugin.ts > /dev/null",
    ]
    checkout_index = next(i for i, step in enumerate(steps)
                          if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout_index < index
    assert steps[checkout_index].get("with", {}).get("fetch-depth", 1) == 1
    assert all(index < i for i, step in enumerate(steps)
               if "-m pytest" in step.get("run", ""))

    clone = tmp_path / "shallow"
    subprocess.run([
        "git", "clone", "--no-checkout", "--no-tags", "--depth=1",
        root.as_uri(), str(clone),
    ], check=True, capture_output=True)

    def git(*args, check=True):
        """Run git only against the isolated clone."""
        return subprocess.run(["git", "-C", str(clone), *args],
                              check=check, capture_output=True, text=True)

    before = git("rev-parse", "HEAD").stdout.strip()
    assert git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    missing = git("show", f"{baseline}:opencode/plugin.ts", check=False)
    assert missing.returncode == 128
    subprocess.run(["bash", "-e", "-c", script], cwd=clone,
                   check=True, capture_output=True)
    after = git("rev-parse", "HEAD").stdout.strip()
    assert after == before
    assert git("show", f"{baseline}:opencode/plugin.ts").stdout
    assert git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert git("tag", "--list").stdout == ""
    print(f"SHALLOW_BASELINE missing_exit={missing.returncode} "
          f"HEAD_before={before} HEAD_after={after} baseline_readable=true shallow=true tags=0")
