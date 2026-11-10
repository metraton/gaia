"""The session-start sweep, on a temp data home, store, registry and git repository: dead runs' leftovers go."""

import importlib
import json
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.fixtures.agent_ids import valid_agent_id

DAY = 86400.0


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age(path: Path, days: float) -> None:
    when = time.time() - days * DAY
    os.utime(path, (when, when))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A temp data home whose registry holds one live session, ``sess-live``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    import gaia.retention.fs_rules as fs_rules
    import gaia.retention.liveness as liveness
    import gaia.retention.session_sweep as session_sweep
    import gaia.retention.worktree_collector as worktree_collector

    for module in (liveness, fs_rules, worktree_collector, session_sweep):
        importlib.reload(module)
    from hooks.modules.session import session_registry

    registry = tmp_path / "session_registry.json"
    monkeypatch.setattr(session_registry, "_get_registry_path", lambda: registry)
    registry.write_text(json.dumps({"sessions": {"sess-live": {
        "started_at": _iso(0), "is_headless": False, "last_heartbeat": time.time(),
    }}}), encoding="utf-8")
    return tmp_path


def _seed(*rows) -> None:
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    con.execute(
        "create table if not exists agent_contract_handoffs (id integer primary key, contract_id text, "
        "agent_id text, harness_agent_id text, session_id text, agent_state text, cut_reason text, created_at text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs (contract_id, agent_id, harness_agent_id, session_id, agent_state, "
        "cut_reason, created_at) values (?, ?, ?, ?, ?, ?, ?)",
        [(cid, cid.split(".")[0], *rest) for cid, *rest in rows],
    )
    con.commit()
    con.close()


def _tmp_dir_of(harness: str) -> Path:
    from gaia.paths import dispatch_tmp_dir

    path = dispatch_tmp_dir(harness)
    (path / "pytest-of-user").mkdir(parents=True)
    (path / "pytest-of-user" / "big.bin").write_bytes(b"x" * 4096)
    _age(path, 3)
    return path


def _verdicts(items):
    return {item["path"]: (item["verdict"], item["reason"]) for item in items}


def test_a_killed_runs_tmp_dir_is_swept(home):
    import gaia.retention.fs_rules as fs_rules
    from gaia.paths import tmp_dir
    from gaia.retention import session_sweep

    killed = _tmp_dir_of("harness-killed")
    _seed((valid_agent_id("killed") + ".k1", "harness-killed", "sess-gone", "IN_PROGRESS", "reaped", _iso(3)))

    assert str(killed) in {a["path"] for a in fs_rules.collectable_turn_scoped(tmp_dir())}
    report = session_sweep.run(home, repos=[])
    assert not killed.exists()
    assert [item["path"] for item in report["removed"]] == [str(killed)]
    assert report["mode"] == "on"


def _spawned(monkeypatch, session_sweep):
    argv = []
    monkeypatch.setattr(session_sweep.subprocess, "Popen", lambda args, **_: argv.append(args))
    return argv


def _past_report(session_sweep, **fields):
    from gaia.paths import data_dir

    data_dir().mkdir(parents=True, exist_ok=True)
    report = {"ran_at": time.time() - DAY, "mode": "on", "removed": [], "refused": [], **fields}
    session_sweep.report_path().write_text(json.dumps(report), encoding="utf-8")


@pytest.mark.parametrize("position,expected", [("on", "on"), ("list", "list"), ("off", None), (None, "on")])
def test_the_switch_decides_what_session_start_starts(home, monkeypatch, position, expected):
    from gaia.retention import session_sweep

    if position is None:
        monkeypatch.delenv("GAIA_SESSION_SWEEP", raising=False)
    else:
        monkeypatch.setenv("GAIA_SESSION_SWEEP", position)
    _past_report(session_sweep)
    argv = _spawned(monkeypatch, session_sweep)

    assert session_sweep.mode() == expected or (position == "off" and session_sweep.mode() == "off")
    assert session_sweep.spawn(home) is (expected is not None)
    assert [a[-2:] for a in argv] == ([["--mode", expected]] if expected else [])


