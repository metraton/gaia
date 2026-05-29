"""
Structural enforcement: curated memory writes from non-curator subagent
dispatches are rejected.

Brief: memory-model-refactor-class-status-links-structural-enforcement (T3).

The signal is the env var ``GAIA_DISPATCH_AGENT`` which a dispatch hook sets
to the name of the subagent being launched. The writer reads it on every
``upsert_memory`` / ``update_memory_field`` / ``delete_memory`` and rejects
the call when the value is set to anything other than the curator pair
(``orchestrator``, ``operator``, ``gaia-orchestrator``, ``gaia-operator``).

When the env var is unset, the call originates from a human shell -- the
authoritative caller -- and is always permitted.

Coverage:
  * non-curator dispatch -> upsert raises MemoryWriteForbidden
  * curator dispatch ('gaia-orchestrator', 'gaia-operator') -> upsert OK
  * human shell (no env var) -> upsert OK
  * update_memory_field and delete_memory enforce the same rule
  * CLI surface: ``gaia memory add`` under non-curator dispatch exits != 0
    with a message containing the structural reason
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Repo-root import bootstrap (mirrors tests/integration/test_memory_cli.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path so the test never touches the real
    ``~/.gaia/gaia.db``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


@pytest.fixture()
def clear_dispatch(monkeypatch):
    """Ensure GAIA_DISPATCH_AGENT starts unset for each test, so the human-
    caller baseline is honoured. Individual tests opt into a dispatch value
    with ``monkeypatch.setenv``."""
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _seed_atom(workspace: str = "me", name: str = "atom_seed") -> None:
    """Create a baseline atom for tests that exercise update / delete paths."""
    from gaia.store.writer import upsert_memory
    upsert_memory(
        workspace,
        name,
        type="atom",
        body="seed body",
        description="seed",
    )


# ---------------------------------------------------------------------------
# upsert_memory enforcement
# ---------------------------------------------------------------------------

def test_upsert_memory_rejects_non_curator_dispatch(tmp_db, clear_dispatch, monkeypatch):
    """A subagent dispatch with GAIA_DISPATCH_AGENT=developer cannot write."""
    from gaia.store.writer import upsert_memory, MemoryWriteForbidden

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    with pytest.raises(MemoryWriteForbidden) as exc_info:
        upsert_memory(
            "me",
            "atom_forbidden",
            type="atom",
            body="should not land",
        )
    msg = str(exc_info.value)
    assert "developer" in msg
    assert "GAIA_DISPATCH_AGENT" in msg
    assert "orchestrator-operator" in msg


def test_upsert_memory_allowed_for_gaia_operator(tmp_db, clear_dispatch, monkeypatch):
    """The operator is one of the two legitimate memory curators."""
    from gaia.store.writer import upsert_memory

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-operator")
    res = upsert_memory(
        "me",
        "atom_curator_operator",
        type="atom",
        body="curator wrote me",
    )
    assert res["status"] == "applied"


def test_upsert_memory_allowed_for_gaia_orchestrator(tmp_db, clear_dispatch, monkeypatch):
    """The orchestrator is the other legitimate memory curator."""
    from gaia.store.writer import upsert_memory

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-orchestrator")
    res = upsert_memory(
        "me",
        "atom_curator_orchestrator",
        type="atom",
        body="curator wrote me",
    )
    assert res["status"] == "applied"


def test_upsert_memory_allowed_for_human_caller_no_env(tmp_db, clear_dispatch):
    """When the env var is absent, the caller is a human shell -- permitted."""
    from gaia.store.writer import upsert_memory

    assert "GAIA_DISPATCH_AGENT" not in os.environ
    res = upsert_memory(
        "me",
        "atom_human_caller",
        type="atom",
        body="human wrote me from a shell",
    )
    assert res["status"] == "applied"


def test_upsert_memory_empty_env_treated_as_unset(tmp_db, clear_dispatch, monkeypatch):
    """An empty string is operationally identical to the env var not being set."""
    from gaia.store.writer import upsert_memory

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "")
    res = upsert_memory(
        "me",
        "atom_empty_env",
        type="atom",
        body="ok with empty string",
    )
    assert res["status"] == "applied"


def test_upsert_memory_rejects_terraform_architect(tmp_db, clear_dispatch, monkeypatch):
    """A second non-curator dispatch is rejected the same way."""
    from gaia.store.writer import upsert_memory, MemoryWriteForbidden

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "platform-architect")
    with pytest.raises(MemoryWriteForbidden):
        upsert_memory(
            "me",
            "atom_tf_forbidden",
            type="atom",
            body="should not land",
        )


# ---------------------------------------------------------------------------
# update_memory_field enforcement
# ---------------------------------------------------------------------------

def test_update_memory_field_rejects_non_curator(tmp_db, clear_dispatch, monkeypatch):
    """The patch path is gated by the same enforcement."""
    from gaia.store.writer import update_memory_field, MemoryWriteForbidden

    _seed_atom()  # seeded with no env var -> permitted

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
    with pytest.raises(MemoryWriteForbidden):
        update_memory_field("me", "atom_seed", "body", "patched")


def test_update_memory_field_allowed_for_curator(tmp_db, clear_dispatch, monkeypatch):
    from gaia.store.writer import update_memory_field

    _seed_atom()

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-orchestrator")
    res = update_memory_field("me", "atom_seed", "body", "patched by curator")
    assert res["status"] == "applied"
    assert res["field"] == "body"


def test_update_memory_field_allowed_human_caller(tmp_db, clear_dispatch):
    from gaia.store.writer import update_memory_field

    _seed_atom()

    assert "GAIA_DISPATCH_AGENT" not in os.environ
    res = update_memory_field("me", "atom_seed", "body", "patched by human")
    assert res["status"] == "applied"


# ---------------------------------------------------------------------------
# delete_memory enforcement
# ---------------------------------------------------------------------------

def test_delete_memory_rejects_non_curator(tmp_db, clear_dispatch, monkeypatch):
    from gaia.store.writer import delete_memory, MemoryWriteForbidden

    _seed_atom()

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gitops-operator")
    with pytest.raises(MemoryWriteForbidden):
        delete_memory("me", "atom_seed")


def test_delete_memory_allowed_for_curator(tmp_db, clear_dispatch, monkeypatch):
    from gaia.store.writer import delete_memory

    _seed_atom()

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "gaia-operator")
    assert delete_memory("me", "atom_seed") is True


def test_delete_memory_allowed_human_caller(tmp_db, clear_dispatch):
    from gaia.store.writer import delete_memory

    _seed_atom()

    assert "GAIA_DISPATCH_AGENT" not in os.environ
    assert delete_memory("me", "atom_seed") is True


# ---------------------------------------------------------------------------
# CLI surface: gaia memory add under non-curator dispatch must exit != 0
# ---------------------------------------------------------------------------

def test_cli_cmd_add_rejects_non_curator_dispatch(tmp_db, tmp_path, monkeypatch, capsys):
    """The CLI handler propagates PermissionError as a structured error and a
    non-zero exit code -- the AC-3 evidence command."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    args = argparse.Namespace(
        name="atom_cli_forbidden",
        type="atom",
        body="should not land",
        description="x",
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc != 0
    combined = (captured.out + captured.err).lower()
    assert "developer" in combined
    assert "memory" in combined


def test_cli_cmd_add_json_error_carries_structural_message(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")

    args = argparse.Namespace(
        name="atom_cli_json_forbidden",
        type="atom",
        body="x",
        description=None,
        workspace="me",
        json=True,
    )
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc != 0
    # The error path emits a JSON envelope containing the structural reason.
    payload = json.loads(captured.out or captured.err)
    msg = json.dumps(payload).lower()
    assert "developer" in msg


def test_cli_cmd_add_allowed_human_caller(tmp_db, tmp_path, monkeypatch, capsys):
    """The same CLI command works fine when no dispatch env var is present."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)

    args = argparse.Namespace(
        name="atom_cli_human",
        type="atom",
        body="human wrote me",
        description="ok",
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()
