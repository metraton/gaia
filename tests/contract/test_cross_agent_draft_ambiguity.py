"""
Security/UX fix -- ``gaia contract`` must never silently guess ACROSS agents.

Confirmed bug: ``bin/cli/contract.py::_resolve_draft_id`` (-> ``gaia.contract
.drafts.resolve_draft_id``) fell back to ``list_draft_ids(agent_id=None)``
when a subcommand (``fill``/``set``/``add``/``view``/``validate``/``finalize``)
was invoked with NEITHER ``--draft-id`` NOR ``--agent-id``. That glob spans
EVERY agent's drafts, so the most-recently-modified draft across the WHOLE
system won -- meaning agent A's plain ``gaia contract view`` (or a mutating
``set``/``add``/``fill``) could silently operate on agent B's draft, with no
warning and no error.

The fix (``gaia.contract.drafts.resolve_draft_id`` /
``AmbiguousDraftError``): when both flags are omitted, resolution is safe by
elimination --

    * 0 drafts system-wide -> None (unchanged; callers report "no draft").
    * exactly 1 draft system-wide -> that one, no ambiguity (unchanged
      silent fallback -- this is the safe case the bug report calls out as
      fine to keep).
    * 2+ drafts but all belonging to the SAME agent -> still returns the
      latest (no CROSS-agent risk, only ever this agent's own drafts).
    * 2+ drafts spanning 2+ DISTINCT agents -> ``AmbiguousDraftError`` is
      raised instead of guessing; the CLI catches it, lists every candidate,
      and exits 1 -- never operating on the wrong agent's draft.

Every CLI check runs as a real subprocess against ``bin/cli/contract.py``'s
standalone shim (not ``bin/gaia`` -- avoids the ``gaia dev``/DB-bootstrap
path, matching the sibling contract CLI test files).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

AGENT_A = "a1111aaaa"
AGENT_B = "a2222bbbb"


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """Isolated GAIA_DATA_DIR per test, inherited by every subprocess call."""
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return dict(os.environ)


def _init(agent_id: str, env: dict) -> str:
    proc = _run(["init", "--agent-id", agent_id, "--json"], env)
    assert proc.returncode == 0, f"init({agent_id}) failed: {proc.stderr!r}"
    return json.loads(proc.stdout)["draft_id"]


# ---------------------------------------------------------------------------
# THE EXACT BUG SCENARIO: 2+ drafts from DIFFERENT agents, no --draft-id, no
# --agent-id -> must refuse to guess (never silently operate on the wrong
# agent's draft), for every affected subcommand.
# ---------------------------------------------------------------------------
def test_view_refuses_to_guess_across_agents(cli_env):
    draft_a = _init(AGENT_A, cli_env)
    draft_b = _init(AGENT_B, cli_env)  # more recently touched -- the old bug
    # picked THIS one purely by mtime, regardless of who is asking.

    proc = _run(["view"], cli_env)

    assert proc.returncode != 0, (
        "view with no --draft-id/--agent-id must refuse when 2+ agents have "
        f"drafts; stdout={proc.stdout!r}"
    )
    # Must never silently render either agent's envelope.
    assert draft_a not in proc.stdout
    assert draft_b not in proc.stdout
    assert "agent_status" not in proc.stdout


def test_set_refuses_to_guess_across_agents_and_does_not_mutate_either_draft(cli_env):
    draft_a = _init(AGENT_A, cli_env)
    draft_b = _init(AGENT_B, cli_env)

    proc = _run(["set", "agent_status.next_action", "hijacked"], cli_env)
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"

    # Neither agent's draft was touched -- the exact "operates on the wrong
    # agent's draft" outcome the bug report describes must not happen.
    view_a = _run(["view", "--draft-id", draft_a], cli_env)
    view_b = _run(["view", "--draft-id", draft_b], cli_env)
    assert "hijacked" not in view_a.stdout
    assert "hijacked" not in view_b.stdout


def test_add_refuses_to_guess_across_agents(cli_env):
    _init(AGENT_A, cli_env)
    draft_b = _init(AGENT_B, cli_env)

    proc = _run(["add", "agent_status.pending_steps", "sneaky-step"], cli_env)
    assert proc.returncode != 0

    view_b = _run(["view", "--draft-id", draft_b], cli_env)
    payload_b = json.loads(view_b.stdout)
    assert payload_b["envelope"]["agent_status"]["pending_steps"] == []


def test_fill_refuses_to_guess_across_agents(cli_env):
    draft_a = _init(AGENT_A, cli_env)
    _init(AGENT_B, cli_env)

    patch = json.dumps({"agent_status": {"next_action": "silently-hijacked"}})
    proc = _run(["fill", "--json", patch], cli_env)
    assert proc.returncode != 0

    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert payload["error"] == "ambiguous_draft"

    view_a = _run(["view", "--draft-id", draft_a], cli_env)
    assert "silently-hijacked" not in view_a.stdout


def test_validate_refuses_to_guess_across_agents(cli_env):
    _init(AGENT_A, cli_env)
    _init(AGENT_B, cli_env)

    proc = _run(["validate"], cli_env)
    assert proc.returncode != 0


def test_finalize_refuses_to_guess_across_agents(cli_env):
    _init(AGENT_A, cli_env)
    _init(AGENT_B, cli_env)

    proc = _run(["finalize"], cli_env)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# The error names every candidate draft so the caller can disambiguate --
# not a bare "something went wrong".
# ---------------------------------------------------------------------------
def test_ambiguous_error_lists_every_candidate_and_both_agents(cli_env):
    draft_a = _init(AGENT_A, cli_env)
    draft_b = _init(AGENT_B, cli_env)

    # `set` (unlike `view`, which has no --json flag) has a genuine
    # output-format --json toggle -- use it to pin the JSON error shape.
    proc = _run(["set", "agent_status.next_action", "x", "--json"], cli_env)
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "error"
    assert payload["error"] == "ambiguous_draft"
    assert set(payload["candidates"]) == {draft_a, draft_b}
    assert AGENT_A in payload["message"]
    assert AGENT_B in payload["message"]
    assert "--draft-id" in payload["message"]
    assert "--agent-id" in payload["message"]


# ---------------------------------------------------------------------------
# EXPLICIT --draft-id always resolves cleanly, even amid a cross-agent
# ambiguity -- the escape hatch the fix requires still works.
# ---------------------------------------------------------------------------
def test_explicit_draft_id_still_resolves_amid_cross_agent_ambiguity(cli_env):
    draft_a = _init(AGENT_A, cli_env)
    _init(AGENT_B, cli_env)

    proc = _run(["view", "--draft-id", draft_a], cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["draft_id"] == draft_a
    assert payload["envelope"]["agent_status"]["agent_id"] == AGENT_A


# ---------------------------------------------------------------------------
# EXPLICIT --agent-id still resolves cleanly amid cross-agent ambiguity --
# scoping to "my own drafts" is a deliberate, safe choice, not a guess.
# ---------------------------------------------------------------------------
def test_explicit_agent_id_still_resolves_amid_cross_agent_ambiguity(cli_env):
    draft_a = _init(AGENT_A, cli_env)
    _init(AGENT_B, cli_env)

    proc = _run(["view", "--agent-id", AGENT_A], cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["draft_id"] == draft_a


# ---------------------------------------------------------------------------
# SAFE FALLBACKS PRESERVED -- the bug report explicitly calls these out as
# fine to keep silent, and they must keep working exactly as before.
# ---------------------------------------------------------------------------
def test_single_draft_system_wide_still_resolves_silently(cli_env):
    draft_id = _init(AGENT_A, cli_env)

    proc = _run(["view"], cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["draft_id"] == draft_id


def test_multiple_drafts_same_agent_still_resolve_to_latest_no_ambiguity(cli_env):
    # Two drafts, but BOTH belong to the SAME agent -- no cross-agent risk,
    # so the latest-mtime fallback must still apply (this is the resumed-
    # agent convenience T5/T6 rely on, unrelated to the bug).
    draft_1 = _init(AGENT_A, cli_env)
    draft_2 = _init(AGENT_A, cli_env)

    # Force deterministic mtime ordering (draft_2 strictly newer) so this
    # assertion never depends on same-second filesystem mtime granularity.
    drafts_dir = Path(cli_env["GAIA_DATA_DIR"]) / "contract_drafts"
    path_1 = drafts_dir / f"{draft_1}.json"
    path_2 = drafts_dir / f"{draft_2}.json"
    now = path_2.stat().st_mtime
    os.utime(path_1, (now - 10, now - 10))
    os.utime(path_2, (now, now))

    proc = _run(["view"], cli_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["draft_id"] == draft_2


# ---------------------------------------------------------------------------
# Unit-level coverage of gaia.contract.drafts.resolve_draft_id directly
# (no subprocess) -- pins the exact exception type and its .candidates.
# ---------------------------------------------------------------------------
def test_resolve_draft_id_raises_ambiguous_draft_error_directly(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    from gaia.contract import drafts as drafts_mod

    importlib.reload(drafts_mod)

    draft_a = drafts_mod.mint_draft_id(AGENT_A)
    draft_b = drafts_mod.mint_draft_id(AGENT_B)
    drafts_mod.save_draft(draft_a, {"agent_status": {"agent_id": AGENT_A}})
    drafts_mod.save_draft(draft_b, {"agent_status": {"agent_id": AGENT_B}})

    with pytest.raises(drafts_mod.AmbiguousDraftError) as excinfo:
        drafts_mod.resolve_draft_id(explicit=None, agent_id=None)

    assert set(excinfo.value.candidates) == {draft_a, draft_b}

    # Scoping to one agent explicitly still resolves cleanly.
    assert drafts_mod.resolve_draft_id(explicit=None, agent_id=AGENT_A) == draft_a

    # An explicit draft id always wins, no exception.
    assert drafts_mod.resolve_draft_id(explicit=draft_b, agent_id=None) == draft_b


def test_resolve_draft_id_same_agent_multiple_drafts_no_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    from gaia.contract import drafts as drafts_mod

    importlib.reload(drafts_mod)

    draft_1 = drafts_mod.mint_draft_id(AGENT_A)
    drafts_mod.save_draft(draft_1, {"agent_status": {"agent_id": AGENT_A}})
    draft_2 = drafts_mod.mint_draft_id(AGENT_A)
    drafts_mod.save_draft(draft_2, {"agent_status": {"agent_id": AGENT_A}})

    # Force deterministic mtime ordering (draft_2 strictly newer) so this
    # assertion never depends on same-second filesystem mtime granularity.
    path_1 = drafts_mod.draft_path(draft_1)
    path_2 = drafts_mod.draft_path(draft_2)
    now = path_2.stat().st_mtime
    os.utime(path_1, (now - 10, now - 10))
    os.utime(path_2, (now, now))

    # No exception: both candidates share the same agent.
    resolved = drafts_mod.resolve_draft_id(explicit=None, agent_id=None)
    assert resolved == draft_2  # most-recently-modified


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
