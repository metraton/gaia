"""A memory proposal `gaia memory add` would reject is refused where the specialist writes it."""

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
AGENT = "a" + "3" * 16
CONTRACT_ID = f"{AGENT}.0000000000bb"


def _proposal(slug: str, mem_type: str) -> dict:
    return {"operation": "create", "create": {
        "slug": slug, "type": mem_type, "description": f"Fact: {slug} holds.", "body": "body"}}


def _delta(*proposals: dict) -> dict:
    return {"version": 1, "proposals": list(proposals)}


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


def _stored_delta(gaia) -> dict:
    view = gaia("view", "--json")
    assert view.returncode == 0, view.stderr
    return json.loads(view.stdout).get("memory_delta") or json.loads(view.stdout).get(
        "envelope", {}).get("memory_delta") or {}


@pytest.mark.parametrize("bad", [
    _proposal("atom_typed_fact", "fact"),
    _proposal("bad slug with spaces!", "atom"),
    _proposal("project_wrong_prefix", "atom"),
])
def test_memory_delta_refused_at_write_by_fill(gaia, bad):
    refused = gaia("fill", "--json", json.dumps({"memory_delta": _delta(bad)}))

    assert refused.returncode != 0, refused.stdout
    message = refused.stdout + refused.stderr
    assert bad["create"]["slug"] in message
    assert "atom" in message and "decision" in message
    assert "replace memory_delta" in message
    assert _stored_delta(gaia) == {}


def test_memory_delta_refused_at_write_by_add(gaia):
    assert gaia("fill", "--json", json.dumps({"memory_delta": _delta(
        _proposal("atom_kept_fact", "atom"))})).returncode == 0

    refused = gaia("add", "memory_delta.proposals",
                   json.dumps(_proposal("atom_typed_fact", "fact")))

    assert refused.returncode != 0
    assert "atom_typed_fact" in refused.stdout + refused.stderr
    assert [p["create"]["slug"] for p in _stored_delta(gaia)["proposals"]] == ["atom_kept_fact"]


def test_memory_delta_corrected_by_set_passes(gaia):
    bad = _delta(_proposal("atom_typed_fact", "fact"))
    assert gaia("set", "memory_delta", json.dumps(bad)).returncode != 0

    fixed = gaia("set", "memory_delta", json.dumps(_delta(_proposal("atom_typed_fact", "atom"))))

    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert [p["create"]["type"] for p in _stored_delta(gaia)["proposals"]] == ["atom"]
