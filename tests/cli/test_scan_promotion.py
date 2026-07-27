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
  * a concurrent agent update_contracts merge is not lost: promotion's
    read-merge-write is one transaction, so both writers' keys survive;
  * dry-run (apply=False) writes nothing and materializes no DB file;
  * a hand-authored FLAT contract with >1 promotable project is AUTO-CONVERTED
    to a map, preserving the old workspace-level metadata under a reserved key;
  * promote_workspace is independently invocable (no scan run required).

Isolation: GAIA_DATA_DIR -> tmp_path so ~/.gaia/gaia.db is never touched.
"""

from __future__ import annotations

import json
import sys
import threading
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
# Flat workspace contract with >1 project -> auto-converted to a map,
# preserving the old workspace-level metadata under the reserved key
# ---------------------------------------------------------------------------

def test_flat_contract_with_multiple_projects_is_converted_to_map(tmp_db):
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
    # The flat multi-project contract is auto-converted, not deferred.
    assert rep["shape"] == "flat"
    assert rep["applied"] is True
    assert rep["added_entries"] == 2

    payload = _read_contract(tmp_db, ws)
    # Both scanned projects are now first-class map entries.
    assert "a" in payload and "b" in payload
    assert payload["a"]["local_path"] == "/home/u/ws/me/a"
    assert payload["b"]["local_path"] == "/home/u/ws/me/b"
    # The map no longer has a top-level workspace-identity `name`.
    assert "name" not in payload


def test_flat_multi_conversion_preserves_workspace_metadata(tmp_db):
    """The old flat top-level metadata survives under the reserved key."""
    from tools.scan.promote import promote_workspace
    from gaia.identity_shape import WORKSPACE_META_KEY
    ws = "me-meta"
    _write_contract(tmp_db, ws, {
        "name": "me", "identity": "me", "local_path": "/home/u/ws/me",
        "_source": "hand-authored",
    })
    _seed_project(tmp_db, ws, "a", path="/home/u/ws/me/a", identity="/home/u/ws/me/a/.git")
    _seed_project(tmp_db, ws, "b", path="/home/u/ws/me/b", identity="/home/u/ws/me/b/.git")

    promote_workspace(ws, db_path=tmp_db, apply=True)
    payload = _read_contract(tmp_db, ws)

    # Hand-authored workspace-level data is preserved verbatim, not lost.
    assert WORKSPACE_META_KEY in payload
    meta = payload[WORKSPACE_META_KEY]
    assert meta["name"] == "me"
    assert meta["identity"] == "me"
    assert meta["local_path"] == "/home/u/ws/me"
    assert meta["_source"] == "hand-authored"


def test_scanner_shape_single_project_not_flat_refreshed(tmp_db):
    """A scanner (workspace_repos) shape must never go through _merge_flat,
    which would inject top-level scan-owned keys and corrupt it. It is
    converted to a map with the scanner payload preserved under the reserved
    key instead."""
    from tools.scan.promote import promote_workspace
    from gaia.identity_shape import WORKSPACE_META_KEY
    ws = "scanner-like"
    _write_contract(tmp_db, ws, {
        "_source": "scanner:stack",
        "name": "bild-platform",
        "type": "multi-repo-workspace",
        "workspace_repos": [{"name": "bild-iac", "path": "bild-iac", "role": "iac"}],
    })
    # Exactly ONE promotable -- the pre-fix bug routed this through _merge_flat.
    _seed_project(tmp_db, ws, "bild-iac", path="/abs/bild-iac",
                  identity="/abs/bild-iac/.git")

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["shape"] == "scanner"
    payload = _read_contract(tmp_db, ws)
    # The scanner payload is preserved intact under the reserved key, NOT
    # polluted with a top-level local_path.
    assert "local_path" not in payload
    assert payload[WORKSPACE_META_KEY]["workspace_repos"][0]["name"] == "bild-iac"
    # The project promoted as a first-class map entry.
    assert payload["bild_iac"]["local_path"] == "/abs/bild-iac"


# ---------------------------------------------------------------------------
# The shared shape classifier (gaia.identity_shape) -- one predicate, four forms
# ---------------------------------------------------------------------------

def test_classify_identity_shape_distinguishes_all_forms():
    from gaia.identity_shape import classify_identity_shape

    # empty
    assert classify_identity_shape(None) == "empty"
    assert classify_identity_shape({}) == "empty"

    # map: slug-keyed, values are project dicts, no top-level name
    assert classify_identity_shape({
        "aos_iac": {"name": "AOS - IaC", "local_path": "/x/aos-iac"},
        "svc": {"local_path": "/x/svc"},
    }) == "map"

    # scanner: top-level name PLUS a workspace_repos list
    assert classify_identity_shape({
        "name": "bild-platform", "type": "multi-repo-workspace",
        "workspace_repos": [{"name": "bild-iac", "path": "bild-iac"}],
    }) == "scanner"

    # flat: top-level name, NO workspace_repos
    assert classify_identity_shape({
        "name": "nfi", "type": "application", "local_path": "/x/nfi",
    }) == "flat"


def test_classify_identity_shape_scanner_is_not_flat():
    """The latent-bug guard: a scanner shape must NOT classify as flat, or the
    flat single-project refresh path would corrupt its structured payload."""
    from gaia.identity_shape import classify_identity_shape
    scanner = {"name": "w", "workspace_repos": []}
    assert classify_identity_shape(scanner) == "scanner"
    assert classify_identity_shape(scanner) != "flat"


# ---------------------------------------------------------------------------
# Vanished propagation: a repo gone from disk is MARKED in the contract,
# never deleted and never hidden
# ---------------------------------------------------------------------------

def _mark_gone(tmp_db, ws, *surviving_names):
    """Soft-delete every project of ``ws`` except ``surviving_names``, through
    the same writer the scan's own reconciliation uses."""
    from gaia.store.writer import mark_missing_in
    return mark_missing_in(
        "projects", ws, [(n,) for n in surviving_names], db_path=tmp_db
    )


