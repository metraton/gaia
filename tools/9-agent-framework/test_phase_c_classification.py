#!/usr/bin/env python3
"""
Tests for Phase C: Finding Classification
"""

import pytest
from finding_classifier import (
    FindingClassifier,
    Finding,
    FindingTier,
    DataOrigin,
    ClassificationResult
)


class TestFindingClassifier:
    """Test finding classification functionality"""

    @pytest.fixture
    def classifier(self):
        """Create FindingClassifier instance"""
        return FindingClassifier()

    @pytest.fixture
    def sample_findings(self):
        """Sample findings for testing"""
        return [
            Finding(
                tier=FindingTier.CRITICAL,
                title="Security vulnerability detected",
                description="SQL injection risk in API endpoint",
                origin=DataOrigin.LOCAL_ONLY,
                suggested_action="Sanitize user input"
            ),
            Finding(
                tier=FindingTier.HIGH,
                title="Performance issue",
                description="Slow query detected in database",
                origin=DataOrigin.LOCAL_ONLY,
                suggested_action="Add database index"
            ),
            Finding(
                tier=FindingTier.MEDIUM,
                title="Configuration mismatch",
                description="Environment variable not set",
                origin=DataOrigin.LOCAL_ONLY,
                suggested_action="Update .env file"
            )
        ]

    def test_classifier_initialization(self, classifier):
        """Test that classifier initializes correctly"""
        assert classifier is not None
        assert hasattr(classifier, 'classify')
        assert hasattr(classifier, 'findings')

    def test_add_finding(self, classifier, sample_findings):
        """Test adding findings to classifier"""
        for finding in sample_findings:
            classifier.add_finding(finding)

        assert len(classifier.findings) == len(sample_findings)

    def test_classify_returns_result(self, classifier, sample_findings):
        """Test that classify returns ClassificationResult"""
        for finding in sample_findings:
            classifier.add_finding(finding)

        result = classifier.classify()
        assert isinstance(result, ClassificationResult)

    def test_classification_counts_by_tier(self, classifier, sample_findings):
        """Test that classification correctly counts findings by tier"""
        for finding in sample_findings:
            classifier.add_finding(finding)

        result = classifier.classify()

        # Should have counts for each tier
        assert hasattr(result, 'critical_count')
        assert hasattr(result, 'high_count')
        assert hasattr(result, 'medium_count')

    def test_escalation_logic_for_critical(self, classifier):
        """Test that CRITICAL findings trigger escalation"""
        critical_finding = Finding(
            tier=FindingTier.CRITICAL,
            title="Critical security issue",
            description="Authentication bypass detected",
            origin=DataOrigin.LOCAL_ONLY,
            suggested_action="Immediate fix required"
        )

        classifier.add_finding(critical_finding)
        result = classifier.classify()

        # Critical findings should trigger escalation to remote validation
        assert result.should_escalate_to_live is True

    def test_no_escalation_for_low_tier(self, classifier):
        """Test that LOW tier findings don't trigger escalation"""
        low_finding = Finding(
            tier=FindingTier.LOW,
            title="Minor issue",
            description="Unused variable detected",
            origin=DataOrigin.LOCAL_ONLY,
            suggested_action="Clean up code"
        )

        classifier.add_finding(low_finding)
        result = classifier.classify()

        # Low findings should not trigger escalation
        assert result.should_escalate_to_live is False

    def test_generate_report(self, classifier, sample_findings):
        """Test that classifier generates human-readable report"""
        for finding in sample_findings:
            classifier.add_finding(finding)

        result = classifier.classify()
        report = classifier.generate_report(result)

        assert isinstance(report, str)
        assert len(report) > 0
        # Report should mention finding tiers
        assert "CRITICAL" in report or "critical" in report

    def test_empty_classifier(self, classifier):
        """Test classification with no findings"""
        result = classifier.classify()

        assert isinstance(result, ClassificationResult)
        # Should handle empty case gracefully


class TestFindingTierLogic:
    """Test tier-specific logic"""

    @pytest.fixture
    def classifier(self):
        return FindingClassifier()

    def test_tier_priority_order(self):
        """Test that tier priorities are correctly ordered"""
        # Higher tier value = higher priority
        assert FindingTier.CRITICAL.value > FindingTier.HIGH.value
        assert FindingTier.HIGH.value > FindingTier.MEDIUM.value
        assert FindingTier.MEDIUM.value > FindingTier.LOW.value

    def test_mixed_tier_classification(self, classifier):
        """Test classification with mixed tier findings"""
        findings = [
            Finding(FindingTier.LOW, "Low", "desc", DataOrigin.LOCAL_ONLY, "action"),
            Finding(FindingTier.CRITICAL, "Critical", "desc", DataOrigin.LOCAL_ONLY, "action"),
            Finding(FindingTier.MEDIUM, "Medium", "desc", DataOrigin.LOCAL_ONLY, "action"),
        ]

        for finding in findings:
            classifier.add_finding(finding)

        result = classifier.classify()

        # Should correctly count each tier
        assert result.critical_count == 1
        assert result.medium_count == 1
        assert result.low_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
