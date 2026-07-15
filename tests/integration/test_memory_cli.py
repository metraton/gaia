"""
Integration tests for `gaia memory add` (DB-only writer).

Mirrors the pattern used by ``test_brief_cli.py``:
  - Routes the substrate DB into ``tmp_path`` via ``GAIA_DATA_DIR`` so tests
    never touch the user's real ``~/.gaia/gaia.db``.
  - Pins cwd to ``tmp_path`` so any accidental filesystem write under a
    relative ``.claude/projects/.../memory/`` path would land where the test
    can detect it.
  - Asserts zero filesystem side effects for every successful insert/update.

Covers the AC of the brief that introduced ``gaia memory add`` (B8 follow-up):
DB-canonical curated memory, no MD writes, UPSERT on duplicate name,
clear error on invalid type.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

# Ensure the gaia package and bin/ are importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route ``gaia.paths.db_path()`` to a tmp dir so tests are isolated."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _read_memory_row(db_path: Path, workspace: str, name: str) -> dict | None:
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT workspace, name, type, description, body, project_ref, "
            "initiative, origin_session_id, updated_at "
            "FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.OperationalError:
        # DB never materialized (e.g. the write was refused before any
        # connection opened) -- then there is certainly no row.
        return None
    finally:
        con.close()


def _seed_project(db_path: Path, workspace: str, name: str,
                   project_identity: str | None = None,
                   status: str = "active") -> None:
    """Seed a minimal `projects` row for --project resolution tests.

    Bypasses upsert_project's permission gate (a plain read-only lookup does
    not need one); inserts directly via the writer's `_connect` so the schema
    is materialized first.
    """
    from gaia.store.writer import _connect
    con = _connect(db_path)
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES (?)", (workspace,))
        con.execute(
            "INSERT INTO projects (workspace, name, project_identity, status) "
            "VALUES (?, ?, ?, ?)",
            (workspace, name, project_identity, status),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Insert path
# ---------------------------------------------------------------------------

def test_add_inserts_row_in_db(tmp_db, tmp_path, monkeypatch, capsys):
    """`gaia memory add` writes a row to the memory table."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="test-mem",
        type="project",
        body="This is the body of a test memory entry.",
        description="One-line description",
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "test-mem")
    assert row is not None
    assert row["type"] == "project"
    assert row["description"] == "One-line description"
    assert row["body"] == "This is the body of a test memory entry."
    assert row["updated_at"], "updated_at must be set"


def test_add_zero_filesystem_side_effects(tmp_db, tmp_path, monkeypatch, capsys):
    """A successful ``gaia memory add`` must not create any .md file."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="no-fs-touch",
        type="project",
        body="body",
        description=None,
        workspace="me",
        json=True,
    )
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    # No legacy memory directory should be created.
    legacy = tmp_path / ".claude" / "projects"
    assert not legacy.exists(), f"unexpected FS write at {legacy}"

    # And the slug must not appear as a file/dir anywhere under tmp_path.
    found = list(tmp_path.rglob("no-fs-touch*"))
    assert found == [], f"unexpected slug-named path(s): {found}"


# ---------------------------------------------------------------------------
# Upsert path
# ---------------------------------------------------------------------------

def test_add_duplicate_name_upserts(tmp_db, tmp_path, monkeypatch, capsys):
    """A second add with the same (workspace, name) updates the row, not errors."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    base = dict(name="dup-mem", type="project", workspace="me",
                description=None, json=False)

    rc1 = _cmd_add(argparse.Namespace(body="initial body", **base))
    assert rc1 == 0, capsys.readouterr()

    rc2 = _cmd_add(argparse.Namespace(body="updated body", **base))
    assert rc2 == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "dup-mem")
    assert row is not None
    assert row["body"] == "updated body", "second call must overwrite body"

    # Only one row exists for this PK.
    import sqlite3
    con = sqlite3.connect(str(tmp_db))
    try:
        cnt = con.execute(
            "SELECT COUNT(*) FROM memory WHERE workspace = ? AND name = ?",
            ("me", "dup-mem"),
        ).fetchone()[0]
    finally:
        con.close()
    assert cnt == 1


