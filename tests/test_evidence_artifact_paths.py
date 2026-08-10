"""Canonical evidence paths cannot drift into a project worktree."""

from pathlib import Path

import pytest

from gaia.evidence.fs import require_canonical_artifact_path


def test_relative_artifact_path_is_rejected():
    with pytest.raises(ValueError, match="repository-relative"):
        require_canonical_artifact_path("evidence/AC-1.txt")


def test_absolute_path_outside_gaia_evidence_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="canonical Gaia evidence root"):
        require_canonical_artifact_path(str(tmp_path / "report.txt"))


def test_path_below_gaia_evidence_is_normalized():
    """A path under the ACTIVE evidence root (data_dir()/evidence) is accepted.

    Uses gaia.paths.evidence_dir() rather than constructing the root from
    HOME: the evidence root resolves through GAIA_DATA_DIR like every other
    Gaia directory (see gaia/evidence/fs.py::_evidence_root), and the
    autouse _isolate_gaia_data_dir fixture already points GAIA_DATA_DIR at a
    per-test tmp directory. Patching HOME here would have no effect and
    would silently stop testing what it claims to.
    """
    from gaia.paths import evidence_dir

    path = evidence_dir() / "me" / "brief" / "AC-1" / "report.txt"

    assert require_canonical_artifact_path(str(path)) == str(path)
