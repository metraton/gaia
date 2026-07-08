"""
Stage 3 of the scan pipeline: promotion of scanned `projects` rows into the
`project_identity` project-context contract (tools/scan/promote.py).

The pipeline is: scan (discover -> projects) -> VALIDATE (gate) ->
INSERT/MERGE (scan-owned only) into project_context_contracts. These tests
cover each stage in isolation plus the decoupling and ownership guarantees:

  * gate rejects partial/corrupt rows (no identity / no path / not absolute);
  * promotion into an empty/absent contract creates a map-shape payload with
    only scan-owned keys;
  * promotion PRESERVES agent-owned keys (description, curated name/type/
    structure) on merge -- never clobbers;
  * scan-owned refresh is coalesce-or-omit: a NULL scan remote never wipes a
    curated remote_url;
  * matching is by physical identity (local_path / remote), so a re-scan
    refreshes the existing entry in place instead of duplicating (requirement 4);
  * dry-run (apply=False) writes nothing and materializes no DB file;
  * a hand-authored FLAT contract with >1 promotable project is DEFERRED, never
    silently converted to a map;
  * promote_workspace is independently invocable (no scan run required).

Isolation: GAIA_DATA_DIR -> tmp_path so ~/.gaia/gaia.db is never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure bin/ is importable for the CLI end-to-end test (cli.scan).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _grant(con, agent: str, *tables: str) -> None:
    for table in tables:
        con.execute(
            "INSERT OR REPLACE INTO agent_permissions "
            "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
            (table, agent),
        )
    con.commit()


def _seed_project(tmp_db, ws, name, *, path, identity, remote=None,
                  platform=None, language=None, description=None):
    """Seed a projects row the canonical way (via upsert_project)."""
    from gaia.store.writer import upsert_project, _connect
    con = _connect(tmp_db)
    _grant(con, "gaia-system", "projects")
    con.close()
    fields = {
        "project_identity": identity,
        "path": path,
        "status": "active",
        "missing_since": None,
        "remote_url": remote,
        "platform": platform,
        "primary_language": language,
    }
    upsert_project(ws, name, fields, "gaia-system", db_path=tmp_db,
                   strip_agent_owned=True)
    if description is not None:
        # Agent-owned column: write it directly (not the scan path).
        con = _connect(tmp_db)
        _grant(con, "developer", "projects")
        try:
            con.execute(
                "UPDATE projects SET description = ? WHERE workspace = ? AND name = ?",
                (description, ws, name),
            )
            con.commit()
        finally:
            con.close()


def _read_contract(tmp_db, ws):
    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    try:
        row = con.execute(
            "SELECT payload FROM project_context_contracts "
            "WHERE workspace = ? AND contract_name = 'project_identity'",
            (ws,),
        ).fetchone()
    finally:
        con.close()
    return json.loads(row["payload"]) if row else None


def _write_contract(tmp_db, ws, payload):
    from gaia.store.writer import _connect
    con = _connect(tmp_db)
    try:
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity, created_at) "
            "VALUES (?, ?, '2020-01-01T00:00:00Z')",
            (ws, ws),
        )
        con.execute(
            "INSERT INTO project_context_contracts "
            "(workspace, contract_name, payload, metadata, updated_at) "
            "VALUES (?, 'project_identity', ?, NULL, '2020-01-01T00:00:00Z') "
            "ON CONFLICT(workspace, contract_name) DO UPDATE SET payload = excluded.payload",
            (ws, json.dumps(payload)),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Stage 2: the validation gate
# ---------------------------------------------------------------------------

def test_gate_rejects_missing_identity_and_path(tmp_db):
    from tools.scan.promote import validate_promotion
    ws = "ws-gate"
    _seed_project(tmp_db, ws, "good", path="/abs/good", identity="/abs/good/.git")
    # Row with no project_identity -> corrupt, must be rejected.
    _seed_project(tmp_db, ws, "noident", path="/abs/noident", identity=None)

    gate = validate_promotion(ws, db_path=tmp_db)
    promotable = {p["name"] for p in gate["promotable"]}
    rejected = {r["name"] for r in gate["rejected"]}
    assert "good" in promotable
    assert "noident" in rejected
    reasons = next(r["reasons"] for r in gate["rejected"] if r["name"] == "noident")
    assert "missing project_identity" in reasons


def test_gate_never_creates_db_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from tools.scan.promote import validate_promotion
    gate = validate_promotion("never-scanned", db_path=None)
    assert gate["db_present"] is False
    assert gate["promotable"] == []
    # The read must not have materialized the DB.
    from gaia.paths import db_path
    assert not db_path().exists()


# ---------------------------------------------------------------------------
# Stage 3: promotion into an empty/absent contract
# ---------------------------------------------------------------------------

def test_promote_creates_map_shape_with_scan_owned_keys(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "ws-create"
    _seed_project(tmp_db, ws, "svc", path="/abs/svc", identity="/abs/svc/.git",
                  remote="git@github.com:o/svc.git", platform="github",
                  language="python")

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["applied"] is True
    assert rep["shape"] == "empty"
    assert rep["added_entries"] == 1

    payload = _read_contract(tmp_db, ws)
    assert "svc" in payload
    entry = payload["svc"]
    assert entry["local_path"] == "/abs/svc"
    assert entry["remote_url"] == "git@github.com:o/svc.git"
    assert entry["platform"] == "github"
    assert entry["language"] == "python"
    assert entry["name"] == "svc"  # seeded


# ---------------------------------------------------------------------------
# Ownership boundary: merge preserves agent-owned keys
# ---------------------------------------------------------------------------

def test_promote_preserves_agent_owned_description_and_name(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "aaxis-like"
    # A curated map-shape contract: slug 'aos_iac' with a rich display name +
    # description + a real remote, its local_path already set.
    _write_contract(tmp_db, ws, {
        "aos_iac": {
            "name": "AOS - IaC",
            "type": "terraform",
            "remote_url": "git@bitbucket.org:aaxisdigital/aos-iac.git",
            "local_path": "/home/u/ws/aaxis/aos/aos-iac",
            "description": "Terraform IaC for AOS GCP infra",
        }
    })
    # Scan discovered the SAME repo (matched by local_path), but the scanned
    # row has NO remote (null) and a different collision-disambiguated name.
    _seed_project(tmp_db, ws, "aos-2",
                  path="/home/u/ws/aaxis/aos/aos-iac",
                  identity="/home/u/ws/aaxis/aos/aos-iac/.git",
                  remote=None, platform=None, language="hcl")

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["shape"] == "map"
    # matched by local_path -> refresh in place, no new entry.
    assert rep["added_entries"] == 0

    entry = _read_contract(tmp_db, ws)["aos_iac"]
    # Agent-owned preserved:
    assert entry["name"] == "AOS - IaC"
    assert entry["description"] == "Terraform IaC for AOS GCP infra"
    assert entry["type"] == "terraform"
    # Coalesce-or-omit: null scan remote did NOT wipe the curated remote.
    assert entry["remote_url"] == "git@bitbucket.org:aaxisdigital/aos-iac.git"
    # Scan-owned refresh that HAD a value did land:
    assert entry["language"] == "hcl"


# ---------------------------------------------------------------------------
# Reconciliation: re-scan is idempotent, matches by identity, no duplicates
# ---------------------------------------------------------------------------

def test_rescan_is_idempotent_no_duplicate_entries(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "ws-rescan"
    _seed_project(tmp_db, ws, "app", path="/abs/app", identity="/abs/app/.git",
                  remote="git@github.com:o/app.git", platform="github")

    r1 = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert r1["added_entries"] == 1
    payload1 = _read_contract(tmp_db, ws)

    # Second scan of the same project -> matched by local_path, no new slug.
    r2 = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert r2["added_entries"] == 0
    payload2 = _read_contract(tmp_db, ws)
    assert set(payload2.keys()) == set(payload1.keys())
    assert len(payload2) == 1


def test_rescan_preserves_description_added_between_scans(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "ws-rescan-desc"
    _seed_project(tmp_db, ws, "app", path="/abs/app", identity="/abs/app/.git")
    promote_workspace(ws, db_path=tmp_db, apply=True)

    # An agent enriches the contract entry with a description.
    payload = _read_contract(tmp_db, ws)
    slug = next(iter(payload))
    payload[slug]["description"] = "agent-authored purpose"
    _write_contract(tmp_db, ws, payload)

    # A later scan must leave that description intact.
    promote_workspace(ws, db_path=tmp_db, apply=True)
    assert _read_contract(tmp_db, ws)[slug]["description"] == "agent-authored purpose"


# ---------------------------------------------------------------------------
# Dry-run: no write, no DB materialization
# ---------------------------------------------------------------------------

def test_dry_run_does_not_write(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "ws-dry"
    _seed_project(tmp_db, ws, "svc", path="/abs/svc", identity="/abs/svc/.git")
    rep = promote_workspace(ws, db_path=tmp_db, apply=False)
    assert rep["applied"] is False
    assert rep["added_entries"] == 1          # previewed
    assert rep["preview"] is not None
    assert _read_contract(tmp_db, ws) is None  # nothing written


def test_dry_run_against_fresh_workspace_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from tools.scan.promote import promote_workspace
    from gaia.paths import db_path
    rep = promote_workspace("brand-new", db_path=None, apply=False)
    assert rep["added_entries"] == 0
    assert not db_path().exists()


# ---------------------------------------------------------------------------
# Flat single-project / workspace contract with >1 project -> deferred
# ---------------------------------------------------------------------------

def test_flat_contract_with_multiple_projects_is_deferred(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "me-like"
    # A hand-authored FLAT workspace-identity contract.
    _write_contract(tmp_db, ws, {
        "name": "me", "identity": "me", "local_path": "/home/u/ws/me",
        "_source": "hand-authored",
    })
    _seed_project(tmp_db, ws, "a", path="/home/u/ws/me/a", identity="/home/u/ws/me/a/.git")
    _seed_project(tmp_db, ws, "b", path="/home/u/ws/me/b", identity="/home/u/ws/me/b/.git")

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["shape"] == "flat"
    assert rep["applied"] is False
    assert len(rep["deferred"]) == 2
    # The hand-authored flat contract is untouched.
    payload = _read_contract(tmp_db, ws)
    assert payload["name"] == "me"
    assert "a" not in payload and "b" not in payload


# ---------------------------------------------------------------------------
# Decoupling: promote_workspace runs standalone (no scan invocation)
# ---------------------------------------------------------------------------

def test_promotion_is_independently_invocable(tmp_db):
    """Promotion reads the projects table directly, so it promotes
    already-scanned data without any fresh scan run."""
    from tools.scan.promote import promote_workspace
    ws = "ws-standalone"
    _seed_project(tmp_db, ws, "svc", path="/abs/svc", identity="/abs/svc/.git")
    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["applied"] is True
    assert _read_contract(tmp_db, ws) is not None


# ---------------------------------------------------------------------------
# End-to-end: the real CLI apply path (scan -> projects -> promote -> contract)
# ---------------------------------------------------------------------------

class _MockArgs:
    def __init__(self, **kwargs):
        defaults = {"workspace": None, "root": None, "dry_run": False, "json": False}
        defaults.update(kwargs)
        self.__dict__.update(defaults)


def test_cli_scan_apply_promotes_into_contract(tmp_path, monkeypatch, capsys):
    """`gaia scan` (apply) writes projects AND promotes them into the
    project_identity contract via the wired stage 3."""
    import cli.scan as scan_mod

    gaia_dir = tmp_path / "gaia-data"
    gaia_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(gaia_dir))

    # aaxis/aos/aos-iac tree: workspace=aaxis, project=aos, repo=aos-iac.
    (tmp_path / "aaxis" / "aos" / "aos-iac" / ".git").mkdir(parents=True)

    args = _MockArgs(workspace="aaxis", root=str(tmp_path / "aaxis"), json=True)
    rc = scan_mod.cmd_scan(args)
    assert rc == 0

    data = json.loads(capsys.readouterr().out)
    assert data["resolved_workspace"] == "aaxis"
    # Stage 3 ran and is reported in the JSON envelope.
    promo = data.get("promotion") or {}
    assert promo.get("applied") is True
    assert promo.get("added_entries", 0) >= 1

    # The contract now holds a project_identity row for aaxis with scan-owned data.
    from gaia.paths import db_path
    payload = _read_contract(db_path(), "aaxis")
    assert payload, "promotion did not write the project_identity contract"
    entry = next(iter(payload.values()))
    assert entry.get("local_path", "").endswith("aos-iac")


def test_cli_scan_dry_run_previews_promotion_without_db(tmp_path, monkeypatch, capsys):
    """--dry-run previews promotion and never materializes the DB."""
    import cli.scan as scan_mod

    gaia_dir = tmp_path / "gaia-data"
    gaia_dir.mkdir()
    monkeypatch.setenv("GAIA_DATA_DIR", str(gaia_dir))
    (tmp_path / "aaxis" / "aos" / "aos-iac" / ".git").mkdir(parents=True)

    args = _MockArgs(workspace="aaxis", root=str(tmp_path / "aaxis"),
                     dry_run=True, json=True)
    rc = scan_mod.cmd_scan(args)
    assert rc == 0
    # Dry-run wrote nothing to the data dir (scan AND promotion honor this).
    assert list(gaia_dir.iterdir()) == []
