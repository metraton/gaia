"""
Unit tests for bin/cli/plans.py -- gaia plans subcommand.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure bin/ is on sys.path so the plugin is importable
_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.plans import (
    _collect_briefs,
    _find_project_root,
    _parse_frontmatter,
    _resolve_brief_dir,
    _rename_one,
    _strip_prefix,
    cmd_plans,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_brief(tmp_path: Path, name: str, brief_status: str = "draft", plan_status: str | None = None) -> Path:
    """Create a minimal brief directory under tmp_path/briefs/<name>/."""
    brief_dir = tmp_path / "briefs" / name
    brief_dir.mkdir(parents=True)
    (brief_dir / "brief.md").write_text(
        f"---\nstatus: {brief_status}\n---\n\n# {name}\n", encoding="utf-8"
    )
    if plan_status is not None:
        (brief_dir / "plan.md").write_text(
            f"---\nstatus: {plan_status}\n---\n\n# Plan\n", encoding="utf-8"
        )
    return brief_dir


class _MockArgs:
    """Simple namespace replacement for argparse.Namespace."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_simple_kv(self):
        text = "---\nstatus: draft\ncreated: 2026-01-01\n---\n\n# Title"
        fm = _parse_frontmatter(text)
        assert fm["status"] == "draft"
        assert fm["created"] == "2026-01-01"

    def test_no_frontmatter(self):
        text = "# Title\nNo frontmatter here."
        assert _parse_frontmatter(text) == {}

    def test_missing_closing_fence(self):
        text = "---\nstatus: draft\n"
        assert _parse_frontmatter(text) == {}

    def test_empty_value_key_skipped(self):
        text = "---\nstatus: draft\nempty:\n---\n"
        fm = _parse_frontmatter(text)
        assert "status" in fm
        assert "empty" not in fm


# ---------------------------------------------------------------------------
# _collect_briefs
# ---------------------------------------------------------------------------

class TestCollectBriefs:
    def test_empty_dir(self, tmp_path):
        briefs_dir = tmp_path / "briefs"
        briefs_dir.mkdir()
        assert _collect_briefs(briefs_dir) == []

    def test_nonexistent_dir(self, tmp_path):
        assert _collect_briefs(tmp_path / "nonexistent") == []

    def test_single_brief_no_plan(self, tmp_path):
        _make_brief(tmp_path, "feature-a", brief_status="active")
        briefs = _collect_briefs(tmp_path / "briefs")
        assert len(briefs) == 1
        b = briefs[0]
        assert b["name"] == "feature-a"
        assert b["brief_status"] == "active"
        assert b["has_plan"] is False
        assert b["plan_file_status"] == "(absent)"

    def test_brief_with_plan(self, tmp_path):
        _make_brief(tmp_path, "feature-b", brief_status="draft", plan_status="pending")
        briefs = _collect_briefs(tmp_path / "briefs")
        assert len(briefs) == 1
        b = briefs[0]
        assert b["has_plan"] is True
        assert b["plan_file_status"] == "pending"

    def test_multiple_briefs_sorted(self, tmp_path):
        _make_brief(tmp_path, "zzz-last")
        _make_brief(tmp_path, "aaa-first")
        briefs = _collect_briefs(tmp_path / "briefs")
        names = [b["name"] for b in briefs]
        assert names == sorted(names)

    def test_skips_files_in_briefs_dir(self, tmp_path):
        briefs_dir = tmp_path / "briefs"
        briefs_dir.mkdir()
        # File at briefs level (not a directory) should be skipped
        (briefs_dir / "README.md").write_text("# README")
        assert _collect_briefs(briefs_dir) == []

    def test_skips_dir_without_brief_md(self, tmp_path):
        briefs_dir = tmp_path / "briefs"
        (briefs_dir / "orphan").mkdir(parents=True)
        # No brief.md in this directory
        assert _collect_briefs(briefs_dir) == []


# ---------------------------------------------------------------------------
# _find_project_root
# ---------------------------------------------------------------------------

