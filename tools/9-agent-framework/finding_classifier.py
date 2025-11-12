#!/usr/bin/env python3
"""
Phase C: Finding Classification

Automatically classifies findings into 4 tiers and specifies data origin.
Does NOT abroadcast - reports concisely.

Reference: Agent-Complete-Workflow.md (Capa 3: Hallazgos)
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class FindingTier(Enum):
    """Classification of findings into non-abroadcasting tiers"""
    CRITICAL = 1  # Security risk, makes things not work
    DEVIATION = 2  # Doesn't follow standards but works
    IMPROVEMENT = 3  # Could be better
    PATTERN = 4  # Pattern detected, will copy


class DataOrigin(Enum):
    """Origin of data point"""
    LOCAL_ONLY = "LOCAL_ONLY"  # Only in repository
    LIVE_ONLY = "LIVE_ONLY"  # Only in infrastructure
    DUAL_VERIFIED = "DUAL_VERIFIED"  # In both, verified
    CONFLICTING = "CONFLICTING"  # In both, contradictory


@dataclass
class Finding:
    """A single finding/issue"""
    tier: FindingTier
    title: str
    description: str
    origin: DataOrigin
    details: Dict[str, Any] = None
    suggested_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.name,
            "title": self.title,
            "description": self.description,
            "origin": self.origin.value,
            "details": self.details or {},
            "suggested_action": self.suggested_action
        }


@dataclass
class ClassificationResult:
    """Result of finding classification"""
    critical_findings: List[Finding]
    deviation_findings: List[Finding]
    improvement_suggestions: List[Finding]
    patterns_detected: List[Finding]

    @property
    def should_escalate_to_live(self) -> bool:
        """Return True if any critical/deviation findings suggest live validation needed"""
        return len(self.critical_findings) > 0 or len(self.deviation_findings) > 0

    @property
    def total_findings(self) -> int:
        return (
            len(self.critical_findings) +
            len(self.deviation_findings) +
            len(self.improvement_suggestions) +
            len(self.patterns_detected)
        )


class FindingClassifier:
    """
    Classifies findings into non-overwhelming, specific tiers.

    Philosophy:
    - Tier 1 (CRITICAL): Report immediately
    - Tier 2 (DEVIATION): Mention passively in summary
    - Tier 3 (IMPROVEMENT): Omit unless very obvious
    - Tier 4 (PATTERN): Apply automatically, just mention in logs
    """

    def __init__(self):
        self.findings: List[Finding] = []

    def add_finding(self, finding: Finding):
        """Register a finding"""
        self.findings.append(finding)
        logger.debug(f"Finding added: {finding.tier.name} - {finding.title}")

    def classify(self) -> ClassificationResult:
        """Organize findings by tier"""
        result = ClassificationResult(
            critical_findings=[f for f in self.findings if f.tier == FindingTier.CRITICAL],
            deviation_findings=[f for f in self.findings if f.tier == FindingTier.DEVIATION],
            improvement_suggestions=[f for f in self.findings if f.tier == FindingTier.IMPROVEMENT],
            patterns_detected=[f for f in self.findings if f.tier == FindingTier.PATTERN]
        )
        return result

    def generate_report(self, result: ClassificationResult, max_chars: int = 500) -> str:
        """
        Generate concise report (max 500 chars per spec).

        Only shows:
        - All CRITICAL findings
        - ONE deviation finding (if any)
        - NO improvement suggestions (omit)
        - Pattern reference (if patterns detected)
        """
        lines = []
        char_count = 0

        # CRITICAL: Always show
        if result.critical_findings:
            lines.append("⚠️ CRITICAL ISSUES:")
            for f in result.critical_findings:
                line = f"  • {f.title}: {f.description}"
                if char_count + len(line) > max_chars:
                    lines.append("  (truncated...)")
                    break
                lines.append(line)
                char_count += len(line)

        # DEVIATION: Show first one, mention count
        if result.deviation_findings:
            if lines:
                lines.append("")
            first_dev = result.deviation_findings[0]
            line = f"📌 {first_dev.title}: {first_dev.description}"
            lines.append(line)
            if len(result.deviation_findings) > 1:
                lines.append(
                    f"  (+ {len(result.deviation_findings) - 1} more pattern deviations)"
                )

        # PATTERNS: Mention silently
        if result.patterns_detected:
            if lines:
                lines.append("")
            lines.append(f"✓ {len(result.patterns_detected)} pattern(s) detected")

        # Data origin summary
        if lines:
            lines.append("")
            origins = self._count_origins(result)
            lines.append(f"Data origin: {origins}")

        return "\n".join(lines)

    def _count_origins(self, result: ClassificationResult) -> str:
        """Summarize data origins"""
        all_findings = (
            result.critical_findings +
            result.deviation_findings +
            result.improvement_suggestions +
            result.patterns_detected
        )

        origins = {}
        for f in all_findings:
            o = f.origin.value
            origins[o] = origins.get(o, 0) + 1

        parts = []
        for origin, count in origins.items():
            parts.append(f"{origin}({count})")
        return " | ".join(parts) if parts else "NO DATA"


# Common finding factories (convenience methods)
class FindingFactory:
    """Create common findings"""

    @staticmethod
    def secrets_in_wrong_location(local_path: str, expected_path: str) -> Finding:
        return Finding(
            tier=FindingTier.CRITICAL,
            title="Secrets in insecure location",
            description=f"Secrets found in {local_path}, should be in {expected_path}",
            origin=DataOrigin.LOCAL_ONLY,
            suggested_action=f"Move secrets to {expected_path}"
        )

    @staticmethod
    def release_name_deviation(actual: str, expected: str) -> Finding:
        return Finding(
            tier=FindingTier.DEVIATION,
            title="Release name doesn't follow convention",
            description=f"Using '{actual}', standard is '{expected}'",
            origin=DataOrigin.LOCAL_ONLY,
            suggested_action="Update to follow naming convention"
        )

    @staticmethod
    def dependency_outdated(package: str, current: str, latest: str) -> Finding:
        return Finding(
            tier=FindingTier.IMPROVEMENT,
            title=f"Package {package} is outdated",
            description=f"Current: {current}, Latest: {latest}",
            origin=DataOrigin.LOCAL_ONLY,
            suggested_action=f"Consider updating to {latest}"
        )

    @staticmethod
    def pattern_detected(pattern_name: str, description: str) -> Finding:
        return Finding(
            tier=FindingTier.PATTERN,
            title=f"Pattern: {pattern_name}",
            description=description,
            origin=DataOrigin.LOCAL_ONLY,
            suggested_action="Will apply same pattern"
        )

    @staticmethod
    def drift_detected(resource: str, local_state: str, live_state: str) -> Finding:
        return Finding(
            tier=FindingTier.DEVIATION,
            title=f"Drift detected in {resource}",
            description=f"Local: {local_state} | Live: {live_state}",
            origin=DataOrigin.CONFLICTING,
            suggested_action="Review and reconcile"
        )


# CLI Usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    classifier = FindingClassifier()

    # Example findings
    classifier.add_finding(FindingFactory.secrets_in_wrong_location("/app/.env", "/secrets/"))
    classifier.add_finding(FindingFactory.release_name_deviation("api-v1", "api-service"))
    classifier.add_finding(FindingFactory.dependency_outdated("numpy", "1.19.0", "1.25.0"))
    classifier.add_finding(FindingFactory.pattern_detected("Docker", "Multi-stage Dockerfile detected"))

    result = classifier.classify()
    print(classifier.generate_report(result))
    print(f"\nTotal findings: {result.total_findings}")
    print(f"Should escalate to live: {result.should_escalate_to_live}")