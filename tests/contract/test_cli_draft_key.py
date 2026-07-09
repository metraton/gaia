"""
AC-5 -- id propio agnostico (M2, task T5).

The Contract CLI mints its OWN contract id and is fully agnostic of the
Claude-Code harness:

    * the draft is created with a CLI-minted contract id, NOT derived from
      ``CLAUDE_SESSION_ID`` (or any other harness env var);
    * behavior is IDENTICAL whether ``CLAUDE_SESSION_ID`` is set or unset;
    * the draft is LOCATABLE in both cases.

All CLI checks run as real subprocesses against ``bin/cli/contract.py``'s
standalone shim (not ``bin/gaia`` -- avoids the ``gaia dev`` / DB-bootstrap
path entirely, per the T4/T5 hard constraints).

The draft-storage module ``gaia.contract.drafts`` is also exercised directly
for the minting / per-agent addressing / atomicity guarantees the CLI builds
on (and that T6 resume-read, T7 finalize, and T13 concurrency build on in
turn).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

VALID_AGENT_ID = "a1234abcd"
OTHER_AGENT_ID = "a99ff00"

# A distinctive sentinel we plant in CLAUDE_SESSION_ID; the minted contract id
# must NEVER embed it (that would prove the CLI read the harness env var).
SESSION_SENTINEL = "cc-session-deadbeefcafef00d-should-never-appear"


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


def _env_with_session(base: dict) -> dict:
    env = dict(base)
    env["CLAUDE_SESSION_ID"] = SESSION_SENTINEL
    return env


def _env_without_session(base: dict) -> dict:
    env = dict(base)
    env.pop("CLAUDE_SESSION_ID", None)
    return env


# ---------------------------------------------------------------------------
# The CLI mints its OWN id -- never derived from CLAUDE_SESSION_ID.
# ---------------------------------------------------------------------------
def test_init_mints_own_id_not_derived_from_claude_session_id(base_env):
    env = _env_with_session(base_env)
    proc = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    draft_id = json.loads(proc.stdout)["draft_id"]

    # The minted id encodes the agent + a random token; it NEVER embeds the
    # harness session value.
    assert SESSION_SENTINEL not in draft_id
    assert draft_id.startswith(f"{VALID_AGENT_ID}.")
    assert re.fullmatch(rf"{VALID_AGENT_ID}\.[0-9a-f]+", draft_id)


def test_two_inits_mint_distinct_ids_even_with_session_set(base_env):
    """Distinct concurrent-style cycles never share an id (the property T13's
    concurrency isolation relies on), regardless of the harness env."""
    env = _env_with_session(base_env)
    a = json.loads(_run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env).stdout)["draft_id"]
    b = json.loads(_run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env).stdout)["draft_id"]
    assert a != b


# ---------------------------------------------------------------------------
# Identical behavior with CLAUDE_SESSION_ID set vs unset.
# ---------------------------------------------------------------------------
def test_identical_behavior_env_set_and_unset(base_env, tmp_path, monkeypatch):
    """Same init sequence, once with the harness env set and once unset, into
    two isolated data dirs -> identical outcome (exit 0, same id shape, draft
    locatable)."""
    results = {}
    for label, mkenv in (("set", _env_with_session), ("unset", _env_without_session)):
        data_dir = tmp_path / f"data_{label}"
        env = mkenv(base_env)
        env["GAIA_DATA_DIR"] = str(data_dir)

        init = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)
        assert init.returncode == 0, f"[{label}] stderr={init.stderr!r}"
        draft_id = json.loads(init.stdout)["draft_id"]

        # Locatable via implicit (mtime) resolution -- no id passed.
        view = _run(["view"], env)
        assert view.returncode == 0, f"[{label}] stderr={view.stderr!r}"
        seen = json.loads(view.stdout)
        assert seen["draft_id"] == draft_id
        assert seen["envelope"]["agent_status"]["agent_id"] == VALID_AGENT_ID

        results[label] = (init.returncode, view.returncode,
                          re.fullmatch(rf"{VALID_AGENT_ID}\.[0-9a-f]+", draft_id) is not None)

    assert results["set"] == results["unset"] == (0, 0, True)


# ---------------------------------------------------------------------------
# Locatable by explicit id in both env states.
# ---------------------------------------------------------------------------
def test_draft_locatable_by_explicit_id_in_both_env_states(base_env, tmp_path):
    for label, mkenv in (("set", _env_with_session), ("unset", _env_without_session)):
        env = mkenv(base_env)
        env["GAIA_DATA_DIR"] = str(tmp_path / f"loc_{label}")

        init = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)
        draft_id = json.loads(init.stdout)["draft_id"]

        view = _run(["view", "--draft-id", draft_id], env)
        assert view.returncode == 0, f"[{label}] stderr={view.stderr!r}"
        assert json.loads(view.stdout)["draft_id"] == draft_id


# ---------------------------------------------------------------------------
# The draft persists OUTSIDE .claude, under Gaia's own substrate.
# ---------------------------------------------------------------------------
def test_draft_persists_outside_dotclaude(base_env, tmp_path):
    env = _env_without_session(base_env)
    data_dir = tmp_path / "substrate"
    env["GAIA_DATA_DIR"] = str(data_dir)

    init = _run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env)
    draft_id = json.loads(init.stdout)["draft_id"]

    draft_file = data_dir / "contract_drafts" / f"{draft_id}.json"
    assert draft_file.is_file(), f"draft not persisted at {draft_file}"
    # No .claude anywhere in the resolved path.
    assert ".claude" not in str(draft_file.resolve()).split(os.sep)


# ---------------------------------------------------------------------------
# Per-agent addressing: two agents' drafts are independently locatable, no
# cross-contamination (feeds T13's concurrency isolation).
# ---------------------------------------------------------------------------
def test_per_agent_drafts_are_independently_locatable(base_env):
    env = _env_without_session(base_env)
    a = json.loads(_run(["init", "--agent-id", VALID_AGENT_ID, "--json"], env).stdout)["draft_id"]
    b = json.loads(_run(["init", "--agent-id", OTHER_AGENT_ID, "--json"], env).stdout)["draft_id"]
    assert a != b

    # Scope resolution to each agent -> each finds ITS OWN latest draft, never
    # the other's.
    va = _run(["view", "--agent-id", VALID_AGENT_ID], env)
    vb = _run(["view", "--agent-id", OTHER_AGENT_ID], env)
    assert json.loads(va.stdout)["draft_id"] == a
    assert json.loads(vb.stdout)["draft_id"] == b


# ---------------------------------------------------------------------------
# Static agnosticism guard: neither the CLI nor the storage module ever READS
# a Claude-Code env var. This makes "never reads CLAUDE_SESSION_ID" a
# structural property, not just an observed one. We inspect the AST for env
# accesses (os.environ.get/[], os.getenv) whose key is a harness var --
# mentioning the name in a docstring/comment (as these files do, to document
# that they do NOT read it) is legitimate and must not trip the guard.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "src",
    [
        CONTRACT_CLI,
        _REPO_ROOT / "gaia" / "contract" / "drafts.py",
    ],
)
def test_source_never_reads_a_harness_env_var(src):
    import ast

    tree = ast.parse(src.read_text(encoding="utf-8"))
    harness_keys = []

    def _is_harness(name) -> bool:
        return isinstance(name, str) and (
            name.startswith("CLAUDE_") or name.startswith("ANTHROPIC_")
        )

    for node in ast.walk(tree):
        # os.getenv("CLAUDE_...") / getenv("CLAUDE_...")
        if isinstance(node, ast.Call):
            fn = node.func
            fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if fname in ("getenv", "get") and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and _is_harness(arg0.value):
                    harness_keys.append(arg0.value)
        # os.environ["CLAUDE_..."]
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and _is_harness(key.value):
                harness_keys.append(key.value)

    assert not harness_keys, (
        f"{src.name} READS harness env var(s): {harness_keys}"
    )


# ---------------------------------------------------------------------------
# The storage module's minting is agnostic and unique in-process too.
# ---------------------------------------------------------------------------
def test_drafts_module_mint_is_agnostic_and_unique(base_env, monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", SESSION_SENTINEL)
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from gaia.contract import drafts

    ids = {drafts.mint_draft_id(VALID_AGENT_ID) for _ in range(200)}
    assert len(ids) == 200  # no collisions
    for did in ids:
        assert SESSION_SENTINEL not in did
        assert did.startswith(f"{VALID_AGENT_ID}.")