def test_add_json_action_is_inserted_then_updated(tmp_db, tmp_path,
                                                  monkeypatch, capsys):
    """JSON output exposes ``action`` so callers can distinguish insert vs update."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    base = dict(name="action-mem", type="user", workspace="me",
                description=None, json=True)

    rc1 = _cmd_add(argparse.Namespace(body="b1", **base))
    assert rc1 == 0
    out1 = capsys.readouterr().out
    payload1 = json.loads(out1)
    assert payload1["action"] == "inserted"

    rc2 = _cmd_add(argparse.Namespace(body="b2", **base))
    assert rc2 == 0
    out2 = capsys.readouterr().out
    payload2 = json.loads(out2)
    assert payload2["action"] == "updated"


# ---------------------------------------------------------------------------
# Validation / error paths
# ---------------------------------------------------------------------------

def test_add_invalid_type_returns_error(tmp_db, tmp_path, monkeypatch, capsys):
    """An unknown ``--type`` is rejected with a clear message and exit 1."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="bad-type",
        type="bogus",
        body="b",
        description=None,
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "type" in captured.err.lower()

    # And no row was written. (When validation rejects before _connect(),
    # the DB file does not yet exist; treat that as "no row" too.)
    if tmp_db.exists():
        row = _read_memory_row(tmp_db, "me", "bad-type")
        assert row is None


def test_add_missing_required_flags(tmp_db, tmp_path, monkeypatch, capsys):
    """Missing ``--body`` returns a clear error without writing a row."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="missing-body",
        type="project",
        body=None,
        description=None,
        workspace="me",
        json=False,
    )
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "body" in captured.err.lower()


# ---------------------------------------------------------------------------
# N3: --project / --project-ref forward-only anchoring
# ---------------------------------------------------------------------------

def _add_args(**overrides) -> argparse.Namespace:
    """Base Namespace for `_cmd_add`, with N3 project fields defaulted."""
    base = dict(
        name="proj-mem", type="project", body="body text",
        description=None, workspace="me", json=False,
        project=None, project_ref=None, initiative=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_add_project_flag_anchors_project_ref(tmp_db, tmp_path, monkeypatch, capsys):
    """`--project=<name>` resolves to the project's project_identity and
    persists it as memory.project_ref."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_project(tmp_db, "me", "x", project_identity="github.com/me/x")

    args = _add_args(name="anchored-mem", project="x")
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "anchored-mem")
    assert row is not None
    assert row["project_ref"] == "github.com/me/x"


def test_add_without_project_flag_leaves_project_ref_null(tmp_db, tmp_path,
                                                           monkeypatch, capsys):
    """Omitting --project leaves memory.project_ref NULL (forward-only;
    historical/organizational rows are not guessed at)."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = _add_args(name="unanchored-mem")
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "unanchored-mem")
    assert row is not None
    assert row["project_ref"] is None


def test_add_project_flag_not_found_errors_clearly(tmp_db, tmp_path,
                                                    monkeypatch, capsys):
    """An unknown --project name is a clear error -- never guessed."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = _add_args(name="ghost-project-mem", project="does-not-exist")
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()
    assert _read_memory_row(tmp_db, "me", "ghost-project-mem") is None


