"""Tests for ``tests.evals.reporter.save_result`` (T3a).

Coverage per T3a:

* ``results/`` directory is created on demand
* the JSON payload round-trips in the T3a/T7 schema
  ``{run_id, catalog, cases: [{id, agent, passed, score, reasons,
  response_snippet}]}``
* the filename derives from ``run_id`` (timestamp-embedded by the caller)
* ``save_result`` returns a :class:`pathlib.Path` pointing at the written file

All tests use ``tmp_path`` -- no writes to the real
``tests/evals/results/`` directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from tests.evals.reporter import save_result


# --- Helpers ---------------------------------------------------------------


@dataclass(frozen=True)
class _CaseEntry:
    """Minimal case-result dataclass mirroring the T3a schema."""

    id: str
    agent: str
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    response_snippet: str = ""


def _sample_payload(run_id: str = "20260420T1200Z-smoke") -> dict:
    """Return a payload shaped like the T3a/T7 schema."""
    return {
        "run_id": run_id,
        "catalog": "context_consumption.yaml",
        "cases": [
            {
                "id": "S1",
                "agent": "developer",
                "passed": True,
                "score": 1.0,
                "reasons": ["expect_present all found (1 keyword(s))"],
                "response_snippet": "push to metraton remote",
            },
            {
                "id": "S2",
                "agent": "cloud-troubleshooter",
                "passed": False,
                "score": 0.5,
                "reasons": ["expect_absent leaked: ['100.64.']"],
                "response_snippet": "scp jorge@100.64.0.1:/src ...",
            },
        ],
    }


# --- Tests -----------------------------------------------------------------


class TestSaveResultDirectoryCreation:
    """``save_result`` creates the target directory when it is absent."""

    def test_creates_missing_results_dir(self, tmp_path: Path):
        results_dir = tmp_path / "not-yet-there"
        assert not results_dir.exists()

        save_result("run-1", _sample_payload(), results_dir=results_dir)

        assert results_dir.is_dir()

    def test_creates_nested_missing_parents(self, tmp_path: Path):
        results_dir = tmp_path / "deep" / "nested" / "results"
        assert not results_dir.exists()

        save_result("run-2", _sample_payload(), results_dir=results_dir)

        assert results_dir.is_dir()

    def test_reuses_existing_dir_without_error(self, tmp_path: Path):
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        # Dropping a sibling file first proves we do not wipe the directory.
        sibling = results_dir / "keep-me.txt"
        sibling.write_text("preserved")

        save_result("run-3", _sample_payload(), results_dir=results_dir)

        assert sibling.read_text() == "preserved"


class TestSaveResultReturnsPath:
    """Return value is a ``Path`` pointing at the written file."""

    def test_returns_path_instance(self, tmp_path: Path):
        path = save_result("run-a", _sample_payload(), results_dir=tmp_path)
        assert isinstance(path, Path)

    def test_returned_path_exists_and_is_file(self, tmp_path: Path):
        path = save_result("run-b", _sample_payload(), results_dir=tmp_path)
        assert path.is_file()

    def test_returned_path_is_inside_results_dir(self, tmp_path: Path):
        path = save_result("run-c", _sample_payload(), results_dir=tmp_path)
        assert path.parent == tmp_path


class TestSaveResultFilename:
    """Filename derives from ``run_id``; callers embed the timestamp."""

    def test_filename_stem_matches_run_id(self, tmp_path: Path):
        run_id = "20260420T1200Z-smoke"
        path = save_result(run_id, _sample_payload(run_id), results_dir=tmp_path)
        assert path.stem == run_id

    def test_filename_suffix_is_json(self, tmp_path: Path):
        path = save_result(
            "20260420T1200Z-smoke", _sample_payload(), results_dir=tmp_path
        )
        assert path.suffix == ".json"

    def test_filename_contains_timestamp_from_caller(self, tmp_path: Path):
        # Per save_result's contract the caller embeds the timestamp. We
        # assert the timestamp string survives round-trip into the filename.
        run_id = "20260420T1200Z-nightly"
        path = save_result(run_id, _sample_payload(run_id), results_dir=tmp_path)
        assert "20260420T1200Z" in path.name


class TestSaveResultPayloadShape:
    """The written JSON round-trips in the T3a/T7 schema."""

    def test_payload_round_trips_as_json(self, tmp_path: Path):
        payload = _sample_payload()
        path = save_result("run-x", payload, results_dir=tmp_path)

        decoded = json.loads(path.read_text())
        assert decoded == payload

    def test_schema_has_required_top_level_keys(self, tmp_path: Path):
        path = save_result("run-y", _sample_payload(), results_dir=tmp_path)
        decoded = json.loads(path.read_text())

        for key in ("run_id", "catalog", "cases"):
            assert key in decoded, f"missing top-level key: {key}"

    def test_each_case_has_required_keys(self, tmp_path: Path):
        path = save_result("run-z", _sample_payload(), results_dir=tmp_path)
        decoded = json.loads(path.read_text())

        required_case_keys = {
            "id",
            "agent",
            "passed",
            "score",
            "reasons",
            "response_snippet",
        }
        for case in decoded["cases"]:
            missing = required_case_keys - set(case.keys())
            assert not missing, f"case missing keys: {missing}"

    def test_dataclasses_are_serialized(self, tmp_path: Path):
        # reporter supports dataclasses and lists of dataclasses.
        payload = {
            "run_id": "dc-run",
            "catalog": "context_consumption.yaml",
            "cases": [
                _CaseEntry(
                    id="S1",
                    agent="developer",
                    passed=True,
                    score=1.0,
                    reasons=["ok"],
                    response_snippet="snippet",
                )
            ],
        }
        path = save_result("dc-run", payload, results_dir=tmp_path)
        decoded = json.loads(path.read_text())

        assert decoded["cases"][0]["id"] == "S1"
        assert decoded["cases"][0]["passed"] is True
        assert decoded["cases"][0]["score"] == 1.0
        assert decoded["cases"][0]["reasons"] == ["ok"]
        assert decoded["cases"][0]["response_snippet"] == "snippet"

    def test_paths_are_stringified(self, tmp_path: Path):
        # reporter._to_jsonable() converts Path objects to strings.
        session = tmp_path / "session.jsonl"
        session.write_text("{}\n")
        payload = {
            "run_id": "path-run",
            "catalog": "context_consumption.yaml",
            "session_path": session,
            "cases": [],
        }
        path = save_result("path-run", payload, results_dir=tmp_path)
        decoded = json.loads(path.read_text())

        assert decoded["session_path"] == str(session)

    def test_overwrites_existing_file_for_same_run_id(self, tmp_path: Path):
        run_id = "overwrite-run"
        first = save_result(
            run_id,
            {"run_id": run_id, "catalog": "v1", "cases": []},
            results_dir=tmp_path,
        )
        second = save_result(
            run_id,
            {"run_id": run_id, "catalog": "v2", "cases": []},
            results_dir=tmp_path,
        )

        assert first == second
        decoded = json.loads(second.read_text())
        assert decoded["catalog"] == "v2"
