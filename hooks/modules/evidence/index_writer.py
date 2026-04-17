"""
INDEX.md writer for evidence runs.

Contract:
    write_index(evidence_dir: Path, results: list[tuple[str, EvidenceResult]]) -> Path

Produces `evidence_dir/INDEX.md` with:
    - One table row per AC: id, type, passed, artifact, error.
    - Deterministic output (same input -> same bytes).
    - Re-execution replaces the file; it does not append.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

from .runner import EvidenceResult


_HEADER = "# Evidence Index\n\n"
_TABLE_HEADER = (
    "| AC | Status | Error |\n"
    "|----|--------|-------|\n"
)


def _format_status(passed: bool) -> str:
    return "pass" if passed else "fail"


def _format_row(ac_id: str, result: EvidenceResult) -> str:
    status = _format_status(result.passed)
    error = result.error or ""
    # Escape pipes in error messages so the table stays well-formed.
    error = error.replace("|", "\\|")
    return f"| {ac_id} | {status} | {error} |\n"


def write_index(
    evidence_dir: Path,
    results: Iterable[Tuple[str, EvidenceResult]],
) -> Path:
    """Render INDEX.md under `evidence_dir` from `results`.

    Idempotent: identical `results` produce identical bytes.
    Replaces any existing INDEX.md (does not append).
    """
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    rows = [_format_row(ac_id, result) for ac_id, result in results]

    content = _HEADER + _TABLE_HEADER + "".join(rows)

    index_path = evidence_dir / "INDEX.md"
    index_path.write_text(content, encoding="utf-8")
    return index_path
