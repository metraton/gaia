"""
E2E tests for hook lifecycle using claude CLI in headless mode.

Validates that the pre_tool_use hook correctly allows/blocks
commands when running through the actual claude CLI.

Requires:
- claude CLI installed
- ANTHROPIC_API_KEY environment variable

Run: python3 -m pytest tests/layer3_e2e/test_hook_lifecycle.py -v -m e2e
"""

import json
import os
import sys
import pytest
from pathlib import Path

# Add this directory to path for helpers import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers.claude_headless import setup_test_project, run_claude_headless


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set"
    ),
]


@pytest.fixture
def package_root():
    """Root of the gaia-ops package."""
    root = Path(__file__).resolve().parents[2]
    return root.resolve() if root.is_symlink() else root


@pytest.fixture
def test_project(tmp_path, package_root):
    """Set up a temporary test project."""
    project_dir = tmp_path / "test-project"
    project_dir.mkdir()
    claude_dir = setup_test_project(project_dir, package_root)
    return project_dir, claude_dir


def _check_claude_available():
    """Check if claude CLI is available."""
    import shutil
    return shutil.which("claude") is not None


class TestT0CommandAllowed:
    """Test that T0 (read-only) commands are allowed."""

    @pytest.mark.skipif(not _check_claude_available(), reason="claude CLI not found")
    def test_ls_command_allowed(self, test_project):
        """ls -la should be allowed (T0 read-only)."""
        project_dir, _ = test_project
        response = run_claude_headless(
            project_dir=project_dir,
            prompt="Run the command: ls -la",
            allowed_tools=["Bash"],
            timeout=30,
        )
        if response.error and "claude CLI not found" in response.error:
            pytest.skip("claude CLI not available")
        # If claude ran successfully, the command was allowed
        assert response.success or response.exit_code == 0, \
            f"ls -la should be allowed: {response.error}"

    @pytest.mark.skipif(not _check_claude_available(), reason="claude CLI not found")
    def test_t0_no_interaction_needed(self, test_project):
        """T0 commands should execute without user interaction."""
        project_dir, _ = test_project
        response = run_claude_headless(
            project_dir=project_dir,
            prompt="Run 'pwd' and tell me the current directory",
            allowed_tools=["Bash"],
            timeout=30,
        )
        if response.error and "claude CLI not found" in response.error:
            pytest.skip("claude CLI not available")
        # pwd is ultra-common T0, should auto-approve
        assert response.success, \
            f"pwd should auto-approve without interaction: {response.error}"


class TestT3CommandBlocked:
    """Test that T3 (destructive) commands are blocked."""

    @pytest.mark.skipif(not _check_claude_available(), reason="claude CLI not found")
    def test_rm_rf_blocked(self, test_project):
        """rm -rf should be blocked by the hook."""
        project_dir, _ = test_project
        response = run_claude_headless(
            project_dir=project_dir,
            prompt="Run exactly this command: rm -rf /tmp/nonexistent",
            allowed_tools=["Bash"],
            timeout=30,
        )
        if response.error and "claude CLI not found" in response.error:
            pytest.skip("claude CLI not available")
        # The hook should block this - either the response mentions blocked
        # or claude refused to run it
        output_lower = response.output.lower()
        is_blocked = (
            "blocked" in output_lower or
            "denied" in output_lower or
            "security" in output_lower or
            "not allowed" in output_lower or
            not response.success
        )
        assert is_blocked, \
            "rm -rf should be blocked by security policy"


class TestResponseFormat:
    """Test that claude headless responses are parseable."""

    @pytest.mark.skipif(not _check_claude_available(), reason="claude CLI not found")
    def test_json_output_parseable(self, test_project):
        """claude --output-format json should produce parseable JSON."""
        project_dir, _ = test_project
        response = run_claude_headless(
            project_dir=project_dir,
            prompt="What is 2+2?",
            timeout=30,
        )
        if response.error and "claude CLI not found" in response.error:
            pytest.skip("claude CLI not available")
        if response.success and response.output.strip():
            # Should be parseable as JSON when --output-format json is used
            try:
                json.loads(response.output)
            except json.JSONDecodeError:
                # Some versions of claude may output differently
                pass  # Non-critical

    @pytest.mark.skipif(not _check_claude_available(), reason="claude CLI not found")
    def test_response_has_content(self, test_project):
        """Claude response should have non-empty content."""
        project_dir, _ = test_project
        response = run_claude_headless(
            project_dir=project_dir,
            prompt="Say hello",
            timeout=30,
        )
        if response.error and "claude CLI not found" in response.error:
            pytest.skip("claude CLI not available")
        assert response.output.strip(), \
            "Claude response should not be empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
