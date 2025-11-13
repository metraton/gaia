#!/usr/bin/env python3
"""
Binary Delegation Matrix
Decisión determinística de cuándo delegar vs. ejecutar local.
"""

import re
import logging
from typing import Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class DelegationDecision(Enum):
    """Decisión binaria de delegación"""
    DELEGATE = "delegate"      # Invocar agente especializado
    LOCAL = "local"           # Ejecutar localmente (orchestrator)
    BLOCKED = "blocked"       # Operación no permitida


@dataclass
class DelegationConditions:
    """Condiciones binarias para decisión de delegación"""

    # File operations
    file_count: int = 0              # Número de archivos a modificar
    file_span_multiple_dirs: bool = False  # Archivos en múltiples directorios

    # Command complexity
    has_chained_commands: bool = False  # Usa && o pipes
    has_infrastructure_keywords: bool = False  # terraform/kubectl/helm/flux
    has_approval_keywords: bool = False  # apply/deploy/push/delete

    # Security tier
    security_tier: str = "T0"       # T0, T1, T2, T3

    # Context requirements
    requires_context: bool = False  # Necesita project-context.json
    requires_credentials: bool = False  # Necesita GCP/AWS/K8s credentials

    # Task metadata
    has_task_id: bool = False       # Request menciona task ID (Txxx)
    task_agent: str = None          # Agente sugerido por task metadata


