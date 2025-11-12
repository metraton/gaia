"""
Approval Gate Enforcement

Ensures that no realization occurs without explicit user approval.
This is a CRITICAL component of the Two-Phase Workflow.
"""

import json
import os
from typing import Dict, Any, Optional
from datetime import datetime


class ApprovalGate:
    """
    Enforces approval gate before any realization actions.

    CRITICAL: This gate is MANDATORY in the workflow. No realization
    can proceed without explicit user approval via AskUserQuestion.
    """

    def __init__(self):
        self.approval_log_path = ".claude/logs/approvals.jsonl"
        self._ensure_log_directory()

    def _ensure_log_directory(self):
        """Ensure the logs directory exists."""
        log_dir = os.path.dirname(self.approval_log_path)
        os.makedirs(log_dir, exist_ok=True)

    def generate_approval_question(
        self,
        realization_package: Dict[str, Any],
        agent_name: str,
        phase: str
    ) -> Dict[str, Any]:
        """
        Generate the approval question configuration for AskUserQuestion tool.

        Args:
            realization_package: The plan to be realized
            agent_name: Name of the agent that generated the plan
            phase: Current phase (e.g., "Phase 3.3")

        Returns:
            Dict with AskUserQuestion configuration
        """

        critical_ops = self._get_critical_operations(realization_package)
        operation_count = self._get_operation_count(realization_package)

        return {
            "questions": [{
                "question": f"¿Apruebas la realización de este plan? Se ejecutará: {critical_ops}",
                "header": "Approval",
                "multiSelect": False,
                "options": [
                    {
                        "label": "✅ Aprobar y ejecutar",
                        "description": f"Proceder con {operation_count} operaciones: {critical_ops}"
                    },
                    {
                        "label": "❌ Rechazar",
                        "description": "Cancelar la realización. No se harán cambios."
                    }
                ]
            }]
        }

    def generate_summary(self, realization_package: Dict[str, Any]) -> str:
        """
        Generate human-readable summary of the realization package.

        This summary should be presented to the user BEFORE calling
        AskUserQuestion so they have full context for their decision.
        """

        summary_parts = []
        summary_parts.append("="*60)
        summary_parts.append("📦 REALIZATION PACKAGE - APPROVAL REQUIRED")
        summary_parts.append("="*60)

        # Files
        if "files" in realization_package:
            files = realization_package["files"]
            summary_parts.append(f"\n**Archivos a crear/modificar:** {len(files)}")
            for file_info in files[:10]:  # Show first 10
                action = file_info.get('action', 'create')
                summary_parts.append(f"  [{action}] {file_info['path']}")
            if len(files) > 10:
                summary_parts.append(f"  ... y {len(files) - 10} archivos más")

        # Git operations
        if "git_operations" in realization_package:
            git_ops = realization_package["git_operations"]
            summary_parts.append(f"\n**Git Operations:**")
            summary_parts.append(f"  Commit: {git_ops.get('commit_message', 'N/A')}")
            summary_parts.append(f"  Branch: {git_ops.get('branch', 'main')}")
            summary_parts.append(f"  Remote: {git_ops.get('remote', 'origin')}")
            summary_parts.append(f"  Operation: git push")

        # Resources affected
        if "resources_affected" in realization_package:
            resources = realization_package["resources_affected"]
            summary_parts.append(f"\n**Recursos Afectados en el Cluster:**")
            for resource_type, items in resources.items():
                if isinstance(items, list):
                    summary_parts.append(f"  {resource_type}: {len(items)}")
                    for item in items[:5]:
                        summary_parts.append(f"    - {item}")
                    if len(items) > 5:
                        summary_parts.append(f"    ... y {len(items) - 5} más")
                else:
                    summary_parts.append(f"  {resource_type}: {items}")

        # Terraform operations
        if "terraform_operations" in realization_package:
            tf_ops = realization_package["terraform_operations"]
            summary_parts.append(f"\n**Terraform Operations:**")
            summary_parts.append(f"  Command: {tf_ops.get('command', 'N/A')}")
            summary_parts.append(f"  Path: {tf_ops.get('path', 'N/A')}")
            if "resources" in tf_ops:
                summary_parts.append(f"  Resources to change: {len(tf_ops['resources'])}")

        # Validation results (if present)
        if "validation_results" in realization_package:
            validation = realization_package["validation_results"]
            summary_parts.append(f"\n**Pre-Deployment Validation:**")
            summary_parts.append(f"  Status: {validation.get('status', 'N/A')}")
            if validation.get('warnings'):
                summary_parts.append(f"  ⚠️  Warnings: {len(validation['warnings'])}")
                for warning in validation['warnings'][:3]:
                    summary_parts.append(f"    - {warning}")

        # Estimated impact
        if "estimated_impact" in realization_package:
            impact = realization_package["estimated_impact"]
            summary_parts.append(f"\n**Estimated Impact:**")
            summary_parts.append(f"  Downtime: {impact.get('downtime', 'None expected')}")
            summary_parts.append(f"  Risk Level: {impact.get('risk_level', 'Medium')}")

        summary_parts.append("\n" + "="*60)

        return "\n".join(summary_parts)

    def _get_critical_operations(self, realization_package: Dict[str, Any]) -> str:
        """Extract critical operations for the approval question."""
        operations = []

        if "git_operations" in realization_package:
            git_ops = realization_package["git_operations"]
            branch = git_ops.get('branch', 'main')
            operations.append(f"git push origin {branch}")

        if "kubectl_operations" in realization_package:
            operations.append("kubectl apply")

        if "terraform_operations" in realization_package:
            tf_ops = realization_package["terraform_operations"]
            command = tf_ops.get('command', 'apply')
            operations.append(f"terraform {command}")

        if "flux_operations" in realization_package:
            operations.append("flux reconcile")

        return ", ".join(operations) if operations else "cambios al repositorio"

    def _get_operation_count(self, realization_package: Dict[str, Any]) -> int:
        """Count total operations in the package."""
        count = 0

        if "files" in realization_package:
            count += len(realization_package["files"])

        if "git_operations" in realization_package:
            count += 2  # commit + push

        if "kubectl_operations" in realization_package:
            kubectl_ops = realization_package["kubectl_operations"]
            count += len(kubectl_ops) if isinstance(kubectl_ops, list) else 1

        if "terraform_operations" in realization_package:
            count += 1

        return count

    def validate_approval_response(self, user_response: str) -> Dict[str, Any]:
        """
        Validate the user's approval response.

        Args:
            user_response: The response from AskUserQuestion

        Returns:
            Dict with validation result and action to take
        """

        if user_response == "✅ Aprobar y ejecutar":
            return {
                "approved": True,
                "action": "proceed_to_realization",
                "message": "✅ Aprobación recibida. Procediendo a Fase 5 (Realization)..."
            }

        elif user_response == "❌ Rechazar":
            return {
                "approved": False,
                "action": "halt_workflow",
                "message": "❌ Realización rechazada por el usuario. Workflow detenido."
            }

        else:
            # User selected "Other" or provided custom response
            return {
                "approved": False,
                "action": "clarify_with_user",
                "message": f"⚠️  Respuesta no estándar recibida: '{user_response}'. Se requiere clarificación.",
                "user_input": user_response
            }

    def log_approval(
        self,
        realization_package: Dict[str, Any],
        user_response: str,
        approved: bool,
        agent_name: str,
        phase: str
    ):
        """
        Log the approval decision for audit trail.

        This creates a permanent record of all approval decisions
        for compliance and troubleshooting purposes.
        """

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent_name,
            "phase": phase,
            "approved": approved,
            "user_response": user_response,
            "files_count": len(realization_package.get("files", [])),
            "operations": self._get_critical_operations(realization_package),
            "git_commit": realization_package.get("git_operations", {}).get("commit_message", "N/A")
        }

        # Append to JSONL log file
        with open(self.approval_log_path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

        return log_entry


# Convenience function for orchestrator to use
def request_approval(
    realization_package: Dict[str, Any],
    agent_name: str,
    phase: str
) -> Dict[str, Any]:
    """
    Main function to request approval from user.

    This should be called by the orchestrator in Phase 4 (Approval Gate).

    Args:
        realization_package: The complete realization package from the agent
        agent_name: Name of the agent that generated the plan
        phase: Current phase identifier

    Returns:
        Dict with:
        - summary: String to present to user
        - question_config: Dict to pass to AskUserQuestion tool
    """

    gate = ApprovalGate()

    return {
        "summary": gate.generate_summary(realization_package),
        "question_config": gate.generate_approval_question(realization_package, agent_name, phase),
        "gate_instance": gate
    }


def process_approval_response(
    gate_instance: ApprovalGate,
    user_response: str,
    realization_package: Dict[str, Any],
    agent_name: str,
    phase: str
) -> Dict[str, Any]:
    """
    Process the user's approval response.

    Args:
        gate_instance: The ApprovalGate instance from request_approval
        user_response: The user's response from AskUserQuestion
        realization_package: The realization package
        agent_name: Name of the agent
        phase: Current phase

    Returns:
        Dict with validation result and action to take
    """

    # Validate response
    validation = gate_instance.validate_approval_response(user_response)

    # Log the decision
    gate_instance.log_approval(
        realization_package,
        user_response,
        validation["approved"],
        agent_name,
        phase
    )

    return validation
