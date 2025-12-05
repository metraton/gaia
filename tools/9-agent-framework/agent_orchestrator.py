#!/usr/bin/env python3
"""
Agent Orchestrator - Unified 5-Layer Agent Execution

Integrates:
- Capa 1: Payload Validation
- Capa 2: Local Discovery
- Capa 3: Finding Classification
- Capa 4: Remote Validation (if discrepancies)
- Capa 5: Execution with Profiles

Reference: Agent-Complete-Workflow.md (5 Capas)
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional
from enum import Enum

from payload_validator import PayloadValidator, ValidationResult
from local_discoverer import LocalDiscoverer, DiscoveryResult
from finding_classifier import FindingClassifier, FindingTier, DataOrigin, Finding
from execution_manager import ExecutionManager, ExecutionMetrics
from logging_manager import JSONLogger, EventType
from remote_validator import RemoteValidator, RemoteValidationResult

logger = logging.getLogger(__name__)


class ExecutionPhase(Enum):
    """Current phase of execution"""
    VALIDATION = "validation"
    DISCOVERY = "discovery"
    CLASSIFICATION = "classification"
    REMOTE_VALIDATION = "remote_validation"
    EXECUTION = "execution"


@dataclass
class AgentExecutionResult:
    """Complete result of agent execution through all 5 layers"""
    success: bool
    phase_reached: ExecutionPhase
    payload_validation: Optional[ValidationResult]
    local_discovery: Optional[DiscoveryResult]
    findings: Optional[Any]  # ClassificationResult
    execution_metrics: Optional[ExecutionMetrics]
    report: str
    duration_ms: int


class AgentOrchestrator:
    """
    Orchestrates agent execution through all 5 layers.

    Philosophy: Process payload → discover local → classify findings →
    validate remote (if needed) → execute with profiles → report concisely
    """

    def __init__(self, agent_type: str = "terraform-architect", log_dir: Path = Path(".claude/logs")):
        self.agent_type = agent_type
        self.json_logger = JSONLogger(log_dir)
        self.validator = PayloadValidator()
        self.execution_manager = ExecutionManager()

    def execute_full_workflow(self, payload: Dict[str, Any]) -> AgentExecutionResult:
        """
        Execute complete workflow (all 5 layers).

        Returns: AgentExecutionResult with complete trace
        """
        start_time = time.time()
        report_lines = []

        # ═════════════════════════════════════════════════════════════════════
        # CAPA 1: PAYLOAD VALIDATION
        # ═════════════════════════════════════════════════════════════════════
        logger.info("Starting Capa 1: Payload Validation")
        val_start = time.time()

        validation_result = self.validator.validate_payload(payload)

        val_duration = int((time.time() - val_start) * 1000)
        self.json_logger.log_validation_complete(
            agent=self.agent_type,
            is_valid=validation_result.is_valid,
            duration_ms=val_duration,
            fields_valid=len(validation_result.valid_fields),
            fields_missing=len(validation_result.missing_fields)
        )

        if not validation_result.is_valid:
            report_lines.append(self.validator.generate_report(validation_result))
            return AgentExecutionResult(
                success=False,
                phase_reached=ExecutionPhase.VALIDATION,
                payload_validation=validation_result,
                local_discovery=None,
                findings=None,
                execution_metrics=None,
                report="\n".join(report_lines),
                duration_ms=int((time.time() - start_time) * 1000)
            )

        report_lines.append("✓ PAYLOAD VALIDATION PASSED\n")

        # ═════════════════════════════════════════════════════════════════════
        # CAPA 2: LOCAL DISCOVERY
        # ═════════════════════════════════════════════════════════════════════
        logger.info("Starting Capa 2: Local Discovery")

        # Get infrastructure paths from payload
        infra_paths = validation_result.valid_fields.get("infrastructure_paths", {})
        if not infra_paths:
            report_lines.append("⚠️ No infrastructure paths provided, skipping discovery")
            discovery_result = None
        else:
            # Discover in main path (usually the first one)
            main_path = list(infra_paths.values())[0] if infra_paths else None

            if main_path:
                disc_start = time.time()
                discoverer = LocalDiscoverer(Path(main_path))
                discovery_result = discoverer.discover()
                disc_duration = int((time.time() - disc_start) * 1000)

                self.json_logger.log_discovery_complete(
                    agent=self.agent_type,
                    files_discovered=sum(
                        len(v) for v in discovery_result.discovered_files.values()
                    ),
                    ssot_count=len(discovery_result.ssot_files),
                    discrepancies=len(discovery_result.discrepancies),
                    duration_ms=disc_duration
                )

                report_lines.append(discoverer.generate_report(discovery_result))
            else:
                discovery_result = None

        # ═════════════════════════════════════════════════════════════════════
        # CAPA 3: FINDING CLASSIFICATION
        # ═════════════════════════════════════════════════════════════════════
        logger.info("Starting Capa 3: Finding Classification")

        classifier = FindingClassifier()

        # Add findings based on discrepancies
        if discovery_result and discovery_result.discrepancies:
            for disc in discovery_result.discrepancies:
                classifier.add_finding(Finding(
                    tier=FindingTier.DEVIATION,
                    title="Discrepancy detected",
                    description=disc,
                    origin=DataOrigin.LOCAL_ONLY,
                    suggested_action="Review for reconciliation"
                ))

        classification_result = classifier.classify()

        # Should we escalate to remote validation?
        should_escalate = classification_result.should_escalate_to_live

        report_lines.append(classifier.generate_report(classification_result))

        # ═════════════════════════════════════════════════════════════════════
        # CAPA 4: REMOTE VALIDATION (if needed)
        # ═════════════════════════════════════════════════════════════════════
        remote_validation_results = []
        if should_escalate:
            logger.info("Escalating to Capa 4: Remote Validation")
            report_lines.append("Escalating to remote validation due to discrepancies...\n")

            # Initialize remote validator (dry_run mode for safety)
            dry_run = payload.get("dry_run", True)
            remote_validator = RemoteValidator(dry_run=dry_run)

            # Validate resources based on findings
            for finding in classifier.findings:
                if finding.tier in [FindingTier.CRITICAL, FindingTier.HIGH]:
                    # Extract resource info from finding
                    # This is a simplified example - real implementation would parse finding details
                    if "kubernetes" in finding.description.lower():
                        # Example: validate Kubernetes resource
                        result = remote_validator.validate_kubernetes_resource(
                            resource_type="deployment",
                            resource_name="example-deployment",
                            namespace="default"
                        )
                        remote_validation_results.append(result)

                    elif "terraform" in finding.description.lower():
                        # Example: validate Terraform resource
                        result = remote_validator.validate_terraform_resource(
                            resource_address="google_compute_instance.example"
                        )
                        remote_validation_results.append(result)

            # Generate validation report
            if remote_validation_results:
                validation_report = remote_validator.generate_report()
                report_lines.append(validation_report)

        # ═════════════════════════════════════════════════════════════════════
        # CAPA 5: EXECUTION (if needed)
        # ═════════════════════════════════════════════════════════════════════
        logger.info("Capa 5: Execution Phase (optional)")

        # For POC, just report that we reached here
        execution_metrics = None  # Would execute if needed

        # ═════════════════════════════════════════════════════════════════════
        # FINAL REPORT
        # ═════════════════════════════════════════════════════════════════════
        total_duration = int((time.time() - start_time) * 1000)

        report_lines.append(f"\n{'='*60}")
        report_lines.append("WORKFLOW COMPLETE")
        report_lines.append(f"Total Duration: {total_duration}ms ({total_duration/1000:.1f}s)")
        report_lines.append(f"{'='*60}\n")

        # Print metrics summary
        self.json_logger.print_metrics_summary()

        return AgentExecutionResult(
            success=True,
            phase_reached=ExecutionPhase.EXECUTION,
            payload_validation=validation_result,
            local_discovery=discovery_result,
            findings=asdict(classification_result) if classification_result else None,
            execution_metrics=execution_metrics,
            report="\n".join(report_lines),
            duration_ms=total_duration
        )

    def generate_final_report(self, result: AgentExecutionResult) -> str:
        """Generate final report for user"""
        lines = []
        lines.append(result.report)

        if result.execution_metrics:
            lines.append(self.execution_manager.generate_report(result.execution_metrics))

        return "\n".join(lines)


# CLI Usage
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    # Example payload
    payload = {
        "contract": {
            "project_details": {
                "name": "test-project",
                "root": "/tmp/test"
            },
            "infrastructure_paths": {
                "terraform_root": "/tmp/test/terraform"
            },
            "operational_guidelines": {
                "action": "plan"
            }
        },
        "enrichment": {}
    }

    orchestrator = AgentOrchestrator("terraform-architect")
    result = orchestrator.execute_full_workflow(payload)

    print(orchestrator.generate_final_report(result))
    print(f"\nExecution Success: {result.success}")
    print(f"Phase Reached: {result.phase_reached.value}")