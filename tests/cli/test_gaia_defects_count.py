"""``gaia defects --count`` answers "how many", not "how many of the first N".

``--limit`` caps the rows a listing prints, per origin. A count that inherits
it reports 20 for a corpus of tens of thousands and reads as a total.
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "bin")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

WS = "count-ws"
ANOMALIES = 25
EVENTS = 22


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    con = _connect(db_path())
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (WS,))
        con.execute(
            "INSERT INTO episodes (episode_id, workspace, timestamp, agent) "
            "VALUES ('ep_count', ?, '2026-07-15T09:00:00+00:00', 'developer')",
            (WS,),
        )
        for i in range(ANOMALIES):
            con.execute(
                "INSERT INTO episode_anomalies "
                "(episode_id, workspace, timestamp, type, severity, message) "
                "VALUES ('ep_count', ?, ?, 'skipped_verification', 'warning', 'm')",
                (WS, f"2026-07-20T09:{i:02d}:00+00:00"),
            )
        for i in range(EVENTS):
            con.execute(
                "INSERT INTO harness_events "
                "(workspace, ts, type, source, agent, result, severity) "
                "VALUES (?, ?, 'agent.cut', 'hook', 'developer', 'r', 'warning')",
                (WS, f"2026-07-21T09:{i:02d}:00+00:00"),
            )
        con.commit()
    finally:
        con.close()


def _run(capsys, **overrides) -> str:
    from cli.defects import cmd_defects

    args = dict(
        origin="all", workspace=WS, since=None, until=None, type=None,
        severity=None, agent=None, limit=20, count=False, json=False,
    )
    args.update(overrides)
    assert cmd_defects(argparse.Namespace(**args)) == 0
    return capsys.readouterr().out.strip()


def test_count_ignores_the_default_listing_limit(seeded, capsys):
    assert _run(capsys, count=True) == str(ANOMALIES + EVENTS)


def test_count_ignores_an_explicit_listing_limit(seeded, capsys):
    assert _run(capsys, count=True, limit=1) == str(ANOMALIES + EVENTS)


def test_count_json_carries_the_uncapped_total(seeded, capsys):
    out = json.loads(_run(capsys, count=True, json=True))
    assert out == {"count": ANOMALIES + EVENTS}


def test_count_still_honours_the_filters(seeded, capsys):
    assert _run(capsys, count=True, origin="subagent") == str(ANOMALIES)
    assert _run(capsys, count=True, workspace="all", type="agent.cut") == str(EVENTS)


def test_listing_is_still_capped_per_origin(seeded, capsys):
    rows = json.loads(_run(capsys, json=True))
    assert len(rows) == 2 * 20
