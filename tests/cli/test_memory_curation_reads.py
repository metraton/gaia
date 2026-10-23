"""Curation reads over the curated memory: what is live, what a search finds, what to merge.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import cli.memory as memory_mod  # noqa: E402

OLD = "user_pref_team_messages_v1"
NEW = "user_pref_team_messages_v2"
LOG = "user_log_pull_request_formatting"


@pytest.fixture(autouse=True)
def _direct_caller(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _run(*argv: str) -> int:
    parser = argparse.ArgumentParser()
    memory_mod.register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["memory", *argv])
    return args.func(args)


def _out(capsys, *argv: str) -> str:
    capsys.readouterr()
    assert _run(*argv) == 0
    return capsys.readouterr().out


def _json(capsys, *argv: str):
    return json.loads(_out(capsys, *argv, "--json"))


def _add_user(name: str, cls: str, body: str) -> None:
    assert _run("add", "--type=user", f"--name={name}", f"--class={cls}",
                f"--description=row {name}", f"--body={body}") == 0


@pytest.fixture()
def lineage():
    _add_user(OLD, "anchor", "Team messages in Teams are short.")
    _add_user(NEW, "anchor", "Team messages in Teams are short, with blank lines between ideas.")
    _add_user(LOG, "log", "Session notes on formatting pull requests.")
    assert _run("link", NEW, OLD, "--kind=supersedes") == 0


def _names(rows) -> list[str]:
    return [row["name"] for row in rows]


def test_list_hides_a_superseded_row_by_default(lineage, capsys):
    names = _names(_json(capsys, "list", "--type=user"))

    assert NEW in names
    assert OLD not in names


def test_list_count_follows_the_live_rows(lineage, capsys):
    assert _out(capsys, "list", "--type=user", "--format=count").strip() == "2"
    assert _out(capsys, "list", "--type=user", "--format=count",
                "--include-superseded").strip() == "3"


def test_include_superseded_names_the_successor(lineage, capsys):
    rows = {r["name"]: r for r in _json(capsys, "list", "--include-superseded")}

    assert rows[OLD]["superseded_by"] == NEW
    assert rows[NEW]["superseded_by"] is None


def test_table_shows_successor_and_class_as_status(lineage, capsys):
    lines = _out(capsys, "list", "--type=user", "--include-superseded").splitlines()
    header = next(line for line in lines if line.startswith("NAME"))
    old_line = next(line for line in lines if line.startswith(OLD))
    log_line = next(line for line in lines if line.startswith(LOG))

    assert "SUPERSEDED_BY" in header
    assert NEW in old_line
    assert log_line.split()[2:4] == ["log", "log"]


def _found(capsys, query: str, *flags: str) -> list[str]:
    payload = _json(capsys, "search", query, "--scope=memory", *flags)
    return _names(payload["results"])


def test_search_finds_rows_holding_any_word_most_matching_first(lineage, capsys):
    found = _found(capsys, "blank-lines teams short formatting")

    assert set(found) == {NEW, LOG}
    assert found[0] == NEW


def test_search_hides_a_superseded_row_unless_asked(lineage, capsys):
    assert OLD not in _found(capsys, "teams short")
    assert OLD in _found(capsys, "teams short", "--include-superseded")


def test_search_drops_stopwords_from_a_plain_multi_word_query(lineage, capsys):
    assert _found(capsys, "para the in formatting") == [LOG]
    assert _found(capsys, "para the in formatting", "--all") == [LOG]


def test_search_keeps_stopwords_inside_quotes_and_fts_syntax(lineage, capsys):
    assert NEW in _found(capsys, "in OR formatting")
    assert NEW in _found(capsys, '"in" OR formatting')
    assert _found(capsys, '"messages in teams"') == [NEW]


def test_search_of_only_stopwords_still_searches(lineage, capsys):
    assert NEW in _found(capsys, "the in")


def test_search_all_requires_every_word_wherever_it_sits(lineage, capsys):
    assert _found(capsys, "blank-lines teams short", "--all") == [NEW]
    assert _found(capsys, "teams formatting", "--all") == []


def test_search_passes_explicit_operators_through(lineage, capsys):
    assert set(_found(capsys, "blank OR formatting")) == {NEW, LOG}


def test_search_filters_by_type_and_class(lineage, capsys):
    assert _found(capsys, "teams", "--type=feedback") == []
    assert _found(capsys, "formatting", "--class=log") == [LOG]
    assert _found(capsys, "formatting", "--class=anchor") == []


def test_link_supersedes_several_old_rows_at_once(capsys):
    _add_user("user_fact_team_v3", "anchor", "The team is five people.")
    _add_user("user_fact_team_v1", "anchor", "The team is three people.")
    _add_user("user_fact_team_v2", "anchor", "The team is four people.")

    for _ in range(2):
        assert _run("link", "user_fact_team_v3", "user_fact_team_v1",
                    "user_fact_team_v2", "--kind=supersedes") == 0

    rows = {r["name"]: r for r in _json(capsys, "list", "--include-superseded")}
    assert rows["user_fact_team_v1"]["superseded_by"] == "user_fact_team_v3"
    assert rows["user_fact_team_v2"]["superseded_by"] == "user_fact_team_v3"
    assert _names(_json(capsys, "list")) == ["user_fact_team_v3"]


def _copy_row_to_workspace(name: str, workspace: str) -> None:
    """Plant the legacy shape a duplicate comes from: one user slug left in a project workspace."""
    from gaia.paths import db_path

    con = sqlite3.connect(str(db_path()))
    try:
        con.execute(
            "INSERT INTO memory (workspace, name, type, description, body, class, updated_at) "
            "SELECT ?, name, type, description, body, class, updated_at FROM memory WHERE name = ?",
            (workspace, name),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture()
def overlapping():
    from gaia.store.writer import upsert_memory

    text = "Deploys run through the release pipeline every Friday afternoon."
    upsert_memory("me", "user_fact_release_day", type="user", body=text, class_="anchor")
    _copy_row_to_workspace("user_fact_release_day", "me")
    upsert_memory("me", "user_fact_release_day_v2", type="user",
                  body=text + " Never on Mondays.", class_="anchor")
    upsert_memory("me", "user_log_release_day", type="user", body=text, class_="log")
    upsert_memory("me", "project_release_day", type="project", initiative="gaia",
                  body=text, class_="anchor")
    upsert_memory("me", "project_release_day_v2", type="project", initiative="gaia",
                  body=text + " Never on Mondays.", class_="anchor")


def _pair(entry: dict) -> set[str]:
    return {entry["a"]["name"], entry["b"]["name"]}


def test_conflicts_keeps_duplicated_slugs_apart(overlapping, capsys):
    payload = _json(capsys, "conflicts", "--owner=user")

    assert [_pair(d) for d in payload["duplicates"]] == [{"user_fact_release_day"}]
    assert all(len(_pair(c)) == 2 for c in payload["candidates"])


def test_conflicts_narrows_to_one_owner_without_log_rows(overlapping, capsys):
    payload = _json(capsys, "conflicts", "--owner=user", "--exclude-class=log")
    entries = payload["candidates"] + payload["duplicates"]

    assert {e["owner"] for e in entries} == {"user"}
    assert not any("user_log_release_day" in _pair(e) for e in entries)
    assert {e["owner"] for e in _json(capsys, "conflicts", "--owner=gaia")["candidates"]} == {
        "project:gaia"}


def test_conflicts_limit_bounds_each_section(overlapping, capsys):
    payload = _json(capsys, "conflicts", "--limit=1")

    assert len(payload["candidates"]) == 1
    assert len(payload["duplicates"]) == 1


def test_stats_counts_live_and_superseded_without_scanning(lineage, capsys, monkeypatch):
    monkeypatch.setattr(memory_mod, "_import_conflict_detector", pytest.fail)

    counts = _json(capsys, "stats")["memory"]

    assert counts["user"] == {"live": 2, "superseded": 1}


def _map_module():
    spec = importlib.util.spec_from_file_location("gaia_cli_map", _REPO_ROOT / "bin" / "cli" / "map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_map_offers_curate_and_reuse_intents():
    gaia_map = _map_module()
    intents = {i.key: gaia_map.render_intent(i) for i in gaia_map.INTENTS}

    assert "gaia memory conflicts --owner" in intents["curate"]
    assert "--kind supersedes" in intents["curate"]
    assert "gaia contract proposals list" in intents["reuse"]
