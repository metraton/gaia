"""Shared fixtures for evidence runner tests.

Fixtures:
  - sample_brief_path: tmp brief.md with 6 ACs mirroring the migrated
    context-evals shape (inline, so tests do not depend on the real brief).
  - evidence_dir: tmp directory for artifacts written during a run.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


SAMPLE_BRIEF_FRONTMATTER = textwrap.dedent(
    """\
    ---
    status: draft
    surface_type: cli
    acceptance_criteria:
      - id: AC-1
        description: "runner test green"
        evidence:
          type: command
          shape:
            run: "echo ok"
            expect: "exit 0"
        artifact: evidence/AC-1.txt
      - id: AC-2
        description: "grader test green"
        evidence:
          type: command
          shape:
            run: "true"
            expect: "exit 0"
        artifact: evidence/AC-2.txt
      - id: AC-3
        description: "catalog test green"
        evidence:
          type: command
          shape:
            run: "true"
            expect: "exit 0"
        artifact: evidence/AC-3.txt
      - id: AC-4
        description: "catalog has >= 7 cases"
        evidence:
          type: artifact
          shape:
            path: catalog.yaml
            kind: yaml
            assert: { op: length_gte, path: "cases", value: 7 }
        artifact: evidence/AC-4.txt
      - id: AC-5
        description: "full suite passes"
        evidence:
          type: command
          shape:
            run: "true"
            expect: "exit 0"
        artifact: evidence/AC-5.txt
      - id: AC-6
        description: "results dir has JSON with timestamp"
        evidence:
          type: artifact
          shape:
            path: results/
            kind: json
            select: latest
            assert: { op: has_field, path: "timestamp" }
        artifact: evidence/AC-6.txt
    ---

    # Sample Feature

    ## Objective
    Test fixture for evidence runner.
    """
)


@pytest.fixture
def sample_brief_path(tmp_path: Path) -> Path:
    """Write a sample brief.md with 6 ACs to a tmp path and return it."""
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE_BRIEF_FRONTMATTER, encoding="utf-8")
    return p


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    """Create and return a fresh tmp evidence/ directory."""
    d = tmp_path / "evidence"
    d.mkdir()
    return d
