"""Adversarial suite against the state-based retention rules.

``tests/retention/test_fs_rules.py`` establishes that the rules WORK. This
file establishes the opposite kind of fact -- that they cannot be MADE to
work when they must not. Every test here is an attempt to force a selection
the policy is supposed to refuse, so each one asserts a negative property:
zero selections, and the file still on disk afterwards.

The property under attack, stated once: **absence of evidence must never
become evidence of disposability.** A rule that cannot read the DB, cannot
recognize an owner, or cannot tell whether a turn is still alive has learned
nothing -- and "nothing" is not permission to delete.

Three attack surfaces, matching the three cases the criterion names:

  (a) the evidence of a closed and verified AC -- reachable only by pointing
      a sweep at the evidence store, or by making a referenced blob look
      unreferenced;
  (b) the scratch of a contract with no terminal row -- the cut-agent case,
      where the ONLY record that the turn ever ran may be the files it left;
  (c) the same scenarios with gaia.db illegible -- absent, corrupt,
      unopenable, or missing its table.

Case (c) carries the DELIBERATE DIVERGENCE from the precedent this policy
otherwise imitates. ``gaia.contract.drafts.collectable_drafts`` degrades, on
an unreadable DB, to an AGE-ONLY lane that KEEPS COLLECTING -- sound there
because a draft is a copy of a durable row. ``test_illegible_db_diverges_
from_the_drafts_precedent`` pins the divergence by running BOTH policies
against the SAME corrupt DB in the same test and asserting they disagree:
the drafts policy still collects, these rules collect nothing.

Case (b) also pins the SECOND divergence axis: a turn that PAUSED
(``APPROVAL_REQUEST``, ``NEEDS_INPUT``, ``BLOCKED``, ``NEEDS_VERIFICATION``)
closed its own contract but has not ended -- it will resume under the SAME
id -- so it must never be collected either, however long the grace window.
``test_paused_non_terminal_turn_is_never_collected`` pins that the rule reads
``gaia.state.TERMINAL_PLAN_STATUSES``, not the wider
``CLOSED_TURN_PLAN_STATUSES`` a contract's own close would satisfy.
"""

import importlib
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

