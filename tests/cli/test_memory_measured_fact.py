"""A measured fact is stored with the date it was measured and the method used, and readers see both.

`gaia memory add --measured-at --method` writes two columns; `memory show` and
`memory get-relevant --initiative` render them next to the row, and a
description whose first word claims a measurement warns when either is missing.
The columns are nullable: a row written before them, or one that measures
nothing, shows no stamp and is never given a made-up one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GAIA = _REPO / "bin" / "gaia"

_BASE = ("--type", "feedback", "--initiative", "demo", "--workspace", "demo")


@pytest.fixture
def gaia(tmp_path):
    """Run the real CLI against a throwaway HOME, data dir and database."""
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    env = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DATA_DIR": str(data),
        "GAIA_DB": str(data / "gaia.db"),
    }
    env.pop("GAIA_DISPATCH_AGENT", None)

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_GAIA), *argv],
            env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    return run


def _add(gaia, name: str, description: str, *extra: str) -> subprocess.CompletedProcess:
    return gaia(
        "memory", "add", "--name", name, *_BASE,
        "--description", description, "--body", "p95 latency of the hook.",
        *extra, "--json",
    )


def _warning_codes(result: subprocess.CompletedProcess) -> set[str]:
    assert result.returncode == 0, result.stdout + result.stderr
    return {w["code"] for w in json.loads(result.stdout).get("warnings", [])}


def test_show_prints_when_and_how_a_stored_fact_was_measured(gaia):
    """The date and the method travel with the row to the reader who opens it."""
    result = _add(
        gaia, "feedback_latency", "Measured: hook p95 is 40 ms.",
        "--measured-at", "2026-10-08", "--method", "pytest -k latency, 50 runs",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    text = gaia("memory", "show", "feedback_latency", "--workspace", "demo")
    as_json = gaia("memory", "show", "feedback_latency", "--workspace", "demo", "--json")

    assert "measured_at: 2026-10-08" in text.stdout
    assert "method: pytest -k latency, 50 runs" in text.stdout
    payload = json.loads(as_json.stdout)
    assert payload["measured_at"] == "2026-10-08"
    assert payload["method"] == "pytest -k latency, 50 runs"


def test_a_row_that_measures_nothing_shows_no_stamp(gaia):
    """No fields given, no stamp rendered: nothing is invented for a row without a measurement."""
    assert _add(gaia, "feedback_plain", "Fact: nothing measured here.").returncode == 0

    text = gaia("memory", "show", "feedback_plain", "--workspace", "demo")

    assert "measured_at" not in text.stdout
    assert "method:" not in text.stdout


def test_get_relevant_for_an_initiative_carries_the_stamp(gaia):
    """The sweep of a project's live threads shows the stamp beside the description, in text and JSON."""
    result = _add(
        gaia, "feedback_latency_thread", "Measured: hook p95 is 40 ms.",
        "--class", "thread", "--status", "carry_forward",
        "--measured-at", "2026-10-08", "--method", "50 runs of the hook",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    text = gaia("memory", "get-relevant", "--initiative", "demo")
    as_json = gaia("memory", "get-relevant", "--initiative", "demo", "--json")

    assert "measured 2026-10-08" in text.stdout
    assert "50 runs of the hook" in text.stdout
    item = json.loads(as_json.stdout)["items"][0]
    assert item["measured_at"] == "2026-10-08"
    assert item["method"] == "50 runs of the hook"


@pytest.mark.parametrize("description", ["MEDIDO: p95 de 40 ms.", "Measured: p95 is 40 ms."])
def test_a_measurement_claim_without_its_stamp_warns_and_still_writes(gaia, description):
    """A description that starts as a measurement but names neither date nor method is flagged."""
    codes = _warning_codes(_add(gaia, "feedback_unstamped", description))

    assert "measurement_unstamped" in codes
    assert gaia("memory", "show", "feedback_unstamped", "--workspace", "demo").returncode == 0


def test_a_measurement_claim_with_only_one_half_of_the_stamp_still_warns(gaia):
    """Date without method, or method without date, is not a stamp."""
    only_date = _warning_codes(
        _add(gaia, "feedback_half_a", "Measured: 40 ms.", "--measured-at", "2026-10-08")
    )
    only_method = _warning_codes(
        _add(gaia, "feedback_half_b", "Measured: 40 ms.", "--method", "50 runs")
    )

    assert "measurement_unstamped" in only_date
    assert "measurement_unstamped" in only_method


def test_a_stamped_or_non_measuring_row_does_not_warn(gaia):
    stamped = _warning_codes(_add(
        gaia, "feedback_stamped", "Measured: 40 ms.",
        "--measured-at", "2026-10-08", "--method", "50 runs",
    ))
    other = _warning_codes(_add(gaia, "feedback_other", "Fact: the hook is fast."))

    assert "measurement_unstamped" not in stamped | other


def test_a_measured_at_that_is_not_a_date_is_refused(gaia):
    """The date is a stored value readers compare, so a phrase like 'yesterday' is rejected at write time."""
    result = _add(
        gaia, "feedback_bad_date", "Measured: 40 ms.",
        "--measured-at", "yesterday", "--method", "50 runs",
    )

    assert result.returncode != 0
    assert "--measured-at" in result.stdout + result.stderr
    assert "not an ISO date" in result.stdout + result.stderr
    assert gaia("memory", "show", "feedback_bad_date", "--workspace", "demo").returncode != 0
