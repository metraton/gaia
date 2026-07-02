"""
Unit tests for store_populator.py -- T2.2 (group_name inference).

Covers AC-1 and AC-2 from the gaia-scan-overhaul brief:
  AC-1: Every repo is persisted as its OWN row (no collapsing).
  AC-2: group_name is inferred from the immediate container directory.

Tests:
  (a) A container directory with multiple repos produces N individual rows,
      each with group_name == the container's basename.
  (b) A repo sitting directly at the workspace root produces group_name=None.
  (c) The aaxis-style 3-level layout (workspace/group/repo) assigns
      group_name correctly for each repo.
  (d) The ME/github-repos layout (~29 repos under one container) assigns
      group_name='github-repos' to all repos.
  (e) scan_workspace_to_store passes group_name through to populate_project.
  (f) populate_project passes group_name through to upsert_project's fields dict.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tools.scan.store_populator import (
    populate_project,
    scan_workspace_to_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(path: Path) -> None:
    """Create a minimal git repo at path (just a .git dir marker)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


# ---------------------------------------------------------------------------
# populate_project: group_name is passed to upsert_project fields
# ---------------------------------------------------------------------------

class TestPopulateProjectGroupName:
    """populate_project threads group_name into the upsert_project fields dict."""

    def _run(self, tmp_path: Path, group_name: str | None) -> dict:
        _make_repo(tmp_path)
        with (
            patch("tools.scan.store_populator.detect_role", return_value="application"),
            patch("tools.scan.store_populator._git_remote_origin", return_value=None),
            patch("tools.scan.store_populator._detect_primary_language", return_value=None),
            # upsert_project is imported lazily inside populate_project; patch the
            # canonical location so the lazy import resolves to the mock.
            patch("gaia.store.upsert_project", return_value={"status": "applied"}) as mock_upsert,
        ):
            res = populate_project(
                workspace="ws",
                project_path=tmp_path,
                agent="scanner",
                group_name=group_name,
            )
        return res, mock_upsert

    def test_group_name_none_passed_to_upsert(self, tmp_path: Path) -> None:
        res, mock_upsert = self._run(tmp_path, group_name=None)
        assert res["group_name"] is None
        _, kwargs = mock_upsert.call_args
        assert kwargs["fields"]["group_name"] is None

    def test_group_name_string_passed_to_upsert(self, tmp_path: Path) -> None:
        res, mock_upsert = self._run(tmp_path, group_name="github-repos")
        assert res["group_name"] == "github-repos"
        _, kwargs = mock_upsert.call_args
        assert kwargs["fields"]["group_name"] == "github-repos"

    def test_result_includes_group_name_key(self, tmp_path: Path) -> None:
        res, _ = self._run(tmp_path, group_name="bildwiz")
        assert "group_name" in res
        assert res["group_name"] == "bildwiz"


# ---------------------------------------------------------------------------
# (b) Repo at root level -> group_name=None
# ---------------------------------------------------------------------------

class TestRootLevelRepo:
    """A repo sitting directly under the workspace root gets group_name=None."""

    def test_root_repo_group_name_is_none(self, tmp_path: Path) -> None:
        # Layout: tmp_path/my-repo/.git
        repo = tmp_path / "my-repo"
        _make_repo(repo)

        captured: list[dict] = []

        def _fake_populate(workspace, project_path, agent, *, db_path=None, group_name=None):
            captured.append({"name": project_path.name, "group_name": group_name})
            return {"applied": 1, "rejected": 0, "role": "application",
                    "identity": "x", "name": project_path.name, "group_name": group_name}

        with (
            patch("tools.scan.store_populator.populate_project", side_effect=_fake_populate),
            patch("tools.scan.store_populator.populate_infrastructure", return_value={}),
            patch("tools.scan.store_populator.populate_orchestration", return_value={}),
            patch("tools.scan.store_populator.populate_features", return_value={}),
            patch("tools.scan.store_populator.populate_apps", return_value={}),
            patch("tools.scan.store_populator.populate_services", return_value={}),
            patch("tools.scan.store_populator.populate_libraries", return_value={}),
            patch("tools.scan.store_populator.populate_gaia_installations", return_value={}),
        ):
            scan_workspace_to_store("ws", tmp_path, "scanner")

        assert len(captured) == 1
        assert captured[0]["name"] == "my-repo"
        assert captured[0]["group_name"] is None


# ---------------------------------------------------------------------------
# (a) Container with multiple repos -> N rows, each with container group_name
# ---------------------------------------------------------------------------

