"""A malformed update_contracts or memory_delta is refused where the specialist writes it, naming the literal keys."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

GAIA = _REPO_ROOT / "bin" / "gaia"
AGENT = "a" + "5" * 16
CONTRACT_ID = f"{AGENT}.0000000000cc"
CREATE = {"operation": "create", "create": {
    "slug": "atom_kept_fact", "type": "atom", "kind": "fact",
    "description": "atom_kept_fact holds.", "body": "body"}}


@pytest.fixture()
def gaia(tmp_path, monkeypatch):
    data = tmp_path / "data"
    env = {k: v for k, v in os.environ.items()
           if k not in ("GAIA_DISPATCH_AGENT", "GAIA_WORKSPACE", "GAIA_SESSION_ID")}
    env.update(GAIA_DATA_DIR=str(data), GAIA_DB=str(data / "gaia.db"),
               HOME=str(tmp_path / "home"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from gaia.store.writer import _connect, insert_dispatched_handoff

    con = _connect()
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('me', 'me')")
        con.commit()
    finally:
        con.close()
    insert_dispatched_handoff(CONTRACT_ID, AGENT, "me", dispatch_project=None)

    def run(*argv: str) -> subprocess.CompletedProcess:
        verb, *rest = argv
        return subprocess.run(
            [sys.executable, str(GAIA), "contract", verb, "--draft-id", CONTRACT_ID, *rest],
            capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)

    return run


def _stored(gaia, field: str):
    view = gaia("view", "--json")
    assert view.returncode == 0, view.stderr
    shown = json.loads(view.stdout)
    return shown.get(field) or shown.get("envelope", {}).get(field)


def test_update_contracts_without_the_literal_keys_is_refused_then_repaired(gaia):
    refused = gaia("set", "update_contracts", '[{"section":"x","payload":{}}]')

    assert refused.returncode != 0, refused.stdout
    message = refused.stdout + refused.stderr
    assert "update_contracts[0]" in message
    assert '"contract"' in message and '"payload"' in message and '"section"' in message
    assert not _stored(gaia, "update_contracts")

    repaired = gaia("set", "update_contracts", '[{"contract":"x","payload":{"k":1}}]')

    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert _stored(gaia, "update_contracts") == [{"contract": "x", "payload": {"k": 1}}]
    assert gaia("set", "update_contracts", '{"stack":{"k":1}}').returncode == 0


@pytest.mark.parametrize("entry,reason", [
    ({"contract": "x", "payload": "not an object"}, "not an object"),
    ("x", "update_contracts[0]"),
])
def test_update_contracts_refused_at_write_by_fill(gaia, entry, reason):
    refused = gaia("fill", "--json", json.dumps({"update_contracts": [entry]}))

    assert refused.returncode != 0, refused.stdout
    assert reason in refused.stdout + refused.stderr
    assert not _stored(gaia, "update_contracts")


@pytest.mark.parametrize("delta,named", [
    ({"version": 2, "proposals": [CREATE]}, "memory_delta.version"),
    ({"version": 1, "proposals": "atom_kept_fact"}, "memory_delta.proposals"),
    ({"version": 1, "proposals": [{"operation": "remove", "remove": {}}]}, "operation"),
    ({"version": 1, "proposals": [{"operation": "create", "create": "atom_kept_fact"}]}, "create"),
])
def test_memory_delta_that_would_record_nothing_is_refused_at_write(gaia, delta, named):
    refused = gaia("set", "memory_delta", json.dumps(delta))

    assert refused.returncode != 0, refused.stdout
    message = refused.stdout + refused.stderr
    assert named in message
    assert "version" in message and "proposals" in message and "operation" in message
    assert not _stored(gaia, "memory_delta")

    repaired = gaia("set", "memory_delta", json.dumps({"version": 1, "proposals": [CREATE]}))

    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
