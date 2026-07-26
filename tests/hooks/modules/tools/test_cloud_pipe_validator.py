#!/usr/bin/env python3
"""
Tests for cloud_pipe_validator.

Validates regex patterns correctly detect violations without false positives:
- Pipe `|` detected, but logical OR `||` is NOT a pipe
- Redirect `>` / `>>` detected, but `2>&1` is NOT a redirect
- Chaining `;` / `&&` detected correctly
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.cloud_pipe_validator import (
    validate_cloud_pipe,
    _find_violation,
    _strip_quoted_sections,
)


class TestPipeDetection:
    """Test pipe regex: should catch real pipes, not logical OR."""

    def test_real_pipe_detected(self):
        """Real pipe `|` in a cloud command should trigger violation."""
        result = validate_cloud_pipe("kubectl get pods | grep nginx")
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_logical_or_not_detected_as_pipe(self):
        """Logical OR `||` should NOT trigger pipe violation."""
        result = validate_cloud_pipe("kubectl get pods || echo 'failed'")
        # || should NOT match as a pipe, but it might match as chaining
        # The key is it does NOT say "pipe" in the reason
        if result is not None:
            reason = result["hookSpecificOutput"]["permissionDecisionReason"].lower()
            # If it's blocked, it should NOT be for "pipe" -- might be "chaining"
            assert "no pipes" not in reason

    def test_logical_or_with_complex_command(self):
        """Complex command with `||` should not be falsely flagged as pipe."""
        result = validate_cloud_pipe("kubectl delete namespace test 2>&1 || echo 'blocked'")
        # Should NOT be flagged as a pipe violation
        if result is not None:
            reason = result["hookSpecificOutput"]["permissionDecisionReason"].lower()
            assert "no pipes" not in reason


class TestRedirectDetection:
    """Test redirect regex: should catch real redirects, not fd duplication."""

    def test_real_redirect_detected(self):
        """Real redirect `>` should trigger violation."""
        result = validate_cloud_pipe("terraform apply > output.log")
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_append_redirect_detected(self):
        """Append redirect `>>` should trigger violation."""
        result = validate_cloud_pipe("gcloud compute instances list >> instances.txt")
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_fd_duplication_not_detected(self):
        """File descriptor duplication `2>&1` should NOT trigger redirect violation."""
        result = validate_cloud_pipe("terraform apply 2>&1")
        assert result is None, (
            f"2>&1 should not trigger a violation, got: "
            f"{result['hookSpecificOutput']['permissionDecisionReason'] if result else 'None'}"
        )

    def test_fd_duplication_in_complex_command(self):
        """Command with `2>&1` should not trigger redirect."""
        result = validate_cloud_pipe("kubectl get pods 2>&1")
        assert result is None

    def test_stderr_redirect_to_dev_null(self):
        """Redirect `2>/dev/null` has a `>` that could match -- verify handling.

        Note: `2>` has a digit before `>`. Our regex uses lookbehind for `>&`
        but the `2>` case is a real redirect (stderr to file), so it SHOULD match.
        """
        result = validate_cloud_pipe("kubectl get pods 2>/dev/null")
        # This IS a real redirect (stderr to /dev/null), so it should match
        assert result is not None


class TestChainingDetection:
    """Test chaining regex: `;` and `&&`."""

    def test_semicolon_detected(self):
        """Semicolon chaining should trigger violation."""
        result = validate_cloud_pipe("kubectl get pods; kubectl get svc")
        assert result is not None
        assert "chaining" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_and_chain_detected(self):
        """Double ampersand `&&` chaining should trigger violation."""
        result = validate_cloud_pipe("kubectl get pods && kubectl get svc")
        assert result is not None
        assert "chaining" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


class TestNonCloudCommands:
    """Test non-cloud command handling.

    Pipes and chaining are cloud-only checks.
    Redirects and background operators are universal checks (all commands).
    """

    @pytest.mark.parametrize("command", [
        "ls -la | grep test",
        "echo hello && echo world",
        "python script.py | head",
    ])
    def test_non_cloud_pipes_and_chains_pass(self, command):
        """Non-cloud pipes and chaining should not trigger a violation."""
        result = validate_cloud_pipe(command)
        assert result is None

    def test_non_cloud_redirect_blocked(self):
        """Redirects are blocked for ALL commands (use Write tool instead)."""
        result = validate_cloud_pipe("cat file.txt > output.txt")
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_non_cloud_background_blocked(self):
        """Background operator is blocked for ALL commands."""
        result = validate_cloud_pipe("sleep 60 &")
        assert result is not None
        assert "background" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_non_cloud_fd_duplication_passes(self):
        """File descriptor duplication (2>&1) should NOT trigger for non-cloud commands."""
        result = validate_cloud_pipe("python3 script.py 2>&1")
        assert result is None


class TestQuotedStrings:
    """Test that operators inside quotes are not detected."""

    def test_pipe_in_quotes_ignored(self):
        """Pipe character inside quotes should not trigger violation."""
        result = validate_cloud_pipe("kubectl get pods --field-selector='status.phase|Running'")
        assert result is None

    def test_redirect_in_quotes_ignored(self):
        """Redirect character inside quotes should not trigger violation."""
        result = validate_cloud_pipe("gcloud compute instances list --filter='name > abc'")
        assert result is None


class TestTerragruntRecognized:
    """terragrunt was absent from CLOUD_CLI_PATTERN -- a live gap, not
    hypothetical: `terragrunt apply -auto-approve ... | tail` reached
    episode_anomalies as a command that evaded the gate entirely."""

    def test_terragrunt_pipe_detected(self):
        """The confirmed live-incident shape: terragrunt output piped to a
        shell utility instead of terragrunt's own output flags."""
        result = validate_cloud_pipe("terragrunt apply -auto-approve | tail")
        assert result is not None
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_terragrunt_redirect_detected(self):
        result = validate_cloud_pipe("terragrunt apply -auto-approve > out.log")
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


