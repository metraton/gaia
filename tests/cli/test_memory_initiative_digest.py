"""
v32 -- transversal initiative digest for `gaia memory get-relevant`.

The default (no --types, no --sections, no --initiative) code path emits a
cross-project worklist of LIVE PENDING threads grouped by the canonical
``memory.initiative`` key:

  * AC-1 memory_digest_cross_project: initiatives are grouped and each shows
    its freshest pending item; multiple initiatives appear together.
  * AC-2 memory_injection_cwd_independent: the digest is byte-identical
    regardless of the launch directory -- cwd no longer filters or prioritises.
  * AC-3 memory_pending_by_project: --initiative=X returns the top-N pending
    of THAT initiative, with overflow.
  * AC-4 memory_pending_excludes_resolved: anchors, resolved/snapshot threads,
    and logs are excluded from the digest by design.

Uses a real temporary SQLite DB (writer._connect materialises the schema on
first connect) so the real query path -- including the memory_links supersedes
subquery and the initiative column -- is exercised.
"""

import json
import sys
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cli.memory as memory_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
        ("testws", "testws", "2026-07-15T00:00:00Z"),
    )
    con.commit()
    con.close()
    return db_path


def _insert(db_path, name, *, class_, status, initiative, updated_at,
            type_="atom", desc=None, workspace="testws"):
    desc = desc if desc is not None else f"desc for {name}"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute(
        "INSERT INTO memory (workspace, name, type, description, body, "
        "                    updated_at, class, status, initiative) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (workspace, name, type_, desc, f"body {name}", updated_at,
         class_, status, initiative),
    )
    con.commit()
    con.close()


