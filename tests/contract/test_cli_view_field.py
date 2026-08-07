"""
`gaia contract view --field <dotted-path>` -- read ONE subtree of the draft.

`view` without `--field` returns the FULL envelope (unchanged behavior).
`view --field <dotted-path>` returns ONLY the subtree that dotted path points
at, addressed with the SAME dotted-path scheme `set`/`add`/`fill` use (the
shared `_split_path` tokenizer, no second parser). An invalid or absent path
is a clean, non-zero-exit error -- never a raw traceback -- and this is a
read-only path: it never mutates the draft, never touches the validator, the
finalized row, or the SubagentStop gate.

Style mirrors test_cli_draft_key.py: real subprocesses against
`bin/cli/contract.py`'s standalone shim, each under an isolated
`GAIA_DATA_DIR` (no `bin/gaia`, no DB bootstrap).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

VALID_AGENT_ID = valid_agent_id("a1234abcd")


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def base_env(tmp_path, monkeypatch):
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    return dict(os.environ)


def _init_draft(env: dict) -> str:
    proc = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)
    assert proc.returncode == 0, f"init failed: stderr={proc.stderr!r}"
    return json.loads(proc.stdout)["draft_id"]


# ---------------------------------------------------------------------------
# --field returns ONLY the pointed-at subtree (a nested list).
# ---------------------------------------------------------------------------
def test_view_field_returns_only_nested_list_subtree(base_env):
    draft_id = _init_draft(base_env)
    for f in ("gaia/contract/view.py", "bin/cli/contract.py"):
        add = _run(["add", "evidence_report.files_checked", f], base_env)
        assert add.returncode == 0, f"add failed: stderr={add.stderr!r}"

    view = _run(["view", "--field", "evidence_report.files_checked"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"

    out = json.loads(view.stdout)
    # ONLY the subtree, not the full envelope -- so it is exactly the list,
    # with no draft_id/envelope wrapper and no sibling keys.
    assert out == ["gaia/contract/view.py", "bin/cli/contract.py"]


def test_view_field_returns_nested_dict_subtree(base_env):
    """A dotted path to an intermediate dict returns that whole dict subtree,
    still without the surrounding envelope or sibling top-level keys."""
    _init_draft(base_env)
    view = _run(["view", "--field", "evidence_report"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"

    out = json.loads(view.stdout)
    assert isinstance(out, dict)
    # All 7 evidence keys present; NO agent_status / consolidation_report leak.
    assert set(out) == {
        "patterns_checked",
        "files_checked",
        "commands_run",
        "key_outputs",
        "verbatim_outputs",
        "cross_layer_impacts",
        "open_gaps",
    }
    assert "agent_status" not in out


def test_view_field_returns_scalar_leaf(base_env):
    """A dotted path to a scalar leaf returns just that scalar."""
    _init_draft(base_env)
    view = _run(["view", "--field", "agent_status.agent_state"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"
    assert json.loads(view.stdout) == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# Empty-but-EXISTING vs. absent: two distinct responses, never confused.
# A fresh draft's evidence_report lists start empty and its optional dict
# fields start null -- both are real, present keys with no content, which
# must read as "exists, empty" (exit 0) and never as "does not exist" (exit
# 1, the case covered above).
# ---------------------------------------------------------------------------
def test_view_field_existing_empty_list_is_not_an_error(base_env):
    _init_draft(base_env)
    view = _run(["view", "--field", "evidence_report.cross_layer_impacts"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"
    assert view.stderr.strip() == ""
    assert json.loads(view.stdout) == []


def test_view_field_existing_null_value_is_not_an_error(base_env):
    _init_draft(base_env)
    view = _run(["view", "--field", "consolidation_report"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"
    assert view.stderr.strip() == ""
    assert json.loads(view.stdout) is None


def test_view_field_empty_and_absent_are_distinguishable_pair(base_env):
    """The same draft, two neighboring calls: one path exists and is empty,
    the other does not exist at all -- exit code alone already tells them
    apart, and stdout is empty on the error side where the empty-but-real
    field printed content (`[]`) on the success side."""
    _init_draft(base_env)
    empty = _run(["view", "--field", "evidence_report.cross_layer_impacts"], base_env)
    absent = _run(["view", "--field", "evidence_report.cross_layer_impact"], base_env)  # typo'd, absent

    assert empty.returncode == 0
    assert json.loads(empty.stdout) == []

    assert absent.returncode != 0
    assert absent.stdout.strip() == ""
    assert "cross_layer_impact" in absent.stderr

    assert empty.returncode != absent.returncode


# ---------------------------------------------------------------------------
# An absent path is a clean, non-zero-exit error -- never a raw traceback.
# ---------------------------------------------------------------------------
def test_view_field_nonexistent_nested_path_clean_error(base_env):
    _init_draft(base_env)
    view = _run(["view", "--field", "evidence_report.does_not_exist"], base_env)

    assert view.returncode != 0
    assert view.stdout.strip() == ""  # nothing printed to stdout on error
    assert "Traceback" not in view.stderr  # clean message, not a crash
    # Names the failing field so the caller can fix it.
    assert "does_not_exist" in view.stderr


def test_view_field_nonexistent_top_level_clean_error(base_env):
    _init_draft(base_env)
    view = _run(["view", "--field", "bogus_top_level"], base_env)

    assert view.returncode != 0
    assert "Traceback" not in view.stderr
    assert "bogus_top_level" in view.stderr


def test_view_field_through_scalar_is_clean_error(base_env):
    """Descending PAST a scalar leaf (treating it as a dict) is a clean error,
    not a traceback -- the read helper never blindly indexes a non-dict."""
    _init_draft(base_env)
    view = _run(
        ["view", "--field", "agent_status.agent_state.deeper"], base_env
    )
    assert view.returncode != 0
    assert "Traceback" not in view.stderr
    assert "deeper" in view.stderr


# ---------------------------------------------------------------------------
# Regression: view WITHOUT --field still returns the FULL envelope, unchanged.
# ---------------------------------------------------------------------------
def test_view_without_field_returns_full_envelope(base_env):
    draft_id = _init_draft(base_env)
    view = _run(["view"], base_env)
    assert view.returncode == 0, f"stderr={view.stderr!r}"

    out = json.loads(view.stdout)
    # The historical shape: a {draft_id, envelope} wrapper around the whole
    # envelope with every top-level section present.
    assert out["draft_id"] == draft_id
    env = out["envelope"]
    assert set(env) >= {
        "agent_status",
        "evidence_report",
        "consolidation_report",
        "approval_request",
    }
    assert env["agent_status"]["agent_id"] == VALID_AGENT_ID


# ---------------------------------------------------------------------------
# --json shapes a --field (or plain view) ERROR as machine-readable JSON,
# matching every other contract subcommand's --json. Success output was
# already valid JSON either way (the subtree or the full envelope); --json
# only changes the ERROR path, which used to be unreachable for `view`
# because the flag itself was never wired into argparse.
# ---------------------------------------------------------------------------
def test_view_field_absent_path_json_error_shape(base_env):
    _init_draft(base_env)
    view = _run(
        ["view", "--field", "evidence_report.does_not_exist", "--json"], base_env
    )
    assert view.returncode != 0
    assert view.stderr.strip() == ""  # JSON error goes to stdout, not stderr
    payload = json.loads(view.stdout)
    assert payload["status"] == "error"
    assert "does_not_exist" in payload["error"]


def test_view_no_draft_found_json_error_shape(base_env):
    view = _run(["view", "--draft-id", "abogus.doesnotexist", "--json"], base_env)
    assert view.returncode != 0
    payload = json.loads(view.stdout)
    assert payload["status"] == "error"
    assert "abogus.doesnotexist" in payload["error"]


def test_view_harness_id_not_found_json_error_shape(base_env):
    """Regression: --harness-id used to hardcode plain-text errors regardless
    of --json (the flag was checked only after this early return)."""
    view = _run(["view", "--harness-id", "no-such-harness-id", "--json"], base_env)
    assert view.returncode != 0
    payload = json.loads(view.stdout)
    assert payload["status"] == "error"
    assert "no-such-harness-id" in payload["error"]


# ---------------------------------------------------------------------------
# --field is READ-ONLY: it never mutates the draft on disk.
# ---------------------------------------------------------------------------
def test_view_field_does_not_mutate_draft(base_env):
    draft_id = _init_draft(base_env)
    data_dir = Path(base_env["GAIA_DATA_DIR"])
    draft_file = data_dir / "contract_drafts" / f"{draft_id}.json"
    before = draft_file.read_text(encoding="utf-8")

    # A successful read and a failed read both leave the file byte-identical.
    assert _run(["view", "--field", "evidence_report"], base_env).returncode == 0
    assert _run(["view", "--field", "nope"], base_env).returncode != 0

    assert draft_file.read_text(encoding="utf-8") == before