class TestStageAnchoredDetection:
    """The second half of the confirmed gap: CLOUD_CLI_PATTERN anchored on
    the first token of the WHOLE command string, so a leading `cd x &&` or
    `;` prefix let a recognized cloud CLI evade the gate even though it was
    genuinely invoked. The check must apply per decomposed stage."""

    def test_cd_prefix_terragrunt_pipe_detected(self):
        """The exact structural hole: `cd x && <cli> ... | ...`. This is the
        shape someone could revert without noticing -- pin it explicitly."""
        result = validate_cloud_pipe(
            "cd /infra/env && terragrunt apply -auto-approve -- -input=false | tail"
        )
        assert result is not None
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_cd_prefix_known_cli_pipe_detected(self):
        """A CLI already in the pattern before this fix (kubectl) also
        evaded the gate behind a `cd x &&` prefix -- not terragrunt-specific."""
        result = validate_cloud_pipe("cd /repo && kubectl get pods | grep Error")
        assert result is not None
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_semicolon_prefix_cloud_cli_chaining_detected(self):
        """A leading `;`-separated command also anchors past the cloud CLI
        under the old whole-string match; chaining must still be caught."""
        result = validate_cloud_pipe("echo start; terraform apply -auto-approve")
        assert result is not None
        assert "chaining" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_cloud_cli_as_grep_argument_not_blocked(self):
        """The central risk of a per-stage fix: mistaking the CLI name
        appearing as an ARGUMENT for an actual invocation. `grep` is the
        command that runs here, not terragrunt/terraform -- must NOT block."""
        result = validate_cloud_pipe("grep terragrunt archivo.tf | head")
        assert result is None

    def test_cloud_cli_as_echo_argument_not_blocked(self):
        """Same principle with `echo`: the string \"terraform\" is data
        being echoed, not a command being run."""
        result = validate_cloud_pipe('echo "terraform" | wc -l')
        assert result is None

    def test_benign_local_pipe_still_passes(self):
        """A pipe between two ordinary local commands, with no cloud CLI
        stage anywhere, remains unblocked -- the fix must not add friction
        to routine Unix piping."""
        result = validate_cloud_pipe("cd /tmp && ls -la | grep report")
        assert result is None


class TestPipeOriginVsDestination:
    """A cloud CLI as pipe ORIGIN (its own output piped away) is a real
    policy violation with a native-flag substitute -- this validator still
    denies it categorically, unchanged. A cloud CLI as pipe DESTINATION
    (receiving piped-in content it did not produce) is NOT the same risk:
    there is no native output flag to substitute, because the CLI is not
    producing the piped output. This validator defers the destination case
    to the normal per-stage T3/mutative-verb classification instead of
    denying it outright -- see test_bash_validator.py for the end-to-end
    confirmation that the deferred case still requires consent (ask), never
    passes silently (allow).
    """

    def test_origin_pipe_still_denied_by_this_validator(self):
        """kubectl piping its OWN output -- origin -- unchanged behavior."""
        result = validate_cloud_pipe("kubectl get pods | grep Error")
        assert result is not None
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_destination_pipe_not_flagged_by_this_validator(self):
        """kubectl RECEIVING piped-in content -- destination -- this
        validator must NOT flag it; the command falls through so the
        per-component T3 classifier (which independently catches `apply`
        as a mutative verb) can run instead of a categorical deny."""
        result = validate_cloud_pipe("kustomize build overlay | kubectl apply -f -")
        assert result is None

    def test_destination_pipe_with_echo_manifest_not_flagged(self):
        result = validate_cloud_pipe('echo "<manifest>" | kubectl apply -f -')
        assert result is None

    def test_origin_and_destination_both_cloud_still_denied(self):
        """When the SAME command is both -- a cloud CLI pipes its own
        output into another cloud CLI -- the origin half is still a real
        violation and must deny (gcloud's own output should use --format,
        regardless of what receives it)."""
        result = validate_cloud_pipe("gcloud compute instances list | kubectl apply -f -")
        assert result is not None
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_destination_redirect_on_same_stage_still_denied(self):
        """The direction split applies ONLY to the pipe rule. A redirect on
        the destination CLI's OWN output is still that CLI's origin-type
        violation (there is no "destination" reading for `>`) and must
        still deny."""
        result = validate_cloud_pipe(
            "cat manifest.yaml | kubectl apply -f - > apply.log"
        )
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


class TestCombinedFalsePositives:
    """Test the specific false positive scenario from the bug report."""

    def test_kubectl_delete_with_or_and_fd_dup(self):
        """The exact bug scenario: `kubectl delete namespace test 2>&1 || echo 'blocked'`.

        This should NOT trigger cloud_pipe_validator at all because:
        - `||` is logical OR, not a pipe
        - `2>&1` is fd duplication, not a redirect
        """
        result = validate_cloud_pipe("kubectl delete namespace test 2>&1 || echo 'blocked'")
        # Should NOT be flagged as a pipe or redirect
        assert result is None, (
            f"Should not trigger cloud_pipe_validator, got: "
            f"{result['hookSpecificOutput']['permissionDecisionReason'] if result else 'None'}"
        )

    def test_real_pipe_still_caught(self):
        """Real pipe in kubectl should still be caught."""
        result = validate_cloud_pipe("kubectl get pods | grep nginx")
        assert result is not None

    def test_real_redirect_still_caught(self):
        """Real redirect in terraform should still be caught."""
        result = validate_cloud_pipe("terraform apply > output.log")
        assert result is not None
