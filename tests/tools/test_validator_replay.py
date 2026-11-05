"""The validator replay engine: transitions between two classifiers over a corpus, friction caps, and its entry's consent cost."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for entry in (str(REPO), str(REPO / "hooks")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tools.validator_replay import corpus, replay  # noqa: E402

ENTRY = "python3 -m pytest tests/replay/test_validator_replay_diff.py -m replay -q"
OLD = {"ls": "FREE", "git push origin main": "SIGNED", "rm -rf /": "BLOCKED", "$X": "UNKNOWN", "cat a": "FREE", "kubectl apply -f x": "SIGNED"}
NEW = {"ls": "FREE", "git push origin main": "FREE", "rm -rf /": "BLOCKED", "$X": "SIGNED", "cat a": "SIGNED", "kubectl apply -f x": "SIGNED"}
CAPS = {"history_newly_asking_max_count": 5, "history_newly_asking_max_fraction": 0.5,
        "pinned_changes_max_outside_declared": 0, "loosened_unreviewed_max": 0, "new_errors_max": 0}


def _stub(table):
    return lambda units: {key: {"verdict": table[unit["command"]], "rule": ""} for key, unit in units.items()}


def _store(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE harness_events (id INTEGER PRIMARY KEY, workspace TEXT, ts TEXT NOT NULL, type TEXT NOT NULL,"
                " source TEXT, agent TEXT, result TEXT, severity TEXT, payload TEXT)")
    con.execute("CREATE TABLE approvals (id TEXT PRIMARY KEY, status TEXT, payload_json TEXT, created_at TEXT)")
    for command in ("ls", "ls", "cat a", "$X", "rm -rf /"):
        con.execute("INSERT INTO harness_events (ts, type, payload) VALUES ('2026-11-01T00:00:00Z', 'tool.called', ?)",
                    (json.dumps({"tool": "Bash", "command": command}),))
    con.execute("INSERT INTO harness_events (ts, type, payload) VALUES ('2026-11-01', 'tool.called', ?)",
                (json.dumps({"tool": "Read", "command": "not a shell call"}),))
    con.execute("INSERT INTO approvals VALUES ('P-1', 'approved', ?, '2026-11-01')",
                (json.dumps({"items": [{"command": "git push origin main"}, {"command": "kubectl apply -f x"}]}),))
    con.execute("INSERT INTO approvals VALUES ('P-2', 'approved', ?, '2026-11-01')", (json.dumps({"items": [{"path": "/x/.claude/a"}]}),))
    con.commit()
    con.close()
    return path


@pytest.fixture
def replay_with(tmp_path):
    table = tmp_path / "table.json"
    table.write_text(json.dumps({"header": {}, "rows": [{"id": "r-ls", "command": "ls", "expected": "FREE"},
                                                        {"id": "r-push", "command": "git push origin main", "expected": "SIGNED"}]}))
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"id": "c-rm", "command": "rm -rf /", "context": "none"}]}))
    db = _store(tmp_path / "gaia.db")

    def run(old, new, caps=CAPS, declared=None):
        return replay.run(caps=caps, out=tmp_path / "out", db=db, table=table, cases=cases, declared=declared,
                          classifiers=(_stub(old), _stub(new)))
    return run


def test_the_matrix_counts_every_transition_and_lists_each_changed_command(replay_with, tmp_path):
    result = replay_with(OLD, NEW)
    assert result["violations"]
    assert json.loads((tmp_path / "out" / "report.json").read_text())["changes"] == result["changes"]
    assert result["sizes"] == {"truth": 2, "history": 4, "approvals": 2, "cases": 1}
    history = result["matrix"]["history"]
    assert set(history) == {"FREE", "SIGNED", "BLOCKED", "UNKNOWN"} and all(set(row) == set(history) for row in history.values())
    assert (history["FREE"]["FREE"], history["FREE"]["SIGNED"], history["UNKNOWN"]["SIGNED"], history["BLOCKED"]["BLOCKED"]) == (1, 1, 1, 1)
    assert result["matrix"]["truth"]["SIGNED"]["FREE"] == 1 and result["matrix"]["approvals"]["SIGNED"]["FREE"] == 1
    changed = {(c["corpus"], c["command"], c["old"], c["new"]) for c in result["changes"]}
    assert changed == {("history", "cat a", "FREE", "SIGNED"), ("history", "$X", "UNKNOWN", "SIGNED"),
                       ("truth", "git push origin main", "SIGNED", "FREE"), ("approvals", "git push origin main", "SIGNED", "FREE")}


def test_identical_classifiers_pass_and_each_cap_alone_fails_the_run(replay_with):
    same = replay_with(OLD, OLD)
    assert not same["changes"] and not same["violations"]
    tightening = dict(OLD, **{"cat a": "SIGNED"})
    assert not replay_with(OLD, tightening)["violations"]
    assert replay_with(OLD, tightening, caps=dict(CAPS, history_newly_asking_max_count=0))["violations"] == [
        "history_newly_asking_max_count: 1 > 0"]
    loosening = dict(OLD, **{"kubectl apply -f x": "FREE"})
    assert replay_with(OLD, loosening)["violations"] == ["loosened_unreviewed_max: 1 > 0"]
    reviewed = {"reviews": {"kubectl apply -f x": "jorge 2026-11-05: an apply here is local"}}
    assert not replay_with(OLD, loosening, declared=reviewed)["violations"]


def test_the_history_reader_opens_the_store_read_only(tmp_path):
    db = _store(tmp_path / "gaia.db")
    before = db.read_bytes()
    con = corpus.open_read_only(db)
    assert corpus.history_commands(con) == ["$X", "cat a", "ls", "rm -rf /"]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("INSERT INTO harness_events (ts, type) VALUES ('t', 'x')")
    con.close()
    assert db.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        corpus.open_read_only(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()


def test_the_report_never_lands_in_the_repository(replay_with, tmp_path):
    with pytest.raises(ValueError):
        replay.run(caps=CAPS, out=REPO / "replay-out", db=tmp_path / "gaia.db", classifiers=(_stub(OLD), _stub(OLD)))


def test_the_curated_cases_hold_local_merges_and_the_three_pushes():
    cases = {(case["command"], case["context"]) for case in corpus.curated_cases(corpus.DEFAULT_CASES)}
    for needed in ("git merge feature-x", "git merge --ff-only feature-x", "git push origin feature-x",
                   "git push origin main", "git push --force origin feature-x"):
        assert (needed, "repo") in cases, needed


def test_the_entry_line_runs_free_through_the_bash_validator(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "data" / "gaia.db"))
    from gaia.paths import ensure_layout
    from modules.tools.bash_validator import BashValidator

    ensure_layout()
    payload = {"cwd": str(REPO), "tool_use_id": "t-replay", "session_id": "s-replay", "agent_id": "areplay01", "agent_type": "gaia-system"}
    result = BashValidator(agent_names_scratch=True).validate(ENTRY, is_subagent=True, session_id="s-replay",
                                                              agent_type="gaia-system", hook_payload=payload)
    assert result.allowed, result.reason
