#!/usr/bin/env python3
"""
Pre-Phase Hook - Ejecutar guards ANTES de cada fase.
Se invoca desde el orchestrator antes de comenzar una fase.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, Any, Optional

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "0-guards"))

from workflow_enforcer import get_enforcer, GuardViolation

logger = logging.getLogger(__name__)


def pre_phase_0_clarification(
    ambiguity_score: float,
    user_prompt: str
) -> Dict[str, Any]:
    """
    Ejecutar guards antes de Phase 0 (Clarification).

    Returns:
        {"allowed": bool, "reason": str}
    """
    enforcer = get_enforcer()

    try:
        # Guard: Ambiguity threshold
        enforcer.enforce(
            "guard_phase_0_ambiguity_threshold",
            ambiguity_score=ambiguity_score,
            threshold=0.3
        )

        return {
            "allowed": True,
            "reason": "Phase 0 guards passed"
        }

    except GuardViolation as e:
        return {
            "allowed": False,
            "reason": str(e)
        }


def pre_phase_1_routing(
    agent_name: str,
    routing_confidence: float,
    available_agents: list
) -> Dict[str, Any]:
    """
    Ejecutar guards antes de Phase 1 (Routing).
    """
    enforcer = get_enforcer()

    try:
        # Guard: Routing confidence
        enforcer.enforce(
            "guard_phase_1_routing_confidence",
            routing_confidence=routing_confidence,
            min_confidence=0.5
        )

        # Guard: Agent exists
        enforcer.enforce(
            "guard_phase_1_agent_exists",
            agent_name=agent_name,
            available_agents=available_agents
        )

        return {"allowed": True, "reason": "Phase 1 guards passed"}

    except GuardViolation as e:
        return {"allowed": False, "reason": str(e)}


def pre_phase_2_context(
    context_payload: Dict[str, Any],
    agent_name: str
) -> Dict[str, Any]:
    """
    Ejecutar guards antes de Phase 2 (Context Provisioning).
    """
    enforcer = get_enforcer()

    # Determinar required sections según el agente
    agent_requirements = {
        "terraform-architect": ["project_details", "terraform_infrastructure", "operational_guidelines"],
        "gitops-operator": ["project_details", "gitops_configuration", "cluster_details"],
        "gcp-troubleshooter": ["project_details", "cluster_details"],
        "devops-developer": ["project_details", "operational_guidelines"]
    }

    required_sections = agent_requirements.get(agent_name, ["project_details"])

    try:
        # Guard: Context completeness
        enforcer.enforce(
            "guard_phase_2_context_completeness",
            context_payload=context_payload,
            required_sections=required_sections
        )

        return {"allowed": True, "reason": "Phase 2 guards passed"}

    except GuardViolation as e:
        return {"allowed": False, "reason": str(e)}


def pre_phase_4_approval(
    tier: str,
    realization_package: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Ejecutar guards antes de Phase 4 (Approval Gate).

    CRITICAL: Este guard NO PUEDE fallar para T3 operations.
    """
    enforcer = get_enforcer()

    try:
        # Guard: Planning complete
        enforcer.enforce(
            "guard_phase_5_planning_complete",
            realization_package=realization_package
        )

        # Note: El guard de approval_received se ejecuta DESPUÉS
        # de recibir la respuesta del usuario en post_phase_4

        return {"allowed": True, "reason": "Phase 4 pre-guards passed"}

    except GuardViolation as e:
        return {"allowed": False, "reason": str(e)}


def pre_phase_5_realization(
    tier: str,
    approval_validation: Dict[str, Any],
    realization_package: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Ejecutar guards antes de Phase 5 (Realization).

    CRITICAL: Valida que T3 operations tengan aprobación.
    """
    enforcer = get_enforcer()

    try:
        # Guard: Approval mandatory for T3
        enforcer.enforce(
            "guard_phase_4_approval_mandatory",
            tier=tier,
            approval_received=approval_validation.get("approved", False)
        )

        # Guard: Approval validation
        if tier == "T3":
            enforcer.enforce(
                "guard_phase_4_approval_validation",
                validation_result=approval_validation
            )

        return {"allowed": True, "reason": "Phase 5 guards passed"}

    except GuardViolation as e:
        return {"allowed": False, "reason": str(e)}


def pre_phase_6_ssot_update(
    tier: str,
    realization_success: bool
) -> Dict[str, Any]:
    """
    Ejecutar guards antes de Phase 6 (SSOT Update).
    """
    if not realization_success:
        return {
            "allowed": False,
            "reason": "Cannot update SSOT: Realization failed"
        }

    # Phase 6 no tiene guards bloqueantes adicionales
    # (el guard de ssot_updated se ejecuta DESPUÉS del update)

    return {"allowed": True, "reason": "Phase 6 pre-guards passed"}


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("🧪 Testing Pre-Phase Hooks...\n")

    # Test Phase 4 pre-guard
    result = pre_phase_4_approval(
        tier="T3",
        realization_package={"files": [], "git_operations": {}}
    )
    print(f"Phase 4 pre-guard: {result}")

    # Test Phase 5 pre-guard (sin approval - debe fallar)
    result = pre_phase_5_realization(
        tier="T3",
        approval_validation={"approved": False},
        realization_package={}
    )
    print(f"Phase 5 pre-guard (no approval): {result}")

    # Test Phase 5 pre-guard (con approval - debe pasar)
    result = pre_phase_5_realization(
        tier="T3",
        approval_validation={"approved": True, "action": "proceed_to_realization"},
        realization_package={}
    )
    print(f"Phase 5 pre-guard (with approval): {result}")