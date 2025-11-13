#!/usr/bin/env python3
"""
Success Metrics Collector
Recolecta métricas accionables del workflow para medir efectividad.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Tipos de métricas recolectadas"""
    ROUTING_DECISION = "routing_decision"
    DELEGATION_DECISION = "delegation_decision"
    GUARD_EXECUTION = "guard_execution"
    PHASE_COMPLETION = "phase_completion"
    APPROVAL_GATE = "approval_gate"
    AGENT_INVOCATION = "agent_invocation"


@dataclass
class RoutingMetric:
    """Métrica de decisión de routing"""
    timestamp: str
    user_request: str
    selected_agent: str
    routing_confidence: float
    routing_method: str  # "task_metadata", "semantic", "keyword"
    correct: Optional[bool] = None  # Set later via feedback


@dataclass
class DelegationMetric:
    """Métrica de decisión de delegación"""
    timestamp: str
    user_request: str
    decision: str  # "delegate", "local", "blocked"
    confidence: float
    rule_matched: str
    actual_execution: str  # "delegated", "local", "skipped"


@dataclass
class GuardMetric:
    """Métrica de ejecución de guard"""
    timestamp: str
    guard_name: str
    phase: str
    passed: bool
    tier: str
    reason: str


@dataclass
class PhaseMetric:
    """Métrica de completitud de fase"""
    timestamp: str
    phase: str
    duration_ms: int
    success: bool
    tier: str
    agent: Optional[str] = None


@dataclass
class ApprovalMetric:
    """Métrica de approval gate"""
    timestamp: str
    tier: str
    agent: str
    approved: bool
    response_time_seconds: float
    files_count: int
    operations_count: int


@dataclass
class AgentInvocationMetric:
    """Métrica de invocación de agente"""
    timestamp: str
    agent: str
    task_description: str
    tier: str
    duration_ms: int
    success: bool
    exit_code: int


