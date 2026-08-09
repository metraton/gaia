#!/usr/bin/env python3
"""Behavior tests for the contract-reported defect capture (AC-2).

A subagent that emits a ``failure_report`` states a concrete defect. These
tests cover the three claims that make the capture worth having:

1. It reads the block through the ONE normalized seam
   (``parse_failure_report``) and turns it into an anomaly carrying its own
   type and a severity.
2. The row actually lands in the raw defect floor -- asserted by reading
   ``episode_anomalies`` back out of a tmp gaia.db after a real
   ``store_episode``, not by trusting the writer's return value.
3. The capture is unrequested and non-blocking: the hook appends it without
   any agent opting in, and forcing the anomaly write to fail leaves the
   turn's outcome byte-identical to the same turn with the write succeeding.

The tmp-DB fixtures mirror ``tests/tools/test_episodic.py`` so the shared
substrate at ``~/.gaia/gaia.db`` is never touched.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = REPO_ROOT / "hooks"
TOOLS_DIR = REPO_ROOT / "tools"
for _p in (str(HOOKS_DIR), str(TOOLS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.agents.defect_capture import (  # noqa: E402
    DEFAULT_DEFECT_SEVERITY,
    DEFECT_ANOMALY_TYPE,
    build_defect_anomaly,
)


def _contract(failure_report=..., agent_state="IN_PROGRESS") -> dict:
    """A shape-valid contract, optionally carrying failure_report.

    ``...`` (the default) omits the key entirely; any other value -- including
    None -- sets it explicitly.
    """
    c = {
        "agent_status": {
            "agent_state": agent_state,
            "agent_id": "a1b2c30f1e2d3c4b5",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    if failure_report is not ...:
        c["failure_report"] = failure_report
    return c


def _well_formed(**overrides) -> dict:
    report = {
        "attempted": "gaia contract finalize --plan-task-id 66",
        "symptom": "the CLI rejected the finalize and the row never landed",
        "evidence": ["Rejected: agent_id mismatch: the draft is keyed to 'a1b2'"],
    }
    report.update(overrides)
    return report


# ---------------------------------------------------------------------------
# No report, or a report the shared seam rejects -> no anomaly, never a raise.
# ---------------------------------------------------------------------------
class TestNoDefectRecorded:
    def test_absent_failure_report_yields_no_anomaly(self):
        assert build_defect_anomaly(_contract()) is None

    def test_explicit_null_yields_no_anomaly(self):
        assert build_defect_anomaly(_contract(failure_report=None)) is None

    @pytest.mark.parametrize("malformed", [
        {"attempted": "x"},                                   # missing symptom
        {"attempted": "x", "symptom": "y"},                   # missing evidence
        {"attempted": "x", "symptom": "y", "evidence": []},   # empty evidence
        {"attempted": "x", "symptom": "y", "evidence": ["e"],
         "severity": "catastrophic"},                         # off-enum severity
        "not an object",
    ])
    def test_malformed_report_yields_no_anomaly(self, malformed):
        assert build_defect_anomaly(_contract(failure_report=malformed)) is None

    def test_non_dict_contract_yields_no_anomaly(self):
        assert build_defect_anomaly(None) is None
        assert build_defect_anomaly("garbage") is None

    def test_never_raises_when_the_seam_itself_breaks(self, monkeypatch):
        import modules.agents.contract_validator as cv
        monkeypatch.setattr(
            cv, "parse_failure_report",
            lambda _c: (_ for _ in ()).throw(RuntimeError("seam exploded")),
        )
        assert build_defect_anomaly(_contract(failure_report=_well_formed())) is None


# ---------------------------------------------------------------------------
# A well-formed report becomes an anomaly with its OWN type and a severity.
# ---------------------------------------------------------------------------
class TestDefectAnomalyShape:
    def test_carries_its_own_anomaly_type(self):
        anomaly = build_defect_anomaly(_contract(failure_report=_well_formed()))
        assert anomaly["type"] == DEFECT_ANOMALY_TYPE
        assert DEFECT_ANOMALY_TYPE == "agent_reported_defect"

    def test_severity_defaults_when_the_agent_omits_it(self):
        anomaly = build_defect_anomaly(_contract(failure_report=_well_formed()))
        assert anomaly["severity"] == DEFAULT_DEFECT_SEVERITY

    @pytest.mark.parametrize("severity", ["info", "warning", "error"])
    def test_declared_severity_passes_through_verbatim(self, severity):
        report = _well_formed(severity=severity)
        anomaly = build_defect_anomaly(_contract(failure_report=report))
        assert anomaly["severity"] == severity

    def test_preserves_attempted_symptom_evidence_and_component(self):
        report = _well_formed(component="hooks/adapters/claude_code.py")
        anomaly = build_defect_anomaly(_contract(failure_report=report), agent="gaia-system")
        assert anomaly["attempted"] == report["attempted"]
        assert anomaly["symptom"] == report["symptom"]
        assert anomaly["evidence"] == report["evidence"]
        assert anomaly["component"] == "hooks/adapters/claude_code.py"
        assert anomaly["agent"] == "gaia-system"

    def test_message_names_the_agent_and_the_symptom(self):
        anomaly = build_defect_anomaly(
            _contract(failure_report=_well_formed()), agent="gaia-system"
        )
        assert "gaia-system" in anomaly["message"]
        assert "the CLI rejected the finalize" in anomaly["message"]


# ---------------------------------------------------------------------------
# The row lands in the raw defect floor -- read back from episode_anomalies.
# ---------------------------------------------------------------------------
@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DISPATCH_WORKSPACE", "test_ws")
    from memory.episodic import EpisodicMemory
    return EpisodicMemory(
        base_path=tmp_path / "episodic-memory",
        db_path=tmp_path / "gaia.db",
    )


def _floor_rows(db_path: Path, episode_id: str) -> list:
    """Read-only query over the raw anomaly floor."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [
            dict(r) for r in con.execute(
                "SELECT type, severity, message, payload FROM episode_anomalies "
                "WHERE episode_id = ?", (episode_id,)
            ).fetchall()
        ]
    finally:
        con.close()