def test_list_only_removes_nothing_and_reports_what_it_would(home):
    from gaia.retention import session_sweep

    killed = _tmp_dir_of("harness-killed")
    _seed((valid_agent_id("killed") + ".k1", "harness-killed", "sess-gone", "DISPATCHED", None, _iso(3)))

    report = session_sweep.run(home, repos=[], mode="list")
    assert killed.exists()
    assert report["removed"] == [] and [i["path"] for i in report["would_remove"]] == [str(killed)]
    assert session_sweep.last_report()["mode"] == "list"


def test_the_automatic_path_walks_no_size(home, repo, monkeypatch):
    from gaia.retention import session_sweep

    def no_walk(*_args, **_kwargs):
        raise AssertionError("the automatic sweep walked a size")

    monkeypatch.setattr(session_sweep, "_size", no_walk)
    killed = _tmp_dir_of("harness-killed")
    _seed((valid_agent_id("killed") + ".k1", "harness-killed", "sess-gone", "DISPATCHED", None, _iso(3)))
    closed = _worktree(repo, "closed", "NEEDS_VERIFICATION", "sess-ended")

    report = session_sweep.run(home, repos=[repo])
    assert not killed.exists() and not closed.exists()
    assert all(item["size"] is None for item in report["removed"])


def test_the_first_automatic_sweep_lists_only_and_says_so_once(home, monkeypatch):
    from gaia.retention import session_sweep

    monkeypatch.delenv("GAIA_SESSION_SWEEP", raising=False)
    argv = _spawned(monkeypatch, session_sweep)
    killed = _tmp_dir_of("harness-killed")
    _seed((valid_agent_id("killed") + ".k1", "harness-killed", "sess-gone", "DISPATCHED", None, _iso(3)))

    assert session_sweep.spawn(home) and argv[-1][-2:] == ["--mode", "first"]
    session_sweep.run(home, repos=[], mode="first")
    assert killed.exists()
    assert session_sweep.spawn(home) is False

    line = session_sweep.first_run_notice()
    assert "would remove 1 item" in line and "gaia cleanup --sweep --dry-run" in line and "next" in line
    assert "\n" not in line
    assert session_sweep.first_run_notice() == ""

    report = session_sweep.last_report()
    report["ran_at"] = time.time() - DAY
    session_sweep.report_path().write_text(json.dumps(report), encoding="utf-8")
    assert session_sweep.spawn(home) and argv[-1][-2:] == ["--mode", "on"]


def test_doctor_sizes_stop_at_their_budget(home):
    from gaia.retention import session_sweep

    big = _tmp_dir_of("harness-big")
    items = [{"kind": "tmp", "path": str(big), "size": None}]

    session_sweep.measure_sizes(items, budget_seconds=0)
    assert items[0]["size"] is None
    session_sweep.measure_sizes(items, budget_seconds=5)
    assert items[0]["size"] >= 4096


def test_a_live_dispatchs_tmp_dir_is_kept(home):
    from gaia.retention import session_sweep

    live = _tmp_dir_of("harness-live")
    _seed((valid_agent_id("live") + ".l1", "harness-live", "sess-live", "IN_PROGRESS", None, _iso(3)))

    assert _verdicts(session_sweep.plan(home, repos=[]))[str(live)][0] == "keep"
    report = session_sweep.run(home, repos=[])
    assert live.exists()
    assert report["removed"] == [] and report["refused"] == []


def test_an_ownerless_tmp_entry_stays_and_is_listed(home):
    from gaia.paths import tmp_dir
    from gaia.retention import session_sweep

    stray = tmp_dir() / "node-compile-cache"
    stray.mkdir(parents=True)
    _age(stray, 30)
    _seed()

    report = session_sweep.run(home, repos=[])
    assert stray.exists()
    assert [(i["path"], i["reason"]) for i in report["refused"]] == [(str(stray), "no owning contract on record")]