class MetricsCollector:
    """
    Collector centralizado de métricas del workflow.

    Escribe métricas en formato JSONL para análisis posterior.
    """

    def __init__(self, metrics_dir: Path = None):
        self.metrics_dir = metrics_dir or Path(".claude/metrics")
        self.metrics_dir.mkdir(exist_ok=True, parents=True)

        # Current month metrics file
        self.metrics_file = self.metrics_dir / f"metrics-{datetime.now().strftime('%Y-%m')}.jsonl"

    def record_routing(self, metric: RoutingMetric):
        """Record routing decision metric"""
        self._write_metric(MetricType.ROUTING_DECISION, asdict(metric))

    def record_delegation(self, metric: DelegationMetric):
        """Record delegation decision metric"""
        self._write_metric(MetricType.DELEGATION_DECISION, asdict(metric))

    def record_guard(self, metric: GuardMetric):
        """Record guard execution metric"""
        self._write_metric(MetricType.GUARD_EXECUTION, asdict(metric))

    def record_phase(self, metric: PhaseMetric):
        """Record phase completion metric"""
        self._write_metric(MetricType.PHASE_COMPLETION, asdict(metric))

    def record_approval(self, metric: ApprovalMetric):
        """Record approval gate metric"""
        self._write_metric(MetricType.APPROVAL_GATE, asdict(metric))

    def record_agent_invocation(self, metric: AgentInvocationMetric):
        """Record agent invocation metric"""
        self._write_metric(MetricType.AGENT_INVOCATION, asdict(metric))

    def _write_metric(self, metric_type: MetricType, data: Dict[str, Any]):
        """Write metric to JSONL file"""
        entry = {
            "metric_type": metric_type.value,
            "data": data
        }

        with open(self.metrics_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.debug(f"Metric recorded: {metric_type.value}")

    # ========================================================================
    # AGGREGATION & ANALYSIS
    # ========================================================================

    def get_metrics(
        self,
        metric_type: Optional[MetricType] = None,
        days: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Load metrics from file.

        Args:
            metric_type: Filter by metric type (None = all)
            days: Load metrics from last N days

        Returns:
            List of metric entries
        """
        cutoff = datetime.now() - timedelta(days=days)
        metrics = []

        # Read current month file
        if self.metrics_file.exists():
            with open(self.metrics_file) as f:
                for line in f:
                    try:
                        entry = json.loads(line)

                        # Filter by type
                        if metric_type and entry["metric_type"] != metric_type.value:
                            continue

                        # Filter by date
                        timestamp_str = entry["data"].get("timestamp", "")
                        if timestamp_str:
                            timestamp = datetime.fromisoformat(timestamp_str)
                            if timestamp < cutoff:
                                continue

                        metrics.append(entry)

                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse metric line: {line}")

        return metrics

    def compute_kpis(self, days: int = 7) -> Dict[str, Any]:
        """
        Compute KPIs from collected metrics.

        Returns:
            Dict with all computed KPIs
        """
        kpis = {}

        # KPI 1: Routing Accuracy
        routing_metrics = self.get_metrics(MetricType.ROUTING_DECISION, days)
        if routing_metrics:
            avg_confidence = sum(m["data"]["routing_confidence"] for m in routing_metrics) / len(routing_metrics)
            semantic_count = sum(1 for m in routing_metrics if m["data"]["routing_method"] == "semantic")

            kpis["routing_accuracy"] = {
                "total_decisions": len(routing_metrics),
                "avg_confidence": round(avg_confidence, 2),
                "semantic_routing_rate": round(semantic_count / len(routing_metrics), 2) if routing_metrics else 0
            }

        # KPI 2: Delegation Effectiveness
        delegation_metrics = self.get_metrics(MetricType.DELEGATION_DECISION, days)
        if delegation_metrics:
            delegate_count = sum(1 for m in delegation_metrics if m["data"]["decision"] == "delegate")
            avg_confidence = sum(m["data"]["confidence"] for m in delegation_metrics) / len(delegation_metrics)

            kpis["delegation_effectiveness"] = {
                "total_decisions": len(delegation_metrics),
                "delegation_rate": round(delegate_count / len(delegation_metrics), 2),
                "avg_confidence": round(avg_confidence, 2)
            }

        # KPI 3: Guard Pass Rate
        guard_metrics = self.get_metrics(MetricType.GUARD_EXECUTION, days)
        if guard_metrics:
            passed_count = sum(1 for m in guard_metrics if m["data"]["passed"])

            # By phase
            by_phase = {}
            for metric in guard_metrics:
                phase = metric["data"]["phase"]
                by_phase.setdefault(phase, {"total": 0, "passed": 0})
                by_phase[phase]["total"] += 1
                if metric["data"]["passed"]:
                    by_phase[phase]["passed"] += 1

            kpis["guard_effectiveness"] = {
                "total_guards": len(guard_metrics),
                "pass_rate": round(passed_count / len(guard_metrics), 2),
                "by_phase": {
                    phase: round(stats["passed"] / stats["total"], 2)
                    for phase, stats in by_phase.items()
                }
            }

        # KPI 4: Phase Skip Rate (should be 0 for Phase 4)
        phase_metrics = self.get_metrics(MetricType.PHASE_COMPLETION, days)
        if phase_metrics:
            phase_counts = {}
            for metric in phase_metrics:
                phase = metric["data"]["phase"]
                phase_counts[phase] = phase_counts.get(phase, 0) + 1

            # Phase 4 should never be skipped for T3
            t3_operations = [m for m in phase_metrics if m["data"]["tier"] == "T3"]
            phase_4_t3 = [m for m in t3_operations if m["data"]["phase"] == "phase_4"]

            phase_4_skip_rate = 1.0 - (len(phase_4_t3) / len(t3_operations)) if t3_operations else 0.0

            kpis["phase_completion"] = {
                "total_workflows": len(phase_metrics),
                "phase_distribution": phase_counts,
                "phase_4_skip_rate_t3": round(phase_4_skip_rate, 2)  # Should be 0.00
            }

        # KPI 5: Approval Gate Metrics
        approval_metrics = self.get_metrics(MetricType.APPROVAL_GATE, days)
        if approval_metrics:
            approved_count = sum(1 for m in approval_metrics if m["data"]["approved"])
            avg_response_time = sum(m["data"]["response_time_seconds"] for m in approval_metrics) / len(approval_metrics)

            kpis["approval_gate"] = {
                "total_approvals": len(approval_metrics),
                "approval_rate": round(approved_count / len(approval_metrics), 2),
                "avg_response_time_seconds": round(avg_response_time, 1)
            }

        # KPI 6: Agent Success Rate
        agent_metrics = self.get_metrics(MetricType.AGENT_INVOCATION, days)
        if agent_metrics:
            success_count = sum(1 for m in agent_metrics if m["data"]["success"])
            avg_duration = sum(m["data"]["duration_ms"] for m in agent_metrics) / len(agent_metrics)

            # By agent
            by_agent = {}
            for metric in agent_metrics:
                agent = metric["data"]["agent"]
                by_agent.setdefault(agent, {"total": 0, "success": 0})
                by_agent[agent]["total"] += 1
                if metric["data"]["success"]:
                    by_agent[agent]["success"] += 1

            kpis["agent_performance"] = {
                "total_invocations": len(agent_metrics),
                "success_rate": round(success_count / len(agent_metrics), 2),
                "avg_duration_ms": round(avg_duration, 0),
                "by_agent": {
                    agent: round(stats["success"] / stats["total"], 2)
                    for agent, stats in by_agent.items()
                }
            }

        # Meta KPI: Overall workflow health
        if all(key in kpis for key in ["routing_accuracy", "guard_effectiveness", "phase_completion"]):
            health_score = (
                kpis["routing_accuracy"]["avg_confidence"] * 0.3 +
                kpis["guard_effectiveness"]["pass_rate"] * 0.3 +
                (1.0 - kpis["phase_completion"]["phase_4_skip_rate_t3"]) * 0.4  # Phase 4 compliance is critical
            )

            kpis["overall_health"] = {
                "score": round(health_score, 2),
                "status": "healthy" if health_score >= 0.8 else "needs_attention"
            }

        return kpis


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    collector = MetricsCollector()

    # Record sample metrics
    print("📊 Recording sample metrics...\n")

    collector.record_routing(RoutingMetric(
        timestamp=datetime.now().isoformat(),
        user_request="check pod status",
        selected_agent="gitops-operator",
        routing_confidence=0.85,
        routing_method="semantic"
    ))

    collector.record_guard(GuardMetric(
        timestamp=datetime.now().isoformat(),
        guard_name="guard_phase_4_approval_mandatory",
        phase="phase_4",
        passed=True,
        tier="T3",
        reason="Approval received"
    ))

    collector.record_delegation(DelegationMetric(
        timestamp=datetime.now().isoformat(),
        user_request="terraform apply",
        decision="delegate",
        confidence=1.0,
        rule_matched="T3 tier mandatory delegation",
        actual_execution="delegated"
    ))

    collector.record_phase(PhaseMetric(
        timestamp=datetime.now().isoformat(),
        phase="phase_4",
        duration_ms=1500,
        success=True,
        tier="T3",
        agent="terraform-architect"
    ))

    collector.record_approval(ApprovalMetric(
        timestamp=datetime.now().isoformat(),
        tier="T3",
        agent="terraform-architect",
        approved=True,
        response_time_seconds=12.5,
        files_count=3,
        operations_count=5
    ))

    collector.record_agent_invocation(AgentInvocationMetric(
        timestamp=datetime.now().isoformat(),
        agent="terraform-architect",
        task_description="Apply infrastructure changes",
        tier="T3",
        duration_ms=45000,
        success=True,
        exit_code=0
    ))

    # Compute KPIs
    print("📈 Computing KPIs...\n")
    kpis = collector.compute_kpis(days=7)
    print(json.dumps(kpis, indent=2))
    print("\n✅ Metrics collector working correctly!")