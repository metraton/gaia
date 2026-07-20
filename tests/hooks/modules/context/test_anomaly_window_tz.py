"""
rc.5 FIX 2: the anomaly "last hour" window must be UTC-against-UTC.

Stored anomaly timestamps are UTC (gaia/store/writer.py::_now_iso). The window
cutoff was computed with datetime.now() (local naive), so on a machine whose TZ
has a non-zero UTC offset the window was skewed by that offset -- inflating it
(over-reporting) for negative offsets and shrinking it for positive offsets.
The fix computes the cutoff with datetime.now(timezone.utc) in BOTH readers:

  - hooks/modules/context/context_injector.py::check_recent_critical_anomalies
  - hooks/modules/context/compact_context_builder.py::_build_anomalies_block

These tests seed known UTC anomalies at -30min (inside the 1h window) and
-90min (outside) and assert that EXACTLY the inside one is counted, regardless
of the process TZ. Under the old naive-now code these same assertions fail
(America/New_York over-reports 2; Asia/Kolkata under-reports 0).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# gaia repo root (…/gaia) so `gaia.store.writer` / `gaia.paths` import cleanly.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# hooks dir so `modules.context…` import cleanly.
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.context.context_injector import check_recent_critical_anomalies
from modules.context.compact_context_builder import _build_anomalies_block

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"  # mirrors writer._now_iso()
_WORKSPACE = "testws"

# TZs spanning zero, a large negative offset, and a large positive offset.
# The fix must make the window identical across all three.
_TZS = ["UTC", "America/New_York", "Asia/Kolkata"]


def _materialize_and_seed() -> None:
    """Create the schema in the isolated DB and seed two critical anomalies."""
    from gaia.store import writer
    from gaia.paths import db_path

    # writer._connect() materializes the full schema at the GAIA_DATA_DIR DB.
    con = writer._connect()
    con.close()

    now = datetime.now(timezone.utc)
    inside = (now - timedelta(minutes=30)).strftime(_TS_FMT)   # within 1h window
    outside = (now - timedelta(minutes=90)).strftime(_TS_FMT)  # outside 1h window

    # Insert on a plain connection (foreign_keys OFF by default) so no parent
    # `episodes` row is needed -- the readers only SELECT, unaffected by FK.
    raw = sqlite3.connect(str(db_path()))
    raw.executemany(
        "INSERT INTO episode_anomalies (episode_id, workspace, timestamp, type, severity) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("ep_inside", _WORKSPACE, inside, "contract_gate_violation", "critical"),
            ("ep_outside", _WORKSPACE, outside, "contract_gate_violation", "critical"),
        ],
    )
    raw.commit()
    raw.close()


@pytest.fixture()
def _seeded(monkeypatch):
    """Pin the workspace and seed the isolated DB (GAIA_DATA_DIR set by conftest)."""
    import gaia.project

    monkeypatch.setattr(gaia.project, "current", lambda *a, **k: _WORKSPACE)
    _materialize_and_seed()
    yield


@pytest.mark.parametrize("tz", _TZS)
def test_critical_anomaly_window_counts_real_hour(_seeded, monkeypatch, tz):
    """check_recent_critical_anomalies() counts exactly the -30min row (inside),
    never the -90min row (outside), regardless of the process TZ."""
    monkeypatch.setenv("TZ", tz)
    time.tzset()

    out = check_recent_critical_anomalies()

    assert "# Recent Critical Anomalies" in out
    # Exactly ONE anomaly in the window -- not 0 (positive-offset shrink) and
    # not 2 (negative-offset inflation) that the old naive cutoff produced.
    assert "1 critical anomaly(ies) in the last hour" in out


@pytest.mark.parametrize("tz", _TZS)
def test_compact_anomalies_block_counts_real_hour(_seeded, monkeypatch, tz):
    """_build_anomalies_block(1) counts exactly the -30min row, TZ-independent."""
    monkeypatch.setenv("TZ", tz)
    time.tzset()

    block = _build_anomalies_block(window_hours=1)

    assert block is not None
    assert "## Active Anomalies" in block
    assert "1 critical:" in block


def test_reset_tz():
    """Restore a sane default TZ after the parametrized cases mutate it."""
    import os
    os.environ["TZ"] = "UTC"
    time.tzset()
