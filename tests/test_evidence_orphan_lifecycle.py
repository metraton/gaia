"""
Evidence caducity by state, no orphans left behind (AC-5).

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

Covers the three independent halves of AC-5:

  (a) Retiring (hard-deleting) a brief no longer abandons its blob-backed
      evidence on disk -- ``delete_brief`` clears every blob before the
      brief row disappears.
  (b) ``gaia.evidence.store.delete_evidence`` -- the evidence deletion
      route -- has at least one real caller in the tree, not just its own
      definition and docstring mention.
  (c) ``gaia.evidence.orphans.find_orphan_blobs`` detects and reports EVERY
      blob file with no referencing row, as a property over an arbitrary
      set of referenced and unreferenced files -- never pinned to a
      specific count, name, or path from any one machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# (a) retiring a brief no longer abandons its blobs on disk
# ---------------------------------------------------------------------------

def test_deleting_brief_removes_its_blob_backed_evidence():
    from gaia.briefs.store import upsert_brief, delete_brief
    from gaia.evidence.fs import write_blob
    from gaia.evidence.store import insert_evidence
    from gaia.paths.resolver import evidence_dir

    workspace = "me"
    brief_name = "brief-evidence-retire-test"
    upsert_brief(workspace, brief_name, {"title": "t", "objective": "o"})

    from gaia.briefs.store import get_brief
    brief = get_brief(workspace, brief_name)
    brief_id = brief["id"]

    # Deposit a blob-backed evidence row directly (bypassing the CLI's
    # inline-vs-blob branching -- irrelevant to this test).
    path, size = write_blob(workspace, brief_name, "AC-1", b"x" * 4097, ext=".bin")
    assert path.exists()
    insert_evidence(
        workspace, brief_id, "AC-1",
        type="file", artifact_path=str(path), size_bytes=size,
    )

    deleted = delete_brief(workspace, brief_name)
    assert deleted is True
    assert not path.exists(), (
        "delete_brief left a blob-backed evidence file on disk after "
        "retiring its owning brief"
    )

    # No loose FILE remains anywhere under the evidence root for a brief
    # that no longer exists (the acceptance criterion is about files, not
    # about whether now-empty parent directories were also pruned).
    brief_subtree = evidence_dir() / workspace / brief_name
    leftover_files = [p for p in brief_subtree.rglob("*") if p.is_file()] \
        if brief_subtree.exists() else []
    assert leftover_files == []


# ---------------------------------------------------------------------------
# (b) the evidence deletion route has at least one real caller
# ---------------------------------------------------------------------------
#
# A text/regex scan cannot tell an invocation from a mention: it matches the
# symbol equally inside a comment, this module's own docstring, or the
# "Public API::" listing in gaia/evidence/store.py -- none of which run the
# function. Parsing the AST and matching only an ``ast.Call`` node whose
# callee resolves to the name ``delete_evidence`` is immune to all three: a
# comment is not parsed at all, and a docstring is an ``ast.Constant``, never
# an ``ast.Call``. Deleting the one real call site (in gaia/briefs/store.py)
# must turn this test red even with the symbol still spelled out in a
# comment or docstring elsewhere in the tree -- verified by temporarily
# removing that call, confirming red, then restoring it.

import ast


def _delete_evidence_call_sites() -> list[str]:
    """Every ``bin/``, ``gaia/``, ``hooks/`` source location that actually
    INVOKES ``delete_evidence(...)`` -- i.e. contains an ``ast.Call`` node
    whose callee name is ``delete_evidence``. A definition (``def
    delete_evidence``), a docstring mention, or a comment never produces an
    ``ast.Call`` node, so none of those can satisfy this check."""
    sites: list[str] = []
    for base in ("bin", "gaia", "hooks"):
        root = _REPO_ROOT / base
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None
                )
                if name == "delete_evidence":
                    sites.append(f"{path}:{node.lineno}")
    return sites


def test_delete_evidence_has_a_real_caller_outside_its_definition():
    call_sites = _delete_evidence_call_sites()
    # The definition module (gaia/evidence/store.py) itself only ever
    # DEFINES delete_evidence; it is never self-called. So any genuine call
    # site found anywhere in bin/, gaia/, hooks/ is a real caller -- the
    # property under test is "at least one exists", not which file it is in.
    assert call_sites, (
        "delete_evidence() has no real caller in bin/, gaia/, or hooks/ -- "
        "it is written but nothing invokes it"
    )


# ---------------------------------------------------------------------------
# (c) the sweep detects and reports EVERY blob without a referencing row
# ---------------------------------------------------------------------------

def test_find_orphan_blobs_reports_every_unreferenced_file_and_only_those():
    from gaia.briefs.store import upsert_brief, get_brief
    from gaia.evidence.fs import write_blob
    from gaia.evidence.store import insert_evidence
    from gaia.evidence.orphans import find_orphan_blobs

    workspace = "me"
    brief_name = "brief-evidence-orphan-sweep-test"
    upsert_brief(workspace, brief_name, {"title": "t", "objective": "o"})
    brief_id = get_brief(workspace, brief_name)["id"]

    # Two REFERENCED blobs: written and then recorded in a row.
    referenced_paths = set()
    for ac_id in ("AC-1", "AC-2"):
        path, size = write_blob(workspace, brief_name, ac_id, b"y" * 4097, ext=".bin")
        insert_evidence(
            workspace, brief_id, ac_id,
            type="file", artifact_path=str(path), size_bytes=size,
        )
        referenced_paths.add(path.resolve())

    # Two UNREFERENCED blobs: written straight to the filesystem, exactly
    # the shape a rejected pre-fix deposit (or any future regression) would
    # leave behind -- no matching evidence row at all.
    orphan_paths = set()
    for ac_id in ("AC-3", "AC-4"):
        path, _size = write_blob(workspace, brief_name, ac_id, b"z" * 10, ext=".bin")
        orphan_paths.add(path.resolve())

    detected = {p.resolve() for p in find_orphan_blobs()}

    assert detected == orphan_paths, (
        f"orphan sweep must report exactly the unreferenced blobs and no "
        f"referenced ones -- expected {orphan_paths!r}, got {detected!r}"
    )
    assert detected.isdisjoint(referenced_paths)


def test_find_orphan_blobs_fails_closed_when_db_unreadable(tmp_path, monkeypatch):
    """With gaia.db unreachable, the sweep must report nothing rather than
    flag every file as an orphan -- mirroring gaia.retention.fs_rules's
    fail-closed posture (absence of evidence means 'cannot confirm', never
    'assume the worst')."""
    from gaia.evidence.orphans import find_orphan_blobs

    # Point GAIA_DATA_DIR somewhere with no gaia.db at all, then write a
    # loose file directly under what would be the evidence root.
    isolated = tmp_path / "no_db_here"
    monkeypatch.setenv("GAIA_DATA_DIR", str(isolated))

    from gaia.paths.resolver import evidence_dir
    root = evidence_dir()
    root.mkdir(parents=True, exist_ok=True)
    loose_file = root / "some-workspace" / "some-brief" / "AC-1" / "deadbeef.bin"
    loose_file.parent.mkdir(parents=True, exist_ok=True)
    loose_file.write_bytes(b"orphan-shaped-but-unverifiable")

    assert find_orphan_blobs() == []
