"""`gaia cleanup --prune --dry-run` on a fixture tree: tmp folders, unowned scratch and paused owners."""

import hashlib
import importlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DAY = 86400.0


def _cid(agent: str, tag: str) -> str:
    return f"{agent}.{hashlib.sha256(tag.encode()).hexdigest()[:8]}"


@pytest.fixture()
def cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    import gaia.retention.fs_rules as fs_mod
    import gaia.retention.liveness as liveness_mod

    importlib.reload(liveness_mod)
    importlib.reload(fs_mod)
    from hooks.modules.session import session_registry

    monkeypatch.setattr(session_registry, "_get_registry_path", lambda: tmp_path / "registry.json")
    bin_dir = str(_REPO_ROOT / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    import cli.cleanup as cleanup_mod

    monkeypatch.setattr(cleanup_mod, "_prune_worktrees", lambda *a, **k: [])
    return cleanup_mod


def _entry(path: Path, age_days: float) -> Path:
    path.mkdir(parents=True)
    (path / "payload.bin").write_bytes(b"x")
    when = time.time() - age_days * DAY
    for p in (path / "payload.bin", path):
        os.utime(p, (when, when))
    return path


def _seed(tmp_path, rows):
    """``rows``: ``(contract_id, harness_agent_id, session_id, agent_state)``."""
    con = sqlite3.connect(str(tmp_path / "gaia.db"))
    con.execute(
        "create table agent_contract_handoffs (id integer primary key, contract_id text, "
        "agent_id text, harness_agent_id text, session_id text, agent_state text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs (contract_id, agent_id, harness_agent_id, session_id, agent_state) "
        "values (?, ?, ?, ?, ?)",
        [(cid, cid.split(".")[0], harness, session, state) for cid, harness, session, state in rows],
    )
    con.commit()
    con.close()


def test_dry_run_selects_unattributed_entries_and_keeps_running_live_and_held(cleanup, tmp_path):
    from gaia.paths import dispatch_tmp_dir, scratch_dir, tmp_dir

    done, running, blocked, alive, open_, held = (valid_agent_id(t) for t in ("rt-done", "rt-run", "rt-blk", "rt-alive", "rt-open", "rt-held"))
    done_id, running_id, blocked_id, alive_id = (_cid(a, a) for a in (done, running, blocked, alive))
    in_progress_id = _cid(open_, "ip")
    _seed(tmp_path, [
        (done_id, done, "s-done", "COMPLETE"),
        (running_id, running, "s-run", "DISPATCHED"),
        (in_progress_id, open_, "s-ip", "IN_PROGRESS"),
        (blocked_id, blocked, "s-gone", "BLOCKED"),
        (alive_id, alive, "s-alive", "APPROVAL_REQUEST"),
    ])
    (tmp_path / "registry.json").write_text(json.dumps(
        {"sessions": {"s-alive": {"started_at": "x", "is_headless": False, "last_heartbeat": time.time()}}}
    ))
    scratch, tmp = scratch_dir(), tmp_dir()
    expected = {
        _entry(tmp / dispatch_tmp_dir(done).name, 2),
        _entry(tmp / "0123456789ab", 10),
        _entry(scratch / "backups", 10),
        _entry(scratch / "validator-replay-20261101T000000", 10),
        _entry(scratch / blocked_id, 10),
    }
    kept = {
        _entry(tmp / dispatch_tmp_dir(running).name, 10),
        _entry(scratch / running_id, 10),
        _entry(scratch / in_progress_id, 10),
        _entry(scratch / alive_id, 10),
        _entry(scratch / "fresh-probe", 1),
        _entry(scratch / f"{held}.notes", 10),
        _entry(tmp_path / "backups", 30),
    }
    before = sorted(p.name for root in (scratch, tmp) for p in root.iterdir())

    with open(scratch / f"{held}.notes" / "payload.bin", "rb"):
        actions = cleanup._apply_retention_policy(tmp_path, dry_run=True)
        selected = {Path(a["path"]) for a in actions if a["label"].startswith(("Scratch", "Tmp"))}
        assert selected == expected
        assert sorted(p.name for root in (scratch, tmp) for p in root.iterdir()) == before
        assert all(p.exists() for p in expected | kept)

        cleanup._apply_retention_policy(tmp_path, dry_run=False)
    assert not any(p.exists() for p in expected)
    assert all(p.exists() for p in kept)


def test_backups_of_a_scratch_copy_stay_with_its_contract(cleanup, tmp_path):
    from gaia.paths import scratch_dir
    from gaia.paths.resolver import db_backup_dir

    copy = scratch_dir() / f"{valid_agent_id('rt-copy')}.abcdef12.db"
    assert db_backup_dir(copy) == scratch_dir() / f"{copy.stem}.backups"
    assert db_backup_dir(tmp_path / "gaia.db") == tmp_path / "backups"
