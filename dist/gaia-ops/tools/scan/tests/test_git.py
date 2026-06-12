"""
Unit tests for the Git Scanner (T022).

Tests platform detection from remote URLs, all remotes listing,
default branch detection, monorepo workspace detection, and
branch strategy detection.
"""

import json
import textwrap
from pathlib import Path
from typing import Any, Dict

import pytest

from tools.scan.scanners.git import (
    GitScanner,
    _detect_branch_strategy,
    _detect_default_branch,
    _detect_platform_from_url,
    _determine_primary_platform,
    _parse_git_config,
)
from tools.scan.tests.conftest import create_git_dir


@pytest.fixture
def scanner() -> GitScanner:
    """Create a GitScanner instance."""
    return GitScanner()


# ---------------------------------------------------------------------------
# Scanner basics
# ---------------------------------------------------------------------------


class TestGitScannerBasics:
    """Test scanner metadata and basic contract."""

    def test_scanner_name(self, scanner: GitScanner) -> None:
        assert scanner.SCANNER_NAME == "git"

    def test_scanner_version(self, scanner: GitScanner) -> None:
        assert scanner.SCANNER_VERSION == "1.0.0"

    def test_owned_sections(self, scanner: GitScanner) -> None:
        assert scanner.OWNED_SECTIONS == ["git"]

    def test_source_tag(self, scanner: GitScanner) -> None:
        assert scanner.source_tag == "scanner:git"


# ---------------------------------------------------------------------------
# Platform detection from URL
# ---------------------------------------------------------------------------


class TestPlatformDetection:
    """Test platform detection from remote URLs."""

    def test_github_ssh(self) -> None:
        assert _detect_platform_from_url("git@github.com:org/repo.git") == "github"

    def test_github_https(self) -> None:
        assert _detect_platform_from_url("https://github.com/org/repo.git") == "github"

    def test_gitlab_ssh(self) -> None:
        assert _detect_platform_from_url("git@gitlab.com:group/project.git") == "gitlab"

    def test_gitlab_https(self) -> None:
        assert _detect_platform_from_url("https://gitlab.com/group/project.git") == "gitlab"

    def test_bitbucket_ssh(self) -> None:
        assert _detect_platform_from_url("git@bitbucket.org:team/repo.git") == "bitbucket"

    def test_bitbucket_https(self) -> None:
        assert _detect_platform_from_url("https://bitbucket.org/team/repo.git") == "bitbucket"

    def test_self_hosted_ssh(self) -> None:
        assert _detect_platform_from_url("git@git.internal.company.com:team/project.git") == "self-hosted"

    def test_self_hosted_https(self) -> None:
        assert _detect_platform_from_url("https://git.internal.company.com/team/project.git") == "self-hosted"

    def test_empty_url(self) -> None:
        assert _detect_platform_from_url("") is None

    def test_none_url(self) -> None:
        assert _detect_platform_from_url("") is None


# ---------------------------------------------------------------------------
# Remote detection
# ---------------------------------------------------------------------------


