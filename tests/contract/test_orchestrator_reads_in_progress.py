"""
AC-20 -- orquestador lee draft in-progress (M2, task T6, decision #8).

"El orquestador puede leer el draft in-progress entre mensajes (gaia
contract view) SIN que el agente re-emita el contrato completo."

The orchestrator is a DIFFERENT actor than the agent writing the draft: it
does not share the agent's in-memory state, and it must never depend on the
agent emitting a fenced ``agent_contract_handoff`` block mid-conversation to
learn where things stand. This file proves that property end-to-end:

    1. The "agent" builds up a draft across several small, independent CLI
       calls (init/set/add) -- each call is its OWN process, so nothing is
       ever held in shared memory the way an in-conversation re-emit would
       be. If the orchestrator's read worked only because of shared process
       state, cutting the process boundary here would break it.
    2. The "orchestrator" -- a separate `gaia contract view` invocation, with
       no knowledge of anything but the agent_id (which it already has from
       dispatch) -- reads the CURRENT in-progress state mid-cycle, before
       the agent finalizes. No prose, no re-emitted JSON block: the CLI
       view IS the read.
    3. The hook adapter's own draft-summary builder (the same one that
       powers the resume injection in AC-18) is exercised directly too,
       proving the orchestrator-facing read and the resume-injection read
       are two callers of the SAME underlying source of truth
       (`gaia.contract.drafts`), not two divergent copies.
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
HOOKS_DIR = _REPO_ROOT / "hooks"

if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402

AGENT_ID = "a99ff00cc"


def _run(args: list, env: dict) -> subprocess.CompletedProcess:
    """Each call is a fresh subprocess -- no shared Python state with any
    other call, mirroring the orchestrator/agent process boundary."""
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia_data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    clear_path_cache()
    monkeypatch.setattr(ClaudeCodeAdapter, "RESUME_MAP_CACHE_DIR", tmp_path / "resume_map")
    monkeypatch.setattr(ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "context_cache")
    env = dict(os.environ)
    yield env
    clear_path_cache()


def test_orchestrator_view_sees_mid_cycle_state_without_agent_reemitting(cli_env):
    """The orchestrator's read happens BETWEEN two agent-side CLI calls, via
    a third, unrelated process -- never through anything the agent 'said'."""
    env = cli_env

    # -- Agent turn 1: starts the draft, sets one field. --
    init = _run(["init", "--agent-id", AGENT_ID, "--json"], env)
    assert init.returncode == 0, f"stderr={init.stderr!r}"
    draft_id = json.loads(init.stdout)["draft_id"]

    set1 = _run(
        ["set", "agent_status.next_action", "investigating AC-20", "--draft-id", draft_id],
        env,
    )
    assert set1.returncode == 0, f"stderr={set1.stderr!r}"

    # -- Orchestrator reads mid-cycle: a fresh process, agent_id only. --
    orchestrator_view = _run(["view", "--agent-id", AGENT_ID], env)
    assert orchestrator_view.returncode == 0, f"stderr={orchestrator_view.stderr!r}"
    seen = json.loads(orchestrator_view.stdout)

    assert seen["draft_id"] == draft_id
    assert seen["envelope"]["agent_status"]["agent_state"] == "IN_PROGRESS"
    assert seen["envelope"]["agent_status"]["next_action"] == "investigating AC-20"
    # The draft is NOT finalized yet -- this really is an in-progress read,
    # not a post-hoc read of a completed row.
    assert seen["envelope"]["agent_status"]["agent_state"] != "COMPLETE"

    # -- Agent turn 2: more progress, unaware the orchestrator peeked. --
    set2 = _run(
        ["add", "evidence_report.commands_run",
         "pytest tests/contract/test_orchestrator_reads_in_progress.py -q", "--draft-id", draft_id],
        env,
    )
    assert set2.returncode == 0, f"stderr={set2.stderr!r}"

    # -- Orchestrator reads again: sees the NEW state, still without the
    # agent ever re-emitting a full contract block anywhere. --
    orchestrator_view_2 = _run(["view", "--agent-id", AGENT_ID], env)
    seen2 = json.loads(orchestrator_view_2.stdout)
    assert seen2["draft_id"] == draft_id
    assert seen2["envelope"]["evidence_report"]["commands_run"] == [
        "pytest tests/contract/test_orchestrator_reads_in_progress.py -q",
    ]
    # First-read field is still there too -- the second read did not reset
    # anything the first observed.
    assert seen2["envelope"]["agent_status"]["next_action"] == "investigating AC-20"


def test_orchestrator_view_never_requires_a_running_agent_process(cli_env):
    """The orchestrator's read must not depend on the agent process still
    being alive -- disk persistence, not an in-memory handoff, is the
    substrate (this is what makes it safe to poll 'mid-conversation')."""
    env = cli_env
    init = _run(["init", "--agent-id", AGENT_ID, "--json"], env)
    draft_id = json.loads(init.stdout)["draft_id"]
    _run(["set", "agent_status.next_action", "mid-flight", "--draft-id", draft_id], env)

    # No agent process exists at all at this point -- only the file on disk
    # (gaia.contract.drafts) does. A brand-new subprocess reads it cleanly.
    view = _run(["view", "--draft-id", draft_id], env)
    assert view.returncode == 0
    assert json.loads(view.stdout)["envelope"]["agent_status"]["next_action"] == "mid-flight"


def test_hook_adapter_draft_summary_matches_the_direct_cli_view(cli_env):
    """The resume-injection helper in the hook adapter (T6) and the CLI's
    own `view` are two callers of the SAME draft -- not divergent copies.
    This is the property that lets the orchestrator (via `contract view`)
    and the resumed agent (via the adapter's injected hint) agree on state."""
    env = cli_env
    init = _run(["init", "--agent-id", AGENT_ID, "--json"], env)
    draft_id = json.loads(init.stdout)["draft_id"]
    _run(
        ["set", "agent_status.next_action", "shared source of truth", "--draft-id", draft_id],
        env,
    )

    cli_seen = json.loads(_run(["view", "--draft-id", draft_id], env).stdout)

    adapter = ClaudeCodeAdapter()
    session_id = "sess-orch-001"
    adapter._adapt_send_message(
        "SendMessage", {"to": AGENT_ID, "message": "go"}, session_id=session_id,
    )
    hook_context = adapter._build_resume_draft_context(session_id)

    assert hook_context is not None
    assert draft_id in hook_context
    assert cli_seen["envelope"]["agent_status"]["next_action"] in hook_context
