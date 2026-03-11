#!/usr/bin/env python3
"""
Tests for workflow telemetry recording.

Validates additive run telemetry, compact context snapshots, and runtime
skill snapshot persistence.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.workflow_recorder import record, record_agent_skill_snapshot
from modules.context.context_injector import build_context_telemetry_snapshot
from modules.core.paths import clear_path_cache


@pytest.fixture(autouse=True)
def isolated_workflow_env(tmp_path, monkeypatch):
    """Isolate workflow telemetry writes to a temporary directory."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("GAIA_WRITE_WORKFLOW_METRICS", "1")
    yield tmp_path
    clear_path_cache()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def test_build_context_telemetry_snapshot_compacts_injected_payload():
    payload = {
        "contract": {
            "cluster_details": {},
            "application_services": {},
        },
        "metadata": {
            "cloud_provider": "gcp",
            "contract_version": "3.0",
            "rules_count": 7,
            "historical_episodes_count": 3,
            "surface_routing_version": "1.0",
            "active_surfaces_count": 2,
            "surface_routing_confidence": 0.91,
        },
        "surface_routing": {
            "primary_surface": "live_runtime",
            "active_surfaces": ["live_runtime", "gitops_desired_state"],
            "dispatch_mode": "parallel",
            "multi_surface": True,
            "recommended_agents": ["cloud-troubleshooter", "gitops-operator"],
        },
        "investigation_brief": {
            "agent_role": "primary",
            "primary_surface": "live_runtime",
            "adjacent_surfaces": ["gitops_desired_state"],
            "cross_check_required": True,
            "consolidation_required": True,
            "required_checks": ["verify rollout", "check logs"],
            "evidence_required": ["PATTERNS_CHECKED", "CROSS_LAYER_IMPACTS"],
        },
        "context_update_contract": {
            "readable_sections": ["cluster_details", "application_services"],
            "writable_sections": ["cluster_details"],
        },
    }

    snapshot = build_context_telemetry_snapshot(payload)

    assert snapshot["contract_sections"] == ["application_services", "cluster_details"]
    assert snapshot["contract_sections_count"] == 2
    assert snapshot["surface_routing"]["primary_surface"] == "live_runtime"
    assert snapshot["surface_routing"]["multi_surface"] is True
    assert snapshot["investigation_brief"]["required_checks_count"] == 2
    assert snapshot["context_update_scope"]["writable_sections"] == ["cluster_details"]
    assert snapshot["context_update_scope"]["readable_sections_count"] == 2


def test_record_persists_additive_run_telemetry(tmp_path):
    task_info = {
        "task_id": "agent-001",
        "agent_id": "agent-001",
        "description": "Diagnose rollout drift",
        "agent": "cloud-troubleshooter",
        "tier": "T0",
        "plan_status": "COMPLETE",
        "tags": ["cloud-troubleshooter"],
        "injected_context": {
            "contract": {
                "cluster_details": {},
                "application_services": {},
            },
            "metadata": {
                "cloud_provider": "gcp",
                "contract_version": "3.0",
                "rules_count": 5,
                "surface_routing_version": "1.0",
                "active_surfaces_count": 1,
            },
            "surface_routing": {
                "primary_surface": "live_runtime",
                "active_surfaces": ["live_runtime"],
                "dispatch_mode": "single_surface",
                "recommended_agents": ["cloud-troubleshooter"],
            },
            "investigation_brief": {
                "agent_role": "primary",
                "primary_surface": "live_runtime",
                "evidence_required": ["PATTERNS_CHECKED"],
            },
            "context_update_contract": {
                "readable_sections": ["cluster_details", "application_services"],
                "writable_sections": ["cluster_details"],
            },
        },
    }
    session_context = {
        "timestamp": "2026-03-11T12:00:00",
        "session_id": "sess-telemetry-001",
    }

    metrics = record(
        task_info,
        agent_output="Cluster looks healthy.",
        session_context=session_context,
        commands_executed=["kubectl get pods -n prod"],
        context_update_result={
            "updated": True,
            "sections_updated": ["cluster_details"],
            "rejected": ["operational_guidelines"],
        },
    )

    assert metrics["agent_id"] == "agent-001"
    assert metrics["commands_executed_count"] == 1
    assert metrics["context_updated"] is True
    assert metrics["context_sections_updated"] == ["cluster_details"]
    assert metrics["context_rejected_sections"] == ["operational_guidelines"]
    assert metrics["context_snapshot"]["surface_routing"]["primary_surface"] == "live_runtime"
    assert "agent-protocol" in metrics["default_skills_snapshot"]["skills"]

    workflow_dir = tmp_path / "project-context" / "workflow-episodic-memory"
    metrics_entries = _read_jsonl(workflow_dir / "metrics.jsonl")
    run_entries = _read_jsonl(workflow_dir / "run-snapshots.jsonl")

    assert len(metrics_entries) == 1
    assert len(run_entries) == 1
    assert metrics_entries[0]["commands_executed"] == ["kubectl get pods -n prod"]
    assert metrics_entries[0]["context_snapshot"]["contract_sections_count"] == 2
    assert run_entries[0]["context_updated"] is True
    assert run_entries[0]["default_skills_snapshot"]["skills_count"] >= 1


def test_record_agent_skill_snapshot_appends_runtime_defaults(tmp_path):
    snapshot = record_agent_skill_snapshot(
        "devops-developer",
        session_context={
            "timestamp": "2026-03-11T12:15:00",
            "session_id": "sess-skills-001",
        },
        task_description="Run targeted telemetry tests",
    )

    assert snapshot["agent"] == "devops-developer"
    assert "developer-patterns" in snapshot["skills"]
    assert snapshot["skills_count"] >= 1

    workflow_dir = tmp_path / "project-context" / "workflow-episodic-memory"
    skill_entries = _read_jsonl(workflow_dir / "agent-skills.jsonl")

    assert len(skill_entries) == 1
    assert skill_entries[0]["session_id"] == "sess-skills-001"
    assert "agent-protocol" in skill_entries[0]["skills"]
