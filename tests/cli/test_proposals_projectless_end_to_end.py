"""A proposal from a turn with no dispatch project is kept, listed and assigned, through the real CLI.

Plan 94 D3/D9: a proposal with no project is listed with no project, and
``accept --project`` assigns one. Every step runs ``bin/gaia`` as a subprocess
on a scratch database; only the dispatch birth and the project row are seeded
in-process, since those belong to the hooks and the scanner.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

GAIA = _REPO_ROOT / "bin" / "gaia"
PROJECT_ID = "github.com/metraton/gaia"
AGENT = "a" + "2" * 16
CONTRACT_ID = f"{AGENT}.0000000000aa"


def _create(slug: str) -> dict:
    return {"operation": "create", "create": {
        "slug": slug, "type": "atom", "description": f"Fact: {slug} holds.",
        "body": f"{slug} was measured in the end-to-end test."}}


def test_proposals_projectless_end_to_end(tmp_path, monkeypatch):
    data = tmp_path / "data"
    env = {k: v for k, v in os.environ.items()
           if k not in ("GAIA_DISPATCH_AGENT", "GAIA_WORKSPACE", "GAIA_SESSION_ID")}
    env.update(GAIA_DATA_DIR=str(data), GAIA_DB=str(data / "gaia.db"),
               HOME=str(tmp_path / "home"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)

    def gaia(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(GAIA), *argv], capture_output=True,
                              text=True, env=env, cwd=str(tmp_path), timeout=120)

    from gaia.store.writer import _connect, insert_dispatched_handoff

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.execute("INSERT INTO projects (workspace, name, path, project_identity) "
                    "VALUES ('me', 'gaia', '/repos/gaia', ?)", (PROJECT_ID,))
        con.commit()
    finally:
        con.close()
    insert_dispatched_handoff(CONTRACT_ID, AGENT, "me", dispatch_project=None)

    patch = {
        "user_facing_summary": "Two facts proposed.",
        "memory_delta": {"version": 1, "proposals": [
            _create("atom_projectless_kept"), _create("atom_projectless_dropped")]},
        "evidence_report": {"verification": {
            "type": "command", "command": "true", "method": "e2e", "result": "pass",
            "details": "ok"}},
    }
    for argv in (
        ("fill", "--draft-id", CONTRACT_ID, "--json", json.dumps(patch)),
        ("set", "--draft-id", CONTRACT_ID, "agent_status.next_action", "done"),
        ("set", "--draft-id", CONTRACT_ID, "agent_status.agent_state", "COMPLETE"),
        ("finalize", "--draft-id", CONTRACT_ID),
    ):
        step = gaia("contract", *argv)
        assert step.returncode == 0, (argv, step.stdout, step.stderr)

    listed = gaia("contract", "proposals", "list", "--json")
    assert listed.returncode == 0, listed.stderr
    pending = json.loads(listed.stdout)["proposals"]
    assert [(p["kind"], p["project"], p["contract_id"]) for p in pending] == [
        ("memory", None, CONTRACT_ID), ("memory", None, CONTRACT_ID)]
    kept, dropped = pending

    accepted = gaia("contract", "proposals", "accept", str(kept["id"]), "--project", PROJECT_ID)
    assert accepted.returncode == 0, (accepted.stdout, accepted.stderr)
    dismissed = gaia("contract", "proposals", "dismiss", str(dropped["id"]),
                     "--reason", "duplicate of an existing atom")
    assert dismissed.returncode == 0, dismissed.stderr

    after = gaia("contract", "proposals", "list", "--json")
    assert json.loads(after.stdout)["proposals"] == []

    view = gaia("contract", "view", "--draft-id", CONTRACT_ID, "--json")
    assert view.returncode == 0, view.stderr
    decisions = {p["id"]: p for p in json.loads(view.stdout)["proposals"]}
    assert (decisions[kept["id"]]["status"], decisions[kept["id"]]["project"]) == (
        "accepted", PROJECT_ID)
    assert (decisions[dropped["id"]]["status"], decisions[dropped["id"]]["project"]) == (
        "dismissed", None)

    con = sqlite3.connect(str(data / "gaia.db"))
    try:
        memory = con.execute("SELECT project_ref FROM memory WHERE name = ?",
                             ("atom_projectless_kept",)).fetchone()
        absent = con.execute("SELECT 1 FROM memory WHERE name = ?",
                             ("atom_projectless_dropped",)).fetchone()
    finally:
        con.close()
    assert memory == (PROJECT_ID,)
    assert absent is None
