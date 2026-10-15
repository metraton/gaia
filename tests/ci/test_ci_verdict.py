""".github/scripts/ci_verdict.py decides whether a commit can reuse an earlier green CI verdict.

The GitHub API is replaced by a routing table, so every case below is the
helper's own decision over a fixed repository history.
"""

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO = "metraton/gaia"

JSON_VERSION_PATCH = '@@ -3 +3 @@\n-  "version": "5.5.0-rc.4",\n+  "version": "5.5.0-rc.5",'
TOML_VERSION_PATCH = '@@ -3 +3 @@\n-version = "5.5.0-rc.4"\n+version = "5.5.0-rc.5"'
CHANGELOG_PATCH = "@@ -81,0 +82,2 @@\n+## [5.5.0-rc.5] - 2026-10-06\n+"
SCRIPTS_PATCH = '@@ -20 +20 @@\n-    "test": "pytest",\n+    "test": "true",'
DEPENDENCY_PATCH = '@@ -30 +30 @@\n-    "chalk": "^5.3.0"\n+    "chalk": "^5.4.0"'


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "_gaia_ci_verdict", PROJECT_ROOT / ".github" / "scripts" / "ci_verdict.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_verdict = _load_helper()


def _default_patch(name):
    if name == "pyproject.toml":
        return TOML_VERSION_PATCH
    if name == "CHANGELOG.md":
        return CHANGELOG_PATCH
    return JSON_VERSION_PATCH


def _commit(sha, tree, parents=(), files=(), patches=None):
    patches = patches or {}
    entries = []
    for name in files:
        entry = {"filename": name}
        patch = patches.get(name, _default_patch(name))
        if patch is not None:
            entry["patch"] = patch
        entries.append(entry)
    return {
        "sha": sha,
        "commit": {"tree": {"sha": tree}},
        "parents": [{"sha": p} for p in parents],
        "files": entries,
    }


def _run(run_id, head_sha, tree, status="completed", conclusion="success"):
    return {
        "id": run_id,
        "head_sha": head_sha,
        "head_commit": {"tree_id": tree},
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
    }


def _jobs(verdict_conclusion="success"):
    return {
        "jobs": [
            {"name": "Python 3.12 (shard 1/4)", "conclusion": "success"},
            {"name": "CI verdict", "conclusion": verdict_conclusion},
        ]
    }


class FakeApi:
    """Answers `gh api` paths from a dict, failing loudly on an unexpected path."""

    def __init__(self, commits, runs, jobs=None):
        self.routes = {f"repos/{REPO}/commits/{c['sha']}": c for c in commits}
        self.routes[ci_verdict.runs_path(REPO, "ci.yml")] = {"workflow_runs": runs}
        for run in runs:
            verdict = (jobs or {}).get(run["id"], "success")
            self.routes[f"repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100"] = _jobs(verdict)

    def __call__(self, path, gh="gh"):
        if path not in self.routes:
            raise AssertionError(f"unexpected API path: {path}")
        return self.routes[path]


def _find(api, sha):
    return ci_verdict.find_reusable_verdict(REPO, sha, workflow="ci.yml", api=api)


def _bump_over_green_parent(files, patches=None):
    return FakeApi(
        commits=[
            _commit("release", "t-release", parents=["parent"], files=files, patches=patches),
            _commit("parent", "t-parent", parents=["gp"], files=["bin/gaia"]),
        ],
        runs=[_run(301, "parent", "t-parent")],
    )


def test_same_sha_with_green_verdict_is_reusable():
    api = FakeApi(
        commits=[_commit("s1", "t1", parents=["p1"], files=["bin/gaia"])],
        runs=[_run(101, "s1", "t1")],
    )
    verdict = _find(api, "s1")
    assert verdict.run_id == 101
    assert verdict.head_sha == "s1"
    assert "same commit" in verdict.reason


def test_squash_commit_reuses_the_pr_head_with_the_same_tree():
    api = FakeApi(
        commits=[_commit("squash", "t-pr", parents=["main-before"], files=["bin/gaia"])],
        runs=[_run(201, "pr-head", "t-pr"), _run(200, "older", "t-old")],
    )
    verdict = _find(api, "squash")
    assert verdict.run_id == 201
    assert verdict.head_sha == "pr-head"
    assert "same tree" in verdict.reason


def test_squash_onto_a_main_that_moved_has_another_tree_and_runs_the_suite():
    api = FakeApi(
        commits=[_commit("squash", "t-merged", parents=["main-after"], files=["bin/gaia"])],
        runs=[_run(210, "pr-head", "t-pr")],
    )
    assert _find(api, "squash") is None


def test_version_only_commit_reuses_its_parent():
    api = _bump_over_green_parent(
        [
            "package.json",
            "pyproject.toml",
            "CHANGELOG.md",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
        ]
    )
    verdict = _find(api, "release")
    assert verdict.run_id == 301
    assert verdict.head_sha == "parent"
    assert "version" in verdict.reason


def test_changelog_entry_alone_reuses_its_parent():
    assert _find(_bump_over_green_parent(["CHANGELOG.md"]), "release").run_id == 301