def test_add_project_flag_no_identity_errors_clearly(tmp_db, tmp_path,
                                                      monkeypatch, capsys):
    """A project that exists but carries no project_identity yet (legacy /
    unscanned row) is a clear error, not a silent NULL anchor."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_project(tmp_db, "me", "unscanned", project_identity=None)

    args = _add_args(name="no-identity-mem", project="unscanned")
    rc = _cmd_add(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "project_identity" in captured.err.lower()
    assert _read_memory_row(tmp_db, "me", "no-identity-mem") is None


def test_add_project_ref_flag_anchors_directly(tmp_db, tmp_path,
                                               monkeypatch, capsys):
    """`--project-ref=<identity>` anchors directly, bypassing name resolution."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    args = _add_args(name="direct-ref-mem", project_ref="github.com/me/direct")
    rc = _cmd_add(args)
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "direct-ref-mem")
    assert row is not None
    assert row["project_ref"] == "github.com/me/direct"


def test_add_update_without_project_flag_preserves_existing_anchor(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    """A later `add` (update) that omits --project must NOT clobber a
    previously-anchored project_ref back to NULL (coalesce-or-omit)."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_project(tmp_db, "me", "x", project_identity="github.com/me/x")

    rc1 = _cmd_add(_add_args(name="sticky-mem", project="x", body="v1"))
    assert rc1 == 0, capsys.readouterr()
    assert _read_memory_row(tmp_db, "me", "sticky-mem")["project_ref"] == "github.com/me/x"

    # Second call: same slug, no --project this time.
    rc2 = _cmd_add(_add_args(name="sticky-mem", body="v2"))
    assert rc2 == 0, capsys.readouterr()
    row = _read_memory_row(tmp_db, "me", "sticky-mem")
    assert row["body"] == "v2", "the update itself must still land"
    assert row["project_ref"] == "github.com/me/x", (
        "omitting --project on update must not erase an existing anchor"
    )


# ---------------------------------------------------------------------------
# Deterministic anchoring contract (AC-1..AC-5)
#
# The function does not guess and does not infer scope from the cwd: at least
# one explicit scope flag (--project preferred, or --workspace) must be given;
# --project must resolve or it is a structured error; a project/workspace
# mismatch is its own structured error; --workspace-only is the explicit
# degraded lane (project_ref NULL, exit 0).
# ---------------------------------------------------------------------------

def _err_payload(capsys) -> dict:
    """Parse the JSON error object printed by a structured-error return."""
    return json.loads(capsys.readouterr().out)


def test_add_ac1_no_scope_errors_structured(tmp_db, tmp_path,
                                             monkeypatch, capsys):
    """AC-1: neither --project nor --workspace -> exit!=0, structured
    'missing_scope' error, and NO row written."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    rc = _cmd_add(_add_args(name="no-scope-mem", workspace=None, json=True))
    assert rc == 1
    payload = _err_payload(capsys)
    assert payload["code"] == "missing_scope"
    assert _read_memory_row(tmp_db, "me", "no-scope-mem") is None


def test_add_ac2_project_resolves_anchors_identity(tmp_db, tmp_path,
                                                   monkeypatch, capsys):
    """AC-2: --project=<scanned project> -> exit 0 and project_ref = that
    project's project_identity."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_project(tmp_db, "me", "century", project_identity="github.com/me/century")

    rc = _cmd_add(_add_args(name="ac2-mem", project="century", workspace="me"))
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "ac2-mem")
    assert row is not None
    assert row["project_ref"] == "github.com/me/century"


def test_add_ac3_unresolvable_project_errors_no_row(tmp_db, tmp_path,
                                                    monkeypatch, capsys):
    """AC-3: --project=<inexistent> -> exit!=0, structured 'project_unresolved'
    error, and NO row written (no silent fallback)."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    rc = _cmd_add(_add_args(name="ac3-mem", project="ghost", workspace="me",
                            json=True))
    assert rc == 1
    payload = _err_payload(capsys)
    assert payload["code"] == "project_unresolved"
    assert _read_memory_row(tmp_db, "me", "ac3-mem") is None


