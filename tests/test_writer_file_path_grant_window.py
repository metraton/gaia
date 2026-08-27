"""The SCOPE_FILE_PATH grant window must outlive a subagent re-dispatch.

A protected-path Write/Edit approval is not consumed on the retry the way a Bash
grant is: the orchestrator closes its turn to present the approval and dispatches
a FRESH subagent, which grounds itself before it reaches the file. The clock runs
from the user's decision, so that whole cycle is spent inside the window. Under
the Bash lane's 5-minute window every SCOPE_FILE_PATH grant ever signed expired
unused (13 rows, 0 consumed); these tests pin the separate window that covers the
cycle and pin that it still ends.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (_REPO_ROOT, _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

APPROVED_FILE = "/home/jorge/ws/me/gaia/hooks/modules/security/approval_grants.py"

# The measured failure: signed 07:09:36, expired 07:14:36, Edit at 07:15:10.
# A re-dispatch that grounds itself before writing lands well past that.
REDISPATCH_MINUTES = 14


@pytest.fixture()
def grant_db(tmp_path, bootstrapped_db_template):
    """An isolated, fully-bootstrapped DB carrying only this test's grants."""
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "gaia.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    return db_path


def _insert(db_path: Path, approval_id: str, **kwargs) -> None:
    from gaia.store.writer import insert_file_path_grant
    from modules.security.approval_scopes import build_file_path_signature

    signature = build_file_path_signature(APPROVED_FILE)
    assert signature is not None
    result = insert_file_path_grant(
        approval_id=approval_id,
        file_path=APPROVED_FILE,
        scope_signature=signature.to_dict(),
        db_path=db_path,
        **kwargs,
    )
    assert result.get("status") == "applied", result


def _age_by(db_path: Path, approval_id: str, minutes: int) -> None:
    """Rewind one grant's timestamps, so it reads as approved `minutes` ago.

    expires_at is absolute and computed at insert, so shifting both stamps back
    is exactly equivalent to having inserted the row that long ago -- and it does
    not require the check side to accept an injected clock.
    """
    shift = timedelta(minutes=minutes)
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT created_at, expires_at FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        assert row is not None
        moved = [
            (datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ") - shift).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            for stamp in row
        ]
        con.execute(
            "UPDATE approval_grants SET created_at = ?, expires_at = ? "
            "WHERE approval_id = ?",
            (moved[0], moved[1], approval_id),
        )
        con.commit()
    finally:
        con.close()


def test_default_window_is_the_file_path_lane_window(grant_db):
    from gaia.store.writer import (
        APPROVAL_GRANT_TTL_MINUTES,
        FILE_PATH_GRANT_TTL_MINUTES,
    )

    _insert(grant_db, "P-" + "a" * 32)

    con = sqlite3.connect(str(grant_db))
    try:
        created_at, expires_at = con.execute(
            "SELECT created_at, expires_at FROM approval_grants WHERE scope = ?",
            ("SCOPE_FILE_PATH",),
        ).fetchone()
    finally:
        con.close()

    span = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(
        created_at, "%Y-%m-%dT%H:%M:%SZ"
    )
    assert span == timedelta(minutes=FILE_PATH_GRANT_TTL_MINUTES)
    assert span != timedelta(minutes=APPROVAL_GRANT_TTL_MINUTES)


def test_grant_survives_a_realistic_redispatch_cycle(grant_db):
    from gaia.store.writer import check_db_file_path_grant

    approval_id = "P-" + "b" * 32
    _insert(grant_db, approval_id)
    _age_by(grant_db, approval_id, REDISPATCH_MINUTES)

    row = check_db_file_path_grant(APPROVED_FILE, db_path=grant_db)
    assert row is not None
    assert row["approval_id"] == approval_id
    assert row["status"] == "PENDING"


def test_the_bash_lane_window_would_have_expired_the_same_grant(grant_db):
    """The defect, reproduced: the inherited 5-minute window rejects the grant
    at the same age the lane's own window accepts it."""
    from gaia.store.writer import (
        APPROVAL_GRANT_TTL_MINUTES,
        check_db_file_path_grant,
    )

    approval_id = "P-" + "c" * 32
    _insert(grant_db, approval_id, ttl_minutes=APPROVAL_GRANT_TTL_MINUTES)
    _age_by(grant_db, approval_id, REDISPATCH_MINUTES)

    assert check_db_file_path_grant(APPROVED_FILE, db_path=grant_db) is None


def test_the_window_still_ends(grant_db):
    from gaia.store.writer import (
        FILE_PATH_GRANT_TTL_MINUTES,
        check_db_file_path_grant,
    )

    approval_id = "P-" + "d" * 32
    _insert(grant_db, approval_id)
    _age_by(grant_db, approval_id, FILE_PATH_GRANT_TTL_MINUTES + 1)

    assert check_db_file_path_grant(APPROVED_FILE, db_path=grant_db) is None


def test_a_second_edit_to_the_same_path_reuses_the_one_grant(grant_db):
    """The lane is deliberately reusable inside its window: a protected-path fix
    is several Edits to one file, and consuming at the first match would demand a
    fresh user approval for each of the rest."""
    from gaia.store.writer import check_db_file_path_grant

    approval_id = "P-" + "e" * 32
    _insert(grant_db, approval_id)
    _age_by(grant_db, approval_id, REDISPATCH_MINUTES)

    first = check_db_file_path_grant(APPROVED_FILE, db_path=grant_db)
    second = check_db_file_path_grant(APPROVED_FILE, db_path=grant_db)
    assert first is not None and second is not None
    assert second["approval_id"] == approval_id
    assert json.loads(second["consumed_indexes_json"]) == []