def _store(memory, anomalies):
    return memory.store_episode(
        prompt="a turn that reported a defect",
        context={"anomalies": anomalies},
        outcome="partial",
    )


class TestDefectLandsInTheRawFloor:
    def test_defect_row_is_readable_with_its_type_and_severity(self, memory):
        report = _well_formed(severity="error", component="gaia/store/writer.py")
        anomaly = build_defect_anomaly(
            _contract(failure_report=report), agent="gaia-system"
        )
        episode_id = _store(memory, [anomaly])

        rows = _floor_rows(memory.db_path, episode_id)
        assert len(rows) == 1
        assert rows[0]["type"] == DEFECT_ANOMALY_TYPE
        assert rows[0]["severity"] == "error"

    def test_payload_preserves_the_full_defect_structure(self, memory):
        report = _well_formed(component="gaia/store/writer.py")
        anomaly = build_defect_anomaly(_contract(failure_report=report))
        episode_id = _store(memory, [anomaly])

        payload = json.loads(_floor_rows(memory.db_path, episode_id)[0]["payload"])
        assert payload["attempted"] == report["attempted"]
        assert payload["symptom"] == report["symptom"]
        assert payload["evidence"] == report["evidence"]
        assert payload["component"] == "gaia/store/writer.py"

    def test_the_new_type_is_aggregated_by_gaia_metrics(self):
        """A new type is free at the storage layer; it must not be a blank in
        the metrics read surface."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_metrics_for_test", REPO_ROOT / "bin" / "cli" / "metrics.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert DEFECT_ANOMALY_TYPE in module._ANOMALY_TYPE_GLOSSARY
        # The report's severity enum must be rankable by the summary, or an
        # "error" defect would sort below a "warning" one.
        for severity in ("info", "warning", "error"):
            assert severity in module._SEVERITY_RANK


# ---------------------------------------------------------------------------
# Non-blocking: a forced write failure leaves the turn outcome identical.
# ---------------------------------------------------------------------------
class TestDefectWriteNeverBlocksTheTurn:
    def _episode_row(self, db_path, episode_id):
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT outcome, plan_status FROM episodes WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
            return dict(row) if row else {}
        finally:
            con.close()

    def test_turn_outcome_identical_when_the_defect_write_is_forced_to_fail(
        self, memory, monkeypatch
    ):
        anomaly = build_defect_anomaly(_contract(failure_report=_well_formed()))

        healthy_id = _store(memory, [anomaly])
        healthy_row = self._episode_row(memory.db_path, healthy_id)
        assert len(_floor_rows(memory.db_path, healthy_id)) == 1

        import gaia.store.writer as writer
        monkeypatch.setattr(
            writer, "insert_episode_anomaly",
            lambda *a, **k: {"status": "error", "reason": "forced failure"},
        )
        broken_id = _store(memory, [anomaly])

        # The episode -- the turn's own record -- is unchanged; only the
        # advisory child row is missing.
        assert broken_id
        assert self._episode_row(memory.db_path, broken_id) == healthy_row
        assert _floor_rows(memory.db_path, broken_id) == []

    def test_episode_writer_swallows_a_raising_anomaly_write(self, memory, monkeypatch):
        """The swallow point named by the self_review gate:
        ``EpisodicMemory.store_episode`` logs and continues past a rejected
        ``insert_episode_anomaly``; ``episode_writer.write`` swallows the rest.
        """
        import gaia.store.writer as writer
        monkeypatch.setattr(
            writer, "insert_episode_anomaly",
            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")),
        )
        anomaly = build_defect_anomaly(_contract(failure_report=_well_formed()))
        # store_episode catches per-anomaly failures inside insert_episode_anomaly;
        # a raise from the patched stub escapes it, which is exactly what
        # episode_writer.write() is there to absorb.
        with pytest.raises(sqlite3.OperationalError):
            _store(memory, [anomaly])

        from modules.memory.episode_writer import write as write_episode
        monkeypatch.setattr(
            "modules.memory.episode_writer.get_session_events", lambda: {}
        )
        assert write_episode({"agent": "gaia-system"}, anomalies=[anomaly]) is None


# ---------------------------------------------------------------------------
# Unrequested: the hook appends the defect on its own, before the episode write.
# ---------------------------------------------------------------------------
class TestDefectCaptureIsUnrequested:
    def _run_hook(self, monkeypatch, contract):
        """Drive the real subagent_stop chain, capturing what reaches the
        episode writer."""
        import subagent_stop

        captured = {}

        def _fake_write_episode(metrics, anomalies=None, commands_executed=None, **_kw):
            captured["anomalies"] = list(anomalies or [])
            return "ep_test"

        monkeypatch.setattr(subagent_stop, "write_episode", _fake_write_episode)
        monkeypatch.setattr(subagent_stop, "signal_gaia_analysis", lambda *a, **k: None)
        monkeypatch.setattr(subagent_stop, "_persist_handoff", lambda **k: None)

        agent_output = (
            "Done.\n\n```agent_contract_handoff\n" + json.dumps(contract) + "\n```\n"
        )
        result = subagent_stop.subagent_stop_hook(
            {"agent": "gaia-system", "task_id": "t1"}, agent_output
        )
        return result, captured.get("anomalies", [])

    def test_subagent_stop_appends_the_defect_without_any_opt_in(self, monkeypatch):
        contract = _contract(failure_report=_well_formed(severity="error"))
        result, anomalies = self._run_hook(monkeypatch, contract)

        assert result["success"] is True
        types = [a.get("type") for a in anomalies]
        assert DEFECT_ANOMALY_TYPE in types, types
        defect = next(a for a in anomalies if a.get("type") == DEFECT_ANOMALY_TYPE)
        assert defect["severity"] == "error"

    def test_a_turn_without_a_report_gets_no_defect_anomaly(self, monkeypatch):
        """Negative control: the anomaly above comes from the new capture, not
        from an ambient anomaly the chain would have produced anyway."""
        result, anomalies = self._run_hook(monkeypatch, _contract())

        assert result["success"] is True
        assert DEFECT_ANOMALY_TYPE not in [a.get("type") for a in anomalies]

    def test_a_malformed_report_never_disturbs_the_turn(self, monkeypatch):
        """The non-blocking half at the hook seam: a report the shared shape
        check rejects leaves the turn's outcome identical to no report."""
        clean_result, clean_anomalies = self._run_hook(monkeypatch, _contract())
        bad_result, bad_anomalies = self._run_hook(
            monkeypatch, _contract(failure_report={"attempted": "only half a block"})
        )

        assert bad_result["success"] is True
        assert bad_result["anomalies_detected"] == clean_result["anomalies_detected"]
        assert [a.get("type") for a in bad_anomalies] == [
            a.get("type") for a in clean_anomalies
        ]


