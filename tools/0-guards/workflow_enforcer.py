#!/usr/bin/env python3
"""
Workflow Guards - Binary Enforcement System
Enfuerza las reglas del workflow ANTES de que se ejecuten las fases.
"""

import sys
import json
import logging
from typing import Dict, Any, Optional, Tuple
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class GuardViolation(Exception):
    """Excepción lanzada cuando un guard falla"""
    pass


class PhaseGuard(Enum):
    """Guards por fase del workflow"""
    PHASE_0_CLARIFICATION = "phase_0_clarification"
    PHASE_1_ROUTING = "phase_1_routing"
    PHASE_2_CONTEXT = "phase_2_context"
    PHASE_3_PLANNING = "phase_3_planning"
    PHASE_4_APPROVAL = "phase_4_approval"
    PHASE_5_REALIZATION = "phase_5_realization"
    PHASE_6_SSOT_UPDATE = "phase_6_ssot_update"


class WorkflowEnforcer:
    """
    Enforcer binario de reglas de workflow.

    Cada guard retorna (pass: bool, reason: str).
    Si pass == False, el workflow se detiene INMEDIATAMENTE.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config = self._load_config(config_path)
        self.guard_history = []

    def _load_config(self, config_path: Optional[Path]) -> Dict[str, Any]:
        """Cargar configuración de guards"""
        if config_path and config_path.exists():
            with open(config_path) as f:
                return json.load(f)

        # Configuración por defecto
        return {
            "enforcement_enabled": True,
            "guards": {
                "phase_4_approval_mandatory": True,
                "phase_2_context_required": True,
                "phase_1_routing_confidence_min": 0.5,
                "tier_escalation_requires_approval": True,
                "ssot_update_after_t3": True
            }
        }

    # ========================================================================
    # PHASE 0: CLARIFICATION GUARDS
    # ========================================================================

    def guard_phase_0_ambiguity_threshold(
        self,
        ambiguity_score: float,
        threshold: float = 0.3
    ) -> Tuple[bool, str]:
        """
        Guard: Si ambiguity_score > threshold, DEBE ejecutarse Phase 0.

        Binary check: ambiguity_score <= threshold OR phase_0_executed == True
        """
        if ambiguity_score > threshold:
            return False, (
                f"Guard Violation: Ambiguity score {ambiguity_score:.2f} exceeds "
                f"threshold {threshold}. Phase 0 (Clarification) is MANDATORY."
            )

        return True, f"Guard passed: Ambiguity score {ambiguity_score:.2f} within acceptable range"

    # ========================================================================
    # PHASE 1: ROUTING GUARDS
    # ========================================================================

    def guard_phase_1_routing_confidence(
        self,
        routing_confidence: float,
        min_confidence: float = 0.5
    ) -> Tuple[bool, str]:
        """
        Guard: Routing confidence debe ser >= min_confidence.

        Binary check: routing_confidence >= min_confidence
        """
        if routing_confidence < min_confidence:
            return False, (
                f"Guard Violation: Routing confidence {routing_confidence:.2f} below "
                f"minimum {min_confidence}. Cannot proceed with low-confidence routing."
            )

        return True, f"Guard passed: Routing confidence {routing_confidence:.2f} acceptable"

    def guard_phase_1_agent_exists(
        self,
        agent_name: str,
        available_agents: list
    ) -> Tuple[bool, str]:
        """
        Guard: El agente seleccionado debe existir.

        Binary check: agent_name in available_agents
        """
        if agent_name not in available_agents:
            return False, (
                f"Guard Violation: Agent '{agent_name}' does not exist. "
                f"Available: {', '.join(available_agents)}"
            )

        return True, f"Guard passed: Agent '{agent_name}' exists"

    # ========================================================================
    # PHASE 2: CONTEXT GUARDS
    # ========================================================================

    def guard_phase_2_context_completeness(
        self,
        context_payload: Dict[str, Any],
        required_sections: list
    ) -> Tuple[bool, str]:
        """
        Guard: Context payload debe contener todas las secciones requeridas.

        Binary check: all(section in context_payload for section in required_sections)
        """
        missing = [s for s in required_sections if s not in context_payload.get("contract", {})]

        if missing:
            return False, (
                f"Guard Violation: Context payload missing required sections: {missing}"
            )

        return True, "Guard passed: Context payload complete"

    # ========================================================================
    # PHASE 4: APPROVAL GUARDS (CRITICAL)
    # ========================================================================

    def guard_phase_4_approval_mandatory(
        self,
        tier: str,
        approval_received: bool
    ) -> Tuple[bool, str]:
        """
        Guard: T3 operations MUST have explicit approval.

        Binary check: (tier != "T3") OR (approval_received == True)

        This is the MOST CRITICAL guard in the entire system.
        """
        if tier == "T3" and not approval_received:
            return False, (
                f"Guard Violation: T3 operation requires explicit user approval. "
                f"Phase 4 (Approval Gate) is MANDATORY and cannot be skipped."
            )

        return True, f"Guard passed: Approval requirement satisfied for tier {tier}"

    def guard_phase_4_approval_validation(
        self,
        validation_result: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Guard: Validation result debe tener approved=True para T3.

        Binary check: validation_result["approved"] == True
        """
        if not validation_result.get("approved", False):
            return False, (
                f"Guard Violation: User did not approve realization. "
                f"Action: {validation_result.get('action', 'unknown')}"
            )

        return True, "Guard passed: User approval received"

    # ========================================================================
    # PHASE 5: REALIZATION GUARDS
    # ========================================================================

    def guard_phase_5_planning_complete(
        self,
        realization_package: Optional[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Guard: No se puede realizar sin un plan completo.

        Binary check: realization_package is not None
        """
        if not realization_package:
            return False, (
                "Guard Violation: Cannot proceed to realization without a plan. "
                "Phase 3 (Planning) must complete successfully first."
            )

        return True, "Guard passed: Realization package exists"

    # ========================================================================
    # PHASE 6: SSOT UPDATE GUARDS
    # ========================================================================

    def guard_phase_6_ssot_update_after_t3(
        self,
        tier: str,
        ssot_updated: bool
    ) -> Tuple[bool, str]:
        """
        Guard: T3 operations MUST update SSOT.

        Binary check: (tier != "T3") OR (ssot_updated == True)
        """
        if tier == "T3" and not ssot_updated:
            return False, (
                f"Guard Violation: T3 operation completed but SSOT not updated. "
                f"Phase 6 (SSOT Update) is MANDATORY after T3 realization."
            )

        return True, f"Guard passed: SSOT update requirement satisfied for tier {tier}"

    # ========================================================================
    # ENFORCEMENT ENGINE
    # ========================================================================

    def enforce(
        self,
        guard_name: str,
        *args,
        **kwargs
    ) -> Tuple[bool, str]:
        """
        Ejecutar un guard y registrar el resultado.

        Args:
            guard_name: Nombre del método guard (e.g., "guard_phase_4_approval_mandatory")
            *args, **kwargs: Argumentos para el guard

        Returns:
            (pass: bool, reason: str)

        Raises:
            GuardViolation: Si enforcement_enabled=True y el guard falla
        """
        # Obtener el método guard
        guard_method = getattr(self, guard_name, None)
        if not guard_method:
            logger.error(f"Guard method '{guard_name}' not found")
            return False, f"Guard method '{guard_name}' not found"

        # Ejecutar el guard
        passed, reason = guard_method(*args, **kwargs)

        # Registrar en historial
        self.guard_history.append({
            "guard": guard_name,
            "passed": passed,
            "reason": reason,
            "args": args,
            "kwargs": kwargs
        })

        # Log resultado
        if passed:
            logger.info(f"✅ {guard_name}: {reason}")
        else:
            logger.error(f"❌ {guard_name}: {reason}")

        # Enforcement: si está habilitado y falló, lanzar excepción
        if not passed and self.config.get("enforcement_enabled", True):
            raise GuardViolation(reason)

        return passed, reason

    def get_guard_report(self) -> str:
        """Generar reporte de todos los guards ejecutados"""
        lines = ["=" * 60, "WORKFLOW GUARDS REPORT", "=" * 60, ""]

        for entry in self.guard_history:
            status = "✅ PASS" if entry["passed"] else "❌ FAIL"
            lines.append(f"{status} | {entry['guard']}")
            lines.append(f"  Reason: {entry['reason']}")
            lines.append("")

        total = len(self.guard_history)
        passed = sum(1 for e in self.guard_history if e["passed"])

        lines.append(f"Total Guards: {total} | Passed: {passed} | Failed: {total - passed}")
        lines.append("=" * 60)

        return "\n".join(lines)


# Singleton instance
_enforcer_instance = None

def get_enforcer(config_path: Optional[Path] = None) -> WorkflowEnforcer:
    """Get singleton enforcer instance"""
    global _enforcer_instance
    if _enforcer_instance is None:
        _enforcer_instance = WorkflowEnforcer(config_path)
    return _enforcer_instance


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    enforcer = get_enforcer()

    # Test cases
    print("🧪 Testing Workflow Guards...\n")

    # Test 1: Phase 4 approval mandatory
    try:
        enforcer.enforce(
            "guard_phase_4_approval_mandatory",
            tier="T3",
            approval_received=False
        )
        print("❌ Test 1 FAILED: Should have raised GuardViolation")
    except GuardViolation as e:
        print(f"✅ Test 1 PASSED: Correctly blocked T3 without approval")

    # Test 2: Routing confidence
    try:
        enforcer.enforce(
            "guard_phase_1_routing_confidence",
            routing_confidence=0.3,
            min_confidence=0.5
        )
        print("❌ Test 2 FAILED: Should have raised GuardViolation")
    except GuardViolation as e:
        print(f"✅ Test 2 PASSED: Correctly blocked low routing confidence")

    # Test 3: Valid approval
    try:
        enforcer.enforce(
            "guard_phase_4_approval_mandatory",
            tier="T3",
            approval_received=True
        )
        print(f"✅ Test 3 PASSED: Correctly allowed T3 with approval")
    except GuardViolation as e:
        print(f"❌ Test 3 FAILED: Should not have raised GuardViolation")

    # Print report
    print("\n" + enforcer.get_guard_report())