AGENT_A = valid_agent_id("adv-a1")
AGENT_B = valid_agent_id("adv-b2")
HOUR = 3600.0
DAY = 86400.0
DECADE = 3650 * DAY

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
skip_if_root = pytest.mark.skipif(
    _IS_ROOT, reason="root bypasses filesystem permission bits"
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

@pytest.fixture()
def fs_rules(tmp_path, monkeypatch):
    """Redirect Gaia's data substrate to tmp and return the policy module."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    import gaia.retention.fs_rules as mod

    importlib.reload(mod)
    return mod


def _cleanup_module():
    bin_dir = _REPO_ROOT / "bin"
    if str(bin_dir) not in sys.path:
        sys.path.insert(0, str(bin_dir))
    import cli.cleanup as cleanup_mod

    return cleanup_mod


def _db():
    from gaia.paths import db_path

    return Path(db_path())


def _seed_rows(rows):
    """Create the handoff shape the policy reads.

    ``rows`` is an iterable of ``(contract_id, session_id, agent_state,
    cut_reason)`` -- the cut marker is carried because several attacks below
    turn on the difference between a turn that closed itself and one that was
    closed for it.
    """
    con = sqlite3.connect(str(_db()))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, "
        " agent_state text, cut_reason text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs "
        "(contract_id, session_id, agent_state, cut_reason) values (?, ?, ?, ?)",
        list(rows),
    )
    con.commit()
    con.close()


def _seed_evidence_row(artifact_path):
    con = sqlite3.connect(str(_db()))
    con.execute(
        "create table if not exists evidence "
        "(id integer primary key, brief_id integer, ac_id text, "
        " artifact_path text)"
    )
    con.execute(
        "insert into evidence (brief_id, ac_id, artifact_path) values (?, ?, ?)",
        (1, "AC-6", str(artifact_path)),
    )
    con.commit()
    con.close()


def _touch(path: Path, age_seconds: float = 0.0, is_dir: bool = False):
    if is_dir:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    if age_seconds:
        when = time.time() - age_seconds
        os.utime(path, (when, when))
    return path


def _corrupt_the_db():
    """Leave gaia.db PRESENT and OPENABLE but not a database.

    This is the case the criterion distinguishes from an absent DB: the file
    exists, ``_ro_db_connect`` returns a live connection, and the failure only
    surfaces when a statement is executed. A rule that only guarded the
    "no file" path would sail straight into this one.
    """
    path = _db()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not a sqlite database, not even close\n" * 64)
    return path


def _deposited_evidence(closed_contract_id: str, age_seconds: float = DECADE):
    """A blob in the canonical evidence store, referenced by a row.

    Shaped like ``gaia/evidence/fs.py::blob_path_for`` --
    ``evidence_dir()/{workspace}/{brief}/{ac}/{uuid}.{ext}`` -- and aged past
    every threshold in the policy, so nothing but the rules themselves is
    standing between it and a sweep.
    """
    from gaia.paths import evidence_dir

    blob = evidence_dir() / "me" / "lo-que-gaia-crea" / "AC-6" / f"{uuid.uuid4()}.txt"
    _touch(blob, age_seconds=age_seconds)
    _seed_evidence_row(blob)
    _seed_rows([(closed_contract_id, None, "COMPLETE", None)])
    return blob


# ===========================================================================
# (a) The evidence of a closed and verified AC
# ===========================================================================

def test_attack_no_retention_rule_is_ever_pointed_at_the_evidence_store(fs_rules, tmp_path):
    """Structural: no sweepable root contains -- or is contained by -- evidence.

    The cheapest way to destroy verified evidence is not a bug inside a rule,
    it is a rule aimed one directory too high. This asserts the aim, for every
    root the retention table resolves, rather than trusting the table's shape.
    """
    from gaia.paths import evidence_dir

    cleanup_mod = _cleanup_module()
    evidence = evidence_dir().resolve()
    evidence.mkdir(parents=True, exist_ok=True)

    roots = [
        (policy["key"], policy["dir_fn"]())
        for policy in cleanup_mod.RETENTION_POLICY
        if "dir_fn" in policy
    ]
    assert roots, "the turn-scoped rules must resolve at least one root"

    for key, root in roots:
        resolved = Path(root).resolve()
        assert resolved != evidence, f"{key} sweeps the evidence store itself"
        assert evidence not in resolved.parents, f"{key} sweeps inside evidence"
        assert resolved not in evidence.parents, f"{key} sweeps a parent of evidence"


def test_attack_full_sweep_cannot_select_verified_evidence(fs_rules, tmp_path):
    """The whole retention pass runs, selects, and still never names a blob.

    A sweep that selected nothing at all would prove nothing -- so this seeds
    a genuinely collectible scratch entry alongside the evidence. The sweep
    fires, takes the scratch, and leaves the blob.
    """
    from gaia.paths import evidence_dir, scratch_dir

    closed_id = f"{AGENT_A}.aa11bb"
    blob = _deposited_evidence(closed_id)
    collectible = _touch(scratch_dir() / closed_id, age_seconds=48 * HOUR, is_dir=True)

    actions = _cleanup_module()._apply_retention_policy(tmp_path / "workspace", dry_run=False)

    selected = {a["path"] for a in actions}
    assert str(collectible) in selected, "the sweep must actually have run"
    assert blob.exists(), "verified evidence survived the sweep"
    evidence_root = str(evidence_dir().resolve())
    assert not [p for p in selected if str(Path(p).resolve()).startswith(evidence_root)]


def test_attack_symlink_from_scratch_into_the_evidence_store(fs_rules):
    """A scratch entry named by a closed contract, pointing at evidence.

    ``collectable_turn_scoped`` decides delete-dir vs delete-file with
    ``entry.is_dir()``, which FOLLOWS the link, and the CLI then hands the
    path to ``shutil.rmtree``. The attack is to make that rmtree land on the
    AC's evidence directory. What must survive is the TARGET.
    """
    from gaia.paths import evidence_dir, scratch_dir

    closed_id = f"{AGENT_A}.cc22dd"
    blob = _deposited_evidence(closed_id)
    ac_dir = blob.parent

    scratch_dir().mkdir(parents=True, exist_ok=True)
    link = scratch_dir() / closed_id
    link.symlink_to(ac_dir, target_is_directory=True)
    old = time.time() - 48 * HOUR
    os.utime(link, (old, old), follow_symlinks=False)
    os.utime(ac_dir, (old, old))

    _cleanup_module()._prune_turn_scoped(scratch_dir(), "Scratch", dry_run=False)

    assert blob.exists(), "the sweep followed a symlink out of scratch"
    assert ac_dir.is_dir(), "the AC's evidence directory was destroyed"


def test_attack_symlink_to_a_single_evidence_blob(fs_rules):
    """Same attack at file granularity: unlink must hit the link, not the blob."""
    from gaia.paths import scratch_dir

    closed_id = f"{AGENT_B}.ee33ff"
    blob = _deposited_evidence(closed_id)

    scratch_dir().mkdir(parents=True, exist_ok=True)
    link = scratch_dir() / f"{closed_id}.txt"
    link.symlink_to(blob)
    old = time.time() - 48 * HOUR
    os.utime(link, (old, old), follow_symlinks=False)

    _cleanup_module()._prune_turn_scoped(scratch_dir(), "Scratch", dry_run=False)

    assert blob.exists(), "unlink resolved the link and took the blob"


def test_attack_corrupt_db_cannot_make_referenced_evidence_look_orphaned(fs_rules):
    """The sharpest form of the property, on the evidence side.

    ``find_orphan_blobs`` answers "which blobs does no row reference?" -- and
    a DB it cannot query answers that question with EVERY blob. Degrading to
    "all of them are orphans" is precisely absence-of-evidence promoted to
    evidence-of-disposability, so the sweep must return nothing instead.
    """
    from gaia.evidence.orphans import find_orphan_blobs
    from gaia.paths import evidence_dir

    blob = _deposited_evidence(f"{AGENT_A}.4400aa")
    assert find_orphan_blobs(evidence_dir()) == [], "a referenced blob is not an orphan"

    _corrupt_the_db()

    assert find_orphan_blobs(evidence_dir()) == []
    assert blob.exists()


# ===========================================================================
# (b) The scratch of a contract with no terminal row -- the cut agent
# ===========================================================================

def test_attack_cut_agent_left_no_row_at_all(fs_rules):
    """The hardest cut: the turn died before anything was written for it."""
    from gaia.paths import scratch_dir

    entry = _touch(scratch_dir() / f"{AGENT_A}.dead01", age_seconds=DECADE, is_dir=True)

    assert fs_rules.collectable_turn_scoped(scratch_dir()) == []
    assert entry.exists()


def test_attack_cut_agent_born_row_never_left_dispatched(fs_rules):
    """The measured population: a born row the agent never converged.

    ``insert_dispatched_handoff`` births rows at ``agent_state='DISPATCHED'``
    with ``cut_reason='never_finalized'``; a harness cut hard enough that
    SubagentStop never fires leaves them exactly there. DISPATCHED is not a
    member of ``VALID_PLAN_STATUSES`` at all, so it can never be read as a
    close -- assert that directly, not just the emptiness of the result.
    """
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.dead02"
    entry = _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True)
    _seed_rows([(contract_id, "s-cut", "DISPATCHED", "never_finalized")])

    assert fs_rules.collectable_turn_scoped(scratch_dir()) == []
    assert fs_rules._closed_contract_ids({contract_id}) == set()
    assert entry.exists()


@pytest.mark.parametrize("state", ["IN_PROGRESS", "", None, "complete", "COMPLETED", "done"])
def test_attack_ambiguous_or_unrecognized_state_never_reads_as_closed(fs_rules, state):
    """Anything that is not exactly a declared close is not a close.

    Includes the near-misses an attacker would reach for -- lowercase, a
    plausible synonym, an empty string, a NULL -- because each one is a way of
    asking the rule to guess. It must not.
    """
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.dead03"
    entry = _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True)
    _seed_rows([(contract_id, None, state, None)])

    assert fs_rules.collectable_turn_scoped(scratch_dir()) == []
    assert entry.exists()


def test_attack_a_closed_sibling_does_not_cover_an_open_contract(fs_rules):
    """Selection is per-id: one closed neighbour is not a licence for the rest."""
    from gaia.paths import scratch_dir

    open_id = f"{AGENT_A}.0aaaaa"
    closed_id = f"{AGENT_B}.0bbbbb"
    kept = _touch(scratch_dir() / open_id, age_seconds=DECADE, is_dir=True)
    _touch(scratch_dir() / closed_id, age_seconds=DECADE, is_dir=True)
    _seed_rows([(closed_id, None, "COMPLETE", None)])

    selected = {r["path"] for r in fs_rules.collectable_turn_scoped(scratch_dir())}

    assert str(kept) not in selected
    assert kept.exists()


def test_attack_closed_id_nested_below_an_open_owner_is_out_of_reach(fs_rules):
    """Only direct children are candidates -- nesting cannot smuggle a target in."""
    from gaia.paths import scratch_dir

    open_id = f"{AGENT_A}.1aaaaa"
    closed_id = f"{AGENT_B}.1bbbbb"
    nested = _touch(
        scratch_dir() / open_id / closed_id, age_seconds=DECADE, is_dir=True
    )
    _seed_rows([(closed_id, None, "COMPLETE", None)])

    assert fs_rules.collectable_turn_scoped(scratch_dir()) == []
    assert nested.exists()


@pytest.mark.parametrize(
    "name",
    [
        # uppercase hex -- shaped like a contract id to a human, not to the rule
        "A0123456789ABCDEF.ABCDEF",
        # one hex digit short of the agent-id floor
        "a0123456789abcde.abcdef",
        # a second suffix: only ONE is stripped, so the stem never matches
        "a0123456789abcdef0.abcdef.json.bak",
        # the separator is there but the token is too short
        "a0123456789abcdef0.abc",
        # a plausible-looking prefix that is not the minted shape at all
        "agent-a0123456789abcdef0.abcdef",
        # whitespace smuggled around a real shape
        " a0123456789abcdef0.abcdef",
    ],
)
def test_attack_names_that_mimic_a_contract_id_without_being_one(fs_rules, name):
    """A name is not an owner. Shape-alike entries are simply not candidates."""
    from gaia.paths import scratch_dir

    entry = _touch(scratch_dir() / name, age_seconds=DECADE, is_dir=True)
    # Seed a closed row under EVERY reading of the name, so the only thing
    # that can save the entry is the id parser refusing to recognize it.
    _seed_rows(
        [
            (name, None, "COMPLETE", None),
            (name.strip(), None, "COMPLETE", None),
            (name.rsplit(".", 1)[0], None, "COMPLETE", None),
            (name.lower(), None, "COMPLETE", None),
        ]
    )

    assert fs_rules._entry_contract_id(name) is None
    assert fs_rules.collectable_turn_scoped(scratch_dir()) == []
    assert entry.exists()


def test_attack_backdated_mtime_alone_selects_nothing(fs_rules):
    """Age is not a lane. Ten years of backdating buys no selection."""
    from gaia.paths import cache_dir, scratch_dir, tmp_dir

    for root in (scratch_dir(), tmp_dir(), cache_dir()):
        _touch(root / f"{AGENT_A}.2aaaaa", age_seconds=DECADE, is_dir=True)
        assert fs_rules.collectable_turn_scoped(root, grace_hours=0) == []


def test_attack_future_mtime_does_not_bypass_the_grace_window(fs_rules):
    """The other direction: a clock pushed forward keeps the entry, never drops it."""
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.3aaaaa"
    entry = _touch(scratch_dir() / contract_id, age_seconds=-DECADE, is_dir=True)
    _seed_rows([(contract_id, None, "COMPLETE", None)])

    assert fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24) == []
    assert entry.exists()


@pytest.mark.parametrize("override", ["-1", "abc", "", "1e9", "0x10", " 24"])
def test_attack_a_malformed_grace_override_cannot_widen_retention(fs_rules, monkeypatch, override):
    """A hostile threshold falls back to the default instead of opening the window."""
    monkeypatch.setenv("GAIA_FS_RETENTION_GRACE_HOURS", override)

    assert fs_rules.resolve_grace_hours() == fs_rules.DEFAULT_GRACE_HOURS


@skip_if_root
def test_attack_permission_denied_mid_traversal_selects_nothing(fs_rules):
    """A directory that cannot be listed has told the rule nothing."""
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.4aaaaa"
    _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True)
    _seed_rows([(contract_id, None, "COMPLETE", None)])
    root = scratch_dir()
    os.chmod(root, 0o000)
    try:
        assert fs_rules.collectable_turn_scoped(root) == []
    finally:
        os.chmod(root, 0o755)


def test_attack_entry_vanishing_mid_sweep_is_skipped_not_guessed(fs_rules, monkeypatch):
    """A stat that fails is not a stat that returned "old".

    Reproduces the real race rather than patching ``Path.stat`` globally: the
    entry is listed, then disappears before its mtime is read.
    """
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.5aaaaa"
    entry = _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True)
    _seed_rows([(contract_id, None, "COMPLETE", None)])
    entry.rmdir()
    monkeypatch.setattr(fs_rules, "_iter_entries", lambda root: [entry])

    assert fs_rules.collectable_turn_scoped(scratch_dir()) == []


# --- The property that closes the hole this suite found --------------------

@pytest.mark.parametrize(
    "state", ["APPROVAL_REQUEST", "NEEDS_INPUT", "BLOCKED", "NEEDS_VERIFICATION"]
)
def test_paused_non_terminal_turn_is_never_collected(fs_rules, state):
    """A turn that PAUSED, not ended, keeps its scratch past any grace window.

    All four states here are members of ``CLOSED_TURN_PLAN_STATUSES`` (the
    turn declared a close) but not of ``TERMINAL_PLAN_STATUSES`` (the verdict
    that will never be replaced) -- an approval, an input, or a verification
    still pending means the turn resumes under the SAME contract id, and a
    grant that lands the next morning is well past a 24h grace window. The
    rule reads ``TERMINAL_PLAN_STATUSES``, so none of these four ever
    qualifies, however long the entry has sat quiet.
    """
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.6aaaaa"
    entry = _touch(scratch_dir() / contract_id, age_seconds=48 * HOUR, is_dir=True)
    _seed_rows([(contract_id, "s-paused", state, "backstop_capture")])

    assert fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=24) == []
    assert entry.exists()


# ===========================================================================
# Rejected-turn text -- keyed by session, same negative property
# ===========================================================================

def test_attack_session_prefix_collision_does_not_select(fs_rules):
    """Session matching is exact equality, never a prefix or a fuzzy match."""
    from gaia.paths import rejected_turns_dir

    root = rejected_turns_dir()
    live = "8f831722-9a04-4e8f-bf0c-a8636692442a"
    closed_neighbour = f"{live}-2"
    entry = _touch(root / f"{live}.{AGENT_A}.txt", age_seconds=DECADE)
    _seed_rows([(f"{AGENT_A}.tok", closed_neighbour, "COMPLETE", None)])

    assert fs_rules.collectable_rejected_turns(root) == []
    assert entry.exists()


def test_attack_rejected_turn_with_no_parsable_session_is_left_alone(fs_rules):
    """An empty session segment yields no key, and no key yields no selection."""
    from gaia.paths import rejected_turns_dir

    root = rejected_turns_dir()
    entry = _touch(root / f".{AGENT_A}.txt", age_seconds=DECADE)
    _seed_rows([("", "", "COMPLETE", None), (f"{AGENT_A}.tok", "", "COMPLETE", None)])

    assert fs_rules.collectable_rejected_turns(root) == []
    assert entry.exists()


def test_attack_rejected_turn_symlink_target_survives(fs_rules):
    """The preserved-text rule unlinks a link, never what the link points at."""
    from gaia.paths import evidence_dir, rejected_turns_dir

    session_id = "e236f38b-77b8-45eb-9634-713f071061e0"
    blob = _touch(evidence_dir() / "me" / "b" / "AC-6" / "kept.txt", age_seconds=DECADE)
    root = rejected_turns_dir()
    root.mkdir(parents=True, exist_ok=True)
    link = root / f"{session_id}.{AGENT_A}.txt"
    link.symlink_to(blob)
    old = time.time() - 48 * HOUR
    os.utime(link, (old, old), follow_symlinks=False)
    _seed_rows([(f"{AGENT_A}.tok", session_id, "COMPLETE", None)])

    _cleanup_module()._prune_rejected_turns(root, "Rejected", dry_run=False)

    assert blob.exists()


# ===========================================================================
# (c) The database is illegible
# ===========================================================================

def _illegible_db_variants(tmp_path):
    """Every way gaia.db can fail to answer, as (name, setup) pairs."""

    def absent():
        path = _db()
        if path.exists():
            path.unlink()

    def corrupt():
        _corrupt_the_db()

    def a_directory():
        path = _db()
        if path.exists():
            path.unlink()
        path.mkdir(parents=True, exist_ok=True)

    def truncated():
        # A real SQLite header followed by nothing -- opens, then fails on read.
        path = _db()
        con = sqlite3.connect(str(path))
        con.execute("create table t (x)")
        con.commit()
        con.close()
        raw = path.read_bytes()
        path.write_bytes(raw[:100] + b"\x00" * 400)

    def no_table():
        path = _db()
        if path.exists():
            path.unlink()
        con = sqlite3.connect(str(path))
        con.execute("create table something_else (x)")
        con.commit()
        con.close()

    return [
        ("absent", absent),
        ("corrupt", corrupt),
        ("a_directory", a_directory),
        ("truncated", truncated),
        ("no_table", no_table),
    ]


@pytest.mark.parametrize("variant", [n for n, _ in _illegible_db_variants(None)])
def test_illegible_db_selects_nothing_and_deletes_nothing(fs_rules, tmp_path, variant):
    """Every illegibility mode degrades to the SAME conservative answer: none.

    Both halves matter. Selecting nothing proves the policy refused; running
    the real (non-dry-run) CLI sweep afterwards and finding the files still
    there proves the refusal is what the caller acts on.
    """
    from gaia.paths import cache_dir, rejected_turns_dir, scratch_dir, tmp_dir

    contract_id = f"{AGENT_A}.7aaaaa"
    session_id = "3d9f6b32-1c44-4f0a-9a3e-7b2c1d0e5f88"
    entries = [
        _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True),
        _touch(tmp_dir() / f"{contract_id}.tgz", age_seconds=DECADE),
        _touch(cache_dir() / contract_id, age_seconds=DECADE, is_dir=True),
        _touch(rejected_turns_dir() / f"{session_id}.{AGENT_A}.txt", age_seconds=DECADE),
    ]
    # Seed the rows FIRST, so the DB genuinely contains the closes the attack
    # wants applied -- then break it. The rule must lose the answer, not gain one.
    _seed_rows([(contract_id, session_id, "COMPLETE", None)])
    setup = dict(_illegible_db_variants(tmp_path))[variant]
    setup()

    cleanup_mod = _cleanup_module()
    for root in (scratch_dir(), tmp_dir(), cache_dir()):
        assert fs_rules.collectable_turn_scoped(root, grace_hours=0) == []
        assert cleanup_mod._prune_turn_scoped(root, "Turn-scoped", dry_run=False) == []
    assert fs_rules.collectable_rejected_turns(rejected_turns_dir(), grace_hours=0) == []
    assert cleanup_mod._prune_rejected_turns(
        rejected_turns_dir(), "Rejected", dry_run=False
    ) == []

    for entry in entries:
        assert entry.exists(), f"{variant}: {entry} was deleted with no evidence"


def test_corrupt_db_is_openable_which_is_what_makes_it_distinct(fs_rules):
    """Guard the guard: the corrupt case must not silently become the absent case.

    If ``_ro_db_connect`` returned None here, every corruption assertion above
    would be re-testing the "no file" path and the real failure mode -- a
    connection that opens and then raises on execute -- would be untested.
    """
    _corrupt_the_db()

    con = fs_rules._ro_db_connect()
    assert con is not None, "the corrupt DB must still OPEN"
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("select contract_id from agent_contract_handoffs").fetchall()
    con.close()

    assert fs_rules._closed_contract_ids({f"{AGENT_A}.8aaaaa"}) == set()
    assert fs_rules._closed_turn_sessions({"any-session"}) == set()


@skip_if_root
def test_illegible_by_permission_selects_nothing(fs_rules):
    """A DB that exists and cannot be read is still a DB that said nothing."""
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.9aaaaa"
    entry = _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True)
    _seed_rows([(contract_id, None, "COMPLETE", None)])
    path = _db()
    os.chmod(path, 0o000)
    try:
        assert fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=0) == []
        assert entry.exists()
    finally:
        os.chmod(path, 0o644)


def test_illegible_db_diverges_from_the_drafts_precedent(fs_rules, tmp_path):
    """The divergence, proven side by side against ONE corrupt database.

    ``gaia.contract.drafts.collectable_drafts`` degrades to its age-only lane
    and KEEPS COLLECTING -- correct there, because a draft is a copy of a row
    that is the durable artifact. These rules govern the only copy, so they
    degrade the other way. Asserting both in one test is what makes the
    divergence a measured fact rather than a claim in a docstring: if either
    side is ever changed to match the other, this fails.
    """
    from gaia.contract.drafts import (
        collectable_drafts,
        drafts_dir,
        resolve_max_age_days,
    )
    from gaia.paths import scratch_dir

    contract_id = f"{AGENT_A}.abcabc"
    draft = _touch(
        drafts_dir() / f"{contract_id}.json",
        age_seconds=(resolve_max_age_days() + 30) * DAY,
    )
    draft.write_text("{}", encoding="utf-8")
    old = time.time() - (resolve_max_age_days() + 30) * DAY
    os.utime(draft, (old, old))
    scratch_entry = _touch(scratch_dir() / contract_id, age_seconds=DECADE, is_dir=True)

    _corrupt_the_db()

    drafts_selected = [str(r["path"]) for r in collectable_drafts()]
    scratch_selected = fs_rules.collectable_turn_scoped(scratch_dir(), grace_hours=0)

    assert str(draft) in drafts_selected, (
        "the precedent still collects by age alone -- if this stops being true "
        "the divergence documented in fs_rules has lost its subject"
    )
    assert [r["reason"] for r in collectable_drafts()] == ["aged"] * len(drafts_selected)
    assert scratch_selected == [], "the new rules must NOT inherit the age-only lane"
    assert scratch_entry.exists()


def test_no_age_only_lane_exists_in_the_module_at_all(fs_rules):
    """Structural: there is no threshold in this module that acts without state.

    The behavioural tests above can only sample the inputs they think of. This
    one asserts the shape: the module exposes a grace window and nothing that
    resembles a max-age backstop, so an age-only lane cannot be reintroduced
    by configuration.
    """
    names = {n for n in dir(fs_rules) if n.isupper() and not n.startswith("_")}
    assert "DEFAULT_GRACE_HOURS" in names
    thresholds = [n for n in names if "MAX_" in n or "_AGE_" in n or n.endswith("_AGE")]
    assert not thresholds, f"an age-only threshold appeared in fs_rules: {thresholds}"
    assert not hasattr(fs_rules, "resolve_max_age_days"), (
        "the drafts policy's age-only resolver has been copied into fs_rules"
    )