@pytest.fixture()
def repo(home):
    path = home / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _worktree(repo: Path, name: str, state: str, session: str, *, seeded: bool = True) -> Path:
    from gaia.worktree import create_agentic_worktree

    contract_id = valid_agent_id(name) + ".w1"
    path = create_agentic_worktree(repo, contract_id, contract_id.split(".")[0], branch=f"wt-{name}")
    _age(path, 3)
    if seeded:
        _seed((contract_id, f"harness-{name}", session, state, None, _iso(3)))
    return path


def test_a_clean_worktree_of_a_closed_owner_goes(home, repo):
    from gaia.retention import session_sweep, worktree_collector

    closed = _worktree(repo, "closed", "NEEDS_VERIFICATION", "sess-ended")
    contract_id = valid_agent_id("closed") + ".w1"

    assert worktree_collector.worktree_collect_reason(contract_id, closed.stat().st_mtime)
    report = session_sweep.run(home, repos=[repo])
    assert not closed.exists()
    assert [i["path"] for i in report["removed"]] == [str(closed)]


def test_a_dirty_worktree_stays_and_is_listed(home, repo):
    from gaia.retention import session_sweep

    dirty = _worktree(repo, "dirty", "NEEDS_VERIFICATION", "sess-ended")
    (dirty / "work.txt").write_text("not committed\n", encoding="utf-8")
    _age(dirty, 3)

    report = session_sweep.run(home, repos=[repo])
    assert dirty.exists()
    assert [(i["path"], i["reason"]) for i in report["refused"]] == [(str(dirty), "uncommitted work, kept as evidence")]


def test_a_worktree_with_unpushed_commits_stays_and_is_listed(home, repo):
    from gaia.retention import session_sweep

    ahead = _worktree(repo, "ahead", "NEEDS_VERIFICATION", "sess-ended")
    (ahead / "work.txt").write_text("committed, never pushed\n", encoding="utf-8")
    _git(ahead, "add", "work.txt")
    _git(ahead, "commit", "-q", "-m", "local only")
    _age(ahead, 3)

    report = session_sweep.run(home, repos=[repo])
    assert ahead.exists()
    assert [(i["path"], i["reason"]) for i in report["refused"]] == [
        (str(ahead), "1 commit(s) no other branch holds, kept as evidence"),
    ]


def test_an_ownerless_worktree_stays_and_is_listed(home, repo):
    from gaia.retention import session_sweep

    orphan = _worktree(repo, "orphan", "COMPLETE", "sess-ended", seeded=False)
    _seed()

    report = session_sweep.run(home, repos=[repo])
    assert orphan.exists()
    assert [i["path"] for i in report["refused"]] == [str(orphan)]
    assert "no readable row" in report["refused"][0]["reason"]


def test_a_live_sessions_worktree_is_never_touched(home, repo):
    from gaia.retention import session_sweep

    live = _worktree(repo, "live", "COMPLETE", "sess-live")

    report = session_sweep.run(home, repos=[repo])
    assert live.exists()
    assert report["removed"] == [] and report["refused"] == []


def test_two_concurrent_sweeps_do_not_collide(home, repo):
    from gaia.retention import session_sweep

    killed = _tmp_dir_of("harness-killed")
    _seed((valid_agent_id("killed") + ".k1", "harness-killed", "sess-gone", "DISPATCHED", None, _iso(3)))
    closed = _worktree(repo, "closed", "APPROVAL_REQUEST", "sess-ended")

    barrier, reports, errors = threading.Barrier(2), [], []

    def sweep():
        barrier.wait()
        try:
            reports.append(session_sweep.run(home, repos=[repo]))
        except Exception as exc:  # noqa: BLE001 -- the assertion below names it
            errors.append(exc)

    threads = [threading.Thread(target=sweep) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert not killed.exists() and not closed.exists()
    removed = [i["path"] for r in reports if r for i in r["removed"]]
    assert sorted(removed) == sorted([str(killed), str(closed)])
    assert all(not r["failed"] for r in reports if r)
    assert not (home / "data" / "session-sweep.claim").exists()
