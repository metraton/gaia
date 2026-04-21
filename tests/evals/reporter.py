"""Results reporter for the eval framework (T1 + T6).

T1 -- :func:`save_result` writes eval run results to
``tests/evals/results/{run_id}.json`` (directory created on demand).

T6 -- baseline snapshot + drift reporter for semantic scenarios:

* :func:`load_baseline` reads ``tests/evals/results/baseline.json`` (the
  last-known-good snapshot); returns an empty skeleton if absent so a
  first run is treated as "all new".
* :func:`compare_to_baseline` diffs a new run against the baseline and
  flags semantic drift (``abs(new_score - baseline_score) > 0.10``) and
  binary regressions (exact-match compare).
* :func:`write_baseline_candidate` persists a promotion candidate to
  ``baseline.candidate.json``; promotion is a manual ``mv`` (see README).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


# Results are written relative to this file: tests/evals/results/
_RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Drift threshold for semantic scenarios: absolute score delta > 10% triggers
# a drift alarm (per plan T6 / brief AC-6).
_DRIFT_THRESHOLD = 0.10


def save_result(run_id: str, results: Any, results_dir: Path | None = None) -> Path:
    """Persist a run's results as JSON.

    Args:
        run_id: Stable identifier for the run; becomes the filename stem.
            Callers typically include a timestamp (e.g.
            ``"20260420T1200Z-smoke"``).
        results: The payload to persist. Accepts dataclasses, lists of
            dataclasses, dicts, and any JSON-serializable structure. The
            final on-disk schema is defined in T3a / T7.
        results_dir: Override the default results directory. Used in
            tests with ``tmp_path``. Defaults to ``tests/evals/results``.

    Returns:
        Absolute :class:`Path` of the written JSON file.
    """
    target_dir = Path(results_dir) if results_dir is not None else _RESULTS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    payload = _to_jsonable(results)

    target = target_dir / f"{run_id}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def _to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses / Paths into JSON-native values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


# --- T6: baseline snapshot + drift reporter ----------------------------------


@dataclass(frozen=True)
class DriftEntry:
    """Per-case drift record produced by :func:`compare_to_baseline`.

    Attributes:
        id: Case id (e.g. ``"S1"``).
        scoring: ``"semantic"`` or ``"binary"``.
        baseline_score: Score recorded in the baseline; ``None`` when the
            case was not in the baseline (treated as a "new" case).
        new_score: Score produced by the current run.
        delta: ``abs(new_score - baseline_score)`` for semantic cases,
            ``0.0`` when ``baseline_score`` is ``None``.
        drift: ``True`` when the case exceeds the drift policy
            (semantic: ``delta > 0.10``; binary: scores differ).
        reason: Short human-readable justification.
    """

    id: str
    scoring: str
    baseline_score: float | None
    new_score: float
    delta: float
    drift: bool
    reason: str


@dataclass(frozen=True)
class DriftReport:
    """Aggregate drift result for a full run.

    Attributes:
        has_drift: ``True`` when any entry has ``drift == True``.
        threshold: The semantic drift threshold used (``0.10`` by default).
        baseline_path: Path to the baseline file consulted.
        missing_baseline: ``True`` when the baseline file was absent; the
            report then treats every case as "new" and does not flag drift.
        entries: One :class:`DriftEntry` per case in the new run.
    """

    has_drift: bool
    threshold: float
    baseline_path: str
    missing_baseline: bool
    entries: list[DriftEntry] = field(default_factory=list)


def _empty_baseline() -> dict:
    """Return the canonical empty baseline skeleton."""
    return {"version": 1, "cases": {}}


def load_baseline(baseline_path: Path | str | None = None) -> dict:
    """Load the baseline snapshot.

    When the file is absent, returns :func:`_empty_baseline` so a first
    run is treated as "all new" (no drift flagged).

    Args:
        baseline_path: Override the default baseline location. Defaults to
            ``tests/evals/results/baseline.json``.

    Returns:
        Parsed baseline dict with at minimum ``{"version", "cases"}`` keys.
    """
    path = _resolve_baseline_path(baseline_path)
    if not path.exists():
        return _empty_baseline()
    data = json.loads(path.read_text())
    # Defensive: keep the schema shape stable even if a hand-edited file
    # dropped one of the top-level keys.
    if "cases" not in data:
        data["cases"] = {}
    if "version" not in data:
        data["version"] = 1
    return data


def compare_to_baseline(
    new_results: Any,
    baseline_path: Path | str | None = None,
    threshold: float = _DRIFT_THRESHOLD,
) -> DriftReport:
    """Diff a new run's scores against the baseline.

    Drift policy:

    * **Semantic** cases (``scoring == "semantic"``): drift when
      ``abs(new_score - baseline_score) > threshold``.
    * **Binary** cases (``scoring == "binary"``): drift when
      ``new_score != baseline_score`` (exact-match compare).
    * **Missing baseline** (file absent or case absent): treated as
      "new case" -- recorded in the report but not flagged as drift.

    Args:
        new_results: Either the full run payload
            (``{"cases": [...]}``) or the case list itself. Each case
            must expose ``id``, ``score``; ``scoring`` is read from the
            case entry when present, else inferred from the baseline.
        baseline_path: Override the default baseline location.
        threshold: Absolute score delta at which semantic drift fires.
            Defaults to ``0.10`` (plan T6).

    Returns:
        A :class:`DriftReport` summarising every case in the new run.
    """
    resolved_path = _resolve_baseline_path(baseline_path)
    baseline = load_baseline(resolved_path)
    missing_baseline = not resolved_path.exists()
    baseline_cases: dict[str, dict] = baseline.get("cases", {})

    cases = _extract_cases(new_results)

    entries: list[DriftEntry] = []
    any_drift = False
    for case in cases:
        case_id = case["id"]
        new_score = float(case.get("score", 0.0))
        scoring = case.get("scoring") or baseline_cases.get(case_id, {}).get(
            "scoring", "semantic"
        )

        base_entry = baseline_cases.get(case_id)
        if base_entry is None or "score" not in base_entry:
            # Unknown in baseline: record but do not flag as drift.
            entries.append(
                DriftEntry(
                    id=case_id,
                    scoring=scoring,
                    baseline_score=None,
                    new_score=new_score,
                    delta=0.0,
                    drift=False,
                    reason="new case (no baseline entry)",
                )
            )
            continue

        base_score = float(base_entry["score"])
        delta = abs(new_score - base_score)
        if scoring == "binary":
            is_drift = new_score != base_score
            reason = (
                f"binary match (both={new_score})"
                if not is_drift
                else f"binary regression baseline={base_score} new={new_score}"
            )
        else:
            is_drift = delta > threshold
            reason = (
                f"within threshold (delta={delta:.3f} <= {threshold})"
                if not is_drift
                else f"semantic drift (delta={delta:.3f} > {threshold})"
            )
        if is_drift:
            any_drift = True
        entries.append(
            DriftEntry(
                id=case_id,
                scoring=scoring,
                baseline_score=base_score,
                new_score=new_score,
                delta=delta,
                drift=is_drift,
                reason=reason,
            )
        )

    return DriftReport(
        has_drift=any_drift,
        threshold=threshold,
        baseline_path=str(resolved_path),
        missing_baseline=missing_baseline,
        entries=entries,
    )


def write_baseline_candidate(
    new_results: Any,
    path: Path | str | None = None,
) -> Path:
    """Write a candidate baseline snapshot for manual promotion.

    The reporter never overwrites the live baseline. Operators promote a
    candidate via ``mv baseline.candidate.json baseline.json`` after
    reviewing the run (see ``tests/evals/README.md`` -- landed in T8).

    Args:
        new_results: Full run payload or case list. Each case must expose
            ``id`` and ``score``; ``scoring`` is recorded when present.
        path: Override the candidate location. Defaults to
            ``tests/evals/results/baseline.candidate.json``.

    Returns:
        Absolute :class:`Path` of the written candidate file.
    """
    target = _resolve_candidate_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    cases = _extract_cases(new_results)
    payload = {
        "version": 1,
        "cases": {
            case["id"]: {
                "score": float(case.get("score", 0.0)),
                "scoring": case.get("scoring", "semantic"),
            }
            for case in cases
        },
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


def _extract_cases(new_results: Any) -> list[dict]:
    """Normalize ``new_results`` into a list of case dicts."""
    payload = _to_jsonable(new_results)
    if isinstance(payload, dict):
        cases = payload.get("cases", [])
    elif isinstance(payload, list):
        cases = payload
    else:
        cases = []
    # Defensive: only keep entries that carry an ``id``.
    return [c for c in cases if isinstance(c, dict) and "id" in c]


def _resolve_baseline_path(baseline_path: Path | str | None) -> Path:
    if baseline_path is None:
        return _RESULTS_DIR / "baseline.json"
    return Path(baseline_path)


def _resolve_candidate_path(candidate_path: Path | str | None) -> Path:
    if candidate_path is None:
        return _RESULTS_DIR / "baseline.candidate.json"
    return Path(candidate_path)
