"""`gaia now` reads the machine's clock without touching a database, and the orchestrator may run it."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GAIA = _REPO / "bin" / "gaia"
if str(_REPO / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO / "hooks"))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from tests.cli.test_help_lanes_match_guard import _READ_HEADER, _help_text, _lane  # noqa: E402

_SPECIALIST_PAYLOAD = {"agent_id": "a0123456789abcdef", "agent_type": "gaia-system"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    db = tmp_path / "data" / "absent.db"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(db))
    return db


def _gaia_now(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_GAIA), "now", *extra],
        capture_output=True, text=True, env=os.environ.copy(), timeout=60,
    )


def test_now_prints_local_time_offset_and_zone_and_creates_no_database(isolated, monkeypatch):
    monkeypatch.setenv("TZ", "America/Santiago")

    result = _gaia_now()

    assert result.returncode == 0, result.stderr
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{2}:\d{2} \(America/Santiago\)",
                     result.stdout), result.stdout
    assert not isolated.exists()
    assert not isolated.parent.exists()


@pytest.mark.parametrize("zone", ["America/Santiago", "UTC"])
def test_now_json_agrees_on_local_time_offset_zone_and_utc(isolated, monkeypatch, zone):
    monkeypatch.setenv("TZ", zone)

    result = _gaia_now("--json")

    assert result.returncode == 0, result.stderr
    reading = json.loads(result.stdout)
    local = datetime.fromisoformat(reading["local"])
    utc = datetime.fromisoformat(reading["utc"].replace("Z", "+00:00"))
    assert local == utc
    assert reading["local"].endswith(reading["offset"])
    assert reading["zone"] == zone
    assert not isolated.exists()


def test_now_falls_back_to_the_offset_when_no_zone_name_resolves(isolated, monkeypatch):
    monkeypatch.setenv("TZ", "<-03>3")

    result = _gaia_now("--json")

    assert result.returncode == 0, result.stderr
    reading = json.loads(result.stdout)
    assert reading["offset"] == "-03:00"
    assert reading["zone"] == "-03:00"


def test_help_lists_now_in_the_read_lane():
    assert ("now",) in _lane(_help_text(), _READ_HEADER)


@pytest.mark.parametrize("payload", [{}, _SPECIALIST_PAYLOAD], ids=["orchestrator", "specialist"])
@pytest.mark.parametrize("extra", ["", " --json"])
def test_the_guard_admits_now_for_the_orchestrator_and_a_specialist(payload, extra):
    assert guard.is_trusted_gaia_binary(str(_GAIA))

    allowed, reason = guard.check(f"{_GAIA} now{extra}", payload)

    assert allowed, reason