# ---------------------------------------------------------------------------
# The PRODUCTION route: ClaudeCodeAdapter.adapt_subagent_stop, not the legacy
# subagent_stop_hook module the class above drives. These call the adapter
# directly and read what actually reached write_episode, rather than
# comparing string positions in the adapter's source -- a source-order
# assertion shows two literals appear in a certain sequence in the file; it
# cannot show the call executes or that the anomaly reaches the write on
# this route.
# ---------------------------------------------------------------------------
class TestAdapterCapturesTheDefectOnTheProductionRoute:
    def _install_module_stubs(self, monkeypatch, captured, parsed_contract, agent_state="IN_PROGRESS"):
        import sys as _sys
        import types as _types
        from modules.agents.contract_validator import (
            parse_failure_report as _real_parse_failure_report,
        )

        def _install_stub(module_name, attrs):
            module = _types.ModuleType(module_name)
            for k, v in attrs.items():
                setattr(module, k, v)
            monkeypatch.setitem(_sys.modules, module_name, module)

        _install_stub(
            "modules.agents.contract_validator",
            {
                "extract_commands_from_evidence": lambda *_a, **_k: [],
                "parse_contract": lambda *_a, **_k: parsed_contract,
                "validate": lambda *_a, **_k: _types.SimpleNamespace(
                    is_valid=True, error_message="",
                ),
                "validate_approval_request": lambda *_a, **_k: None,
                "validate_verbatim_outputs_consistency": lambda *_a, **_k: None,
                "_resolve_status": lambda *_a, **_k: agent_state,
                # Left genuine (not stubbed): this is the exact normalization
                # seam build_defect_anomaly reads through, and the whole
                # point of this class is to exercise it for real.
                "parse_failure_report": _real_parse_failure_report,
            },
        )
        _install_stub(
            "modules.agents.response_contract",
            {
                "save_validation_result": lambda *_a, **_k: None,
                "validate_response_contract": lambda *_a, **_k: _types.SimpleNamespace(
                    valid=True, errors=[], warnings=[],
                ),
                "resolve_agent_id": lambda *_a, **_k: "agent-id",
            },
        )
        _install_stub(
            "modules.agents.task_info_builder",
            {
                "build_task_info_from_hook_data": lambda hook_data, _agent_output: {
                    "agent": hook_data.get("agent_type", "unknown"),
                    "agent_id": hook_data.get("agent_id", "unknown"),
                    "task_id": "task-id",
                    "agent_transcript_path": hook_data.get("agent_transcript_path", ""),
                },
            },
        )
        _install_stub(
            "modules.agents.transcript_reader",
            {
                "read_transcript": lambda *_a, **_k: "",
                "read_full_transcript_text": lambda *_a, **_k: "",
            },
        )
        _install_stub(
            "modules.audit.workflow_auditor",
            {"audit": lambda *_a, **_k: [], "signal_gaia_analysis": lambda *_a, **_k: None},
        )
        _install_stub("modules.audit.workflow_recorder", {"record": lambda *_a, **_k: {}})
        _install_stub(
            "modules.context.context_writer",
            {
                "process_context_updates": lambda *_a, **_k: None,
                "process_update_contracts": lambda *_a, **_k: {},
            },
        )

        def _fake_write_episode(metrics, anomalies=None, commands_executed=None, **_kw):
            captured["anomalies"] = list(anomalies or [])
            return "ep_test"

        _install_stub("modules.memory.episode_writer", {"write": _fake_write_episode})
        _install_stub(
            "modules.security.approval_cleanup", {"cleanup": lambda *_a, **_k: None},
        )
        _install_stub(
            "modules.security.approval_grants",
            {"consume_session_grants": lambda *_a, **_k: 0},
        )

    def _build_event(self, payload):
        from adapters.types import HookEvent, HookEventType, HostDistribution
        return HookEvent(
            event_type=HookEventType.SUBAGENT_STOP,
            session_id=payload["session_id"],
            payload=payload,
            distribution=HostDistribution(channel="npm"),
        )

    def _payload(self):
        return {
            "hook_event_name": "SubagentStop",
            "session_id": "sess-defect-route",
            "agent_type": "gaia-system",
            "agent_id": "a1b2c3d0f1e2d3c4b",
            "agent_transcript_path": "",
            "last_assistant_message": "irrelevant -- parse_contract is stubbed below",
            "cwd": "/home/user/project",
            "stop_hook_active": True,
            "permission_mode": "default",
        }

    def test_adapter_passes_the_defect_anomaly_through_to_write_episode(self, monkeypatch):
        from adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        payload = self._payload()
        report = _well_formed(severity="error")
        parsed_contract = _contract(failure_report=report)

        captured = {}
        self._install_module_stubs(monkeypatch, captured, parsed_contract)
        monkeypatch.setattr(adapter, "_get_gaia_agent_names", lambda: [payload["agent_type"]])

        adapter.adapt_subagent_stop(self._build_event(payload))

        anomalies = captured.get("anomalies", [])
        types = [a.get("type") for a in anomalies]
        assert DEFECT_ANOMALY_TYPE in types, (
            "adapt_subagent_stop (the production route) must pass the "
            f"contract-reported defect through to write_episode; got {types}"
        )
        defect = next(a for a in anomalies if a.get("type") == DEFECT_ANOMALY_TYPE)
        assert defect["severity"] == "error"
        assert defect["attempted"] == report["attempted"]
        assert defect["symptom"] == report["symptom"]

    def test_negative_control_no_failure_report_yields_no_defect_anomaly(self, monkeypatch):
        """Without this, the positive test above could be catching an anomaly
        the chain produces on its own -- the exact trap the legacy-route
        producer test guards against with its own negative control."""
        from adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        payload = self._payload()
        parsed_contract = _contract()  # no failure_report key at all

        captured = {}
        self._install_module_stubs(monkeypatch, captured, parsed_contract)
        monkeypatch.setattr(adapter, "_get_gaia_agent_names", lambda: [payload["agent_type"]])

        adapter.adapt_subagent_stop(self._build_event(payload))

        anomalies = captured.get("anomalies", [])
        assert DEFECT_ANOMALY_TYPE not in [a.get("type") for a in anomalies]
