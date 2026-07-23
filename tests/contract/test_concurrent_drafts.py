"""
AC-14 -- concurrency isolation (M6, task T13).

Two GENUINELY CONCURRENT ``init -> set/add -> finalize`` cycles, run against
the SAME shared substrate (one ``GAIA_DATA_DIR``, one ``gaia.db``, even the
SAME ``agent_id`` -- the harder case), produce TWO DISTINCT rows in
``agent_contract_handoffs`` with NO cross-contamination: neither cycle's
fields ever leak into the other's draft file or the other's persisted row.

This test builds on, but does not re-prove, T5/T7's isolation primitives:
    * T5 (``gaia/contract/drafts.py``): per-id draft files, no shared mutable
      pointer, atomic ``os.replace`` writes.
    * T7 (``gaia.store.writer.finalize_agent_contract_handoff``):
      ``contract_id``-keyed UNIQUE index, idempotent UPSERT.
``tests/contract/test_cli_e2e_idempotent.py::test_distinct_drafts_produce_distinct_rows``
already covers two distinct drafts finalized SEQUENTIALLY. What is new here
is genuine, adversarial, same-instant CONCURRENCY: two real OS processes per
step, launched from two real OS threads, forced to overlap in wall-clock
time via a ``threading.Barrier`` so that init(A)/init(B) race each other,
add(A)/add(B) race each other, and so on through finalize(A)/finalize(B).

Why a Barrier (not a raw ``time.sleep`` race) buys genuine, NON-FLAKY
concurrency:
    A ``Barrier(2)`` blocks both worker threads until BOTH have reached the
    same step boundary, then releases them together -- so the two threads'
    ``subprocess.run`` calls for that step are launched at (as close to)
    the same instant as the OS scheduler allows, every single run. This is
    real concurrency (two independent child processes genuinely overlapping
    in execution, not simulated or mocked), yet the correctness property
    under test does NOT depend on which of the two subprocesses' syscalls
    actually wins any given race -- each cycle only ever touches its OWN
    ``--draft-id`` (the explicit-always-wins addressing T5's docstring
    calls out as "the concurrency-safe primary key each concurrent cycle
    carries"). So the outcome is deterministic (always 2 correct, isolated
    rows) regardless of scheduler timing -- no flake, but still a real race
    at the OS level, not merely a sequential simulation of one.

Every CLI call runs as a real subprocess against ``bin/cli/contract.py``'s
standalone shim (not ``bin/gaia`` -- avoids the ``gaia dev`` / DB-bootstrap
path, per the T4/T5/T7 hard constraints).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

# Deliberately the SAME agent_id for both concurrent cycles -- the harder
# isolation case: same agent prefix, same glob namespace in
# gaia.contract.drafts.list_draft_ids(), same workspace, same DB, racing at
# every step. If cross-contamination were possible anywhere in the stack,
# sharing the agent_id is what would surface it.
SHARED_AGENT_ID = "a1234abcd"


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call.

    Both cycles' drafts (``<data_dir>/contract_drafts/``) and the SQLite DB
    (``<data_dir>/gaia.db``) land under this ONE shared dir -- the two
    concurrent cycles genuinely share the same substrate, exactly the
    "two concurrent cycles in one session" scenario AC-14 describes.
    """
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _db_path(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "gaia.db"


def _count_handoffs(env: dict, contract_id: Optional[str] = None) -> int:
    db = _db_path(env)
    if not db.is_file():
        return 0
    con = sqlite3.connect(str(db))
    try:
        if contract_id is None:
            row = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs"
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) FROM agent_contract_handoffs WHERE contract_id = ?",
                (contract_id,),
            ).fetchone()
        return int(row[0])
    finally:
        con.close()


def _fetch_row(env: dict, contract_id: str) -> sqlite3.Row:
    con = sqlite3.connect(str(_db_path(env)))
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT id, contract_id, agent_id, agent_state, raw_handoff_json "
            "FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        assert row is not None, f"expected a row for contract_id={contract_id!r}"
        return row
    finally:
        con.close()


def _drafts_dir(env: dict) -> Path:
    return Path(env["GAIA_DATA_DIR"]) / "contract_drafts"


