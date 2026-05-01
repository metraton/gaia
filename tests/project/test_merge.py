"""Tests for gaia.project.merge() -- workspace consolidation."""

from pathlib import Path

import pytest

from gaia.project import MergeResult, merge


@pytest.fixture
def workspaces(monkeypatch, tmp_path):
    """Set GAIA_DATA_DIR so workspaces_dir() points at a tmp location."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return tmp_path / "workspaces"


def _write(path: Path, content: str = "x") -> None:
    """Helper: create file with content, including parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ---------------------------------------------------------------------------
# Preview (no confirm) -- does NOT move files
# ---------------------------------------------------------------------------

def test_preview_does_not_move_files(workspaces):
    src = workspaces / "from-id"
    _write(src / "a.txt", "alpha")
    _write(src / "sub/b.txt", "beta")

    result = merge("from-id", "to-id", confirm=False)

    # Source still has its files
    assert (src / "a.txt").is_file()
    assert (src / "sub/b.txt").is_file()
    # Target was not created
    assert not (workspaces / "to-id").exists()
    # Result reports the preview
    assert sorted(p for p, _ in result.preview) == ["a.txt", "sub/b.txt"]
    assert result.conflicts == []
    assert result.moved == []


def test_preview_returns_merge_result(workspaces):
    src = workspaces / "from-id"
    _write(src / "x.txt")
    result = merge("from-id", "to-id")
    assert isinstance(result, MergeResult)


# ---------------------------------------------------------------------------
# Confirm -- moves files for non-conflicting paths
# ---------------------------------------------------------------------------

def test_confirm_moves_non_conflicting_files(workspaces):
    src = workspaces / "from-id"
    dst = workspaces / "to-id"
    _write(src / "a.txt", "alpha")
    _write(src / "sub/b.txt", "beta")
    dst.mkdir(parents=True)

    result = merge("from-id", "to-id", confirm=True)

    assert (dst / "a.txt").read_text() == "alpha"
    assert (dst / "sub/b.txt").read_text() == "beta"
    assert sorted(result.moved) == ["a.txt", "sub/b.txt"]
    assert result.conflicts == []


def test_confirm_cleans_up_empty_source_when_no_conflicts(workspaces):
    src = workspaces / "from-id"
    _write(src / "only.txt")
    merge("from-id", "to-id", confirm=True)
    # Source dir should be removed once empty
    assert not src.exists()


# ---------------------------------------------------------------------------
# Conflicts -- same path on both sides, NOT overwritten
# ---------------------------------------------------------------------------

def test_conflict_file_not_overwritten(workspaces):
    src = workspaces / "from-id"
    dst = workspaces / "to-id"
    _write(src / "shared.txt", "from-source")
    _write(dst / "shared.txt", "from-target")

    result = merge("from-id", "to-id", confirm=True)

    # Target keeps its original content
    assert (dst / "shared.txt").read_text() == "from-target"
    # Source still has its file (not moved because conflict)
    assert (src / "shared.txt").read_text() == "from-source"
    assert "shared.txt" in result.conflicts
    assert "shared.txt" not in result.moved


def test_partial_merge_with_some_conflicts(workspaces):
    src = workspaces / "from-id"
    dst = workspaces / "to-id"
    _write(src / "shared.txt", "src")
    _write(src / "unique.txt", "u")
    _write(dst / "shared.txt", "dst")

    result = merge("from-id", "to-id", confirm=True)

    assert "shared.txt" in result.conflicts
    assert "unique.txt" in result.moved
    # Unique was moved
    assert not (src / "unique.txt").exists()
    assert (dst / "unique.txt").read_text() == "u"
    # Shared remains in src
    assert (src / "shared.txt").read_text() == "src"


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_merge_idempotent_when_source_missing(workspaces):
    """If from_id has no directory (already merged or never existed), no error."""
    result = merge("nonexistent-id", "to-id", confirm=True)
    assert result.preview == []
    assert result.conflicts == []
    assert result.moved == []


def test_merge_from_equals_to_is_noop(workspaces):
    """from_id == to_id is a no-op, returns empty MergeResult."""
    src = workspaces / "same-id"
    _write(src / "file.txt")
    result = merge("same-id", "same-id", confirm=True)
    assert result.preview == []
    assert result.moved == []
    # File still in place
    assert (src / "file.txt").is_file()


def test_merge_canonical_identity_path(workspaces):
    """Identity strings like 'host/owner/repo' map to nested directories."""
    src = workspaces / "github.com/owner/old-repo"
    dst_path = workspaces / "github.com/owner/new-repo"
    _write(src / "data.txt", "payload")

    result = merge("github.com/owner/old-repo", "github.com/owner/new-repo", confirm=True)

    assert (dst_path / "data.txt").read_text() == "payload"
    assert "data.txt" in result.moved
