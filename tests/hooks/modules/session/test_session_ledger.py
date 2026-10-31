"""The Resume point is one session's ledger, written by PreCompact and shown only after a compaction, a resume or a fork."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

_REPO = Path(__file__).resolve().parents[4]
for _path in (str(_REPO), str(_REPO / "hooks")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

A, B = "ses-a", "ses-b"


def _contract(con, contract_id, session_id, state, description):
    con.execute(
        "INSERT INTO agent_contract_handoffs (contract_id, agent_id, session_id, workspace, agent_state, "
        "dispatch_description, raw_handoff_json) VALUES (?, ?, ?, 'me', ?, ?, '{}')",
        (contract_id, contract_id.split(".")[0], session_id, state, description),
    )


def _approval(con, approval_id, session_id, what):
    con.execute(
        "INSERT INTO approvals (id, agent_id, session_id, status, payload_json) VALUES (?, 'a1', ?, 'pending', ?)",
        (approval_id, session_id, json.dumps({"what": what})),
    )


def _worktree(root, project, name, contract_id, branch):
    gitdir = root / "repo.git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    tree = root / ".project-worktrees" / project / name
    tree.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    (gitdir / "gaia-worktree.json").write_text(json.dumps({
        "repo": str(root), "project": project, "contract_id": contract_id, "agent_id": "a1",
        "branch": branch, "commit": "80d055095641342c", "lifecycle": "created", "path": str(tree),
    }), encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch, bootstrapped_db_template):
    """Session A and session B, each with an open contract, a pending signature, a memory row and a worktree."""
    data = tmp_path / "data"
    path = copy_bootstrapped_db(bootstrapped_db_template, data / "gaia.db")
    (tmp_path / "home").mkdir()
    for name, value in (("HOME", tmp_path / "home"), ("GAIA_DATA_DIR", data), ("GAIA_DB", path)):
        monkeypatch.setenv(name, str(value))
    for name in ("GAIA_HOST_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "ws"
    con = sqlite3.connect(path)
    con.execute("INSERT INTO workspaces (name, root_path) VALUES ('me', ?)", (str(root),))
    _contract(con, "a0000000000000a01.t", A, "IN_PROGRESS", "Build the session ledger")
    _contract(con, "a0000000000000a02.t", A, "COMPLETE", "Settled work of A")
    _contract(con, "b0000000000000b01.t", B, "NEEDS_INPUT", "Work of session B")
    _approval(con, "P-a", A, "Push the ledger branch")
    _approval(con, "P-b", B, "Push something of B")
    for slug, session in (("atom_written_by_a", A), ("atom_written_by_b", B)):
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, origin_session_id) "
            "VALUES ('me', ?, 'project', 'd', 'b', ?)", (slug, session),
        )
    con.execute("INSERT INTO briefs (workspace, name) VALUES ('me', 'brief-global')")
    con.execute("INSERT INTO plans (brief_id, status) VALUES (1, 'active')")
    con.commit()
    con.close()
    _worktree(root, "gaia", "wt-a", "a0000000000000a01.t", "feat/ledger-a")
    _worktree(root, "gaia", "wt-b", "b0000000000000b01.t", "feat/work-b")
    return path


def _birth(source, session_id=A):
    from modules.session.session_manifest import build_session_context

    return build_session_context(source=source, session_id=session_id)


def _pre_compact(session_id, env_overrides=None):
    env = {**os.environ, **(env_overrides or {})}
    payload = {"hook_event_name": "PreCompact", "session_id": session_id, "trigger": "auto",
               "transcript_path": "", "cwd": os.getcwd()}
    return subprocess.run(
        [sys.executable, str(_REPO / "hooks" / "pre_compact.py")],
        input=json.dumps(payload), env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("source", ["startup", "clear"])
def test_startup_and_clear_carry_no_resume_point_and_no_global_counts(db, source):
    from gaia.session_snapshot import write_resume_point

    write_resume_point(A, "next: rebase the ledger branch")
    birth = _birth(source)

    assert "## Environment" in birth
    for absent in ("## Resume point", "Active plan", "Pending proposals", "Pending signatures",
                   "P-a", "rebase the ledger branch"):
        assert absent not in birth


def test_pre_compact_writes_this_sessions_ledger_and_counts_each_compaction(db):
    from gaia.session_ledger import read_ledger

    first = _pre_compact(A)
    assert (first.returncode, first.stdout.strip()) == (0, "{}"), first.stderr
    assert _pre_compact(A).returncode == 0

    ledger = read_ledger(A)
    assert ledger["compactions"] == 2
    assert ledger["in_flight"] == [
        {"title": "Build the session ledger", "state": "IN_PROGRESS", "contract_id": "a0000000000000a01.t"}
    ]
    assert ledger["pending"] == [{"id": "P-a", "what": "Push the ledger branch", "state": "pending"}]
    assert ledger["written"] == [{"workspace": "me", "slug": "atom_written_by_a"}]
    assert [(b["branch"], b["commit"]) for b in ledger["branches"]] == [("feat/ledger-a", "80d055095641")]
    assert read_ledger(B) is None


def test_the_written_list_reads_origin_session_id_when_no_signal_names_the_session(db):
    from gaia.session_ledger import build_ledger

    con = sqlite3.connect(db)
    columns = {row[1] for row in con.execute("PRAGMA table_info(memory)")}
    signals = con.execute("SELECT COUNT(*) FROM signals WHERE ref = 'atom_written_by_a'").fetchone()[0]
    con.close()
    migrations = (_REPO / "scripts" / "migrations").glob("*.sql")

    assert "origin_session_id" in columns and signals == 0
    assert not [m.name for m in migrations if re.search(r"DROP\s+COLUMN\s+origin_session_id", m.read_text(), re.I)]
    assert build_ledger(A)["written"] == [{"workspace": "me", "slug": "atom_written_by_a"}]


@pytest.mark.parametrize("source", ["compact", "resume", "fork"])
def test_compact_resume_and_fork_render_only_this_sessions_ledger(db, source):
    from gaia.session_ledger import write_ledger

    write_ledger(A)
    birth = _birth(source)
    resume = birth.split("## Resume point\n", 1)[1]

    assert "## Environment" in birth
    assert resume.startswith(f"session={A} compactions=1\n")
    assert 'in_flight: "Build the session ledger" state=IN_PROGRESS ref=contract:a0000000000000a01.t' in resume
    assert 'pending: P-a "Push the ledger branch" state=pending' in resume
    assert "written: atom_written_by_a ref=memory:me/atom_written_by_a" in resume
    assert "branches: feat/ledger-a at 80d055095641" in resume
    for foreign in ("b0000000000000b01", "P-b", "atom_written_by_b", "feat/work-b", "Settled work of A",
                    "brief-global", "Pending proposals"):
        assert foreign not in birth


def test_a_hand_note_is_shown_only_when_this_session_wrote_it(db):
    from gaia.session_ledger import write_ledger
    from gaia.session_snapshot import _resume_path, write_resume_point

    write_ledger(A)
    with pytest.raises(ValueError):
        write_resume_point(A, "planted by B", written_by=B)
    path = _resume_path(A)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": "planted by B", "written_by": B}), encoding="utf-8")
    assert "planted by B" not in _birth("compact")

    write_resume_point(A, "next: request the push", written_by=A)
    assert 'note: "next: request the push"' in _birth("compact")


def test_every_list_is_capped_with_its_remainder_and_the_birth_fits_the_budget(db):
    from gaia.session_ledger import LIST_CAPS, _ledger_path
    from gaia.session_snapshot import RESUME_POINT_MAX_CHARS, write_resume_point
    from modules.session.session_manifest import BIRTH_BUDGET

    many = 40
    ledger = {
        "session_id": A, "compactions": 3, "unavailable": [],
        "in_flight": [{"title": "t" * 300, "state": "IN_PROGRESS", "contract_id": f"a{i:016x}.t"} for i in range(many)],
        "pending": [{"id": f"P-{i}", "what": "w" * 300, "state": "pending"} for i in range(many)],
        "written": [{"workspace": "me", "slug": f"atom_{i}"} for i in range(many)],
        "branches": [{"branch": f"feat/b{i}", "commit": "80d055095641"} for i in range(many)],
    }
    path = _ledger_path(A)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger), encoding="utf-8")
    write_resume_point(A, "n" * RESUME_POINT_MAX_CHARS, written_by=A)

    birth = _birth("compact")
    lines = birth.split("## Resume point\n", 1)[1].split("\n\n", 1)[0].splitlines()

    assert len(birth) <= BIRTH_BUDGET
    for key, cap in LIST_CAPS.items():
        listed = [line for line in lines if line.startswith(f"{key}: ")]
        assert len(listed) == cap and listed[-1].endswith(f"(+{many - cap})")
    assert all(len(line) < 200 for line in lines if not line.startswith("note: "))
    assert "n" * RESUME_POINT_MAX_CHARS in birth


@pytest.mark.parametrize("breakage", ["corrupt_db", "invalid_session"])
def test_pre_compact_never_blocks_and_leaves_no_ledger_when_it_fails(db, tmp_path, breakage):
    from gaia.session_ledger import read_ledger

    env, session = {"GAIA_DEBUG": "1"}, A
    if breakage == "corrupt_db":
        junk = tmp_path / "junk.db"
        junk.write_text("not a database", encoding="utf-8")
        env["GAIA_DB"] = str(junk)
    else:
        session = "../escape"
    result = _pre_compact(session, env)

    assert (result.returncode, result.stdout.strip()) == (0, "{}"), result.stderr
    assert read_ledger(A) is None
    logged = "".join(log.read_text(encoding="utf-8") for log in tmp_path.rglob("hooks-*.log"))
    assert "PreCompact: session ledger not written" in logged
