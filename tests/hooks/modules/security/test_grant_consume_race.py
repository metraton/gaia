#!/usr/bin/env python3
"""Concurrency audit for consume_db_semantic_grant() (M1 atomicity guarantee).

M1 implemented grant consumption as a single conditional UPDATE:

    UPDATE approval_grants
    SET status = 'CONSUMED', consumed_at = ?
    WHERE approval_id = ?
      AND scope = 'SCOPE_SEMANTIC_SIGNATURE'
      AND status = 'PENDING'

and returns True iff cur.rowcount > 0. The atomicity claim is: when N
concurrent callers race to consume the SAME PENDING grant, exactly ONE call
observes rowcount > 0 (wins) and all others observe rowcount == 0 (lose) --
never zero winners (the grant would be unusable) and never more than one
(the same approval would authorize N commands, defeating single-use replay
protection).

This module does not assert on gaia.store.writer source lines; it drives the
public API (consume_db_semantic_grant) from N real OS threads against a
single, real, file-backed SQLite database -- the same substrate production
uses (~/.gaia/gaia.db) -- and observes the outcome. Threads matter here (not
mocks) because the property under test is a race across independent SQLite
connections attempting the same UPDATE ... WHERE status='PENDING' against one
underlying file, which is exactly the multi-process access pattern the real
system exercises when several PostToolUse hooks fire close together.

Satisfies: T8 (M3 audit milestone, approvals redesign) -- "un solo ganador
del consumo".
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.store.writer import (  # noqa: E402
    consume_db_semantic_grant,
    insert_semantic_grant,
)

# Number of concurrent consumers racing for the same grant. Large enough to
# make a race-condition bug (more than one winner) show up reliably across
# runs, small enough to keep the test fast and within SQLite's default busy
# timeout when transactions serialize correctly.
_N_CONSUMERS = 25


def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _seed_pending_grant(db_path: Path, approval_id: str, command: str) -> None:
    """Materialize the schema (via the real _connect bootstrap) and insert one
    PENDING SCOPE_SEMANTIC_SIGNATURE grant using the public writer API."""
    scope_signature = {
        "kind": "SCOPE_SEMANTIC_SIGNATURE",
        "normalized_command": command,
    }
    result = insert_semantic_grant(
        approval_id,
        command,
        scope_signature,
        agent_id="race-test-agent",
        session_id="race-test-session",
        db_path=db_path,
    )
    assert result.get("status") == "applied", (
        f"seed insert_semantic_grant must succeed: {result!r}"
    )

    # Confirm the seed landed as PENDING before racing consumers at it.
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT status FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        assert row is not None, "seeded grant row must exist"
        assert row[0] == "PENDING", f"seeded grant must be PENDING, got {row[0]!r}"
    finally:
        con.close()


class TestConsumeGrantRaceHasExactlyOneWinner:
    """N concurrent consumers of the SAME PENDING grant -> exactly one winner."""

    def test_exactly_one_winner_among_n_concurrent_consumers(self, tmp_path):
        """consume_db_semantic_grant() under a real thread race: sum(True)==1."""
        db_path = tmp_path / "grant_race.db"
        approval_id = "P-race-0001"
        command = "kubectl apply -f deploy.yaml"

        _seed_pending_grant(db_path, approval_id, command)

        def _consume_once() -> bool:
            return consume_db_semantic_grant(approval_id, db_path=db_path)

        results: list[bool] = []
        with ThreadPoolExecutor(max_workers=_N_CONSUMERS) as pool:
            futures = [pool.submit(_consume_once) for _ in range(_N_CONSUMERS)]
            for fut in as_completed(futures):
                results.append(fut.result())

        winners = sum(1 for r in results if r is True)
        losers = sum(1 for r in results if r is False)

        assert winners == 1, (
            f"expected exactly ONE winner among {_N_CONSUMERS} concurrent "
            f"consumers, got {winners} winners and {losers} losers -- the "
            f"grant is consumable more than once (or zero times), breaking "
            f"single-use replay protection."
        )
        assert losers == _N_CONSUMERS - 1, (
            f"expected all remaining {_N_CONSUMERS - 1} consumers to observe "
            f"rowcount=0, got {losers} losers (some result was neither a "
            f"clean win nor a clean loss)."
        )

        # Final DB state: CONSUMED exactly once, with consumed_at stamped.
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT status, consumed_at FROM approval_grants "
                "WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            assert row is not None
            assert row[0] == "CONSUMED", (
                f"grant must end in CONSUMED status, got {row[0]!r}"
            )
            assert row[1] is not None, "consumed_at must be stamped"
        finally:
            con.close()

    def test_repeated_races_are_stable(self, tmp_path):
        """Run the race multiple times (fresh grant each round) -- no flake.

        A single passing race is not strong evidence against a narrow
        TOCTOU window; repeating the race across independent grants makes a
        latent race condition far less likely to hide behind timing luck.
        """
        rounds = 5
        for i in range(rounds):
            db_path = tmp_path / f"grant_race_round_{i}.db"
            approval_id = f"P-race-round-{i:04d}"
            command = f"terraform apply -auto-approve round-{i}"

            _seed_pending_grant(db_path, approval_id, command)

            def _consume_once() -> bool:
                return consume_db_semantic_grant(approval_id, db_path=db_path)

            with ThreadPoolExecutor(max_workers=_N_CONSUMERS) as pool:
                futures = [pool.submit(_consume_once) for _ in range(_N_CONSUMERS)]
                results = [fut.result() for fut in as_completed(futures)]

            winners = sum(1 for r in results if r is True)
            assert winners == 1, (
                f"round {i}: expected exactly one winner, got {winners}"
            )

    def test_consume_of_already_consumed_grant_never_wins_again(self, tmp_path):
        """After the race, any further consume attempt against the same
        approval_id must keep returning False -- CONSUMED is a terminal state
        for this replay-protection check, not a transient one."""
        db_path = tmp_path / "grant_race_terminal.db"
        approval_id = "P-race-terminal"
        command = "helm upgrade app ."

        _seed_pending_grant(db_path, approval_id, command)

        def _consume_once() -> bool:
            return consume_db_semantic_grant(approval_id, db_path=db_path)

        with ThreadPoolExecutor(max_workers=_N_CONSUMERS) as pool:
            futures = [pool.submit(_consume_once) for _ in range(_N_CONSUMERS)]
            first_wave = [fut.result() for fut in as_completed(futures)]
        assert sum(1 for r in first_wave if r is True) == 1

        # A second wave against the now-CONSUMED grant must be all losers.
        with ThreadPoolExecutor(max_workers=_N_CONSUMERS) as pool:
            futures = [pool.submit(_consume_once) for _ in range(_N_CONSUMERS)]
            second_wave = [fut.result() for fut in as_completed(futures)]

        assert all(r is False for r in second_wave), (
            "no consumer should be able to re-win an already-CONSUMED grant"
        )
