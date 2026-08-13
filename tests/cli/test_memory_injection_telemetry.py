"""
P1 injection telemetry, wired into `gaia memory get-relevant` (digest,
sections, legacy --types) -- telemetria-de-uso-en-memoria-curada, task 6.

Property under test, not a command list: an AUTOMATIC-INJECTION surface
renders a bounded, capped context block nobody named a row for. Every
renderer here SELECTS more rows from the DB than it ultimately EMITS (quota
caps, char-budget trims), and ``injection_count`` must bump exactly once per
row EFFECTIVELY RENDERED -- never once per row merely selected. Counting the
selection instead of the emission is the trap this task exists to avoid: the
number still looks plausible, so a wrong count would ship silently.

The --initiative retrieval surface (task 5) stays wired to "deliberate" and
must never move ``injection_count`` here either -- mixing the two families
would let a row already inside the injected block reinforce itself every
time it is shown, freezing the ranking (P1's central design decision).

Uses a real temporary SQLite DB (writer._connect materialises the schema on
first connect), mirroring the fixture pattern already used by
test_memory_deliberate_telemetry.py / test_memory_initiative_digest.py /
test_memory_get_relevant_v4.py.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli.memory as memory_mod  # noqa: E402

_WORKSPACE = "testws"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Real SQLite DB at tmp_path/gaia.db, routed through writer._connect."""
    db_path = tmp_path / "gaia.db"

    from gaia.store import writer as _w
    from gaia import paths as _paths

    monkeypatch.setattr(_paths, "db_path", lambda: db_path)
    monkeypatch.setattr(_w, "_db_path", lambda: db_path)

    con = _w._connect(db_path)
    con.execute(
        "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
        "VALUES (?, ?, ?)",
        (_WORKSPACE, _WORKSPACE, "2026-08-12T00:00:00Z"),
    )
    con.commit()
    con.close()
    return db_path


def _insert(db_path, name, *, type_="atom", class_="thread", status="open",
            desc="d", body="b", updated_at="2026-08-12T00:00:00Z",
            initiative=None, workspace=_WORKSPACE):
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, "
        "                    updated_at, class, status, initiative) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workspace, name, type_, desc, body, updated_at, class_, status,
         initiative),
    )
    con.commit()
    con.close()


def _row(db_path, name, workspace=_WORKSPACE):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT injection_count, deliberate_count, last_injected_at, "
            "       last_deliberate_at, updated_at "
            "FROM memory WHERE workspace=? AND name=?",
            (workspace, name),
        ).fetchone())
    finally:
        con.close()