class TestContainerWithMultipleRepos:
    """A container directory holding N repos produces N individual rows
    with group_name == container.name (AC-1 + AC-2)."""

    def test_three_repos_under_container(self, tmp_path: Path) -> None:
        # Layout: tmp_path/github-repos/{repo-a,repo-b,repo-c}/.git
        container = tmp_path / "github-repos"
        for name in ("repo-a", "repo-b", "repo-c"):
            _make_repo(container / name)

        captured: list[dict] = []

        def _fake_populate(workspace, project_path, agent, *, db_path=None, group_name=None):
            captured.append({"name": project_path.name, "group_name": group_name})
            return {"applied": 1, "rejected": 0, "role": "application",
                    "identity": "x", "name": project_path.name, "group_name": group_name}

        with (
            patch("tools.scan.store_populator.populate_project", side_effect=_fake_populate),
            patch("tools.scan.store_populator.populate_infrastructure", return_value={}),
            patch("tools.scan.store_populator.populate_orchestration", return_value={}),
            patch("tools.scan.store_populator.populate_features", return_value={}),
            patch("tools.scan.store_populator.populate_apps", return_value={}),
            patch("tools.scan.store_populator.populate_services", return_value={}),
            patch("tools.scan.store_populator.populate_libraries", return_value={}),
            patch("tools.scan.store_populator.populate_gaia_installations", return_value={}),
        ):
            scan_workspace_to_store("ws", tmp_path, "scanner")

        # AC-1: exactly 3 rows (no collapsing)
        assert len(captured) == 3, f"Expected 3 rows, got {len(captured)}: {captured}"
        # AC-2: every row has group_name == 'github-repos'
        names = sorted(r["name"] for r in captured)
        assert names == ["repo-a", "repo-b", "repo-c"]
        for row in captured:
            assert row["group_name"] == "github-repos", (
                f"repo {row['name']}: expected group_name='github-repos', got {row['group_name']!r}"
            )

    def test_no_collapse_each_repo_is_own_row(self, tmp_path: Path) -> None:
        """Asserts AC-1: N repos -> N rows, never 1 collapsed row."""
        container = tmp_path / "mygroup"
        for name in ("a", "b"):
            _make_repo(container / name)

        captured: list[dict] = []

        def _fake_populate(workspace, project_path, agent, *, db_path=None, group_name=None):
            captured.append(project_path.name)
            return {"applied": 1, "rejected": 0, "role": "application",
                    "identity": "x", "name": project_path.name, "group_name": group_name}

        with (
            patch("tools.scan.store_populator.populate_project", side_effect=_fake_populate),
            patch("tools.scan.store_populator.populate_infrastructure", return_value={}),
            patch("tools.scan.store_populator.populate_orchestration", return_value={}),
            patch("tools.scan.store_populator.populate_features", return_value={}),
            patch("tools.scan.store_populator.populate_apps", return_value={}),
            patch("tools.scan.store_populator.populate_services", return_value={}),
            patch("tools.scan.store_populator.populate_libraries", return_value={}),
            patch("tools.scan.store_populator.populate_gaia_installations", return_value={}),
        ):
            scan_workspace_to_store("ws", tmp_path, "scanner")

        assert captured == sorted(captured), "should be sorted by _list_repos"
        assert len(captured) == 2


# ---------------------------------------------------------------------------
# (c) Aaxis 3-level model (workspace/group/repo)
# ---------------------------------------------------------------------------

class TestAaxisThreeLevelModel:
    """The aaxis workspace has containers (bildwiz, qxo, rnd, nfi) each holding
    multiple repos.  Each repo row must carry group_name == its container name."""

    def _build_aaxis_workspace(self, root: Path) -> dict[str, list[str]]:
        """Create a fixture mirroring the aaxis layout.

        Returns a dict of {group_name: [repo_names]} for assertion.
        """
        layout = {
            "bildwiz": ["platform-repo", "infra-repo"],
            "qxo": ["qxo-monorepo", "qxo-gitops"],
            "rnd": ["ml-experiments"],
            "nfi": ["nfi-core", "nfi-api", "nfi-docs"],
        }
        for group, repos in layout.items():
            for repo in repos:
                _make_repo(root / group / repo)
        return layout

    def test_aaxis_group_names(self, tmp_path: Path) -> None:
        layout = self._build_aaxis_workspace(tmp_path)

        captured: list[dict] = []

        def _fake_populate(workspace, project_path, agent, *, db_path=None, group_name=None):
            captured.append({"name": project_path.name, "group_name": group_name})
            return {"applied": 1, "rejected": 0, "role": "application",
                    "identity": "x", "name": project_path.name, "group_name": group_name}

        with (
            patch("tools.scan.store_populator.populate_project", side_effect=_fake_populate),
            patch("tools.scan.store_populator.populate_infrastructure", return_value={}),
            patch("tools.scan.store_populator.populate_orchestration", return_value={}),
            patch("tools.scan.store_populator.populate_features", return_value={}),
            patch("tools.scan.store_populator.populate_apps", return_value={}),
            patch("tools.scan.store_populator.populate_services", return_value={}),
            patch("tools.scan.store_populator.populate_libraries", return_value={}),
            patch("tools.scan.store_populator.populate_gaia_installations", return_value={}),
        ):
            scan_workspace_to_store("aaxis", tmp_path, "scanner")

        total_repos = sum(len(v) for v in layout.values())
        assert len(captured) == total_repos, (
            f"Expected {total_repos} rows, got {len(captured)}"
        )

        by_group: dict[str, list[str]] = {}
        for row in captured:
            by_group.setdefault(row["group_name"], []).append(row["name"])

        for group, expected_repos in layout.items():
            assert group in by_group, f"group '{group}' missing from results"
            assert sorted(by_group[group]) == sorted(expected_repos), (
                f"group '{group}': expected {sorted(expected_repos)}, got {sorted(by_group[group])}"
            )


