"""The SubagentStop close runs in a host-neutral core any adapter can drive.

The host below is not ClaudeCodeAdapter and supplies only what a host owns:
its agent roster, its stop-reason reading and its resume-map directory. If the
gate, the dispatch-row lookup, the rejection circuit and its cut, the episode
write, the workflow audit, the approval cleanup or the user_facing_summary
relay still lived in the Claude Code adapter, driving the core from this host
would not reach them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters import subagent_stop_core  # noqa: E402
from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.types import AgentCompletion  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    mirror_partial_contract_handoff,
    stamp_harness_agent_id,
)
from modules.agents import contract_validator, rejection_circuit  # noqa: E402
from modules.audit import workflow_auditor  # noqa: E402
from modules.memory import episode_writer  # noqa: E402
from modules.security import approval_cleanup  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_TYPE = "gaia-system"
AGENT_ID = valid_agent_id("stop-core")
HARNESS_AGENT_ID = valid_agent_id("stop-core-harness")
SESSION_ID = "sess-stop-core"
SUMMARY = "Movi el cierre del subagente a un nucleo neutral."

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            **{k: [] for k in _EVIDENCE_KEYS},
            "verification": {
                "type": "command",
                "command": "pytest -q tests/hooks",
                "method": "ran the touched slice",
                "result": "pass",
                "details": "green",
            },
        },
        "consolidation_report": None,
        "approval_request": None,
        "user_facing_summary": SUMMARY,
    }


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    monkeypatch.delenv("GAIA_CONTRACT_MAX_REJECTIONS", raising=False)


@pytest.fixture()
def db_path(tmp_path) -> Path:
    return tmp_path / "gaia_data" / "gaia.db"


@pytest.fixture()
def calls(monkeypatch):
    """Record every call the core makes into its responsibilities, passing
    each call through to the real implementation."""
    seen: dict = {}

    def spy(owner, name):
        real = getattr(owner, name)

        def wrapper(*args, **kwargs):
            seen.setdefault(name, []).append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    spy(subagent_stop_core, "_resolve_subagent_stop_gate_full")
    spy(rejection_circuit, "record_rejection")
    spy(rejection_circuit, "reset")
    spy(episode_writer, "write")
    spy(workflow_auditor, "audit")
    spy(approval_cleanup, "cleanup")
    spy(contract_validator, "parse_user_facing_summary")
    return seen


def _foreign_host(tmp_path: Path) -> subagent_stop_core.SubagentStopHost:
    return subagent_stop_core.SubagentStopHost(
        agent_roster=lambda: {AGENT_TYPE},
        classify_stop_reason=lambda _raw: "violation",
        resume_map_dir=tmp_path / "resume_map",
    )


def _run_core(host) -> subagent_stop_core.SubagentStopOutcome:
    hook_data = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": AGENT_TYPE,
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": "",
        "cwd": "/tmp",
    }
    completion = AgentCompletion(
        agent_type=AGENT_TYPE,
        agent_id=HARNESS_AGENT_ID,
        transcript_path="",
        last_message="",
        session_id=SESSION_ID,
    )
    return subagent_stop_core.run_subagent_stop(
        host, hook_data, completion, event_session_id=SESSION_ID,
    )


def _birth(db_path: Path) -> str:
    contract_id = f"{AGENT_ID}.core"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db_path,
    )
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db_path)
    return contract_id


def test_accepted_close_runs_every_responsibility_from_the_core(db_path, tmp_path, calls):
    contract_id = _birth(db_path)
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(_envelope()),
        session_id=SESSION_ID,
        db_path=db_path,
    )

    outcome = _run_core(_foreign_host(tmp_path))

    assert outcome.rejected is False
    assert outcome.result["contract_gate_source"] == "row"
    assert outcome.user_message == f"Resumen de {AGENT_TYPE} para el usuario: {SUMMARY}"
    assert outcome.result["user_facing_summary"] == SUMMARY
    assert "systemMessage" not in outcome.result
    assert "hookSpecificOutput" not in outcome.result
    for name in (
        "_resolve_subagent_stop_gate_full", "reset", "write", "audit",
        "cleanup", "parse_user_facing_summary",
    ):
        assert calls.get(name), f"core never called {name}"
    assert calls["cleanup"][0][0][0] == AGENT_TYPE
    assert "record_rejection" not in calls


def test_rejections_count_toward_the_cut_and_the_cut_closes_degraded(db_path, tmp_path, calls):
    contract_id = _birth(db_path)
    mirror_partial_contract_handoff(contract_id, json.dumps(_envelope()), db_path=db_path)
    host = _foreign_host(tmp_path)

    outcomes = [_run_core(host) for _ in range(rejection_circuit.DEFAULT_MAX_REJECTIONS)]

    assert [o.rejected for o in outcomes[:-1]] == [True] * (len(outcomes) - 1)
    assert outcomes[0].result["contract_rejected"] is True
    assert outcomes[0].result["contract_gate_source"] == "row_unfinalized"
    assert outcomes[0].user_message is None
    cut = outcomes[-1]
    assert cut.rejected is False
    assert cut.result["contract_circuit_open"] is True
    assert cut.result["contract_complete"] is False
    assert "CORTADO" in cut.user_message
    assert "user_facing_summary" not in cut.result
    assert len(calls["record_rejection"]) == len(outcomes)
    assert calls["write"][-1][1]["outcome_override"] == "failed"


def test_claude_code_adapter_only_formats_the_core_outcome(monkeypatch):
    captured = {}

    def fake_core(host, hook_data, completion, *, event_session_id):
        captured["host"] = host
        captured["session"] = event_session_id
        return subagent_stop_core.SubagentStopOutcome(
            result={"success": True, "contract_rejected": True},
            rejected=True,
            user_message="aviso",
        )

    monkeypatch.setattr(subagent_stop_core, "run_subagent_stop", fake_core)
    adapter = ClaudeCodeAdapter()
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": AGENT_TYPE,
        "agent_id": AGENT_ID,
        "last_assistant_message": "",
    }

    response = adapter.adapt_subagent_stop(adapter.parse_event(json.dumps(payload)))

    assert response.exit_code == 2
    assert response.output["systemMessage"] == "aviso"
    assert response.output["hookSpecificOutput"] == {"hookEventName": "SubagentStop"}
    assert captured["session"] == SESSION_ID
    assert captured["host"].classify_stop_reason("max_tokens") == "truncation"
