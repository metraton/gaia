#!/usr/bin/env python3
"""Tests for the re-init-proof skip-file matching in mutkill_approval_grants.py.

ROOT CAUSE THIS GUARDS AGAINST
------------------------------
`cosmic-ray init` regenerates a fresh uuid4 `job_id` for every mutant on every
run. The equivalents-*.skip files used to list bare job_ids, so after a re-init
the listed ids matched NOTHING in the new session and silently excluded zero
mutants -- a false "100% killable". The fix keys exclusion on the mutant's
STABLE identity (operator + source span + occurrence), which init preserves.

These tests build two synthetic sessions with the SAME mutants but DIFFERENT
job_ids (exactly what a re-init produces) and prove a stable-id skip excludes
the same logical mutant in BOTH -- the property the old job_id matching lacked.
"""

import sqlite3
import uuid
from pathlib import Path

import pytest

import importlib.util

_HARNESS = (
    Path(__file__).resolve().parent / "mutkill_approval_grants.py"
)
_spec = importlib.util.spec_from_file_location("mutkill_harness", _HARNESS)
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)


# The four logical mutants every synthetic session contains. job_id is assigned
# fresh per-session (mimicking re-init); everything else is the stable identity.
_MUTANTS = [
    # (operator_name, occurrence, start_row, start_col, end_row, end_col)
    ("core/ReplaceComparisonOperator_Eq_Is", 0, 40, 20, 40, 22),
    ("core/RemoveDecorator", 2, 79, 0, 80, 0),
    ("core/NumberReplacer", 1, 79, 19, 79, 22),
    ("core/ReplaceBreakWithContinue", 0, 193, 12, 193, 17),
]


def _make_session(path: Path) -> dict:
    """Create a synthetic cosmic-ray session with the 4 mutants, each given a
    FRESH random job_id. Returns {stable_id: job_id} for assertions."""
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE mutation_specs ("
        "module_path VARCHAR, operator_name VARCHAR, operator_args JSON, "
        "occurrence INTEGER, start_pos_row INTEGER, start_pos_col INTEGER, "
        "end_pos_row INTEGER, end_pos_col INTEGER, definition_name VARCHAR, "
        "job_id VARCHAR NOT NULL, PRIMARY KEY (job_id))"
    )
    mapping = {}
    for (op, occ, sr, sc, er, ec) in _MUTANTS:
        jid = uuid.uuid4().hex  # fresh every session -> mimics re-init
        con.execute(
            "INSERT INTO mutation_specs (module_path, operator_name, "
            "operator_args, occurrence, start_pos_row, start_pos_col, "
            "end_pos_row, end_pos_col, definition_name, job_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("hooks/modules/security/tiers.py", op, "{}", occ, sr, sc, er, ec,
             None, jid),
        )
        sid = mk.stable_id(
            {"operator_name": op, "occurrence": occ,
             "start_pos": (sr, sc), "end_pos": (er, ec)}
        )
        mapping[sid] = jid
    con.commit()
    con.close()
    return mapping


def test_stable_id_is_deterministic_from_ast_fields():
    """stable_id() depends only on operator + span + occurrence, never job_id."""
    a = mk.stable_id({"operator_name": "core/NumberReplacer", "occurrence": 1,
                      "start_pos": (79, 19), "end_pos": (79, 22)})
    b = mk.stable_id({"operator_name": "core/NumberReplacer", "occurrence": 1,
                      "start_pos": (79, 19), "end_pos": (79, 22)})
    assert a == b
    assert a == "core/NumberReplacer|79:19-79:22|1"
    # A different occurrence (the disambiguator for same-span mutants) differs.
    c = mk.stable_id({"operator_name": "core/NumberReplacer", "occurrence": 0,
                      "start_pos": (79, 19), "end_pos": (79, 22)})
    assert c != a