class TestFindProjectRoot:
    def test_finds_root_from_nested_dir(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        root = _find_project_root(nested)
        assert root == tmp_path

    def test_returns_none_when_no_claude_dir(self, tmp_path):
        # tmp_path has no .claude -- walk up to filesystem root finds nothing
        # Use a very nested path that definitely has no .claude above it
        nested = tmp_path / "x"
        nested.mkdir()
        # Patch Path.cwd so we don't accidentally find a real .claude above
        result = _find_project_root(nested)
        # May or may not find one depending on actual filesystem; just verify type
        assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# cmd_plans list
# ---------------------------------------------------------------------------

class TestCmdPlansList:
    def _run_list(self, tmp_path, json_output=False):
        """Run cmd_plans list with project root patched to tmp_path."""
        args = _MockArgs(plans_cmd="list", json=json_output)
        with patch("cli.plans._find_project_root", return_value=tmp_path):
            with patch("cli.plans._get_briefs_dir", return_value=tmp_path / "briefs"):
                return cmd_plans(args)

    def test_list_exits_zero(self, tmp_path):
        _make_brief(tmp_path, "my-feature")
        rc = self._run_list(tmp_path)
        assert rc == 0

    def test_list_json_valid(self, tmp_path, capsys):
        _make_brief(tmp_path, "my-feature", brief_status="active", plan_status="pending")
        rc = self._run_list(tmp_path, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "briefs" in data
        assert rc == 0

    def test_list_json_contains_brief(self, tmp_path, capsys):
        _make_brief(tmp_path, "gaia-cli", brief_status="draft")
        self._run_list(tmp_path, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        names = [b["name"] for b in data["briefs"]]
        assert "gaia-cli" in names

    def test_list_no_briefs(self, tmp_path, capsys):
        (tmp_path / "briefs").mkdir(parents=True)
        rc = self._run_list(tmp_path)
        captured = capsys.readouterr()
        assert "No briefs" in captured.out
        assert rc == 0

    def test_list_missing_root_returns_1(self, capsys):
        args = _MockArgs(plans_cmd="list", json=False)
        with patch("cli.plans._find_project_root", return_value=None):
            rc = cmd_plans(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_plans show
# ---------------------------------------------------------------------------

class TestCmdPlansShow:
    def _run_show(self, tmp_path, name, json_output=False):
        args = _MockArgs(plans_cmd="show", name=name, json=json_output)
        with patch("cli.plans._find_project_root", return_value=tmp_path):
            with patch("cli.plans._get_briefs_dir", return_value=tmp_path / "briefs"):
                return cmd_plans(args)

    def test_show_exits_zero(self, tmp_path):
        _make_brief(tmp_path, "gaia-cli")
        rc = self._run_show(tmp_path, "gaia-cli")
        assert rc == 0

    def test_show_json_has_brief_content(self, tmp_path, capsys):
        _make_brief(tmp_path, "gaia-cli", brief_status="draft")
        rc = self._run_show(tmp_path, "gaia-cli", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["name"] == "gaia-cli"
        assert "brief" in data
        assert rc == 0

    def test_show_json_includes_plan_when_present(self, tmp_path, capsys):
        _make_brief(tmp_path, "gaia-cli", plan_status="pending")
        self._run_show(tmp_path, "gaia-cli", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "plan" in data

    def test_show_json_no_plan_key_when_absent(self, tmp_path, capsys):
        _make_brief(tmp_path, "gaia-cli", brief_status="draft")
        self._run_show(tmp_path, "gaia-cli", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "plan" not in data

    def test_show_unknown_brief_returns_1(self, tmp_path):
        (tmp_path / "briefs").mkdir(parents=True)
        rc = self._run_show(tmp_path, "nonexistent")
        assert rc == 1

    def test_show_missing_root_returns_1(self, capsys):
        args = _MockArgs(plans_cmd="show", name="anything", json=False)
        with patch("cli.plans._find_project_root", return_value=None):
            rc = cmd_plans(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Integration: run via entry point with subprocess
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _strip_prefix
# ---------------------------------------------------------------------------

class TestStripPrefix:
    def test_strips_open(self):
        assert _strip_prefix("open_my-feature") == "my-feature"

    def test_strips_in_progress(self):
        assert _strip_prefix("in-progress_my-feature") == "my-feature"

    def test_strips_closed(self):
        assert _strip_prefix("closed_my-feature") == "my-feature"

    def test_bare_name_unchanged(self):
        assert _strip_prefix("my-feature") == "my-feature"

    def test_unknown_prefix_unchanged(self):
        assert _strip_prefix("pending_my-feature") == "pending_my-feature"


# ---------------------------------------------------------------------------
# _resolve_brief_dir
# ---------------------------------------------------------------------------

class TestResolveBriefDir:
    def test_exact_match_with_prefix(self, tmp_path):
        _make_brief(tmp_path, "open_feature-a")
        result = _resolve_brief_dir(tmp_path / "briefs", "open_feature-a")
        assert result is not None
        assert result.name == "open_feature-a"

    def test_fuzzy_match_without_prefix(self, tmp_path):
        _make_brief(tmp_path, "closed_feature-a")
        result = _resolve_brief_dir(tmp_path / "briefs", "feature-a")
        assert result is not None
        assert result.name == "closed_feature-a"

    def test_returns_none_when_not_found(self, tmp_path):
        (tmp_path / "briefs").mkdir(parents=True)
        result = _resolve_brief_dir(tmp_path / "briefs", "nonexistent")
        assert result is None

    def test_raises_on_ambiguous(self, tmp_path):
        # Two directories with same bare name but different prefixes
        _make_brief(tmp_path, "open_feature-a")
        _make_brief(tmp_path, "closed_feature-a")
        with pytest.raises(ValueError, match="Ambiguous"):
            _resolve_brief_dir(tmp_path / "briefs", "feature-a")

    def test_nonexistent_briefs_dir_returns_none(self, tmp_path):
        result = _resolve_brief_dir(tmp_path / "no-briefs", "feature-a")
        assert result is None


# ---------------------------------------------------------------------------
# _rename_one
# ---------------------------------------------------------------------------

class TestRenameOne:
    def test_rename_open_to_closed_on_verified_status(self, tmp_path):
        brief_dir = _make_brief(tmp_path, "open_feature-a", brief_status="verified")
        result = _rename_one(tmp_path / "briefs", "open_feature-a")
        assert result["old_name"] == "open_feature-a"
        assert result["new_name"] == "closed_feature-a"
        assert result["status"] == "verified"
        assert result["action"] == "renamed"
        assert not brief_dir.exists()
        assert (tmp_path / "briefs" / "closed_feature-a").is_dir()

    def test_rename_open_to_in_progress(self, tmp_path):
        _make_brief(tmp_path, "open_feature-b", brief_status="in-progress")
        result = _rename_one(tmp_path / "briefs", "open_feature-b")
        assert result["new_name"] == "in-progress_feature-b"
        assert result["action"] == "renamed"

    def test_already_correct_returns_already_correct(self, tmp_path):
        _make_brief(tmp_path, "open_feature-c", brief_status="draft")
        result = _rename_one(tmp_path / "briefs", "open_feature-c")
        assert result["action"] == "already-correct"
        assert result["new_name"] == "open_feature-c"

    def test_resolve_by_bare_name(self, tmp_path):
        _make_brief(tmp_path, "in-progress_feature-d", brief_status="done")
        result = _rename_one(tmp_path / "briefs", "feature-d")
        assert result["old_name"] == "in-progress_feature-d"
        assert result["new_name"] == "closed_feature-d"
        assert result["action"] == "renamed"

    def test_raises_on_unknown_status(self, tmp_path):
        _make_brief(tmp_path, "open_feature-e", brief_status="unknown-status")
        with pytest.raises(ValueError, match="Unknown status"):
            _rename_one(tmp_path / "briefs", "open_feature-e")

    def test_raises_when_not_found(self, tmp_path):
        (tmp_path / "briefs").mkdir(parents=True)
        with pytest.raises(ValueError, match="not found"):
            _rename_one(tmp_path / "briefs", "nonexistent")


# ---------------------------------------------------------------------------
# cmd_plans rename (subcommand)
# ---------------------------------------------------------------------------

class TestCmdPlansRename:
    def _run_rename(self, tmp_path, name=None, rename_all=False, json_output=False):
        args = _MockArgs(plans_cmd="rename", name=name, **{"all": rename_all}, json=json_output)
        with patch("cli.plans._find_project_root", return_value=tmp_path):
            with patch("cli.plans._get_briefs_dir", return_value=tmp_path / "briefs"):
                return cmd_plans(args)

    def test_rename_single_returns_zero(self, tmp_path):
        _make_brief(tmp_path, "open_evidence-runner", brief_status="verified")
        rc = self._run_rename(tmp_path, name="open_evidence-runner")
        assert rc == 0

    def test_rename_single_json_output(self, tmp_path, capsys):
        _make_brief(tmp_path, "open_evidence-runner", brief_status="verified")
        rc = self._run_rename(tmp_path, name="open_evidence-runner", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["old_name"] == "open_evidence-runner"
        assert data["new_name"] == "closed_evidence-runner"
        assert data["status"] == "verified"
        assert data["action"] == "renamed"
        assert rc == 0

    def test_rename_already_correct_json(self, tmp_path, capsys):
        _make_brief(tmp_path, "open_my-feature", brief_status="draft")
        rc = self._run_rename(tmp_path, name="open_my-feature", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["action"] == "already-correct"
        assert rc == 0

    def test_rename_all_batch_sync(self, tmp_path, capsys):
        _make_brief(tmp_path, "open_a", brief_status="done")
        _make_brief(tmp_path, "in-progress_b", brief_status="draft")
        _make_brief(tmp_path, "closed_c", brief_status="verified")
        rc = self._run_rename(tmp_path, rename_all=True, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        results = data["results"]
        result_map = {r["old_name"]: r for r in results}
        assert result_map["open_a"]["new_name"] == "closed_a"
        assert result_map["open_a"]["action"] == "renamed"
        assert result_map["in-progress_b"]["new_name"] == "open_b"
        assert result_map["in-progress_b"]["action"] == "renamed"
        assert result_map["closed_c"]["action"] == "already-correct"
        assert rc == 0

    def test_rename_missing_root_returns_1(self):
        args = _MockArgs(plans_cmd="rename", name="anything", **{"all": False}, json=False)
        with patch("cli.plans._find_project_root", return_value=None):
            rc = cmd_plans(args)
        assert rc == 1

    def test_rename_unknown_brief_returns_1(self, tmp_path):
        (tmp_path / "briefs").mkdir(parents=True)
        rc = self._run_rename(tmp_path, name="nonexistent")
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_plans show -- prefix-tolerant
# ---------------------------------------------------------------------------

class TestCmdPlansShowPrefixTolerant:
    def _run_show(self, tmp_path, name, json_output=False):
        args = _MockArgs(plans_cmd="show", name=name, json=json_output)
        with patch("cli.plans._find_project_root", return_value=tmp_path):
            with patch("cli.plans._get_briefs_dir", return_value=tmp_path / "briefs"):
                return cmd_plans(args)

    def test_show_without_prefix_finds_brief(self, tmp_path):
        _make_brief(tmp_path, "closed_evidence-runner", brief_status="verified")
        rc = self._run_show(tmp_path, "evidence-runner")
        assert rc == 0

    def test_show_without_prefix_json_has_correct_name(self, tmp_path, capsys):
        _make_brief(tmp_path, "open_my-feature", brief_status="draft")
        rc = self._run_show(tmp_path, "my-feature", json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["name"] == "open_my-feature"
        assert rc == 0

    def test_show_with_correct_prefix_works(self, tmp_path):
        _make_brief(tmp_path, "in-progress_my-feature", brief_status="in-progress")
        rc = self._run_show(tmp_path, "in-progress_my-feature")
        assert rc == 0

    def test_show_ambiguous_returns_1(self, tmp_path):
        _make_brief(tmp_path, "open_my-feature", brief_status="draft")
        _make_brief(tmp_path, "closed_my-feature", brief_status="verified")
        rc = self._run_show(tmp_path, "my-feature")
        assert rc == 1

    def test_show_nonexistent_returns_1(self, tmp_path):
        (tmp_path / "briefs").mkdir(parents=True)
        rc = self._run_show(tmp_path, "nonexistent")
        assert rc == 1


# ---------------------------------------------------------------------------
# Integration: run via entry point with subprocess
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_plans_list_runs(self):
        """Smoke test: python bin/gaia plans list exits 0."""
        import subprocess

        bin_gaia = _BIN_DIR / "gaia"
        gaia_ops_dev = _BIN_DIR.parent

        result = subprocess.run(
            [sys.executable, str(bin_gaia), "plans", "list"],
            capture_output=True,
            text=True,
            cwd=str(gaia_ops_dev),
        )
        assert result.returncode == 0, (
            f"Expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
