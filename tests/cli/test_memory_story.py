"""
Tests for the curated-memory READ layer: ``gaia memory story <slug>``,
``gaia memory show --links|--history``, and the ``show --json`` class/status
gap fix.

Read-only (T0). All queries route through ``gaia.store.reader`` helpers
(``PRAGMA query_only = ON``); nothing here mutates production state -- the
substrate DB is redirected into ``tmp_path`` via ``GAIA_DATA_DIR``.

Coverage:
  * multi-node lineage -> fused, chronologically ordered timeline
  * note with no links -> single-node story
  * show --links / --history on a note WITH and WITHOUT data
  * show --json emits class + status (the gap fix)
  * depth limit + cycle safety over a link cycle
  * story --json payload is well-formed {nodes, edges, timeline, final_states}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path bootstrap (mirrors tests/cli/test_memory_link.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into tmp_path; clear leaked dispatch env."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


def _build_parser():
    """Argparse harness mirroring the registered `gaia memory` layout."""
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    memory_mod.register(subparsers)
    return parser, memory_mod


@pytest.fixture()
def lineage(tmp_db):
    """Seed a three-node lineage with history and links.

    proj_a (thread, open -> carry_forward, one append)
      -[graduated_to]-> proj_b (anchor)
      -[supersedes]->    proj_c (older, retired)
    """
    from gaia.store.writer import (
        upsert_memory, reclassify_memory, update_memory_field,
        insert_memory_link,
    )
    upsert_memory("me", "proj_a", type="project", body="start")
    reclassify_memory("me", "proj_a", class_="thread", status="open")
    update_memory_field("me", "proj_a", "body", "appended text", append=True)
    reclassify_memory("me", "proj_a", status="carry_forward")
    upsert_memory("me", "proj_b", type="project", body="anchor body")
    reclassify_memory("me", "proj_b", class_="anchor")
    upsert_memory("me", "proj_c", type="project", body="old body")
    insert_memory_link("me", "proj_a", "proj_b", "graduated_to")
    insert_memory_link("me", "proj_b", "proj_c", "supersedes")
    return tmp_db


# ---------------------------------------------------------------------------
# reader.build_memory_story -- lineage + fused timeline
# ---------------------------------------------------------------------------

def test_multi_node_lineage_fused_timeline(lineage):
    from gaia.store.reader import build_memory_story
    story = build_memory_story("me", "proj_a")

    names = {n["name"] for n in story["nodes"]}
    assert names == {"proj_a", "proj_b", "proj_c"}

    # seed role + graph roles resolved.
    role = {n["name"]: n["role"] for n in story["nodes"]}
    assert role["proj_a"] == "queried"
    assert role["proj_b"] == "graduation_target"
    assert role["proj_c"] == "superseded"

    # Timeline is chronologically ordered (changed_at ascending, stable).
    ts = [ev["ts"] for ev in story["timeline"] if ev["ts"]]
    assert ts == sorted(ts)

    kinds = [ev["kind"] for ev in story["timeline"]]
    assert "birth" in kinds          # approximate birth emitted
    assert "append" in kinds         # the update_memory_field append
    assert "status" in kinds         # open -> carry_forward transition
    assert "link" in kinds           # edges surfaced as events

    # Birth is flagged approximate.
    births = [ev for ev in story["timeline"] if ev["kind"] == "birth"]
    assert births and all(ev.get("approximate") is True for ev in births)

    # Append carries a positive char delta.
    appends = [ev for ev in story["timeline"] if ev["kind"] == "append"]
    assert appends and all(ev["body_delta"] > 0 for ev in appends)

    # Final-state table reflects current class/status per node.
    finals = {f["name"]: f for f in story["final_states"]}
    assert finals["proj_a"]["class"] == "thread"
    assert finals["proj_a"]["status"] == "carry_forward"
    assert finals["proj_b"]["class"] == "anchor"
    assert finals["proj_b"]["status"] is None


def test_single_node_story_no_links(tmp_db):
    from gaia.store.writer import upsert_memory
    from gaia.store.reader import build_memory_story
    upsert_memory("me", "lonely_note", type="project", body="just me")

    story = build_memory_story("me", "lonely_note")
    assert [n["name"] for n in story["nodes"]] == ["lonely_note"]
    assert story["edges"] == []
    # A freshly-added row (INSERT, never UPDATEd) fires no history trigger, so
    # the timeline is empty -- but the node still appears in final_states.
    assert len(story["final_states"]) == 1
    assert story["final_states"][0]["name"] == "lonely_note"
    assert story["final_states"][0]["role"] == "queried"


def test_depth_limit_and_cycle_safety(tmp_db):
    """A link cycle must not loop forever; max_depth bounds reach."""
    from gaia.store.writer import upsert_memory, insert_memory_link
    from gaia.store.reader import build_memory_story
    # Cycle: a -> b -> c -> a, plus a far chain c -> d -> e.
    for slug in ("proj_a", "proj_b", "proj_c", "proj_d", "proj_e"):
        upsert_memory("me", slug, type="project", body="x")
    insert_memory_link("me", "proj_a", "proj_b", "relates_to")
    insert_memory_link("me", "proj_b", "proj_c", "relates_to")
    insert_memory_link("me", "proj_c", "proj_a", "relates_to")  # closes cycle
    insert_memory_link("me", "proj_c", "proj_d", "relates_to")
    insert_memory_link("me", "proj_d", "proj_e", "relates_to")

    # Terminates (cycle-safe) and reaches the whole connected component.
    story = build_memory_story("me", "proj_a", max_depth=5)
    assert {n["name"] for n in story["nodes"]} == {
        "proj_a", "proj_b", "proj_c", "proj_d", "proj_e",
    }

    # A tight depth cap prunes the far nodes but still terminates.
    shallow = build_memory_story("me", "proj_a", max_depth=1)
    reached = {n["name"] for n in shallow["nodes"]}
    assert "proj_a" in reached
    assert "proj_e" not in reached  # 4 hops away, beyond depth 1


# ---------------------------------------------------------------------------
# CLI: gaia memory story
# ---------------------------------------------------------------------------

def test_cli_story_json_well_formed(lineage, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "story", "proj_a",
                              "--workspace=me", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    payload = json.loads(captured.out)
    assert set(payload) >= {"nodes", "edges", "timeline", "final_states"}
    assert payload["seed"] == "proj_a"
    assert isinstance(payload["nodes"], list)
    assert isinstance(payload["timeline"], list)
    assert len(payload["edges"]) == 2


def test_cli_story_narration_default(lineage, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "story", "proj_a", "--workspace=me"])
    rc = args.func(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Story of memory 'proj_a'" in out
    assert "Timeline" in out
    assert "Final state" in out
    assert "proj_b" in out and "proj_c" in out


def test_cli_story_missing_slug_errors(tmp_db, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "story", "ghost", "--workspace=me"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in (captured.err + captured.out).lower()


# ---------------------------------------------------------------------------
# CLI: gaia memory show --json emits class + status (the gap fix)
# ---------------------------------------------------------------------------

def test_show_json_emits_class_and_status(lineage, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "show", "proj_a",
                              "--workspace=me", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    payload = json.loads(captured.out)
    assert payload["class"] == "thread"
    assert payload["status"] == "carry_forward"
    # Existing keys preserved (backward compatible).
    assert payload["name"] == "proj_a"
    assert "body" in payload


# ---------------------------------------------------------------------------
# CLI: gaia memory show --links
# ---------------------------------------------------------------------------

def test_show_links_with_data(lineage, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "show", "proj_b",
                              "--workspace=me", "--links", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    payload = json.loads(captured.out)
    # proj_b is dst of graduated_to (in) and src of supersedes (out).
    kinds_in = {e["kind"] for e in payload["links"]["in"]}
    kinds_out = {e["kind"] for e in payload["links"]["out"]}
    assert "graduated_to" in kinds_in
    assert "supersedes" in kinds_out
    # created_at is carried on every edge.
    for e in payload["links"]["in"] + payload["links"]["out"]:
        assert "created_at" in e


def test_show_links_without_data(tmp_db, capsys):
    from gaia.store.writer import upsert_memory
    upsert_memory("me", "solo", type="project", body="b")
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "show", "solo",
                              "--workspace=me", "--links", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["links"] == {"out": [], "in": []}


# ---------------------------------------------------------------------------
# CLI: gaia memory show --history
# ---------------------------------------------------------------------------

def test_show_history_with_data(lineage, capsys):
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "show", "proj_a",
                              "--workspace=me", "--history", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0, f"stderr={captured.err}"
    payload = json.loads(captured.out)
    hist = payload["history"]
    assert len(hist) >= 2  # append + status transitions
    # Version index reports fields + size delta, NOT full bodies.
    for hv in hist:
        assert "changed_at" in hv
        assert "fields_changed" in hv
        assert "body_delta" in hv
        assert "before_body" not in hv
        assert "after_body" not in hv
    # At least one body-growing version exists.
    assert any("body" in hv["fields_changed"] and hv["body_delta"] > 0
               for hv in hist)
    # A status transition version exists.
    assert any("status" in hv["fields_changed"] for hv in hist)


def test_show_history_without_data(tmp_db, capsys):
    from gaia.store.writer import upsert_memory
    upsert_memory("me", "fresh", type="project", body="b")
    parser, _ = _build_parser()
    args = parser.parse_args(["memory", "show", "fresh",
                              "--workspace=me", "--history", "--json"])
    rc = args.func(args)
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    # A never-UPDATEd row has no history rows.
    assert payload["history"] == []
