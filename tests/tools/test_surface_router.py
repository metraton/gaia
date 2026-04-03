import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

from surface_router import (  # noqa: E402
    build_investigation_brief,
    classify_surfaces,
    load_surface_routing_config,
)


def test_load_surface_routing_config():
    config = load_surface_routing_config()

    assert config["version"] == "1.0"
    assert "terraform_iac" in config["surfaces"]
    assert config["surfaces"]["terraform_iac"]["primary_agent"] == "terraform-architect"


def test_classify_single_surface_task():
    config = load_surface_routing_config()

    routing = classify_surfaces(
        "Review terraform state drift in the shared module and IAM policy.",
        current_agent="terraform-architect",
        routing_config=config,
    )

    assert routing["primary_surface"] == "terraform_iac"
    assert routing["active_surfaces"] == ["terraform_iac"]
    assert routing["dispatch_mode"] == "single_surface"
    assert routing["recommended_agents"] == ["terraform-architect"]


def test_classify_multi_surface_task():
    config = load_surface_routing_config()

    routing = classify_surfaces(
        "Investigate why the CI pipeline changed the image tag, the deployment rollout failed, and kubectl logs show runtime errors.",
        current_agent="developer",
        routing_config=config,
    )

    assert routing["multi_surface"] is True
    assert "app_ci_tooling" in routing["active_surfaces"]
    assert "gitops_desired_state" in routing["active_surfaces"]
    assert "live_runtime" in routing["active_surfaces"]
    assert routing["dispatch_mode"] == "parallel"


def test_classify_falls_back_to_agent_surface_when_signals_are_weak():
    config = load_surface_routing_config()

    routing = classify_surfaces(
        "Need a quick look at this task.",
        current_agent="gitops-operator",
        routing_config=config,
    )

    assert routing["active_surfaces"] == ["gitops_desired_state"]
    assert routing["primary_surface"] == "gitops_desired_state"
    assert routing["confidence"] > 0.0


def test_build_investigation_brief_for_cross_surface_task():
    config = load_surface_routing_config()
    contract_context = {
        "project_identity": {},
        "stack": {},
        "git": {},
        "environment": {},
        "infrastructure": {},
        "application_services": {},
        "operational_guidelines": {},
    }

    brief = build_investigation_brief(
        "Investigate why the CI pipeline changed the image tag, the deployment rollout failed, and kubectl logs show runtime errors.",
        "developer",
        contract_context,
        routing_config=config,
    )

    assert brief["agent_role"] == "primary"
    assert brief["dispatch_mode"] == "parallel"
    assert brief["cross_check_required"] is True
    assert brief["consolidation_required"] is True
    assert "gitops_desired_state" in brief["adjacent_surfaces"]
    assert "live_runtime" in brief["adjacent_surfaces"]
    assert brief["contract_sections_to_anchor"] == [
        "application_services",
        "environment",
        "git",
        "infrastructure",
        "operational_guidelines",
        "project_identity",
        "stack",
    ]
    assert "COMMANDS_RUN" in brief["evidence_required"]
    assert "OWNERSHIP_ASSESSMENT" in brief["consolidation_fields"]