def _args(**overrides):
    base = {
        "workspace": "testws",
        "limit": 8,
        "max_chars": 1500,
        "types": None,
        "sections": None,
        "initiative": None,
        "json": True,
        "func": memory_mod._cmd_get_relevant,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _run(args, capsys):
    rc = memory_mod._cmd_get_relevant(args)
    out = capsys.readouterr().out
    assert rc == 0
    return json.loads(out)


# ---------------------------------------------------------------------------
# AC-1: cross-project digest
# ---------------------------------------------------------------------------

class TestMemoryDigestCrossProject:
    def test_memory_digest_cross_project(self, tmp_db, capsys):
        # Three initiatives, each with a live pending thread.
        _insert(tmp_db, "gaia_open", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-14T10:00:00Z")
        _insert(tmp_db, "balance_carry", class_="thread", status="carry_forward",
                initiative="balance", updated_at="2026-07-15T09:00:00Z")
        _insert(tmp_db, "branchkinect_open", class_="thread", status="open",
                initiative="branchkinect", updated_at="2026-07-13T08:00:00Z")

        payload = _run(_args(), capsys)
        block = payload["block"]

        # All three initiatives are present in the same digest (transversal).
        initiatives = {i["initiative"] for i in payload["items"]}
        assert {"gaia", "balance", "branchkinect"} <= initiatives
        assert "Pendientes vivos por proyecto" in block
        assert "[gaia]" in block and "[balance]" in block

        # Ordered by recency of freshest pending: balance (07-15) first.
        assert payload["items"][0]["initiative"] == "balance"

    def test_per_initiative_overflow_hint(self, tmp_db, capsys):
        # One initiative with 3 pending -> top-1 shown + "+2 más" hint.
        _insert(tmp_db, "gaia_a", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-15T10:00:00Z")
        _insert(tmp_db, "gaia_b", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-14T10:00:00Z")
        _insert(tmp_db, "gaia_c", class_="thread", status="carry_forward",
                initiative="gaia", updated_at="2026-07-13T10:00:00Z")

        payload = _run(_args(), capsys)
        block = payload["block"]
        gaia_items = [i for i in payload["items"] if i["initiative"] == "gaia"]
        # top-1 only rendered as an item.
        assert len(gaia_items) == 1
        assert gaia_items[0]["pending_count"] == 3
        assert "+2 más en gaia" in block

    def test_global_overflow_beyond_top_k(self, tmp_db, capsys):
        # More initiatives than TOP_K -> "+N proyectos más".
        for i in range(memory_mod._DIGEST_TOP_K + 3):
            _insert(tmp_db, f"proj_{i}_open", class_="thread", status="open",
                    initiative=f"proj{i}", updated_at=f"2026-07-15T{i:02d}:00:00Z")
        payload = _run(_args(), capsys)
        assert payload["overflow_projects"] == 3
        assert "proyectos más" in payload["block"]

    def test_null_initiative_is_otros_bucket(self, tmp_db, capsys):
        _insert(tmp_db, "loose_open", class_="thread", status="open",
                initiative=None, updated_at="2026-07-15T10:00:00Z")
        payload = _run(_args(), capsys)
        assert any(i["initiative"] == "otros" for i in payload["items"])
        assert "[otros]" in payload["block"]


# ---------------------------------------------------------------------------
# AC-2: cwd-independent injection
# ---------------------------------------------------------------------------

class TestMemoryInjectionCwdIndependent:
    def test_memory_injection_cwd_independent(self, tmp_db, tmp_path,
                                              monkeypatch, capsys):
        # Rows anchored to several initiatives.
        _insert(tmp_db, "gaia_open", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-14T10:00:00Z")
        _insert(tmp_db, "balance_open", class_="thread", status="open",
                initiative="balance", updated_at="2026-07-15T10:00:00Z")

        # Simulate launching from a workspace root ...
        root = tmp_path / "ws_root"
        root.mkdir()
        monkeypatch.chdir(root)
        block_root = _run(_args(), capsys)["block"]

        # ... and from inside one project dir.
        proj = tmp_path / "ws_root" / "gaia"
        proj.mkdir()
        monkeypatch.chdir(proj)
        block_proj = _run(_args(), capsys)["block"]

        # The digest is byte-identical -- cwd neither filters nor prioritises.
        assert block_root == block_proj
        assert "[gaia]" in block_root and "[balance]" in block_root


# ---------------------------------------------------------------------------
# AC-3: project mode
# ---------------------------------------------------------------------------

class TestMemoryPendingByProject:
    def test_memory_pending_by_project(self, tmp_db, capsys):
        # gaia has several pending; balance has one (must NOT leak in).
        for i in range(3):
            _insert(tmp_db, f"gaia_{i}", class_="thread", status="open",
                    initiative="gaia", updated_at=f"2026-07-1{i}T10:00:00Z")
        _insert(tmp_db, "balance_x", class_="thread", status="open",
                initiative="balance", updated_at="2026-07-15T10:00:00Z")

        payload = _run(_args(initiative="gaia"), capsys)
        inits = {i["initiative"] for i in payload["items"]}
        names = {i["name"] for i in payload["items"]}
        assert inits == {"gaia"}
        assert "balance_x" not in names
        assert "Pendientes de gaia" in payload["block"]

    def test_project_mode_returns_whole_corpus_uncapped(self, tmp_db, capsys):
        # Well past any historical top-N: every row must come back, and the
        # overflow footer must be gone entirely -- nothing is withheld.
        total = 23
        for i in range(total):
            _insert(tmp_db, f"gaia_{i}", class_="thread", status="open",
                    initiative="gaia", updated_at=f"2026-07-15T{i:02d}:00:00Z")
        payload = _run(_args(initiative="gaia"), capsys)
        assert len(payload["items"]) == total
        assert payload["overflow"] == 0
        assert "más en gaia" not in payload["block"]
        assert payload["block"].count("\n- gaia_") == total

    def test_project_mode_normalises_key(self, tmp_db, capsys):
        # Stored key is normalised; request with a raw label still resolves.
        _insert(tmp_db, "bk_open", class_="thread", status="open",
                initiative="branch_kinect", updated_at="2026-07-15T10:00:00Z")
        payload = _run(_args(initiative="Branch Kinect"), capsys)
        assert {i["name"] for i in payload["items"]} == {"bk_open"}

    def test_project_mode_otros_targets_null(self, tmp_db, capsys):
        _insert(tmp_db, "loose", class_="thread", status="open",
                initiative=None, updated_at="2026-07-15T10:00:00Z")
        _insert(tmp_db, "gaia_open", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-15T10:00:00Z")
        payload = _run(_args(initiative="otros"), capsys)
        names = {i["name"] for i in payload["items"]}
        assert names == {"loose"}


# ---------------------------------------------------------------------------
# Whole-corpus retrieval: body projected, description untruncated
# ---------------------------------------------------------------------------

# The fixed body shape of a promoted defect (skills/memory/reference.md). The
# retrieval surface must return it intact: the consumer splits on "## " to
# recover the fields, so a body truncated anywhere destroys the last field.
_DEFECT_HEADINGS = ("## Symptom", "## Component", "## Evidence",
                    "## Reproduction")


def _defect_body() -> str:
    return "\n\n".join(
        f"{h}\ndetail for {h[3:].lower()}" for h in _DEFECT_HEADINGS
    )


class TestProjectModeCorpusShape:
    def test_body_is_projected_and_splits_into_the_four_fields(
            self, tmp_db, capsys):
        _insert(tmp_db, "feedback_x_y", class_="thread", status="carry_forward",
                initiative="gaia_system", updated_at="2026-07-15T10:00:00Z",
                type_="feedback")
        con = sqlite3.connect(str(tmp_db))
        con.execute("UPDATE memory SET body = ? WHERE name = ?",
                    (_defect_body(), "feedback_x_y"))
        con.commit()
        con.close()

        payload = _run(_args(initiative="gaia_system"), capsys)
        item = payload["items"][0]
        assert item["class"] == "thread"
        assert item["memory_status"] == "carry_forward"
        assert item["initiative"] == "gaia_system"
        headings = [
            line.strip() for line in item["body"].splitlines()
            if line.startswith("## ")
        ]
        assert headings == list(_DEFECT_HEADINGS)

    def test_description_is_returned_verbatim(self, tmp_db, capsys):
        # Longer than every truncation limit in the module, so any surviving
        # cap shows up as a shortened string rather than a silent pass.
        long_desc = "D" * (max(memory_mod._RELEVANT_ITEM_DESC_MAX,
                               memory_mod._DIGEST_DESC_MAX) * 4)
        _insert(tmp_db, "feedback_long", class_="thread",
                status="carry_forward", initiative="gaia_system",
                updated_at="2026-07-15T10:00:00Z", desc=long_desc)
        payload = _run(_args(initiative="gaia_system"), capsys)
        assert payload["items"][0]["description"] == long_desc
        assert "…" not in payload["block"]


# ---------------------------------------------------------------------------
# Regression: the SessionStart injection renderers are NOT widened
# ---------------------------------------------------------------------------

class TestInjectionRenderersUnchanged:
    """The digest and section renderers share the read path project mode
    widened. They feed an unrequested SessionStart block, so they must stay
    capped, char-budgeted and body-less."""

    def _seed_many(self, tmp_db, n=14, desc_len=400):
        for i in range(n):
            _insert(tmp_db, f"row_{i}", class_="thread", status="carry_forward",
                    initiative=f"proj{i}", updated_at=f"2026-07-15T{i:02d}:00:00Z",
                    desc="L" * desc_len)

    def test_digest_block_did_not_grow(self, tmp_db, capsys):
        self._seed_many(tmp_db)
        payload = _run(_args(max_chars=1500), capsys)
        assert len(payload["block"]) <= 1500
        assert len(payload["items"]) <= memory_mod._DIGEST_TOP_K
        for item in payload["items"]:
            assert item["description"].endswith("…")
            assert len(item["description"]) <= memory_mod._DIGEST_DESC_MAX + 1
            assert "body" not in item

    def test_digest_block_is_invariant_to_body_size(self, tmp_db, capsys):
        # Differential oracle for "the injection block did not grow": the body
        # is now SELECTed on this shared path, so prove it never reaches the
        # rendered block by growing it 5000x and diffing the two renders.
        self._seed_many(tmp_db, n=4, desc_len=80)
        before = _run(_args(max_chars=1500), capsys)["block"]
        con = sqlite3.connect(str(tmp_db))
        con.execute("UPDATE memory SET body = ?", ("B" * 5000,))
        con.commit()
        con.close()
        after = _run(_args(max_chars=1500), capsys)["block"]
        assert after == before

    def test_sections_renderer_still_capped(self, tmp_db, capsys):
        self._seed_many(tmp_db)
        payload = _run(_args(sections="carry_forward", max_chars=800), capsys)
        assert len(payload["block"]) <= 800
        assert len(payload["items"]) <= memory_mod._RELEVANT_CARRY_FORWARD_CAP
        for item in payload["items"]:
            assert len(item["description"]) <= (
                memory_mod._RELEVANT_ITEM_DESC_MAX + 1
            )
            assert "body" not in item


# ---------------------------------------------------------------------------
# AC-4: excludes resolved / snapshots / anchors
# ---------------------------------------------------------------------------

class TestMemoryPendingExcludesResolved:
    def test_memory_pending_excludes_resolved(self, tmp_db, capsys):
        # LIVE pending (must appear).
        _insert(tmp_db, "gaia_open", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-15T10:00:00Z")
        _insert(tmp_db, "gaia_carry", class_="thread", status="carry_forward",
                initiative="gaia", updated_at="2026-07-14T10:00:00Z")
        # Resolved thread (must be excluded).
        _insert(tmp_db, "gaia_resolved", class_="thread", status="resolved",
                initiative="gaia", updated_at="2026-07-15T11:00:00Z")
        # Anchor (durable fact -- excluded by design).
        _insert(tmp_db, "gaia_anchor", class_="anchor", status=None,
                initiative="gaia", updated_at="2026-07-15T12:00:00Z")
        # Log (never injects).
        _insert(tmp_db, "gaia_log", class_="log", status=None,
                initiative="gaia", updated_at="2026-07-15T13:00:00Z")

        payload = _run(_args(), capsys)
        names = {i["name"] for i in payload["items"]}
        gaia_bucket = [i for i in payload["items"] if i["initiative"] == "gaia"]

        assert "gaia_resolved" not in names
        assert "gaia_anchor" not in names
        assert "gaia_log" not in names
        # Only the two live-pending threads count toward the bucket.
        assert gaia_bucket[0]["pending_count"] == 2

    def test_supersedes_destination_excluded(self, tmp_db, capsys):
        _insert(tmp_db, "gaia_old", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-14T10:00:00Z")
        _insert(tmp_db, "gaia_new", class_="thread", status="open",
                initiative="gaia", updated_at="2026-07-15T10:00:00Z")
        con = sqlite3.connect(str(tmp_db))
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            ("testws", "gaia_new", "gaia_old", "supersedes",
             "2026-07-15T10:00:00Z"),
        )
        con.commit()
        con.close()

        payload = _run(_args(), capsys)
        names = {i["name"] for i in payload["items"]}
        assert "gaia_old" not in names
        assert "gaia_new" in names