class BinaryDelegationMatrix:
    """
    Matriz binaria para decisiones de delegación.

    Cada condición es un check binario (True/False).
    La decisión final se toma mediante reglas determinísticas.
    """

    def __init__(self):
        self.decision_history = []

    def analyze_request(self, user_request: str, context: Dict[str, Any] = None) -> DelegationConditions:
        """
        Analizar request y extraer condiciones binarias.

        Returns:
            DelegationConditions con todos los flags binarios
        """
        context = context or {}
        conditions = DelegationConditions()

        # File count (si se especifica)
        conditions.file_count = context.get("file_count", 0)
        conditions.file_span_multiple_dirs = context.get("multiple_directories", False)

        # Command complexity
        conditions.has_chained_commands = bool(re.search(r'&&|\||;', user_request))

        # Infrastructure keywords
        infra_keywords = ["terraform", "terragrunt", "kubectl", "helm", "flux", "gcloud", "aws"]
        conditions.has_infrastructure_keywords = any(kw in user_request.lower() for kw in infra_keywords)

        # Approval keywords (T3 indicators)
        approval_keywords = ["apply", "deploy", "push", "delete", "destroy", "create"]
        conditions.has_approval_keywords = any(kw in user_request.lower() for kw in approval_keywords)

        # Security tier (estimado)
        if conditions.has_approval_keywords:
            conditions.security_tier = "T3"
        elif any(kw in user_request.lower() for kw in ["validate", "plan", "template"]):
            conditions.security_tier = "T1"
        else:
            conditions.security_tier = "T0"

        # Context requirements
        conditions.requires_context = conditions.has_infrastructure_keywords
        conditions.requires_credentials = any(kw in user_request.lower() for kw in ["kubectl", "gcloud", "aws", "flux"])

        # Task metadata
        task_match = re.search(r'\b(T\d+|TASK-\d+)\b', user_request, re.IGNORECASE)
        conditions.has_task_id = bool(task_match)
        conditions.task_agent = context.get("task_agent")  # Desde task metadata

        return conditions

    def decide(self, conditions: DelegationConditions) -> Tuple[DelegationDecision, str, float]:
        """
        Decisión binaria determinística de delegación.

        Returns:
            (decision, reason, confidence)

        Confidence:
            1.0 = Decision absoluta (regla binaria clara)
            0.8 = Decision con minor uncertainty
            0.5 = Decision ambigua (fallback rules)
        """

        # ====================================================================
        # RULE 1: Task metadata routing (highest priority)
        # ====================================================================
        if conditions.has_task_id and conditions.task_agent:
            self._log_decision(conditions, DelegationDecision.DELEGATE, "Task metadata routing")
            return (
                DelegationDecision.DELEGATE,
                f"Task metadata specifies agent: {conditions.task_agent}",
                1.0  # Absolute confidence
            )

        # ====================================================================
        # RULE 2: T3 operations ALWAYS delegate (mandatory)
        # ====================================================================
        if conditions.security_tier == "T3":
            self._log_decision(conditions, DelegationDecision.DELEGATE, "T3 tier mandatory delegation")
            return (
                DelegationDecision.DELEGATE,
                "T3 operations require specialized agent + approval gate",
                1.0  # Absolute confidence
            )

        # ====================================================================
        # RULE 3: Multi-file operations (>= 3 files) → DELEGATE
        # ====================================================================
        if conditions.file_count >= 3:
            self._log_decision(conditions, DelegationDecision.DELEGATE, "Multi-file threshold")
            return (
                DelegationDecision.DELEGATE,
                f"Multi-file operation ({conditions.file_count} files) requires agent coordination",
                0.9  # High confidence
            )

        # ====================================================================
        # RULE 4: Files span multiple directories → DELEGATE
        # ====================================================================
        if conditions.file_span_multiple_dirs:
            self._log_decision(conditions, DelegationDecision.DELEGATE, "Multiple directories")
            return (
                DelegationDecision.DELEGATE,
                "Files span multiple directories - requires agent workflow",
                0.9  # High confidence
            )

        # ====================================================================
        # RULE 5: Infrastructure keywords + requires context → DELEGATE
        # ====================================================================
        if conditions.has_infrastructure_keywords and conditions.requires_context:
            self._log_decision(conditions, DelegationDecision.DELEGATE, "Infrastructure + context required")
            return (
                DelegationDecision.DELEGATE,
                "Infrastructure operation requires specialized agent with context",
                0.85  # High confidence
            )

        # ====================================================================
        # RULE 6: Chained commands → DELEGATE (safety)
        # ====================================================================
        if conditions.has_chained_commands:
            self._log_decision(conditions, DelegationDecision.DELEGATE, "Chained commands safety")
            return (
                DelegationDecision.DELEGATE,
                "Chained commands require agent workflow for safety",
                0.8  # Medium-high confidence
            )

        # ====================================================================
        # RULE 7: Simple read-only operations → LOCAL
        # ====================================================================
        if (conditions.security_tier == "T0" and
            not conditions.has_approval_keywords and
            conditions.file_count <= 1):
            self._log_decision(conditions, DelegationDecision.LOCAL, "Simple atomic operation")
            return (
                DelegationDecision.LOCAL,
                "Atomic T0 operation - safe for local execution",
                0.9  # High confidence
            )

        # ====================================================================
        # RULE 8: T1 validation operations → LOCAL (if atomic)
        # ====================================================================
        if (conditions.security_tier == "T1" and
            conditions.file_count <= 1 and
            not conditions.requires_credentials):
            self._log_decision(conditions, DelegationDecision.LOCAL, "Simple T1 validation")
            return (
                DelegationDecision.LOCAL,
                "Simple T1 validation - safe for local execution",
                0.85  # High confidence
            )

        # ====================================================================
        # FALLBACK: When in doubt, DELEGATE (safety default)
        # ====================================================================
        self._log_decision(conditions, DelegationDecision.DELEGATE, "Safety fallback")
        return (
            DelegationDecision.DELEGATE,
            "Ambiguous case - delegating for safety",
            0.5  # Low confidence (fallback)
        )

    def _log_decision(self, conditions: DelegationConditions, decision: DelegationDecision, reason: str):
        """Log decision for audit trail"""
        self.decision_history.append({
            "conditions": conditions,
            "decision": decision,
            "reason": reason
        })
        logger.info(f"Delegation decision: {decision.value} | Reason: {reason}")

    def get_decision_report(self) -> str:
        """Generate decision history report"""
        lines = ["=" * 60, "DELEGATION DECISIONS REPORT", "=" * 60, ""]

        for i, entry in enumerate(self.decision_history, 1):
            lines.append(f"Decision {i}: {entry['decision'].value}")
            lines.append(f"  Reason: {entry['reason']}")
            lines.append(f"  Conditions:")
            lines.append(f"    - Tier: {entry['conditions'].security_tier}")
            lines.append(f"    - Files: {entry['conditions'].file_count}")
            lines.append(f"    - Infrastructure keywords: {entry['conditions'].has_infrastructure_keywords}")
            lines.append("")

        return "\n".join(lines)


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    matrix = BinaryDelegationMatrix()

    test_cases = [
        ("git status", {}),
        ("git commit -m 'test'", {}),
        ("terraform apply", {}),
        ("kubectl get pods", {}),
        ("kubectl apply -f manifest.yaml", {}),
        ("Modify 5 files across terraform/ and k8s/", {"file_count": 5, "multiple_directories": True}),
        ("Read file README.md", {"file_count": 1}),
        ("Validate terraform && check syntax", {}),
    ]

    print("🧪 Testing Binary Delegation Matrix...\n")

    for request, context in test_cases:
        conditions = matrix.analyze_request(request, context)
        decision, reason, confidence = matrix.decide(conditions)

        print(f"Request: {request}")
        print(f"  Decision: {decision.value} (confidence: {confidence:.2f})")
        print(f"  Reason: {reason}")
        print(f"  Conditions: Tier={conditions.security_tier}, Files={conditions.file_count}")
        print()