"""Tests for INDEX.md writer.

Contract:
    write_index(evidence_dir: Path, results: list[tuple[str, EvidenceResult]]) -> Path

    - Writes `evidence_dir/INDEX.md` with a table: AC id, type, passed, artifact.
    - Idempotent: same input -> same output bytes.
    - Re-execution semantics: a second call REPLACES the file; it does not append.
      INDEX.md reflects the CURRENT state of evidence/, not history.
"""
from __future__ import annotations

from pathlib import Path

from hooks.modules.evidence.index_writer import write_index
from hooks.modules.evidence.runner import EvidenceResult


def _mk(passed: bool, artifact: Path, error: str | None = None) -> EvidenceResult:
    return EvidenceResult(
        passed=passed,
        output="",
        artifact_path=artifact,
        error=error,
    )


def test_writes_index_with_three_results(evidence_dir: Path) -> None:
    r1 = _mk(True, evidence_dir / "AC-1.txt")
    r2 = _mk(True, evidence_dir / "AC-2.txt")
    r3 = _mk(False, evidence_dir / "AC-3.txt", error="assert failed")

    idx = write_index(
        evidence_dir,
        [("AC-1", r1), ("AC-2", r2), ("AC-3", r3)],
    )

    assert idx.name == "INDEX.md"
    text = idx.read_text()
    assert "AC-1" in text
    assert "AC-2" in text
    assert "AC-3" in text
    # Pass/fail signals present in human-readable form.
    assert "pass" in text.lower()
    assert "fail" in text.lower()


def test_index_is_idempotent(evidence_dir: Path) -> None:
    """Same results -> same INDEX.md bytes across runs."""
    r1 = _mk(True, evidence_dir / "AC-1.txt")
    results = [("AC-1", r1)]

    first = write_index(evidence_dir, results).read_text()
    second = write_index(evidence_dir, results).read_text()
    assert first == second


def test_reexecution_replaces_not_appends(evidence_dir: Path) -> None:
    """After a second write with different results, INDEX reflects only the latest set.

    Mirrors the "filesystem is the source; INDEX is the summary" contract:
    rerunning AC-N overwrites AC-N's artifact and triggers a fresh INDEX.
    """
    r_fail = _mk(False, evidence_dir / "AC-1.txt", error="first run failed")
    write_index(evidence_dir, [("AC-1", r_fail)])

    r_pass = _mk(True, evidence_dir / "AC-1.txt")
    idx = write_index(evidence_dir, [("AC-1", r_pass)])

    text = idx.read_text()
    # The latest result is pass; no lingering "fail" / "first run failed" state.
    assert "first run failed" not in text
    # A single AC-1 row (no duplication from the first call).
    assert text.count("AC-1") == 1
