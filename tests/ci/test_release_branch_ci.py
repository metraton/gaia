"""CI tests a pull request into a release branch, every push to one, and keeps a pre-release off main."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
JOBS = CI["jobs"]
# PyYAML reads the bare workflow key ``on`` as the boolean True.
TRIGGERS = CI[True]


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "_gaia_main_version_guard", ROOT / ".github" / "scripts" / "main_version_guard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(root, version, **overrides):
    versions = {"package.json": version, "plugin": version, "pyproject.toml": version}
    versions.update(overrides)
    (root / ".claude-plugin").mkdir()
    (root / "package.json").write_text(json.dumps({"version": versions["package.json"]}), encoding="utf-8")
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": versions["plugin"]}), encoding="utf-8")
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{versions["pyproject.toml"]}"\n', encoding="utf-8")
    return root


def test_pull_requests_into_release_branches_run_ci_like_pushes_to_them():
    assert "release/**" in TRIGGERS["pull_request"]["branches"]
    assert "main" in TRIGGERS["pull_request"]["branches"]
    assert TRIGGERS["push"]["branches"] == TRIGGERS["pull_request"]["branches"]


def test_a_push_to_a_release_branch_is_never_superseded_by_the_next_one():
    concurrency = CI["concurrency"]
    assert "startsWith(github.ref, 'refs/heads/release/')" in concurrency["group"]
    assert "github.sha" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is True


def test_the_reuse_decision_still_looks_up_the_pull_request_head():
    (lookup,) = [s for s in JOBS["decide"]["steps"] if s.get("id") == "lookup"]
    assert lookup["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert "if" not in JOBS["decide"]


def test_the_main_guard_runs_for_main_only_and_reaches_the_verdict():
    condition = JOBS["main-version-guard"]["if"]
    assert "github.base_ref == 'main'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert "release" not in condition
    assert "decide" not in JOBS["main-version-guard"].get("needs", [])
    assert "main-version-guard" in JOBS["ci-verdict"]["needs"]


def test_a_stable_tree_passes_the_main_guard(tmp_path, capsys):
    assert _load_guard().main(["--root", str(_tree(tmp_path, "5.8.0"))]) == 0


@pytest.mark.parametrize(
    "source", ["package.json", "plugin", "pyproject.toml"], ids=["package", "plugin", "pyproject"]
)
def test_a_pre_release_in_any_version_source_fails_the_main_guard(tmp_path, capsys, source):
    root = _tree(tmp_path, "5.8.0", **{source: "5.8.0-rc.1"})
    assert _load_guard().main(["--root", str(root)]) == 1
    assert "5.8.0-rc.1" in capsys.readouterr().err