class TestRemoteDetection:
    """Test all remotes listed from git config."""

    def test_detect_github_platform(
        self, scanner: GitScanner, git_project_github: Path
    ) -> None:
        result = scanner.scan(git_project_github)
        git_section = result.sections["git"]
        assert git_section["platform"] == "github"

    def test_detect_gitlab_platform(
        self, scanner: GitScanner, git_project_gitlab: Path
    ) -> None:
        result = scanner.scan(git_project_gitlab)
        git_section = result.sections["git"]
        assert git_section["platform"] == "gitlab"

    def test_detect_bitbucket_platform(
        self, scanner: GitScanner, git_project_bitbucket: Path
    ) -> None:
        result = scanner.scan(git_project_bitbucket)
        git_section = result.sections["git"]
        assert git_section["platform"] == "bitbucket"

    def test_detect_selfhosted_platform(
        self, scanner: GitScanner, git_project_selfhosted: Path
    ) -> None:
        result = scanner.scan(git_project_selfhosted)
        git_section = result.sections["git"]
        assert git_section["platform"] == "self-hosted"

    def test_all_remotes_listed(
        self, scanner: GitScanner, git_project_github: Path
    ) -> None:
        result = scanner.scan(git_project_github)
        git_section = result.sections["git"]
        remote_names = [r["name"] for r in git_section["remotes"]]
        assert "origin" in remote_names
        assert "upstream" in remote_names
        assert len(remote_names) == 2

    def test_remote_has_url_and_platform(
        self, scanner: GitScanner, git_project_github: Path
    ) -> None:
        result = scanner.scan(git_project_github)
        git_section = result.sections["git"]
        origin = [r for r in git_section["remotes"] if r["name"] == "origin"][0]
        assert "github.com" in origin["url"]
        assert origin["platform"] == "github"

    def test_multiple_remotes_different_platforms(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(
            tmp_path,
            remote_url="git@github.com:org/repo.git",
            extra_remotes={"upstream": "git@gitlab.com:group/repo.git"},
        )
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        remote_names = [r["name"] for r in git_section["remotes"]]
        assert "origin" in remote_names
        assert "upstream" in remote_names


# ---------------------------------------------------------------------------
# Default branch detection
# ---------------------------------------------------------------------------


class TestDefaultBranchDetection:
    """Test default branch detection from HEAD."""

    def test_detect_main_branch(
        self, scanner: GitScanner, git_project_github: Path
    ) -> None:
        result = scanner.scan(git_project_github)
        git_section = result.sections["git"]
        assert git_section["default_branch"] == "main"

    def test_detect_master_branch(
        self, scanner: GitScanner, git_project_bitbucket: Path
    ) -> None:
        result = scanner.scan(git_project_bitbucket)
        git_section = result.sections["git"]
        assert git_section["default_branch"] == "master"

    def test_detect_custom_branch(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path, default_branch="develop")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["default_branch"] == "develop"


# ---------------------------------------------------------------------------
# Monorepo workspace detection
# ---------------------------------------------------------------------------


class TestMonorepoWorkspaceDetection:
    """Test monorepo workspace field in git scanner.

    Note: Monorepo detection is now owned by StackScanner. The git scanner
    always returns workspace_config=None. These tests verify the git scanner
    no longer duplicates monorepo detection.
    """

    def test_monorepo_always_none_with_turbo(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path)
        (tmp_path / "turbo.json").write_text("{}")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["monorepo"]["workspace_config"] is None

    def test_monorepo_always_none_with_nx(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path)
        (tmp_path / "nx.json").write_text("{}")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["monorepo"]["workspace_config"] is None

    def test_monorepo_always_none_with_lerna(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path)
        (tmp_path / "lerna.json").write_text("{}")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["monorepo"]["workspace_config"] is None

    def test_monorepo_always_none_with_pnpm(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["monorepo"]["workspace_config"] is None

    def test_monorepo_always_none_with_npm(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path)
        pkg = {"name": "test", "workspaces": ["packages/*"]}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["monorepo"]["workspace_config"] is None

    def test_no_workspace_config(
        self, scanner: GitScanner, git_project_github: Path
    ) -> None:
        result = scanner.scan(git_project_github)
        git_section = result.sections["git"]
        assert git_section["monorepo"]["workspace_config"] is None


# ---------------------------------------------------------------------------
# Branch strategy detection
# ---------------------------------------------------------------------------


class TestBranchStrategyDetection:
    """Test branch strategy detection from branch patterns."""

    def test_detect_gitflow(
        self, scanner: GitScanner, git_project_gitflow: Path
    ) -> None:
        result = scanner.scan(git_project_gitflow)
        git_section = result.sections["git"]
        strategy = git_section["branch_strategy"]
        assert strategy["detected"] is True
        assert strategy["pattern"] == "gitflow"

    def test_detect_trunk_based(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path, default_branch="main")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        strategy = git_section["branch_strategy"]
        assert strategy["detected"] is True
        assert strategy["pattern"] == "trunk-based"

    def test_detect_github_flow(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(
            tmp_path,
            default_branch="main",
            branches=["feature/new-ui", "feature/api-v2", "fix/bug-123", "chore/deps"],
        )
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        strategy = git_section["branch_strategy"]
        assert strategy["detected"] is True
        assert strategy["pattern"] == "github-flow"

    def test_gitflow_indicators_include_develop(
        self, scanner: GitScanner, git_project_gitflow: Path
    ) -> None:
        result = scanner.scan(git_project_gitflow)
        strategy = result.sections["git"]["branch_strategy"]
        assert any("develop" in ind for ind in strategy["indicators"])


# ---------------------------------------------------------------------------
# No .git directory
# ---------------------------------------------------------------------------


class TestNoGitDirectory:
    """Test behavior when no .git directory exists."""

    def test_no_git_returns_section_with_nulls(
        self, scanner: GitScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        git_section = result.sections["git"]
        assert git_section["platform"] is None
        assert git_section["remotes"] == []
        assert git_section["default_branch"] is None

    def test_no_git_branch_strategy_not_detected(
        self, scanner: GitScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        strategy = result.sections["git"]["branch_strategy"]
        assert strategy["detected"] is False

    def test_no_git_still_has_source_tag(
        self, scanner: GitScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        assert result.sections["git"]["_source"] == "scanner:git"

    def test_no_git_has_warning(
        self, scanner: GitScanner, empty_project: Path
    ) -> None:
        result = scanner.scan(empty_project)
        assert any("No .git" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Git in subdirectory (monorepo wrapper / nested repo)
# ---------------------------------------------------------------------------


class TestGitInSubdirectory:
    """Test that scanner finds .git in immediate subdirectories."""

    def test_finds_git_in_subdirectory(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        sub = tmp_path / "my-monorepo"
        sub.mkdir()
        create_git_dir(
            sub,
            remote_url="git@gitlab.com:org/my-monorepo.git",
            default_branch="main",
        )
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["platform"] == "gitlab"
        assert git_section["default_branch"] == "main"
        assert len(git_section["remotes"]) >= 1

    def test_git_root_field_set_when_in_subdir(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        sub = tmp_path / "qxo-monorepo"
        sub.mkdir()
        create_git_dir(sub)
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["git_root"] == "qxo-monorepo"

    def test_git_root_not_set_when_at_root(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        create_git_dir(tmp_path)
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert "git_root" not in git_section

    def test_warning_when_git_in_subdir(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        sub = tmp_path / "inner-repo"
        sub.mkdir()
        create_git_dir(sub)
        result = scanner.scan(tmp_path)
        assert any("subdirectory" in w for w in result.warnings)

    def test_no_git_anywhere_returns_nulls(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        (tmp_path / "some-dir").mkdir()
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["platform"] is None
        assert git_section["remotes"] == []

    def test_skips_dotdirs_and_vendor(
        self, scanner: GitScanner, tmp_path: Path
    ) -> None:
        # .git in a hidden dir should not be picked up as a subdirectory match
        hidden = tmp_path / ".hidden-repo"
        hidden.mkdir()
        create_git_dir(hidden, remote_url="git@github.com:hidden/repo.git")
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        create_git_dir(vendor, remote_url="git@github.com:vendor/repo.git")
        result = scanner.scan(tmp_path)
        git_section = result.sections["git"]
        assert git_section["platform"] is None