def _bootstrap_schema(env: dict) -> None:
    """Pre-materialize the gaia.db schema SEQUENTIALLY before the race starts.

    FINDING (discovered by this test, reported -- NOT fixed here per the
    T13 strict lane): ``gaia.store.writer._connect()`` has a TOCTOU race
    between its ``fresh = not db_path.exists()`` freshness check and
    ``sqlite3.connect()`` (which itself creates the empty file as a side
    effect). When two processes race to create a BRAND-NEW ``gaia.db`` for
    the very first time, the second connection can land in the window after
    the first has created the (still schema-less) file but before it has
    run ``executescript`` + ``commit``, and fails with
    ``sqlite3.OperationalError: no such table: workspaces``. Reproduced
    deterministically (100% of runs) by racing two concurrent
    ``finalize_agent_contract_handoff`` calls against a directory with no
    pre-existing ``gaia.db``.

    This race is orthogonal to what AC-14 targets: T5's per-draft-file
    isolation and T7's ``contract_id``-keyed UNIQUE-index idempotency both
    assume a materialized schema, not a fresh-file race. Pre-warming the
    schema HERE (sequentially, once, before any concurrent step) lets this
    test isolate and prove the row-level concurrency property T5/T7 actually
    built, without conflating it with this separate, pre-existing bootstrap
    race in the writer's connection layer -- which belongs to whoever owns
    ``gaia.store.writer._connect`` and is reported as a finding, not fixed
    here.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from gaia.store.writer import _connect

    db_path = Path(env["GAIA_DATA_DIR"]) / "gaia.db"
    con = _connect(db_path)
    con.close()


class _CycleResult:
    def __init__(self, marker: str):
        self.marker = marker
        self.draft_id: Optional[str] = None
        self.handoff_id: Optional[int] = None
        self.created: Optional[bool] = None
        self.errors: list = []


def _run_cycle(
    env: dict,
    barrier: threading.Barrier,
    marker: str,
    result: _CycleResult,
) -> None:
    """One full init -> add -> fill -> set -> finalize cycle, marker-tagged.

    Every step waits on the shared barrier immediately before running its
    subprocess, so this cycle's step N and the sibling cycle's step N are
    launched together -- genuine overlap at every stage, not just at the
    end.
    """
    def _check(proc: subprocess.CompletedProcess, step: str) -> None:
        if proc.returncode != 0:
            result.errors.append(
                f"[{marker}] {step} failed rc={proc.returncode} "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )

    # Step 1: init -- mint a fresh, per-cycle draft id.
    barrier.wait(timeout=30)
    init_proc = _run(["init", "--agent-id", SHARED_AGENT_ID, "--json"], env)
    _check(init_proc, "init")
    if init_proc.returncode != 0:
        return
    draft_id = json.loads(init_proc.stdout)["draft_id"]
    result.draft_id = draft_id

    # Step 2: add -- a marker-tagged evidence_report.key_outputs entry,
    # explicit --draft-id (the concurrency-safe addressing; never rely on
    # "most recently touched"). The isolation-proof marker channel lives in
    # evidence_report (a free-form field), NOT agent_status.pending_steps /
    # next_action: COMPLETE_SHAPE (R4) requires those be [] / 'done' on
    # every COMPLETE contract, so the per-draft marker this test needs to
    # survive to the persisted row had to move off of them.
    barrier.wait(timeout=30)
    add_proc = _run(
        ["add", "evidence_report.key_outputs", f"marker-{marker}", "--draft-id", draft_id],
        env,
    )
    _check(add_proc, "add")

    # Step 3: fill -- a marker-tagged verification detail (a second,
    # independent isolation signal alongside the key_outputs marker above)
    # plus next_action set to the literal 'done' every COMPLETE contract
    # must carry (COMPLETE_SHAPE, R4) -- not a per-marker value.
    barrier.wait(timeout=30)
    patch = json.dumps({
        "agent_status": {"next_action": "done"},
        "evidence_report": {
            "verification": {
                "method": "pytest",
                "result": "pass",
                "details": f"AC-14 concurrency cycle {marker}",
            },
        },
    })
    fill_proc = _run(["fill", "--json", patch, "--draft-id", draft_id], env)
    _check(fill_proc, "fill")

    # Step 4: set plan_status=COMPLETE (requires the fill above to have
    # already landed verification.result=="pass").
    barrier.wait(timeout=30)
    complete_proc = _run(
        ["set", "agent_status.plan_status", "COMPLETE", "--draft-id", draft_id],
        env,
    )
    _check(complete_proc, "set plan_status")

    # Step 5: finalize -- the sole writer of the agent_contract_handoffs row.
    barrier.wait(timeout=30)
    fin_proc = _run(["finalize", "--draft-id", draft_id, "--json"], env)
    _check(fin_proc, "finalize")
    if fin_proc.returncode == 0:
        payload = json.loads(fin_proc.stdout)
        result.handoff_id = payload["handoff_id"]
        result.created = payload["created"]


def _run_concurrent_pair(env: dict, marker_a: str, marker_b: str):
    barrier = threading.Barrier(2)
    result_a = _CycleResult(marker_a)
    result_b = _CycleResult(marker_b)

    thread_a = threading.Thread(target=_run_cycle, args=(env, barrier, marker_a, result_a))
    thread_b = threading.Thread(target=_run_cycle, args=(env, barrier, marker_b, result_b))

    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=60)
    thread_b.join(timeout=60)

    assert not thread_a.is_alive(), "cycle A did not finish within timeout"
    assert not thread_b.is_alive(), "cycle B did not finish within timeout"
    assert not result_a.errors, f"cycle A had step failures: {result_a.errors}"
    assert not result_b.errors, f"cycle B had step failures: {result_b.errors}"
    return result_a, result_b


# ---------------------------------------------------------------------------
# AC-14 headline: two concurrent cycles -> two distinct rows, no cross-
# contamination in the DB or the on-disk drafts.
# ---------------------------------------------------------------------------
def test_two_concurrent_cycles_produce_two_distinct_uncontaminated_rows(cli_env):
    _bootstrap_schema(cli_env)
    assert _count_handoffs(cli_env) == 0  # nothing before (schema only, no rows)

    result_a, result_b = _run_concurrent_pair(cli_env, "A", "B")

    # Two genuinely distinct draft ids and handoff ids -- not the same
    # contract, not a collapsed/duplicated write.
    assert result_a.draft_id is not None and result_b.draft_id is not None
    assert result_a.draft_id != result_b.draft_id
    assert result_a.handoff_id is not None and result_b.handoff_id is not None
    assert result_a.handoff_id != result_b.handoff_id
    assert result_a.created is True
    assert result_b.created is True

    # Exactly two rows overall, one per contract id.
    assert _count_handoffs(cli_env) == 2
    assert _count_handoffs(cli_env, contract_id=result_a.draft_id) == 1
    assert _count_handoffs(cli_env, contract_id=result_b.draft_id) == 1

    # No cross-contamination in the persisted rows: A's row carries ONLY
    # A's marker; B's marker never leaked into it, and vice versa. The
    # COMPLETE contract itself is compliant (pending_steps == [], next_action
    # == 'done') -- the isolation proof lives in evidence_report.key_outputs
    # and evidence_report.verification.details instead.
    row_a = _fetch_row(cli_env, result_a.draft_id)
    row_b = _fetch_row(cli_env, result_b.draft_id)

    assert row_a["agent_id"] == SHARED_AGENT_ID
    assert row_b["agent_id"] == SHARED_AGENT_ID
    assert row_a["agent_state"] == "COMPLETE"
    assert row_b["agent_state"] == "COMPLETE"

    envelope_a = json.loads(row_a["raw_handoff_json"])
    envelope_b = json.loads(row_b["raw_handoff_json"])

    assert envelope_a["agent_status"]["pending_steps"] == []
    assert envelope_b["agent_status"]["pending_steps"] == []
    assert envelope_a["agent_status"]["next_action"] == "done"
    assert envelope_b["agent_status"]["next_action"] == "done"
    assert envelope_a["evidence_report"]["key_outputs"] == ["marker-A"]
    assert envelope_b["evidence_report"]["key_outputs"] == ["marker-B"]
    assert envelope_a["evidence_report"]["verification"]["details"] == (
        "AC-14 concurrency cycle A"
    )
    assert envelope_b["evidence_report"]["verification"]["details"] == (
        "AC-14 concurrency cycle B"
    )

    # "marker-B" must never appear anywhere inside A's persisted envelope,
    # and symmetrically for A inside B's -- the strongest single check
    # against any partial-merge / shared-buffer contamination.
    raw_a = row_a["raw_handoff_json"]
    raw_b = row_b["raw_handoff_json"]
    assert "marker-B" not in raw_a and "cycle B" not in raw_a
    assert "marker-A" not in raw_b and "cycle A" not in raw_b


# ---------------------------------------------------------------------------
# No cross-contamination at the DRAFT-FILE layer either (not only the DB):
# each draft file on disk, post-finalize, still holds only its own marker.
# ---------------------------------------------------------------------------
def test_concurrent_cycles_do_not_contaminate_each_others_draft_files(cli_env):
    _bootstrap_schema(cli_env)
    result_a, result_b = _run_concurrent_pair(cli_env, "X", "Y")

    drafts_dir = _drafts_dir(cli_env)
    path_a = drafts_dir / f"{result_a.draft_id}.json"
    path_b = drafts_dir / f"{result_b.draft_id}.json"
    assert path_a.is_file()
    assert path_b.is_file()
    assert path_a != path_b  # distinct files, never a shared/clobbered path

    envelope_a = json.loads(path_a.read_text(encoding="utf-8"))
    envelope_b = json.loads(path_b.read_text(encoding="utf-8"))

    assert envelope_a["evidence_report"]["key_outputs"] == ["marker-X"]
    assert envelope_b["evidence_report"]["key_outputs"] == ["marker-Y"]
    assert envelope_a["agent_status"]["pending_steps"] == []
    assert envelope_b["agent_status"]["pending_steps"] == []
    assert envelope_a["agent_status"]["agent_id"] == SHARED_AGENT_ID
    assert envelope_b["agent_status"]["agent_id"] == SHARED_AGENT_ID

    raw_a = path_a.read_text(encoding="utf-8")
    raw_b = path_b.read_text(encoding="utf-8")
    assert "marker-Y" not in raw_a
    assert "marker-X" not in raw_b


# ---------------------------------------------------------------------------
# Stability: repeat the concurrent race across several independent rounds.
# A single passing race is weak evidence against a narrow TOCTOU window;
# repeating it (fresh drafts + fresh markers each round, same shared DB)
# makes a latent isolation bug far less likely to hide behind timing luck.
# ---------------------------------------------------------------------------
def test_repeated_concurrent_rounds_stay_isolated(cli_env):
    _bootstrap_schema(cli_env)
    rounds = 3
    seen_draft_ids: set = set()
    seen_handoff_ids: set = set()

    for i in range(rounds):
        marker_a, marker_b = f"R{i}A", f"R{i}B"
        result_a, result_b = _run_concurrent_pair(cli_env, marker_a, marker_b)

        assert result_a.draft_id not in seen_draft_ids
        assert result_b.draft_id not in seen_draft_ids
        seen_draft_ids.add(result_a.draft_id)
        seen_draft_ids.add(result_b.draft_id)

        assert result_a.handoff_id not in seen_handoff_ids
        assert result_b.handoff_id not in seen_handoff_ids
        seen_handoff_ids.add(result_a.handoff_id)
        seen_handoff_ids.add(result_b.handoff_id)

        row_a = _fetch_row(cli_env, result_a.draft_id)
        row_b = _fetch_row(cli_env, result_b.draft_id)
        envelope_a = json.loads(row_a["raw_handoff_json"])
        envelope_b = json.loads(row_b["raw_handoff_json"])
        assert envelope_a["evidence_report"]["key_outputs"] == [f"marker-{marker_a}"]
        assert envelope_b["evidence_report"]["key_outputs"] == [f"marker-{marker_b}"]

    # After all rounds: exactly 2 * rounds rows total, every one distinct.
    assert _count_handoffs(cli_env) == 2 * rounds
    assert len(seen_draft_ids) == 2 * rounds
    assert len(seen_handoff_ids) == 2 * rounds
