"""Tests for brief frontmatter loader.

Contract:
    load_acs(brief_path: Path) -> list[Evidence]

    Evidence dataclass fields:
      id: str
      description: str
      type: str          # command | url | playwright | artifact | metric
      shape: dict        # type-specific
      artifact: str      # relative artifact path

Exceptions:
    InvalidBriefFrontmatter -- YAML parse error or missing '---' markers
    UnknownEvidenceType    -- evidence.type outside the allowed set
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hooks.modules.evidence.loader import (
    Evidence,
    InvalidBriefFrontmatter,
    UnknownEvidenceType,
    load_acs,
)


def test_load_six_acs_from_sample_brief(sample_brief_path: Path) -> None:
    """Sample brief has 6 ACs; loader returns 6 typed Evidence objects."""
    acs = load_acs(sample_brief_path)
    assert len(acs) == 6
    assert all(isinstance(ac, Evidence) for ac in acs)
    ids = [ac.id for ac in acs]
    assert ids == ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6"]


def test_loaded_ac_fields_present(sample_brief_path: Path) -> None:
    """Each AC carries id, description, type, shape, artifact."""
    acs = load_acs(sample_brief_path)
    ac1 = acs[0]
    assert ac1.id == "AC-1"
    assert ac1.description == "runner test green"
    assert ac1.type == "command"
    assert ac1.shape == {"run": "echo ok", "expect": "exit 0"}
    assert ac1.artifact == "evidence/AC-1.txt"


def test_loaded_artifact_ac_preserves_nested_shape(sample_brief_path: Path) -> None:
    """Artifact-type AC retains its nested assert spec."""
    acs = load_acs(sample_brief_path)
    ac4 = acs[3]
    assert ac4.type == "artifact"
    assert ac4.shape["path"] == "catalog.yaml"
    assert ac4.shape["kind"] == "yaml"
    assert ac4.shape["assert"] == {
        "op": "length_gte", "path": "cases", "value": 7,
    }


def test_loaded_artifact_with_select_latest(sample_brief_path: Path) -> None:
    """Artifact shape supports `select: latest` for directory-as-path."""
    acs = load_acs(sample_brief_path)
    ac6 = acs[5]
    assert ac6.shape.get("select") == "latest"
    assert ac6.shape["path"] == "results/"


def test_missing_acceptance_criteria_returns_empty(tmp_path: Path) -> None:
    """A brief without acceptance_criteria in frontmatter -> empty list, no crash."""
    p = tmp_path / "brief.md"
    p.write_text("---\nstatus: draft\n---\n\n# Empty\n", encoding="utf-8")
    assert load_acs(p) == []


def test_malformed_yaml_raises_invalid_frontmatter(tmp_path: Path) -> None:
    """Syntactically invalid YAML in frontmatter -> InvalidBriefFrontmatter."""
    p = tmp_path / "brief.md"
    p.write_text("---\nstatus: : broken\n---\n", encoding="utf-8")
    with pytest.raises(InvalidBriefFrontmatter):
        load_acs(p)


def test_missing_frontmatter_markers_raises(tmp_path: Path) -> None:
    """No '---' markers at all -> InvalidBriefFrontmatter."""
    p = tmp_path / "brief.md"
    p.write_text("# Plain markdown, no frontmatter\n", encoding="utf-8")
    with pytest.raises(InvalidBriefFrontmatter):
        load_acs(p)


def test_unknown_evidence_type_raises(tmp_path: Path) -> None:
    """evidence.type outside {command, url, playwright, artifact, metric} -> UnknownEvidenceType."""
    p = tmp_path / "brief.md"
    p.write_text(
        "---\n"
        "status: draft\n"
        "acceptance_criteria:\n"
        "  - id: AC-X\n"
        "    description: bogus\n"
        "    evidence:\n"
        "      type: telepathy\n"
        "      shape: {}\n"
        "    artifact: evidence/AC-X.txt\n"
        "---\n",
        encoding="utf-8",
    )
    with pytest.raises(UnknownEvidenceType):
        load_acs(p)
