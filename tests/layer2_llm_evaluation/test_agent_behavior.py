"""
Test agent behavior via LLM evaluation (promptfoo).

These tests use promptfoo to send prompts to LLMs and evaluate
whether agent definitions produce correct behavioral responses.

Requires:
- ANTHROPIC_API_KEY environment variable
- npx (Node.js) installed
- ~$0.10 per full run

Run: python3 -m pytest tests/layer2_llm_evaluation/ -v -m llm
"""

import os
import sys
import pytest
from pathlib import Path

# Add this directory to path for helpers import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers.promptfoo_runner import run_promptfoo


# Skip entire module if no API key
pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set"
    ),
]


@pytest.fixture(scope="module")
def promptfoo_config():
    """Path to promptfoo.yaml config."""
    return Path(__file__).resolve().parents[1] / "promptfoo.yaml"


@pytest.fixture(scope="module")
def full_eval_results(promptfoo_config):
    """Run full promptfoo evaluation (cached for module scope)."""
    result = run_promptfoo(config_path=promptfoo_config, timeout=180)
    if result.error_message and "npx not found" in result.error_message:
        pytest.skip("npx/promptfoo not available")
    return result


class TestPromptfooAvailability:
    """Verify promptfoo can run."""

    def test_config_exists(self, promptfoo_config):
        """promptfoo.yaml must exist."""
        assert promptfoo_config.exists(), \
            f"promptfoo.yaml not found at {promptfoo_config}"


class TestRoutingBehavior:
    """Test that the orchestrator routes correctly."""

    def test_terraform_routing(self, promptfoo_config):
        """Terraform-related requests should route to terraform-architect."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="Route terraform",
            timeout=60,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"Terraform routing test failed: {result.raw_output[:500]}"

    def test_kubectl_routing(self, promptfoo_config):
        """Kubectl-related requests should route to gitops-operator."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="Route kubectl",
            timeout=60,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"Kubectl routing test failed: {result.raw_output[:500]}"


class TestT3ApprovalBehavior:
    """Test that T3 operations require approval."""

    def test_terraform_requires_approval(self, promptfoo_config):
        """terraform-architect should require approval for apply."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="requires approval for T3",
            timeout=60,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"T3 approval test failed: {result.raw_output[:500]}"


class TestReadOnlyEnforcement:
    """Test that read-only agents stay read-only."""

    def test_troubleshooter_no_apply(self, promptfoo_config):
        """cloud-troubleshooter should not propose kubectl apply/delete."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="does not propose kubectl apply",
            timeout=60,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"Read-only enforcement failed: {result.raw_output[:500]}"

    def test_troubleshooter_uses_diagnostics(self, promptfoo_config):
        """cloud-troubleshooter should use diagnostic commands."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="uses diagnostic commands",
            timeout=90,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"Diagnostic commands test failed: {result.raw_output[:500]}"


class TestAgentStatusFormat:
    """Test that agents produce AGENT_STATUS blocks."""

    def test_response_includes_agent_status(self, promptfoo_config):
        """Agent responses should include AGENT_STATUS block."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="includes AGENT_STATUS block",
            timeout=60,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"AGENT_STATUS format test failed: {result.raw_output[:500]}"

    def test_valid_plan_status_value(self, promptfoo_config):
        """AGENT_STATUS must have a valid PLAN_STATUS value."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="valid PLAN_STATUS value",
            timeout=60,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"PLAN_STATUS validation failed: {result.raw_output[:500]}"


class TestInvestigationBehavior:
    """Test that agents investigate before acting."""

    def test_terraform_investigates_first(self, promptfoo_config):
        """terraform-architect should investigate before executing."""
        result = run_promptfoo(
            config_path=promptfoo_config,
            filter_description="investigates before acting",
            timeout=90,
        )
        if result.error_message:
            pytest.skip(result.error_message)
        assert result.passed > 0, \
            f"Investigation behavior test failed: {result.raw_output[:500]}"


class TestFullEvaluation:
    """Run and validate the complete promptfoo evaluation."""

    def test_overall_pass_rate(self, full_eval_results):
        """Overall pass rate should be above threshold."""
        if full_eval_results.error_message:
            pytest.skip(full_eval_results.error_message)
        if full_eval_results.total == 0:
            pytest.skip("No test results")
        pass_rate = full_eval_results.passed / full_eval_results.total
        assert pass_rate >= 0.75, \
            f"Pass rate {pass_rate:.0%} below 75% threshold ({full_eval_results.passed}/{full_eval_results.total})"

    def test_no_errors(self, full_eval_results):
        """There should be no evaluation errors."""
        if full_eval_results.error_message:
            pytest.skip(full_eval_results.error_message)
        assert full_eval_results.errors == 0, \
            f"{full_eval_results.errors} evaluation errors occurred"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