@pytest.mark.parametrize(
    ("name", "patch"),
    [
        ("package.json", SCRIPTS_PATCH),
        ("package.json", DEPENDENCY_PATCH),
        ("package.json", JSON_VERSION_PATCH + "\n" + SCRIPTS_PATCH),
        ("pyproject.toml", '@@ -9 +9 @@\n-addopts = "-n auto"\n+addopts = ""'),
        ("hooks/hooks.json", '@@ -5 +5 @@\n-      "command": "a"\n+      "command": "b"'),
        (".claude-plugin/plugin.json", '@@ -4 +4 @@\n-  "name": "gaia",\n+  "name": "other",'),
        ("package.json", None),
    ],
    ids=["scripts", "dependency", "version-plus-scripts", "pytest-config", "hooks", "plugin-name", "no-patch"],
)
def test_version_source_edit_beyond_the_version_is_code(name, patch):
    api = _bump_over_green_parent([name], patches={name: patch})
    assert _find(api, "release") is None


def test_changed_tree_has_no_reusable_verdict():
    api = FakeApi(
        commits=[
            _commit("new", "t-new", parents=["parent"], files=["bin/cli/plan.py"]),
            _commit("parent", "t-parent", parents=["gp"], files=["bin/gaia"]),
        ],
        runs=[_run(401, "parent", "t-parent")],
    )
    assert _find(api, "new") is None


def test_diff_mixing_version_and_code_is_not_reusable():
    api = FakeApi(
        commits=[
            _commit("mixed", "t-mixed", parents=["parent"], files=["package.json", "bin/gaia"]),
            _commit("parent", "t-parent", parents=["gp"], files=["bin/gaia"]),
        ],
        runs=[_run(501, "parent", "t-parent")],
    )
    assert _find(api, "mixed") is None


def test_version_file_outside_its_exact_path_is_code():
    api = FakeApi(
        commits=[
            _commit("nested", "t-nested", parents=["parent"], files=["tests/fixtures/package.json"]),
            _commit("parent", "t-parent", parents=["gp"], files=["bin/gaia"]),
        ],
        runs=[_run(502, "parent", "t-parent")],
    )
    assert _find(api, "nested") is None


@pytest.mark.parametrize(
    "run",
    [
        _run(601, "s1", "t1", conclusion="failure"),
        _run(602, "s1", "t1", status="in_progress", conclusion=None),
        _run(603, "s1", "t1", conclusion="cancelled"),
    ],
    ids=["red", "pending", "cancelled"],
)
def test_red_or_pending_run_is_not_reusable(run):
    api = FakeApi(commits=[_commit("s1", "t1", parents=["p1"], files=["bin/gaia"])], runs=[run])
    assert _find(api, "s1") is None


def test_green_run_whose_ci_verdict_job_is_red_is_not_reusable():
    api = FakeApi(
        commits=[_commit("s1", "t1", parents=["p1"], files=["bin/gaia"])],
        runs=[_run(701, "s1", "t1")],
        jobs={701: "failure"},
    )
    assert _find(api, "s1") is None


def test_same_commit_outranks_same_tree_and_red_candidates_are_passed_over():
    api = FakeApi(
        commits=[_commit("s1", "t1", parents=["p1"], files=["bin/gaia"])],
        runs=[_run(803, "twin", "t1"), _run(802, "s1", "t1"), _run(801, "s1", "t1")],
        jobs={802: "failure"},
    )
    assert _find(api, "s1").run_id == 801


def test_cli_reports_the_source_run_and_writes_github_outputs(monkeypatch, tmp_path, capsys):
    api = FakeApi(
        commits=[_commit("s1", "t1", parents=["p1"], files=["bin/gaia"])],
        runs=[_run(901, "s1", "t1")],
    )
    monkeypatch.setattr(ci_verdict, "gh_api", api)
    outputs = tmp_path / "out"
    code = ci_verdict.main(["s1", "--repo", REPO, "--github-output", str(outputs)])
    assert code == 0
    assert "901" in capsys.readouterr().out
    lines = outputs.read_text().splitlines()
    assert "reusable=true" in lines
    assert "source_run=901" in lines


def test_cli_exits_one_when_nothing_is_reusable(monkeypatch, tmp_path):
    api = FakeApi(commits=[_commit("s1", "t1", parents=["p1"], files=["bin/gaia"])], runs=[])
    monkeypatch.setattr(ci_verdict, "gh_api", api)
    outputs = tmp_path / "out"
    assert ci_verdict.main(["s1", "--repo", REPO, "--github-output", str(outputs)]) == 1
    assert "reusable=false" in outputs.read_text().splitlines()


def test_cli_exits_two_and_runs_everything_when_the_api_fails(monkeypatch, tmp_path):
    def broken(path, gh="gh"):
        raise ci_verdict.ApiError("gh api failed: HTTP 403")

    monkeypatch.setattr(ci_verdict, "gh_api", broken)
    outputs = tmp_path / "out"
    assert ci_verdict.main(["s1", "--repo", REPO, "--github-output", str(outputs)]) == 2
    assert "reusable=false" in outputs.read_text().splitlines()


def test_gh_api_failure_raises_api_error(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "HTTP 404"

    monkeypatch.setattr(ci_verdict.subprocess, "run", lambda *a, **k: Completed())
    with pytest.raises(ci_verdict.ApiError, match="HTTP 404"):
        ci_verdict.gh_api("repos/x/y/commits/z")


def test_gh_api_parses_json(monkeypatch):
    class Completed:
        returncode = 0
        stdout = json.dumps({"sha": "z"})
        stderr = ""

    monkeypatch.setattr(ci_verdict.subprocess, "run", lambda *a, **k: Completed())
    assert ci_verdict.gh_api("repos/x/y/commits/z") == {"sha": "z"}
