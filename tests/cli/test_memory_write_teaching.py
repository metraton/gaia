"""Writing memory through the CLI shows the shape of a row and warns about the mistakes a user later pays for.

A body or description too long to be read whole, a row that names only a
workspace and so has no owner, a changed fact overwritten in place, and a user
preference that may be covering a Gaia bug each surface at the moment of
writing. They are warnings: the row is still written. The help of
`gaia memory add` carries the claim vocabulary and the three owners.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_GAIA = _REPO / "bin" / "gaia"
for _path in (_REPO, _REPO / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture
def gaia(tmp_path):
    """Run the real CLI against a throwaway HOME, data dir and database."""
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

    return run


def _warning_codes(result: subprocess.CompletedProcess) -> set[str]:
    assert result.returncode == 0, result.stdout + result.stderr
    return {w["code"] for w in json.loads(result.stdout).get("warnings", [])}


def _add(gaia, *argv: str) -> set[str]:
    return _warning_codes(gaia("memory", "add", *argv, "--json"))


def test_add_help_lists_the_claim_vocabulary_the_three_owners_and_the_skill(gaia):
    """A writer reading `gaia memory add --help` learns every claim kind, whose row it is, and where the judgment lives."""
    from gaia.store.memory_placement import KIND_MEANING

    result = gaia("memory", "add", "--help")

    assert result.returncode == 0, result.stderr
    for kind, meaning in KIND_MEANING.items():
        assert kind in result.stdout and meaning.split(";")[0] in result.stdout
    for owner in ("--type=user", "--project=", "--initiative=gaia_system"):
        assert owner in result.stdout
    assert "Skill('memory')" in result.stdout


def test_a_description_or_body_over_its_threshold_warns_and_still_writes(gaia):
    """A row too long to be read whole is flagged when written, not discovered when it is dropped."""
    from cli.memory import _BODY_WARN_CHARS, _DESCRIPTION_WARN_CHARS

    long_description = "Hecho: " + "d" * _DESCRIPTION_WARN_CHARS
    long_body = "b" * (_BODY_WARN_CHARS + 1)

    codes = _add(
        gaia, "--name", "atom_long", "--type", "atom", "--kind", "fact", "--initiative", "demo",
        "--workspace", "demo", "--description", long_description, "--body", long_body,
    )
    short = _add(
        gaia, "--name", "atom_short", "--type", "atom", "--kind", "fact", "--initiative", "demo",
        "--workspace", "demo", "--description", "Hecho: corto.", "--body", "corto",
    )

    assert {"description_long", "body_long"} <= codes
    assert not {"description_long", "body_long"} & short
    assert gaia("memory", "show", "atom_long", "--workspace", "demo").returncode == 0


def test_a_row_scoped_only_by_workspace_is_told_the_workspace_owns_it(gaia):
    """A workspace-only row lands on the workspace owner and says so; the same row with a group key does not."""
    workspace_owned = _add(
        gaia, "--name", "atom_ws", "--type", "atom", "--kind", "fact",
        "--workspace", "demo", "--description", "x holds.", "--body", "x",
    )
    group_owned = _add(
        gaia, "--name", "atom_group", "--type", "atom", "--kind", "fact", "--initiative", "demo",
        "--workspace", "demo", "--description", "x holds.", "--body", "x",
    )

    assert "workspace_owner" in workspace_owned
    assert "workspace_owner" not in group_owned


def test_adding_over_an_existing_name_with_another_body_warns_of_an_in_place_rewrite(gaia):
    """A changed fact written over the old row is flagged and pointed at a new row plus a supersedes link."""
    base = ("--name", "atom_fact", "--type", "atom", "--kind", "fact", "--initiative", "demo",
            "--workspace", "demo", "--description", "Hecho: x.")
    first = _add(gaia, *base, "--body", "version one")
    same = _add(gaia, *base, "--replace", "--body", "version one")
    result = gaia("memory", "add", *base, "--replace", "--body", "version two", "--json")
    changed = _warning_codes(result)

    assert "rewrite_in_place" not in first | same
    assert "rewrite_in_place" in changed
    message = next(
        w["message"] for w in json.loads(result.stdout)["warnings"]
        if w["code"] == "rewrite_in_place"
    )
    assert "--kind=supersedes" in message


def test_a_user_preference_is_asked_whether_it_is_his_difference(gaia):
    """The difference question follows the preference kind, whatever the description opens with."""
    preference = _add(
        gaia, "--name", "user_pref_x", "--type", "user", "--kind", "preference",
        "--description", "Answers: keep them short.", "--body", "Prefers short answers.",
    )
    fact = _add(
        gaia, "--name", "user_fact_x", "--type", "user", "--kind", "fact",
        "--description", "Preference words aside, lives in Chile.", "--body", "Lives in Chile.",
    )

    assert "preference_or_bug" in preference
    assert "preference_or_bug" not in fact


def test_a_curated_type_refuses_a_kind_its_slug_prefix_contradicts(gaia):
    """Until the type column is retired, atom holds facts, decision decisions and negative lessons."""
    result = gaia("memory", "add", "--name", "atom_x", "--type", "atom", "--kind", "lesson",
                  "--workspace", "demo", "--initiative", "demo", "--body", "x", "--json")

    assert result.returncode != 0
    assert "--type=atom holds a fact only" in result.stdout + result.stderr


def test_the_difference_question_routes_a_universal_rule_to_the_harness_and_keeps_walls_out(gaia):
    """What any user would want goes to gaia_system, a bend needs his agreement, and a wall is never stored."""
    result = gaia(
        "memory", "add", "--name", "user_pref_msg", "--type", "user", "--kind", "preference",
        "--description", "Preference: short answers.", "--body", "Prefers short answers.",
        "--json",
    )
    message = next(
        w["message"] for w in json.loads(result.stdout)["warnings"]
        if w["code"] == "preference_or_bug"
    )

    assert "his difference from what Gaia does for anyone" in message
    assert "any user would want" in message
    assert "--type=feedback --kind=defect --initiative=gaia_system" in message
    assert "agreed" in message
    assert "consent, security or the contract" in message
    assert "worked perfectly" not in message


def test_a_preference_that_mentions_a_hook_gets_no_extra_warning(gaia):
    """Whether a preference is really a harness rule is judgment the CLI does not guess at."""
    codes = _add(
        gaia, "--name", "user_pref_hook", "--type", "user", "--kind", "preference",
        "--description", "Preferencia: que el hook recuerde code-standards.",
        "--body", "El hook de PreToolUse debe recordarle al subagente code-standards.",
    )

    assert codes == {"preference_or_bug"}