def _history_count(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM memory_history").fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Digest mode (no flags) -- AC-4
# ---------------------------------------------------------------------------

class TestDigestModeInjection:
    def _args(self, **overrides):
        base = {
            "workspace": _WORKSPACE, "limit": 8, "max_chars": 1500,
            "types": None, "sections": None, "initiative": None,
            "json": True, "func": memory_mod._cmd_get_relevant,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_only_the_rendered_top_row_bumps_injection(self, tmp_db, capsys):
        """One initiative, three pending rows: only the freshest ("top") one
        gets its own bullet -- the other two are only counted in the
        "+N más" hint, never individually rendered. This is the required
        selected-more-than-emitted demonstration (AC criterion 1)."""
        _insert(tmp_db, "gaia_a", initiative="gaia",
                updated_at="2026-08-12T15:00:00Z")
        _insert(tmp_db, "gaia_b", initiative="gaia",
                updated_at="2026-08-12T14:00:00Z")
        _insert(tmp_db, "gaia_c", initiative="gaia",
                updated_at="2026-08-12T13:00:00Z")
        before = {n: _row(tmp_db, n) for n in ("gaia_a", "gaia_b", "gaia_c")}
        before_history = _history_count(tmp_db)

        rc = memory_mod._cmd_get_relevant(self._args())
        payload = json.loads(capsys.readouterr().out)

        after = {n: _row(tmp_db, n) for n in ("gaia_a", "gaia_b", "gaia_c")}
        after_history = _history_count(tmp_db)

        assert rc == 0
        assert [i["name"] for i in payload["items"]] == ["gaia_a"]
        assert "+2 más en gaia" in payload["block"]

        # SELECTED (3 rows fetched, pending_count=3) but only 1 EMITTED.
        assert payload["items"][0]["pending_count"] == 3

        assert after["gaia_a"]["injection_count"] == \
            before["gaia_a"]["injection_count"] + 1
        assert after["gaia_a"]["last_injected_at"] is not None
        for name in ("gaia_b", "gaia_c"):
            assert after[name]["injection_count"] == \
                before[name]["injection_count"], (
                f"{name} was selected but never rendered -- must not bump"
            )
            assert after[name]["last_injected_at"] is None

        for name in ("gaia_a", "gaia_b", "gaia_c"):
            assert after[name]["deliberate_count"] == \
                before[name]["deliberate_count"] == 0
            assert after[name]["updated_at"] == before[name]["updated_at"]
        assert after_history == before_history

    def test_digest_second_call_renders_byte_identical_block(
        self, tmp_db, capsys,
    ):
        """The first call's telemetry write (injection_count, last_injected_at)
        must not perturb ordering, selection, or trimming: calling the digest
        twice in a row renders the SAME block both times (AC criterion 5,
        narrowed to the property a unit test can pin: measuring never
        changes what is measured)."""
        _insert(tmp_db, "gaia_open", initiative="gaia",
                updated_at="2026-08-12T10:00:00Z")
        _insert(tmp_db, "balance_carry", class_="thread", status="carry_forward",
                initiative="balance", updated_at="2026-08-12T09:00:00Z")

        args = self._args(json=False)
        memory_mod._cmd_get_relevant(args)
        first = capsys.readouterr().out
        memory_mod._cmd_get_relevant(args)
        second = capsys.readouterr().out

        assert first == second

    def test_digest_degrades_when_telemetry_raises(self, tmp_db, capsys):
        """Best-effort: a telemetry failure must never break block assembly
        (AC criterion 6)."""
        _insert(tmp_db, "gaia_open", initiative="gaia",
                updated_at="2026-08-12T10:00:00Z")

        with mock.patch(
            "gaia.store.writer.record_memory_access",
            side_effect=RuntimeError("boom"),
        ):
            rc = memory_mod._cmd_get_relevant(self._args())
        payload = json.loads(capsys.readouterr().out)

        assert rc == 0
        assert payload["items"][0]["name"] == "gaia_open"
        assert "Pendientes vivos por proyecto" in payload["block"]


# ---------------------------------------------------------------------------
# Sections mode (--sections=...) -- AC-4
# ---------------------------------------------------------------------------

class TestSectionsModeInjection:
    def _args(self, **overrides):
        base = {
            "workspace": _WORKSPACE, "limit": 8, "max_chars": 560,
            "types": None, "sections": "anchor,thread_open",
            "initiative": None, "json": True,
            "func": memory_mod._cmd_get_relevant,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _seed_trim_scenario(self, tmp_db):
        # A tight budget forces the char-budget trim to drop exactly the
        # freshest thread_open row (trimming removes the LAST bullet in a
        # section span, and section rows are fetched stalest-first) while
        # the anchor and the stalest thread_open row both survive. Verified
        # empirically against the real renderer before being pinned here.
        _insert(tmp_db, "anchor_1", class_="anchor", status=None,
                desc="y" * 60, updated_at="2026-08-12T09:00:00Z")
        _insert(tmp_db, "open_old", class_="thread", status="open",
                desc="z" * 80, updated_at="2026-08-12T08:00:00Z")
        _insert(tmp_db, "open_new", class_="thread", status="open",
                desc="w" * 80, updated_at="2026-08-12T10:00:00Z")

    def test_char_budget_trim_only_bumps_survivors(self, tmp_db, capsys):
        """SELECTS 3 rows (all three matched the section queries) but the
        char budget forces a trim, so fewer than 3 are actually EMITTED.
        Only the survivors bump injection (AC criterion 1)."""
        self._seed_trim_scenario(tmp_db)
        before = {n: _row(tmp_db, n)
                  for n in ("anchor_1", "open_old", "open_new")}
        before_history = _history_count(tmp_db)

        rc = memory_mod._cmd_get_relevant(self._args())
        payload = json.loads(capsys.readouterr().out)
        block = payload["block"]

        after = {n: _row(tmp_db, n)
                  for n in ("anchor_1", "open_old", "open_new")}
        after_history = _history_count(tmp_db)

        assert rc == 0
        # The trap: items_flat (JSON payload) still names all 3 -- selection,
        # not emission. The rendered TEXT block is the ground truth for what
        # was actually shown.
        assert {i["name"] for i in payload["items"]} == \
            {"anchor_1", "open_old", "open_new"}
        assert "open_new" not in block, (
            "the freshest thread_open row must be the one the char budget "
            "trims here -- fixture drifted from the pinned scenario"
        )
        assert "anchor_1" in block and "open_old" in block

        for name in ("anchor_1", "open_old"):
            assert after[name]["injection_count"] == \
                before[name]["injection_count"] + 1
            assert after[name]["last_injected_at"] is not None
        assert after["open_new"]["injection_count"] == \
            before["open_new"]["injection_count"], (
            "open_new was SELECTED (present in items_flat) but never "
            "EMITTED (trimmed from the block) -- must not bump"
        )
        assert after["open_new"]["last_injected_at"] is None

        for name in ("anchor_1", "open_old", "open_new"):
            assert after[name]["deliberate_count"] == \
                before[name]["deliberate_count"] == 0
            assert after[name]["updated_at"] == before[name]["updated_at"]
        assert after_history == before_history

    def test_sections_second_call_renders_byte_identical_block(
        self, tmp_db, capsys,
    ):
        self._seed_trim_scenario(tmp_db)
        args = self._args(json=False)
        memory_mod._cmd_get_relevant(args)
        first = capsys.readouterr().out
        memory_mod._cmd_get_relevant(args)
        second = capsys.readouterr().out
        assert first == second

    def test_sections_degrades_when_telemetry_raises(self, tmp_db, capsys):
        self._seed_trim_scenario(tmp_db)
        with mock.patch(
            "gaia.store.writer.record_memory_access",
            side_effect=RuntimeError("boom"),
        ):
            rc = memory_mod._cmd_get_relevant(self._args())
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert "anchor_1" in payload["block"]


# ---------------------------------------------------------------------------
# Legacy --types=... mode -- AC-4
# ---------------------------------------------------------------------------

class TestLegacyTypesModeInjection:
    def _args(self, **overrides):
        base = {
            "workspace": _WORKSPACE, "limit": 8, "max_chars": 250,
            "types": "atom", "sections": None, "initiative": None,
            "json": True, "func": memory_mod._cmd_get_relevant,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def _seed(self, tmp_db):
        for i in range(3):
            _insert(tmp_db, f"atom_{i}", desc="a" * 100,
                    updated_at=f"2026-08-12T{10+i:02d}:00:00Z")

    def test_char_budget_trim_only_bumps_survivors(self, tmp_db, capsys):
        self._seed(tmp_db)
        before = {n: _row(tmp_db, n) for n in ("atom_0", "atom_1", "atom_2")}

        rc = memory_mod._cmd_get_relevant(self._args())
        payload = json.loads(capsys.readouterr().out)
        block = payload["block"]

        after = {n: _row(tmp_db, n) for n in ("atom_0", "atom_1", "atom_2")}

        assert rc == 0
        assert len(payload["items"]) == 3  # all 3 selected (stale JSON list)
        # Only the freshest survives the tight budget.
        assert "atom_2" in block
        assert "atom_0" not in block and "atom_1" not in block

        assert after["atom_2"]["injection_count"] == \
            before["atom_2"]["injection_count"] + 1
        for name in ("atom_0", "atom_1"):
            assert after[name]["injection_count"] == \
                before[name]["injection_count"]
        for name in ("atom_0", "atom_1", "atom_2"):
            assert after[name]["deliberate_count"] == 0
            assert after[name]["updated_at"] == before[name]["updated_at"]

    def test_legacy_types_second_call_renders_byte_identical_block(
        self, tmp_db, capsys,
    ):
        self._seed(tmp_db)
        args = self._args(json=False)
        memory_mod._cmd_get_relevant(args)
        first = capsys.readouterr().out
        memory_mod._cmd_get_relevant(args)
        second = capsys.readouterr().out
        assert first == second


# ---------------------------------------------------------------------------
# Cross-surface separation: --initiative is DELIBERATE, never injection
# ---------------------------------------------------------------------------

class TestInitiativeModeStaysDeliberateOnly:
    """The confusion this task warns about: --initiative shares the
    get-relevant command family with digest/sections/types, but it is the
    DELIBERATE retrieval surface task 5 already wired. It must never move
    injection_count -- mixing the two would let an already-injected row
    reinforce itself, freezing the ranking."""

    def test_initiative_mode_never_bumps_injection(self, tmp_db, capsys):
        _insert(tmp_db, "demo_only", initiative="demoproj",
                class_="thread", status="open")
        before = _row(tmp_db, "demo_only")

        args = SimpleNamespace(
            workspace=_WORKSPACE, limit=8, max_chars=1500, types=None,
            sections=None, initiative="demoproj", json=True,
            func=memory_mod._cmd_get_relevant,
        )
        rc = memory_mod._cmd_get_relevant(args)
        capsys.readouterr()

        after = _row(tmp_db, "demo_only")

        assert rc == 0
        assert after["injection_count"] == before["injection_count"] == 0
        assert after["deliberate_count"] == before["deliberate_count"] + 1
