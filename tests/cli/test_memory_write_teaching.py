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
    from gaia.store.memory_claims import MEMORY_CLAIM_KINDS

    result = gaia("memory", "add", "--help")

    assert result.returncode == 0, result.stderr
    for kind in MEMORY_CLAIM_KINDS:
        assert f"{kind}:" in result.stdout
    for owner in ("--type=user", "--project=", "--initiative=gaia_system"):
        assert owner in result.stdout
    assert "Skill('memory')" in result.stdout


def test_a_description_or_body_over_its_threshold_warns_and_still_writes(gaia):
    """A row too long to be read whole is flagged when written, not discovered when it is dropped."""
    from cli.memory import _BODY_WARN_CHARS, _DESCRIPTION_WARN_CHARS

    long_description = "Hecho: " + "d" * _DESCRIPTION_WARN_CHARS
    long_body = "b" * (_BODY_WARN_CHARS + 1)

    codes = _add(
        gaia, "--name", "atom_long", "--type", "atom", "--initiative", "demo",
        "--workspace", "demo", "--description", long_description, "--body", long_body,
    )
    short = _add(
        gaia, "--name", "atom_short", "--type", "atom", "--initiative", "demo",
        "--workspace", "demo", "--description", "Hecho: corto.", "--body", "corto",
    )

    assert {"description_long", "body_long"} <= codes
    assert not {"description_long", "body_long"} & short
    assert gaia("memory", "show", "atom_long", "--workspace", "demo").returncode == 0


def test_a_non_user_row_scoped_only_by_workspace_warns_that_it_has_no_owner(gaia):
    """A container is not an owner: a workspace-only row warns, the same row with a project key does not."""
    ownerless = _add(
        gaia, "--name", "feedback_ownerless", "--type", "feedback",
        "--workspace", "demo", "--description", "Hecho: x.", "--body", "x",
    )
    owned = _add(
        gaia, "--name", "feedback_owned", "--type", "feedback", "--initiative", "demo",
        "--workspace", "demo", "--description", "Hecho: x.", "--body", "x",
    )

    assert "no_owner" in ownerless
    assert "no_owner" not in owned


def test_adding_over_an_existing_name_with_another_body_warns_of_an_in_place_rewrite(gaia):
    """A changed fact written over the old row is flagged and pointed at a new row plus a supersedes link."""
    base = ("--name", "atom_fact", "--type", "atom", "--initiative", "demo",
            "--workspace", "demo", "--description", "Hecho: x.")
    first = _add(gaia, *base, "--body", "version one")
    same = _add(gaia, *base, "--body", "version one")
    result = gaia("memory", "add", *base, "--body", "version two", "--json")
    changed = _warning_codes(result)

    assert "rewrite_in_place" not in first | same
    assert "rewrite_in_place" in changed
    message = next(
        w["message"] for w in json.loads(result.stdout)["warnings"]
        if w["code"] == "rewrite_in_place"
    )
    assert "--kind=supersedes" in message


def test_a_user_preference_is_asked_whether_it_would_hold_if_gaia_worked_perfectly(gaia):
    """The perfect-Gaia question follows a user preference and not a user fact."""
    preference = _add(
        gaia, "--name", "user_pref_x", "--type", "user",
        "--description", "Preferencia: respuestas breves.", "--body", "Prefiere respuestas breves.",
    )
    fact = _add(
        gaia, "--name", "user_fact_x", "--type", "user",
        "--description", "Hecho: vive en Chile.", "--body", "Vive en Chile.",
    )

    assert "preference_or_bug" in preference
    assert "preference_or_bug" not in fact


def test_a_preference_that_mentions_a_hook_gets_no_extra_warning(gaia):
    """Whether a preference is really a harness rule is judgment the CLI does not guess at."""
    codes = _add(
        gaia, "--name", "user_pref_hook", "--type", "user",
        "--description", "Preferencia: que el hook recuerde code-standards.",
        "--body", "El hook de PreToolUse debe recordarle al subagente code-standards.",
    )

    assert codes == {"preference_or_bug"}