def test_gate_returns_missing_rows_separately_from_promotable(tmp_db):
    from tools.scan.promote import validate_promotion
    ws = "ws-gate-missing"
    _seed_project(tmp_db, ws, "alive", path="/abs/alive", identity="/abs/alive/.git")
    _seed_project(tmp_db, ws, "ghost", path="/abs/ghost", identity="/abs/ghost/.git")
    _mark_gone(tmp_db, ws, "alive")

    gate = validate_promotion(ws, db_path=tmp_db)
    assert [p["name"] for p in gate["promotable"]] == ["alive"]
    assert [m["name"] for m in gate["missing"]] == ["ghost"]
    # A vanished row is neither promoted nor rejected -- it is its own lane.
    assert gate["rejected"] == []


def test_vanished_project_is_marked_missing_in_the_contract(tmp_db):
    """The defect this closes: a repo deleted from disk used to stay in the
    contract, indistinguishable from a live one."""
    from tools.scan.promote import promote_workspace
    ws = "ws-vanish"
    _seed_project(tmp_db, ws, "alive", path="/abs/alive", identity="/abs/alive/.git")
    _seed_project(tmp_db, ws, "ghost", path="/abs/ghost", identity="/abs/ghost/.git")
    promote_workspace(ws, db_path=tmp_db, apply=True)
    assert "ghost" in _read_contract(tmp_db, ws)

    # The repo disappears; the scan's reconciliation soft-deletes its row.
    _mark_gone(tmp_db, ws, "alive")

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["applied"] is True
    assert rep["outcome"] == "applied"
    assert rep["marked_missing_entries"] == 1

    payload = _read_contract(tmp_db, ws)
    # Marked and still visible -- never deleted, never hidden.
    assert "ghost" in payload
    assert payload["ghost"]["missing_since"]
    assert payload["ghost"]["local_path"] == "/abs/ghost"
    assert "missing_since" not in payload["alive"]