def test_add_ac4_project_workspace_mismatch_errors_no_row(tmp_db, tmp_path,
                                                          monkeypatch, capsys):
    """AC-4: --project=<X> --workspace=<Y> that do not correspond -> exit!=0,
    structured 'project_workspace_mismatch' error, and NO row written."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    # 'century' exists ONLY under workspace 'century-inc'.
    _seed_project(tmp_db, "century-inc", "century",
                  project_identity="github.com/me/century")

    rc = _cmd_add(_add_args(name="ac4-mem", project="century", workspace="me",
                            json=True))
    assert rc == 1
    payload = _err_payload(capsys)
    assert payload["code"] == "project_workspace_mismatch"
    assert payload["found_in"] == ["century-inc"]
    assert _read_memory_row(tmp_db, "me", "ac4-mem") is None


def test_add_ac5_workspace_only_degraded_lane_null(tmp_db, tmp_path,
                                                   monkeypatch, capsys):
    """AC-5: --workspace only (no --project) -> exit 0, project_ref NULL.
    Legitimate explicit workspace-scoped note."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    rc = _cmd_add(_add_args(name="ac5-mem", workspace="me"))
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "ac5-mem")
    assert row is not None
    assert row["project_ref"] is None


def test_add_project_no_identity_errors_structured(tmp_db, tmp_path,
                                                   monkeypatch, capsys):
    """A --project that exists in the workspace but has no project_identity yet
    -> structured 'project_no_identity' error, no row (not a silent NULL)."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_project(tmp_db, "me", "unscanned", project_identity=None)

    rc = _cmd_add(_add_args(name="no-id-mem", project="unscanned",
                            workspace="me", json=True))
    assert rc == 1
    payload = _err_payload(capsys)
    assert payload["code"] == "project_no_identity"
    assert _read_memory_row(tmp_db, "me", "no-id-mem") is None


# ---------------------------------------------------------------------------
# v32: initiative -- canonical project/initiative grouping key
# ---------------------------------------------------------------------------

def test_add_project_flag_populates_initiative_from_basename(tmp_db, tmp_path,
                                                             monkeypatch, capsys):
    """--project (git) resolves project_ref AND derives initiative from the
    repo basename of that anchor."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_project(tmp_db, "me", "gaia",
                  project_identity="/home/jorge/ws/me/gaia/.git")

    rc = _cmd_add(_add_args(name="git-init-mem", project="gaia"))
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "git-init-mem")
    assert row["project_ref"] == "/home/jorge/ws/me/gaia/.git"
    assert row["initiative"] == "gaia"


def test_add_initiative_flag_logical_no_git_anchor(tmp_db, tmp_path,
                                                   monkeypatch, capsys):
    """--initiative sets a LOGICAL initiative key (normalized) with no git
    project; project_ref stays NULL."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    rc = _cmd_add(_add_args(name="logical-init-mem", initiative="BranchKinect",
                            workspace="me"))
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "logical-init-mem")
    assert row["initiative"] == "branchkinect"
    assert row["project_ref"] is None


def test_add_workspace_only_leaves_initiative_null(tmp_db, tmp_path,
                                                   monkeypatch, capsys):
    """--workspace only (no --project / --initiative) -> initiative NULL."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    rc = _cmd_add(_add_args(name="ws-only-init", workspace="me"))
    assert rc == 0, capsys.readouterr()

    row = _read_memory_row(tmp_db, "me", "ws-only-init")
    assert row["initiative"] is None