def test_parse_skip_file_classifies_stable_vs_legacy(tmp_path):
    """A 32-hex token is legacy job_id; anything else is a stable id. Inline
    notes after the token are ignored."""
    skip = tmp_path / "equiv.skip"
    legacy_jid = uuid.uuid4().hex  # 32 hex
    skip.write_text(
        "# a comment\n"
        "\n"
        "core/NumberReplacer|79:19-79:22|1   # transparent maxsize\n"
        f"{legacy_jid}\n"
        "core/RemoveDecorator|79:0-80:0|2\n",
        encoding="utf-8",
    )
    stable, legacy = mk.parse_skip_file(skip)
    assert stable == {
        "core/NumberReplacer|79:19-79:22|1",
        "core/RemoveDecorator|79:0-80:0|2",
    }
    assert legacy == {legacy_jid}


def test_stable_skip_survives_reinit(tmp_path):
    """THE CORE PROPERTY: a stable-id skip excludes the SAME logical mutant in
    two sessions whose job_ids differ (a re-init). Legacy job_id matching would
    exclude it in the first session and ZERO in the second."""
    s1 = tmp_path / "session1.sqlite"
    s2 = tmp_path / "session2.sqlite"
    map1 = _make_session(s1)
    map2 = _make_session(s2)

    # Sanity: same stable ids, DIFFERENT job_ids across the two sessions.
    assert set(map1) == set(map2)
    assert set(map1.values()).isdisjoint(set(map2.values())), \
        "job_ids must differ across sessions to model a re-init"

    target_sid = "core/NumberReplacer|79:19-79:22|1"
    skip = tmp_path / "equiv.skip"
    skip.write_text(target_sid + "\n", encoding="utf-8")
    stable, legacy = mk.parse_skip_file(skip)

    specs1 = mk.load_specs(s1)
    specs2 = mk.load_specs(s2)

    excl1, unmatched1 = mk.compute_skip_jobids(specs1, stable, legacy)
    excl2, unmatched2 = mk.compute_skip_jobids(specs2, stable, legacy)

    # Resolved to the correct (different) job_id in each session, nothing stale.
    assert excl1 == {map1[target_sid]}
    assert excl2 == {map2[target_sid]}
    assert excl1 != excl2  # different uuid per session
    assert unmatched1 == [] and unmatched2 == []


def test_legacy_jobid_goes_stale_after_reinit(tmp_path):
    """Demonstrates the bug the fix prevents: a legacy job_id from session1
    matches NOTHING in session2 (the silent zero-exclusion). The harness now
    surfaces it as `unmatched` instead of pretending it applied."""
    s1 = tmp_path / "session1.sqlite"
    s2 = tmp_path / "session2.sqlite"
    map1 = _make_session(s1)
    _make_session(s2)

    target_sid = "core/RemoveDecorator|79:0-80:0|2"
    stale_jid = map1[target_sid]  # a valid id in s1 only
    skip = tmp_path / "equiv.skip"
    skip.write_text(stale_jid + "\n", encoding="utf-8")
    stable, legacy = mk.parse_skip_file(skip)
    assert legacy == {stale_jid} and stable == set()

    specs2 = mk.load_specs(s2)
    excl2, unmatched2 = mk.compute_skip_jobids(specs2, stable, legacy)
    assert excl2 == set()          # silently excluded NOTHING -> the old bug
    assert unmatched2 == [stale_jid]  # now surfaced, not hidden


def test_unmatched_stable_id_is_reported(tmp_path):
    """A stable id whose span no longer exists (source moved) is reported as
    unmatched, never silently dropped."""
    s1 = tmp_path / "session1.sqlite"
    _make_session(s1)
    skip = tmp_path / "equiv.skip"
    skip.write_text("core/NumberReplacer|999:0-999:1|0\n", encoding="utf-8")
    stable, legacy = mk.parse_skip_file(skip)
    specs = mk.load_specs(s1)
    excl, unmatched = mk.compute_skip_jobids(specs, stable, legacy)
    assert excl == set()
    assert unmatched == ["core/NumberReplacer|999:0-999:1|0"]
