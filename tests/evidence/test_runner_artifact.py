"""Tests for the artifact-type evidence runner.

Contract:
    run_artifact(shape: dict, artifact_path: Path) -> EvidenceResult

    shape keys:
      path: str              # file or directory (relative to brief dir or absolute)
      kind: "json" | "yaml"  # how to parse the target
      select: "latest" | None  # if path is a directory, pick newest matching file
      assert: dict           # DSL spec -- see test_assertions.py
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import yaml

from hooks.modules.evidence.runner import run_artifact


def test_yaml_length_gte_passes_with_seven_cases(tmp_path: Path) -> None:
    """cases list has 7 entries, assert length_gte 7 -> pass."""
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({"cases": list(range(7))}), encoding="utf-8")
    art = tmp_path / "AC.txt"

    result = run_artifact(
        {
            "path": str(catalog),
            "kind": "yaml",
            "assert": {"op": "length_gte", "path": "cases", "value": 7},
        },
        art,
    )
    assert result.passed is True
    assert art.exists()


def test_yaml_length_gte_fails_with_five_cases(tmp_path: Path) -> None:
    """cases list has 5 entries, assert length_gte 7 -> fail."""
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump({"cases": list(range(5))}), encoding="utf-8")
    art = tmp_path / "AC.txt"

    result = run_artifact(
        {
            "path": str(catalog),
            "kind": "yaml",
            "assert": {"op": "length_gte", "path": "cases", "value": 7},
        },
        art,
    )
    assert result.passed is False


def test_json_has_field_passes(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"timestamp": "2026-04-16", "n": 3}),
                      encoding="utf-8")
    art = tmp_path / "AC.txt"

    result = run_artifact(
        {
            "path": str(report),
            "kind": "json",
            "assert": {"op": "has_field", "path": "timestamp"},
        },
        art,
    )
    assert result.passed is True


def test_malformed_yaml_marks_failed(tmp_path: Path) -> None:
    """YAML parse error -> passed=False, error mentions yaml."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("cases: : : invalid", encoding="utf-8")
    art = tmp_path / "AC.txt"

    result = run_artifact(
        {
            "path": str(broken),
            "kind": "yaml",
            "assert": {"op": "length_gte", "path": "cases", "value": 1},
        },
        art,
    )
    assert result.passed is False
    assert result.error is not None
    assert "yaml" in result.error.lower()


def test_select_latest_picks_newest_in_directory(tmp_path: Path) -> None:
    """3 JSON files with different mtimes; select: latest -> newest wins."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    old = results_dir / "old.json"
    old.write_text(json.dumps({}), encoding="utf-8")  # no timestamp -> would fail
    # Make `old` visibly older.
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))

    mid = results_dir / "mid.json"
    mid.write_text(json.dumps({}), encoding="utf-8")
    one_hour_ago = time.time() - 3600
    os.utime(mid, (one_hour_ago, one_hour_ago))

    newest = results_dir / "newest.json"
    newest.write_text(json.dumps({"timestamp": "2026-04-16"}), encoding="utf-8")
    # newest inherits current mtime by default.

    art = tmp_path / "AC.txt"
    result = run_artifact(
        {
            "path": str(results_dir),
            "kind": "json",
            "select": "latest",
            "assert": {"op": "has_field", "path": "timestamp"},
        },
        art,
    )
    assert result.passed is True, (
        "Expected select:latest to asssert on newest.json (has timestamp), "
        "not old.json / mid.json (missing timestamp)."
    )