# ---------------------------------------------------------------------------
# (d) ME / github-repos model
# ---------------------------------------------------------------------------

class TestGithubReposContainer:
    """The ME workspace has a github-repos/ container holding ~29 repos.
    Each repo should be an individual row with group_name='github-repos'."""

    def test_github_repos_container(self, tmp_path: Path) -> None:
        # Simulate 5 repos under github-repos (representative subset)
        repo_names = [f"repo-{i:02d}" for i in range(5)]
        container = tmp_path / "github-repos"
        for name in repo_names:
            _make_repo(container / name)

        captured: list[dict] = []

        def _fake_populate(workspace, project_path, agent, *, db_path=None, group_name=None):
            captured.append({"name": project_path.name, "group_name": group_name})
            return {"applied": 1, "rejected": 0, "role": "application",
                    "identity": "x", "name": project_path.name, "group_name": group_name}

        with (
            patch("tools.scan.store_populator.populate_project", side_effect=_fake_populate),
            patch("tools.scan.store_populator.populate_infrastructure", return_value={}),
            patch("tools.scan.store_populator.populate_orchestration", return_value={}),
            patch("tools.scan.store_populator.populate_features", return_value={}),
            patch("tools.scan.store_populator.populate_apps", return_value={}),
            patch("tools.scan.store_populator.populate_services", return_value={}),
            patch("tools.scan.store_populator.populate_libraries", return_value={}),
            patch("tools.scan.store_populator.populate_gaia_installations", return_value={}),
        ):
            scan_workspace_to_store("me", tmp_path, "scanner")

        assert len(captured) == len(repo_names), (
            f"Expected {len(repo_names)} rows, got {len(captured)}"
        )
        for row in captured:
            assert row["group_name"] == "github-repos", (
                f"repo {row['name']}: expected group_name='github-repos', got {row['group_name']!r}"
            )


# ---------------------------------------------------------------------------
# (e) Mixed workspace: some root-level repos, some in containers
# ---------------------------------------------------------------------------

class TestMixedWorkspace:
    """Workspace with both root-level repos and grouped repos handles
    group_name assignment correctly for each."""

    def test_mixed_root_and_grouped(self, tmp_path: Path) -> None:
        # Root-level repos
        _make_repo(tmp_path / "standalone-a")
        _make_repo(tmp_path / "standalone-b")
        # Grouped repos
        _make_repo(tmp_path / "group-x" / "repo-1")
        _make_repo(tmp_path / "group-x" / "repo-2")

        captured: list[dict] = []

        def _fake_populate(workspace, project_path, agent, *, db_path=None, group_name=None):
            captured.append({"name": project_path.name, "group_name": group_name})
            return {"applied": 1, "rejected": 0, "role": "application",
                    "identity": "x", "name": project_path.name, "group_name": group_name}

        with (
            patch("tools.scan.store_populator.populate_project", side_effect=_fake_populate),
            patch("tools.scan.store_populator.populate_infrastructure", return_value={}),
            patch("tools.scan.store_populator.populate_orchestration", return_value={}),
            patch("tools.scan.store_populator.populate_features", return_value={}),
            patch("tools.scan.store_populator.populate_apps", return_value={}),
            patch("tools.scan.store_populator.populate_services", return_value={}),
            patch("tools.scan.store_populator.populate_libraries", return_value={}),
            patch("tools.scan.store_populator.populate_gaia_installations", return_value={}),
        ):
            scan_workspace_to_store("ws", tmp_path, "scanner")

        assert len(captured) == 4

        by_name = {r["name"]: r["group_name"] for r in captured}

        # Root-level repos have no group
        assert by_name["standalone-a"] is None
        assert by_name["standalone-b"] is None

        # Grouped repos carry the container name
        assert by_name["repo-1"] == "group-x"
        assert by_name["repo-2"] == "group-x"
