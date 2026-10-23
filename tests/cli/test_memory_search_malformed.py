"""A memory search whose text is not valid FTS5 syntax falls back to its plain words and never prints a traceback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_GAIA = Path(__file__).resolve().parents[2] / "bin" / "gaia"

FINDS_THE_ROW = ["(teams", '"formato', "teams AND", "teams OR", "^teams", "C++ teams", "teams -formato"]
FINDS_NOTHING = ["NOT", "", "AND", '"', "(", "++"]


@pytest.fixture
def search(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    env = {
        **os.environ,
        "HOME": str(home),
        "GAIA_DATA_DIR": str(data),
        "GAIA_DB": str(data / "gaia.db"),
    }
    env.pop("GAIA_DISPATCH_AGENT", None)

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_GAIA), *argv],
            env=env, cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    added = run(
        "memory", "add", "--name", "decision_teams_format", "--type", "decision",
        "--initiative", "demo", "--workspace", "demo", "--class", "thread",
        "--status", "open", "--description", "Decision: teams format.",
        "--body", "teams formato PR",
    )
    assert added.returncode == 0, added.stderr
    return lambda query, *flags: run(
        "memory", "search", query, "--workspace", "demo", "--json", *flags,
    )


@pytest.mark.parametrize("query", FINDS_THE_ROW, ids=repr)
def test_a_malformed_query_searches_its_plain_words(search, query):
    result = search(query)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    names = {row["name"] for row in json.loads(result.stdout)["curated"]}
    assert names == {"decision_teams_format"}


@pytest.mark.parametrize("query", FINDS_NOTHING, ids=repr)
def test_a_query_with_no_usable_word_returns_no_rows_without_a_traceback(search, query):
    result = search(query)

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["curated"] == []


@pytest.mark.parametrize("query", ["(teams", '"formato'], ids=repr)
def test_the_memory_scope_falls_back_the_same_way(search, query):
    result = search(query, "--scope", "memory")

    assert "Traceback" not in result.stderr, result.stderr
    assert [row["name"] for row in json.loads(result.stdout)["results"]] == ["decision_teams_format"]


def test_valid_fts_syntax_is_still_honored(search):
    assert [row["name"] for row in json.loads(search("team*").stdout)["curated"]] == ["decision_teams_format"]
    assert json.loads(search("teams AND nosuchword").stdout)["curated"] == []
