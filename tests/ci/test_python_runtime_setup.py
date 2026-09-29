"""The Python CI suite provisions the runtime its plugin tests execute."""

import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from tests.conftest import require_tool


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
    subprocess.run([require_tool("sh"), "-e", "-c", script], cwd=clone,
                   check=True, capture_output=True)
    after = git("rev-parse", "HEAD").stdout.strip()
    assert after == before
    assert git("show", f"{baseline}:opencode/plugin.ts").stdout
    assert git("rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    assert git("tag", "--list").stdout == ""
    print(f"SHALLOW_BASELINE missing_exit={missing.returncode} "
          f"HEAD_before={before} HEAD_after={after} baseline_readable=true shallow=true tags=0")


_EXHAUSTIVE_OPENCODE_MATRIX = (
    "tests/integration/test_opencode_protected_edit_bootstrap.py::"
    "test_exhaustive_file_alias_payload_and_path_matrix_reaches_real_bridge"
)


def _pytest_arguments(run):
    """The arguments after ``-m pytest`` in a workflow ``run`` script."""
    command = re.sub(r"\$\{\{[^}]*\}\}", "EXPR", run.replace("\\\n", " "))
    tokens = shlex.split(command[command.index("-m pytest"):])
    return tokens[2:]


def _collected(root, arguments):
    """Node ids a collection from the repo root selects with these arguments.

    A second -q would switch the listing from node ids to per-file counts.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         *(argument for argument in arguments if argument != "-q"), "-n", "0"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_exhaustive_opencode_matrix_leaves_per_pr_ci_but_keeps_a_scheduled_run():
    """The per-PR suite leaves the exhaustive matrix out and a scheduled workflow runs it.

    Per-PR CI names no test path, so it runs the shared selection, and a
    separate-token option value would be taken as a path when pytest picks its
    rootdir and configfile (run 36487229785 lost pyproject.toml that way).
    """
    root = Path(__file__).resolve().parents[2]
    workflows = root / ".github" / "workflows"
    ci = yaml.safe_load((workflows / "ci.yml").read_text(encoding="utf-8"))
    ci_arguments = [
        _pytest_arguments(step["run"])
        for step in ci["jobs"]["test-python"]["steps"]
        if "-m pytest" in step.get("run", "")
    ]
    assert ci_arguments
    for arguments in ci_arguments:
        assert [token for token in arguments if not token.startswith("-")] == []

    module = _EXHAUSTIVE_OPENCODE_MATRIX.split("::")[0]
    shared_selection = _collected(root, [module])
    assert shared_selection
    assert _EXHAUSTIVE_OPENCODE_MATRIX not in shared_selection

    nightly = yaml.safe_load((workflows / "nightly.yml").read_text(encoding="utf-8"))
    # PyYAML reads the bare workflow key ``on`` as the boolean True.
    assert nightly[True]["schedule"]
    nightly_arguments = [
        _pytest_arguments(step["run"])
        for job in nightly["jobs"].values()
        for step in job["steps"]
        if "-m pytest" in step.get("run", "")
    ]
    assert any(
        _EXHAUSTIVE_OPENCODE_MATRIX in _collected(root, arguments)
        for arguments in nightly_arguments
    )