def test_marking_missing_preserves_agent_owned_description_and_structure(tmp_db):
    """The merge is where curated content is protected -- marking a vanished
    entry must not cost the agent-authored keys."""
    from tools.scan.promote import promote_workspace
    ws = "ws-vanish-curated"
    _write_contract(tmp_db, ws, {
        "ghost": {
            "name": "Ghost Service",
            "type": "application",
            "local_path": "/abs/ghost",
            "remote_url": "git@github.com:o/ghost.git",
            "description": "Curated by an agent: the billing edge service",
            "apps": ["api", "worker"],
            "package_manager": "pnpm",
        },
    })
    _seed_project(tmp_db, ws, "ghost", path="/abs/ghost", identity="/abs/ghost/.git")
    _mark_gone(tmp_db, ws)  # nothing survives

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["marked_missing_entries"] == 1

    entry = _read_contract(tmp_db, ws)["ghost"]
    assert entry["missing_since"]
    assert entry["description"] == "Curated by an agent: the billing edge service"
    assert entry["name"] == "Ghost Service"
    assert entry["type"] == "application"
    assert entry["apps"] == ["api", "worker"]
    assert entry["package_manager"] == "pnpm"
    assert entry["local_path"] == "/abs/ghost"


def test_reappearing_project_clears_its_missing_mark(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "ws-reappear"
    _write_contract(tmp_db, ws, {
        "back": {
            "name": "back",
            "local_path": "/abs/back",
            "description": "curated",
            "missing_since": "2020-01-01T00:00:00+00:00",
        },
    })
    _seed_project(tmp_db, ws, "back", path="/abs/back", identity="/abs/back/.git")

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert rep["applied"] is True
    entry = _read_contract(tmp_db, ws)["back"]
    assert "missing_since" not in entry
    assert entry["description"] == "curated"


def test_second_scan_over_a_still_missing_repo_is_a_no_op(tmp_db):
    """The mark is idempotent: a still-vanished repo does not re-write the
    contract on every scan."""
    from tools.scan.promote import promote_workspace
    ws = "ws-vanish-idem"
    _seed_project(tmp_db, ws, "ghost", path="/abs/ghost", identity="/abs/ghost/.git")
    promote_workspace(ws, db_path=tmp_db, apply=True)
    _mark_gone(tmp_db, ws)
    first = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert first["marked_missing_entries"] == 1

    second = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert second["marked_missing_entries"] == 0
    assert second["applied"] is False
    assert second["outcome"] == "no-op"


# ---------------------------------------------------------------------------
# Observability: `outcome` distinguishes the three applied=False situations
# ---------------------------------------------------------------------------

def test_outcome_distinguishes_no_op_from_nothing_promotable(tmp_db):
    from tools.scan.promote import promote_workspace
    ws_empty = "ws-outcome-empty"
    nothing = promote_workspace(ws_empty, db_path=tmp_db, apply=True)
    assert nothing["applied"] is False
    assert nothing["outcome"] == "nothing-promotable"
    assert nothing["shape"] is None  # the merge never ran

    ws = "ws-outcome-noop"
    _seed_project(tmp_db, ws, "svc", path="/abs/svc", identity="/abs/svc/.git")
    first = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert first["outcome"] == "applied"

    again = promote_workspace(ws, db_path=tmp_db, apply=True)
    assert again["applied"] is False
    assert again["outcome"] == "no-op"      # the merge ran; nothing changed
    assert again["shape"] == "map"


def test_outcome_dry_run_is_distinct_from_no_op(tmp_db):
    from tools.scan.promote import promote_workspace
    ws = "ws-outcome-dry"
    _seed_project(tmp_db, ws, "svc", path="/abs/svc", identity="/abs/svc/.git")
    rep = promote_workspace(ws, db_path=tmp_db, apply=False)
    assert rep["applied"] is False
    assert rep["outcome"] == "dry-run"
    assert rep["added_entries"] == 1
    assert _read_contract(tmp_db, ws) is None


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
# Atomicity: promotion vs a concurrent agent update_contracts merge
# ---------------------------------------------------------------------------

AGENT_OWNED_VALUE = "curated by the agent"


def _promote_with_an_interleaved_agent_merge(tmp_db, monkeypatch, ws):
    """Run a promotion with an agent ``update_contracts`` merge forced into the
    seam between promotion's read and its write. Returns the resulting entry.

    The interleaving is FORCED rather than raced, so the check bites every run:
    ``classify_identity_shape`` is called after promotion's read and before its
    write on either connection lifecycle, so it is the seam where the agent
    thread is released. That thread is then awaited only briefly -- long enough
    to complete when promotion holds no write lock (a non-atomic writer, whose
    delta then sits on the row promotion is about to overwrite), and short enough
    that it is still blocked when promotion holds ``BEGIN IMMEDIATE`` (the atomic
    writer, so the delta lands after the COMMIT and merges into fresh data).
    """
    from hooks.modules.context.context_writer import apply_update
    from tools.scan.promote import promote_workspace

    _seed_project(tmp_db, ws, "alpha", path="/abs/alpha",
                  identity="/abs/alpha/.git",
                  remote="git@github.com:me/alpha.git",
                  platform="github", language="python")
    # An entry matched by local_path, with no platform/language yet -- so
    # promotion has a real scan-owned refresh to write.
    _write_contract(tmp_db, ws, {"alpha": {"name": "alpha",
                                           "local_path": "/abs/alpha"}})

    audits: list = []

    def _agent_merge() -> None:
        audits.append(apply_update(
            {"contract": "project_identity",
             "payload": {"alpha": {"description": AGENT_OWNED_VALUE}}},
            "developer", workspace=ws, db_path=tmp_db,
        ))

    thread = threading.Thread(target=_agent_merge)
    import gaia.identity_shape as identity_shape
    real_classify = identity_shape.classify_identity_shape

    def _classify_then_release_the_agent(payload):
        if not thread.is_alive() and not audits:
            thread.start()
            thread.join(timeout=0.5)
        return real_classify(payload)

    monkeypatch.setattr(identity_shape, "classify_identity_shape",
                        _classify_then_release_the_agent)

    rep = promote_workspace(ws, db_path=tmp_db, apply=True)
    thread.join(timeout=30)

    assert not thread.is_alive(), "the agent merge thread never finished"
    assert audits and audits[0]["success"], f"agent merge failed: {audits!r}"
    assert rep["applied"] is True
    return _read_contract(tmp_db, ws)["alpha"]


def test_concurrent_agent_merge_survives_promotion(tmp_db, monkeypatch):
    """A concurrent ``update_contracts`` merge must not be lost by promotion.

    ``project_context_contracts`` has two writers -- promotion here and
    ``context_writer.apply_update`` for an agent's delta -- and both finish with
    ``payload = excluded.payload``. So the write is only safe if the payload it
    replaces was read under the same lock. The property is order-independent:
    the final payload carries the fresh scan-owned keys AND the agent-owned key
    that existed.
    """
    entry = _promote_with_an_interleaved_agent_merge(tmp_db, monkeypatch, "ws-race")
    assert entry.get("description") == AGENT_OWNED_VALUE, (
        "the concurrent agent merge was lost by promotion"
    )
    assert entry["platform"] == "github"
    assert entry["language"] == "python"


def test_interleaving_harness_detects_a_non_atomic_writer(tmp_db, monkeypatch):
    """Counterfactual: the harness above must FAIL a non-atomic writer.

    Without this, the property test could pass vacuously -- an interleaving that
    never actually happens proves nothing. Here promotion's read-modify-write is
    replaced with the same merge across TWO connections (the shape it had before
    the transaction was introduced, built from the same helpers rather than a
    stale copy of them), and the agent's delta is duly lost. The loss is what is
    asserted, so the harness is shown to bite on the exact defect it guards.
    """
    import tools.scan.promote as promote

    def _non_atomic_merge_and_write(workspace, promotable, missing, db_path):
        from gaia.store.writer import _connect
        existing = promote._read_identity_contract(workspace, db_path)
        shape, payload, stats = promote._merge_for_shape(existing, promotable, missing)
        if payload is None or not promote._stats_changed(stats):
            return shape, payload, stats, False
        con = _connect(db_path)
        try:
            promote._upsert_identity_payload(con, workspace, payload)
            con.commit()
        finally:
            con.close()
        return shape, payload, stats, True

    monkeypatch.setattr(promote, "_merge_and_write_atomically",
                        _non_atomic_merge_and_write)

    entry = _promote_with_an_interleaved_agent_merge(
        tmp_db, monkeypatch, "ws-race-nonatomic")
    assert "description" not in entry, (
        "the non-atomic writer did not lose the agent merge -- the harness is "
        "not exercising the interleaving it claims to"
    )
    # The scan-owned side still landed, so the loss is specifically the agent's.
    assert entry["platform"] == "github"


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
