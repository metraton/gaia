#!/usr/bin/env python3
"""
Tests for workflow telemetry recording.

After T4 of brief ``episodic-workflow-to-db`` the recorder no longer writes
``run-snapshots.jsonl`` or ``metrics.jsonl``. ``record()`` now just builds
the metrics dict and returns it; persistence happens downstream in
``hooks/modules/memory/episode_writer.write()`` via ``store_episode()``,
which inserts into the ``episodes`` table.

The tests verify:
1. ``record()`` returns the full metrics dict with the expected fields.
2. ``record()`` does NOT create ``run-snapshots.jsonl`` or ``metrics.jsonl``
   under the workflow memory dir, even when ``GAIA_WRITE_WORKFLOW_METRICS=1``
   (the gate is intentionally inert post-T4).
3. ``build_context_telemetry_snapshot()`` compacts the injected payload as
   before -- that helper still feeds the in-memory metrics dict that the
   audit pipeline hands off to ``store_episode``.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.audit.workflow_recorder import record  # noqa: E402
from modules.context.context_injector import build_context_telemetry_snapshot  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_workflow_env(tmp_path, monkeypatch):
    """Isolate workflow telemetry under a temp dir; assertions verify the
    directory stays clean."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("GAIA_WRITE_WORKFLOW_METRICS", "1")
    yield tmp_path
    clear_path_cache()


def test_build_context_telemetry_snapshot_compacts_injected_payload():
    payload = {
        "project_knowledge": {
            "cluster_details": {},
            "application_services": {},
        },
        "metadata": {
            "cloud_provider": "gcp",
            "contract_version": "3.0",
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
        "write_permissions": {
            "readable_sections": ["cluster_details", "application_services"],
            "writable_sections": ["cluster_details"],
        },
    }

    snapshot = build_context_telemetry_snapshot(payload)

    assert snapshot["contract_sections"] == ["application_services", "cluster_details"]
    assert snapshot["contract_sections_count"] == 2
    assert snapshot["surface_routing"]["primary_surface"] == "live_runtime"
    assert snapshot["surface_routing"]["multi_surface"] is True
    # context_injector renamed investigation_brief -> agent_contract_handoff
    # (T2.1a). The legacy key in the fixture payload still feeds the new field
    # via the dual-read fallback in build_context_telemetry_snapshot().
    assert snapshot["agent_contract_handoff"]["required_checks_count"] == 2
    assert snapshot["context_update_scope"]["writable_sections"] == ["cluster_details"]
    assert snapshot["context_update_scope"]["readable_sections_count"] == 2


def test_record_returns_metrics_dict_without_jsonl_side_effects(tmp_path):
    task_info = {
        "task_id": "agent-001",
        "agent_id": "agent-001",
        "description": "Diagnose rollout drift",
        "agent": "cloud-troubleshooter",
        "tier": "T0",
        "plan_status": "COMPLETE",
        "tags": ["cloud-troubleshooter"],
        "injected_context": {
            "project_knowledge": {
                "cluster_details": {},
                "application_services": {},
            },
            "metadata": {
                "cloud_provider": "gcp",
                "contract_version": "3.0",
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
            "write_permissions": {
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
    assert metrics["agent"] == "cloud-troubleshooter"
    assert metrics["tier"] == "T0"
    assert metrics["plan_status"] == "COMPLETE"
    assert metrics["commands_executed_count"] == 1
    assert metrics["commands_executed"] == ["kubectl get pods -n prod"]
    assert metrics["context_updated"] is True
    assert metrics["context_sections_updated"] == ["cluster_details"]
    assert metrics["context_rejected_sections"] == ["operational_guidelines"]
    assert (
        metrics["context_snapshot"]["surface_routing"]["primary_surface"]
        == "live_runtime"
    )
    assert "agent-protocol" in metrics["default_skills_snapshot"]["skills"]

    # T4 invariant: workflow_recorder no longer writes JSONL files.
    workflow_dir = tmp_path / "project-context" / "workflow-episodic-memory"
    assert not (workflow_dir / "metrics.jsonl").exists()
    assert not (workflow_dir / "run-snapshots.jsonl").exists()
