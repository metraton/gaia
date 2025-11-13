#!/usr/bin/env python3
"""
Metrics Dashboard Generator
Genera reporte human-readable de métricas.
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).parent))

from metrics_collector import MetricsCollector


class MetricsDashboard:
    """Generate human-readable metrics dashboard"""

    def __init__(self, collector: MetricsCollector):
        self.collector = collector

    def generate_report(self, days: int = 7) -> str:
        """Generate full metrics report"""
        kpis = self.collector.compute_kpis(days)

        lines = []
        lines.append("=" * 80)
        lines.append(f"📊 GAIA-OPS SUCCESS METRICS DASHBOARD")
        lines.append(f"Period: Last {days} days | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("=" * 80)
        lines.append("")

        # Overall Health
        if "overall_health" in kpis:
            health = kpis["overall_health"]
            status_emoji = "✅" if health["status"] == "healthy" else "⚠️"
            lines.append(f"{status_emoji} OVERALL HEALTH: {health['score']:.2f} ({health['status']})")
            lines.append("")

        # KPI 1: Routing Accuracy
        if "routing_accuracy" in kpis:
            ra = kpis["routing_accuracy"]
            lines.append("🎯 ROUTING ACCURACY")
            lines.append(f"   Total Decisions: {ra['total_decisions']}")
            lines.append(f"   Avg Confidence: {ra['avg_confidence']:.2f}")
            lines.append(f"   Semantic Routing Rate: {ra['semantic_routing_rate']:.1%}")
            lines.append("")

        # KPI 2: Delegation Effectiveness
        if "delegation_effectiveness" in kpis:
            de = kpis["delegation_effectiveness"]
            lines.append("🔀 DELEGATION EFFECTIVENESS")
            lines.append(f"   Total Decisions: {de['total_decisions']}")
            lines.append(f"   Delegation Rate: {de['delegation_rate']:.1%}")
            lines.append(f"   Avg Confidence: {de['avg_confidence']:.2f}")
            lines.append("")

        # KPI 3: Guard Effectiveness
        if "guard_effectiveness" in kpis:
            ge = kpis["guard_effectiveness"]
            lines.append("🛡️  GUARD EFFECTIVENESS")
            lines.append(f"   Total Guards: {ge['total_guards']}")
            lines.append(f"   Pass Rate: {ge['pass_rate']:.1%}")
            if "by_phase" in ge:
                lines.append("   By Phase:")
                for phase, pass_rate in ge["by_phase"].items():
                    status = "✅" if pass_rate >= 0.9 else "⚠️"
                    lines.append(f"     {status} {phase}: {pass_rate:.1%}")
            lines.append("")

        # KPI 4: Phase Completion
        if "phase_completion" in kpis:
            pc = kpis["phase_completion"]
            lines.append("📋 PHASE COMPLETION")
            lines.append(f"   Total Workflows: {pc['total_workflows']}")

            # Phase 4 skip rate (CRITICAL - should be 0%)
            skip_rate = pc["phase_4_skip_rate_t3"]
            status = "✅" if skip_rate == 0.0 else "🚨"
            lines.append(f"   {status} Phase 4 Skip Rate (T3): {skip_rate:.1%} (target: 0%)")

            if "phase_distribution" in pc:
                lines.append("   Phase Distribution:")
                for phase, count in pc["phase_distribution"].items():
                    lines.append(f"     - {phase}: {count}")
            lines.append("")

        # KPI 5: Approval Gate
        if "approval_gate" in kpis:
            ag = kpis["approval_gate"]
            lines.append("✋ APPROVAL GATE")
            lines.append(f"   Total Approvals: {ag['total_approvals']}")
            lines.append(f"   Approval Rate: {ag['approval_rate']:.1%}")
            lines.append(f"   Avg Response Time: {ag['avg_response_time_seconds']:.1f}s")
            lines.append("")

        # KPI 6: Agent Performance
        if "agent_performance" in kpis:
            ap = kpis["agent_performance"]
            lines.append("🤖 AGENT PERFORMANCE")
            lines.append(f"   Total Invocations: {ap['total_invocations']}")
            lines.append(f"   Success Rate: {ap['success_rate']:.1%}")
            lines.append(f"   Avg Duration: {ap['avg_duration_ms']:.0f}ms")
            if "by_agent" in ap:
                lines.append("   By Agent:")
                for agent, success_rate in ap["by_agent"].items():
                    status = "✅" if success_rate >= 0.8 else "⚠️"
                    lines.append(f"     {status} {agent}: {success_rate:.1%}")
            lines.append("")

        # Recommendations
        lines.append("💡 RECOMMENDATIONS")
        recommendations = self._generate_recommendations(kpis)
        if recommendations:
            for rec in recommendations:
                lines.append(f"   - {rec}")
        else:
            lines.append("   ✅ All metrics within acceptable thresholds")
        lines.append("")

        lines.append("=" * 80)

        return "\n".join(lines)

    def _generate_recommendations(self, kpis: Dict[str, Any]) -> list:
        """Generate actionable recommendations based on KPIs"""
        recommendations = []

        # Check routing confidence
        if "routing_accuracy" in kpis:
            if kpis["routing_accuracy"]["avg_confidence"] < 0.7:
                recommendations.append(
                    "⚠️  Routing confidence below 0.7 - Consider improving intent keywords or embeddings"
                )

        # Check Phase 4 skip rate
        if "phase_completion" in kpis:
            if kpis["phase_completion"]["phase_4_skip_rate_t3"] > 0.0:
                recommendations.append(
                    "🚨 CRITICAL: Phase 4 being skipped for T3 operations - Enforce approval gate immediately"
                )

        # Check guard pass rate
        if "guard_effectiveness" in kpis:
            if kpis["guard_effectiveness"]["pass_rate"] < 0.9:
                recommendations.append(
                    "⚠️  Guard pass rate below 90% - Review guard configuration for false positives"
                )

        # Check agent success rate
        if "agent_performance" in kpis:
            if kpis["agent_performance"]["success_rate"] < 0.8:
                recommendations.append(
                    "⚠️  Agent success rate below 80% - Review agent workflows and error handling"
                )

            # Check individual agents
            if "by_agent" in kpis["agent_performance"]:
                for agent, success_rate in kpis["agent_performance"]["by_agent"].items():
                    if success_rate < 0.7:
                        recommendations.append(
                            f"⚠️  Agent '{agent}' success rate {success_rate:.1%} - Review agent implementation"
                        )

        return recommendations

    def generate_json_report(self, days: int = 7) -> str:
        """Generate JSON report for programmatic consumption"""
        kpis = self.collector.compute_kpis(days)
        kpis["generated_at"] = datetime.now().isoformat()
        kpis["period_days"] = days
        return json.dumps(kpis, indent=2)


# CLI
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Metrics Dashboard")
    parser.add_argument("--days", type=int, default=7, help="Days to analyze")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    collector = MetricsCollector()
    dashboard = MetricsDashboard(collector)

    if args.json:
        print(dashboard.generate_json_report(args.days))
    else:
        print(dashboard.generate_report(args.days))