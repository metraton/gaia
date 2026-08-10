"""Whether a contract's owning session is still alive
(``gaia.retention.session_liveness``).

Three cases, matching AC-9's gate:

  1. a contract whose session's heartbeat is fresh (within
     ``HEARTBEAT_TTL_SECONDS``) reads ALIVE;
  2. one whose heartbeat exists but is older than the TTL reads DEAD;
  3. one whose session registry is absent, unreadable, or simply carries no
     entry for that session reads UNKNOWN -- never DEAD.

Case 3 is the adversarial assertion this module exists to guarantee: a
collector that only checks "is this DEAD" and otherwise leaves an entry
alone gets the correct behavior for free ONLY if the illegible/absent case
can never surface as DEAD. Every sub-case that could plausibly be confused
with "the session is gone" (missing file, corrupt file, no entry, a legacy
entry with no heartbeat, an unimportable registry module) is exercised
separately so no single one of them can regress into DEAD unnoticed.
"""

import importlib
import sqlite3
import sys
import time
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

CONTRACT_ALIVE = valid_agent_id("sl-alive") + ".c1"
CONTRACT_DEAD = valid_agent_id("sl-dead") + ".c2"
CONTRACT_NO_SESSION = valid_agent_id("sl-nosession") + ".c3"
CONTRACT_UNKNOWN_SESSION = valid_agent_id("sl-unknownsession") + ".c4"


@pytest.fixture()
def liveness_mod(tmp_path, monkeypatch):
    """Redirect gaia.db and the session registry file to tmp, each in isolation."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    import gaia.retention.fs_rules as fs_rules_mod
    import gaia.retention.liveness as mod

    importlib.reload(fs_rules_mod)
    importlib.reload(mod)

    from hooks.modules.session import session_registry

    registry_file = tmp_path / "session_registry.json"
    monkeypatch.setattr(session_registry, "_get_registry_path", lambda: registry_file)

    return mod


def _seed_contract_rows(rows):
    """Create the minimal agent_contract_handoffs shape this module reads.

    ``rows`` is an iterable of ``(contract_id, session_id)``.
    """
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, agent_state text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs (contract_id, session_id, agent_state) "
        "values (?, ?, 'COMPLETE')",
        list(rows),
    )
    con.commit()
    con.close()


def _write_registry_entry(tmp_path, session_id, last_heartbeat):
    import json

    registry_file = tmp_path / "session_registry.json"
    registry_file.write_text(
        json.dumps(
            {
                "sessions": {
                    session_id: {
                        "started_at": "2026-01-01T00:00:00+00:00",
                        "is_headless": False,
                        "last_heartbeat": last_heartbeat,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Case 1 -- fresh heartbeat reads ALIVE.
# ---------------------------------------------------------------------------

def test_fresh_heartbeat_reads_alive(liveness_mod, tmp_path):
    _seed_contract_rows([(CONTRACT_ALIVE, "sess-alive")])
    _write_registry_entry(tmp_path, "sess-alive", last_heartbeat=time.time() - 30)

    assert liveness_mod.session_liveness_for_contract(CONTRACT_ALIVE) == (
        liveness_mod.SessionLiveness.ALIVE
    )


# ---------------------------------------------------------------------------
# Case 2 -- stale (past-TTL) heartbeat reads DEAD.
# ---------------------------------------------------------------------------

def test_stale_heartbeat_reads_dead(liveness_mod, tmp_path):
    from hooks.modules.session.session_registry import HEARTBEAT_TTL_SECONDS

    _seed_contract_rows([(CONTRACT_DEAD, "sess-dead")])
    _write_registry_entry(
        tmp_path,
        "sess-dead",
        last_heartbeat=time.time() - (HEARTBEAT_TTL_SECONDS + 60),
    )

    assert liveness_mod.session_liveness_for_contract(CONTRACT_DEAD) == (
        liveness_mod.SessionLiveness.DEAD
    )


# ---------------------------------------------------------------------------
# Case 3 -- illegible or absent reads UNKNOWN, never DEAD (the adversarial
# assertion). Each sub-case below is a different route to "no evidence" and
# each must resolve to UNKNOWN on its own.
# ---------------------------------------------------------------------------

class TestUnknownNeverReadsDead:
    def test_registry_file_absent(self, liveness_mod, tmp_path):
        _seed_contract_rows([(CONTRACT_UNKNOWN_SESSION, "sess-no-file")])
        # No _write_registry_entry call -- the file never exists.

        result = liveness_mod.session_liveness_for_contract(CONTRACT_UNKNOWN_SESSION)

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD

    def test_registry_file_corrupt(self, liveness_mod, tmp_path):
        session_id = "sess-corrupt"
        _seed_contract_rows([(CONTRACT_UNKNOWN_SESSION, session_id)])
        (tmp_path / "session_registry.json").write_text(
            "{not valid json", encoding="utf-8"
        )

        result = liveness_mod.session_liveness_for_contract(CONTRACT_UNKNOWN_SESSION)

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD

    def test_no_entry_for_this_session(self, liveness_mod, tmp_path):
        _seed_contract_rows([(CONTRACT_UNKNOWN_SESSION, "sess-absent-entry")])
        # A registry that IS readable but never heard of this session id --
        # e.g. already swept, or never registered.
        _write_registry_entry(tmp_path, "some-other-session", last_heartbeat=time.time())

        result = liveness_mod.session_liveness_for_contract(CONTRACT_UNKNOWN_SESSION)

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD

    def test_legacy_entry_with_no_heartbeat(self, liveness_mod, tmp_path):
        import json

        session_id = "sess-legacy"
        _seed_contract_rows([(CONTRACT_UNKNOWN_SESSION, session_id)])
        registry_file = tmp_path / "session_registry.json"
        registry_file.write_text(
            json.dumps({"sessions": {session_id: {"pid": 12345}}}),
            encoding="utf-8",
        )

        result = liveness_mod.session_liveness_for_contract(CONTRACT_UNKNOWN_SESSION)

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD

    def test_contract_row_absent(self, liveness_mod, tmp_path):
        # No _seed_contract_rows call at all -- the table doesn't even exist.
        result = liveness_mod.session_liveness_for_contract("no-such-contract")

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD

    def test_contract_row_with_no_session_id(self, liveness_mod, tmp_path):
        _seed_contract_rows([(CONTRACT_NO_SESSION, None)])

        result = liveness_mod.session_liveness_for_contract(CONTRACT_NO_SESSION)

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD

    def test_session_registry_module_unimportable(
        self, liveness_mod, tmp_path, monkeypatch
    ):
        """The hooks package cannot be imported at all (partial install)."""
        import builtins

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "hooks.modules.session.session_registry" or name.startswith(
                "hooks.modules.session.session_registry"
            ):
                raise ImportError("simulated partial install")
            return real_import(name, *args, **kwargs)

        _seed_contract_rows([(CONTRACT_UNKNOWN_SESSION, "sess-unimportable")])
        monkeypatch.setattr(builtins, "__import__", _blocked_import)

        result = liveness_mod.session_liveness_for_contract(CONTRACT_UNKNOWN_SESSION)

        assert result == liveness_mod.SessionLiveness.UNKNOWN
        assert result != liveness_mod.SessionLiveness.DEAD
