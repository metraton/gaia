"""``gaia memory move`` re-homes a row in place; ``list --json`` names owners; ``show --no-count`` reads without counting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from gaia.paths import db_path
    return db_path()


@pytest.fixture()
def corpus(tmp_db):
    """Rows of the container workspace ``ws``: two linked decisions, one with history, and a group lesson."""
    from gaia.store.writer import insert_memory_link, upsert_memory
    upsert_memory("ws", "decision_alpha", type="decision", kind="decision", body="first")
    upsert_memory("ws", "decision_alpha", type="decision", kind="decision", body="second",
                  replace=True)
    upsert_memory("ws", "decision_beta", type="decision", kind="decision", body="beta")
    upsert_memory("ws", "negative_gamma", type="negative", kind="lesson", body="gamma",
                  initiative="branchkinect")
    insert_memory_link("ws", "decision_alpha", "decision_beta", "derived_from")
    insert_memory_link("ws", "decision_beta", "decision_alpha", "relates_to")
    return tmp_db


def _run(capsys, *argv):
    import cli.memory as memory_mod
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", *argv])
    rc = args.func(args)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_move_keeps_slug_body_links_and_history_and_records_the_old_owner(corpus, capsys):
    from gaia.store.reader import memory_history_for, memory_links_for
    from gaia.store.writer import get_memory

    rc, out, err = _run(capsys, "move", "decision_alpha", "--to", "group:me/branchkinect",
                        "--from", "ws", "--json")

    assert rc == 0, err
    assert json.loads(out)["owner_to"] == "group"
    assert get_memory("ws", "decision_alpha") is None
    moved = get_memory("me", "decision_alpha")
    assert (moved["body"], moved["owner_level"], moved["initiative"]) == (
        "second", "group", "branchkinect")
    edges = {(e["src_name"], e["dst_name"], e["kind"])
             for e in memory_links_for("me", ["decision_alpha"])}
    assert edges == {("decision_alpha", "decision_beta", "derived_from"),
                     ("decision_beta", "decision_alpha", "relates_to")}
    history = memory_history_for("me", ["decision_alpha"])
    assert [h["after_body"] for h in history] == ["second", "second"]
    assert (history[-1]["before_workspace"], history[-1]["before_initiative"]) == ("ws", None)

    rc, out, _ = _run(capsys, "show", "decision_alpha", "--workspace", "me", "--history", "--json")
    assert rc == 0
    assert json.loads(out)["history"][-1]["workspace_from"] == "ws"


def test_move_refuses_a_kind_the_target_cannot_hold_and_a_taken_slug(corpus, capsys):
    from gaia.store.writer import get_memory, upsert_memory

    rc, out, _ = _run(capsys, "move", "negative_gamma", "--to", "workspace:me", "--from", "ws",
                      "--json")
    refused = json.loads(out)
    assert (rc, refused["code"]) == (1, "kind_refused")
    assert "group or project" in refused["error"]
    assert get_memory("ws", "negative_gamma")["initiative"] == "branchkinect"

    upsert_memory("me", "decision_beta", type="decision", kind="decision", body="other")
    rc, out, _ = _run(capsys, "move", "decision_beta", "--workspace", "me", "--from", "ws",
                      "--json")
    assert (rc, json.loads(out)["code"]) == (1, "name_collision")
    assert get_memory("ws", "decision_beta")["body"] == "beta"


def test_batch_reports_each_line_and_partial_failure(corpus, capsys, tmp_path):
    batch = tmp_path / "moves.tsv"
    batch.write_text(
        "# slug\towner\tkind\n"
        "decision_alpha\tworkspace:me\n"
        "negative_gamma\tworkspace:me\n"
        "decision_beta\tnowhere\n"
        "negative_gamma\tproject:me/absent\tlesson\n",
        encoding="utf-8",
    )

    rc, out, _ = _run(capsys, "move", "--from-file", str(batch), "--from", "ws", "--json")

    report = json.loads(out)
    assert rc == 1
    assert report["status"] == "partial"
    assert report["counts"] == {"moved": 1, "noop": 0, "refused": 3}
    assert [(r["line"], r["status"], r.get("code")) for r in report["results"]] == [
        (2, "moved", None), (3, "refused", "kind_refused"),
        (4, "refused", "bad_owner_spec"), (5, "refused", "target_unresolved")]


def test_orchestrator_lane_admits_move_and_a_specialist_is_refused(monkeypatch):
    from modules.security import gaia_cli_only_guard as guard
    from modules.security.subagent_memory_write_guard import check

    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)
    command = "memory move decision_alpha --to workspace:me --from ws"
    assert guard.check(f"/trusted/node_modules/gaia/bin/gaia {command}",
                       {"session_id": "s"}) == (True, None)
    allowed, reason = check(f"gaia {command}", is_subagent=True, agent_type="developer")
    assert not allowed and reason

    from modules.security.tiers import SecurityTier, classify_command_tier
    assert classify_command_tier(f"gaia {command}") != SecurityTier.T3_BLOCKED


def test_list_json_names_the_owner_and_leaves_user_rows_out(corpus, capsys):
    from gaia.store.writer import upsert_memory
    upsert_memory("ws", "user_prefers_x", type="user", kind="preference", body="x",
                  description="Everywhere: prefer x")

    rc, out, _ = _run(capsys, "list", "--workspace", "ws", "--json")
    rows = {r["name"]: r for r in json.loads(out)}
    assert rc == 0
    assert "user_prefers_x" not in rows
    assert (rows["negative_gamma"]["owner"], rows["negative_gamma"]["initiative"],
            rows["negative_gamma"]["workspace"]) == ("group", "branchkinect", "ws")
    assert rows["decision_alpha"]["owner"] == "workspace"

    _, out, _ = _run(capsys, "list", "--workspace", "ws", "--include-user", "--json")
    assert {"user_prefers_x": "user"}.items() <= {
        r["name"]: r["owner"] for r in json.loads(out)}.items()


def test_show_no_count_leaves_deliberate_count_unchanged(corpus, capsys):
    from gaia.store.writer import get_memory

    rc, _, _ = _run(capsys, "show", "decision_beta", "--workspace", "ws", "--no-count")
    assert rc == 0
    assert get_memory("ws", "decision_beta")["deliberate_count"] == 0

    _run(capsys, "show", "decision_beta", "--workspace", "ws")
    assert get_memory("ws", "decision_beta")["deliberate_count"] == 1