def test_add_initiative_emitted_in_json(tmp_db, tmp_path, monkeypatch, capsys):
    """The resolved initiative key surfaces in the JSON output."""
    from cli.memory import _cmd_add

    monkeypatch.chdir(tmp_path)
    rc = _cmd_add(_add_args(name="json-init-mem", initiative="axisio",
                            workspace="me", json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["initiative"] == "axisio"


# ---------------------------------------------------------------------------
# list / show (curated) / delete / edit
# ---------------------------------------------------------------------------

def _seed_curated(tmp_db, name, type_, body, description=None):
    from gaia.store.writer import upsert_memory
    upsert_memory("me", name, type=type_, body=body,
                  description=description, db_path=tmp_db)


def test_list_returns_rows(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "p1", "project", "body1", description="D1")
    _seed_curated(tmp_db, "u1", "user", "body2", description="D2")

    args = argparse.Namespace(
        type=None, workspace="me", format="json", json=False,
    )
    rc = _cmd_list(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = sorted(r["name"] for r in payload)
    assert names == ["p1", "u1"]


def test_list_filters_by_type(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "p1", "project", "b")
    _seed_curated(tmp_db, "f1", "feedback", "b")

    args = argparse.Namespace(
        type="project", workspace="me", format="count", json=False,
    )
    rc = _cmd_list(args)
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out == "1"


def test_curated_show_prints_body(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_curated_show

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "demo", "project", "the body content",
                  description="some description")

    args = argparse.Namespace(name="demo", workspace="me", json=True)
    rc = _cmd_curated_show(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "demo"
    assert payload["body"] == "the body content"
    assert payload["description"] == "some description"


def test_curated_show_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_curated_show

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(name="ghost", workspace="me", json=False)
    rc = _cmd_curated_show(args)
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_delete_curated_with_yes(tmp_db, tmp_path, monkeypatch, capsys):
    """scan-v2 SV3: the default `gaia memory delete` is a SOFT delete
    (tombstone). The row and its body survive; it just becomes invisible to
    reads (get_memory returns None)."""
    from cli.memory import _cmd_delete
    from gaia.store.writer import get_memory

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "doomed-mem", "project", "body")
    assert _read_memory_row(tmp_db, "me", "doomed-mem") is not None

    args = argparse.Namespace(name="doomed-mem", workspace="me",
                              yes=True, json=False, hard=False)
    rc = _cmd_delete(args)
    assert rc == 0, capsys.readouterr()
    # Row physically survives (tombstone), but is invisible to reads.
    raw = _read_memory_row(tmp_db, "me", "doomed-mem")
    assert raw is not None, "soft-delete must NOT physically remove the row"
    assert get_memory("me", "doomed-mem", db_path=tmp_db) is None
    # Zero filesystem side effects
    assert not (tmp_path / ".claude").exists()


def test_delete_curated_hard_removes_row(tmp_db, tmp_path, monkeypatch, capsys):
    """scan-v2 SV3: `gaia memory delete --hard` physically removes the row
    (explicit human curation)."""
    from cli.memory import _cmd_delete

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "doomed-mem", "project", "body")

    args = argparse.Namespace(name="doomed-mem", workspace="me",
                              yes=True, json=False, hard=True)
    rc = _cmd_delete(args)
    assert rc == 0, capsys.readouterr()
    assert _read_memory_row(tmp_db, "me", "doomed-mem") is None


def test_delete_curated_aborts_on_no(tmp_db, tmp_path, monkeypatch, capsys):
    import builtins
    from cli.memory import _cmd_delete

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "keepme", "project", "body")
    monkeypatch.setattr(builtins, "input", lambda *a, **kw: "n")

    args = argparse.Namespace(name="keepme", workspace="me",
                              yes=False, json=False)
    rc = _cmd_delete(args)
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "abort" in out or "not deleted" in out
    assert _read_memory_row(tmp_db, "me", "keepme") is not None


def test_delete_curated_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_delete

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(name="ghost", workspace="me",
                              yes=True, json=False)
    rc = _cmd_delete(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_edit_curated_overwrite_body(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "patchme", "project", "old body")

    args = argparse.Namespace(
        name="patchme", workspace="me",
        field="body", content="new body", append=False, json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    row = _read_memory_row(tmp_db, "me", "patchme")
    assert row["body"] == "new body"


def test_edit_curated_append_description(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "appendme", "project", "body",
                  description="first")

    args = argparse.Namespace(
        name="appendme", workspace="me",
        field="description", content="second", append=True, json=False,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    row = _read_memory_row(tmp_db, "me", "appendme")
    assert row["description"] == "first\n\nsecond"


def test_edit_curated_invalid_field(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "guarded", "project", "body")

    args = argparse.Namespace(
        name="guarded", workspace="me",
        field="type", content="user", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid memory field" in captured.err.lower()


def test_edit_curated_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        name="ghost", workspace="me",
        field="body", content="x", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err.lower()


def test_edit_curated_empty_content(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "intact", "project", "body")

    args = argparse.Namespace(
        name="intact", workspace="me",
        field="body", content="", append=False, json=False,
    )
    rc = _cmd_edit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "content" in captured.err.lower()


def test_edit_curated_zero_fs_side_effects(tmp_db, tmp_path,
                                           monkeypatch, capsys):
    from cli.memory import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "fs-check", "project", "body")
    args = argparse.Namespace(
        name="fs-check", workspace="me",
        field="body", content="updated", append=False, json=True,
    )
    rc = _cmd_edit(args)
    assert rc == 0, capsys.readouterr()
    assert not (tmp_path / ".claude").exists()
    assert list(tmp_path.rglob("fs-check")) == []


# ---------------------------------------------------------------------------
# Scoped search
# ---------------------------------------------------------------------------

def test_search_scope_memory(tmp_db, tmp_path, monkeypatch, capsys):
    """`--scope=memory` (canonical name) searches the memory_fts mirror only."""
    from cli.memory import _cmd_search_scoped

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "alpha", "project",
                  "the zenithal-mooncrest token appears here")
    _seed_curated(tmp_db, "beta", "project", "unrelated content")

    args = argparse.Namespace(
        query="zenithal-mooncrest", limit=10, scope="memory",
        workspace="me", json=True,
    )
    rc = _cmd_search_scoped(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "memory"
    names = [r["name"] for r in payload["results"]]
    assert "alpha" in names
    assert "beta" not in names


def test_search_scope_curated_alias_warns_and_translates(tmp_db, tmp_path,
                                                         monkeypatch, capsys):
    """`--scope=curated` still works as a deprecated alias for `memory`."""
    from cli.memory import _cmd_search_scoped

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "alpha", "project",
                  "the zenithal-mooncrest token appears here")

    args = argparse.Namespace(
        query="zenithal-mooncrest", limit=10, scope="curated",
        workspace="me", json=True,
    )
    rc = _cmd_search_scoped(args)
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    # Output uses the canonical name ('memory') even when invoked with curated.
    assert payload["scope"] == "memory"
    # Deprecation warning lands on stderr.
    assert "deprecated" in captured.err.lower()


def test_search_scope_both_emits_two_buckets(tmp_db, tmp_path,
                                             monkeypatch, capsys):
    """`--scope=both` returns episodes + curated keys in JSON output."""
    from cli.memory import _cmd_search_scoped

    monkeypatch.chdir(tmp_path)
    _seed_curated(tmp_db, "alpha", "project",
                  "another zenithal-mooncrest mention")

    args = argparse.Namespace(
        query="zenithal-mooncrest", limit=10, scope="both",
        workspace="me", json=True,
    )
    rc = _cmd_search_scoped(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "both"
    assert "episodes" in payload
    assert "curated" in payload
    curated_names = [r["name"] for r in payload["curated"]]
    assert "alpha" in curated_names


def test_add_registers_subcommand_choice():
    """``gaia memory add`` is wired into the argparse tree."""
    import cli.memory as memory_mod

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="subcommand")
    memory_mod.register(subs)

    mem_parser = subs.choices["memory"]
    nested_subs = None
    for action in mem_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            nested_subs = action
            break
    assert nested_subs is not None
    for verb in ("add", "list", "show", "delete", "edit",
                 "episode-show", "search"):
        assert verb in nested_subs.choices, f"missing verb: {verb}"
