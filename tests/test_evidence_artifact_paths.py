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


def test_path_below_gaia_evidence_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".gaia" / "evidence" / "me" / "brief" / "AC-1" / "report.txt"

    assert require_canonical_artifact_path(str(path)) == str(path)
