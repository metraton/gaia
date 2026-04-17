#!/usr/bin/env python3
"""Tests for --help / -h flag exemption in mutative_verbs.

When a command carries --help, -h, or "help" as a subcommand, it only
prints usage text and cannot mutate state. The detector must classify
such commands as non-mutative regardless of which verb follows.

Root cause context: ghost pending approvals P-738355ab and P-0b06738b
were created because "gaia update --help 2>&1" and "gaia approvals clean --help 2>&1"
were wrongly classified as T3 MUTATIVE even though --help is a pure
inspection flag.
"""

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command


class TestHelpFlagExemption:
    """--help must downgrade any command to non-mutative."""

    def test_gaia_update_help_is_not_mutative(self):
        """Regression: ghost P-738355ab was created from this exact command."""
        result = detect_mutative_command(
            "/home/jorge/ws/me/gaia-ops-dev/bin/gaia update --help 2>&1"
        )
        assert result.is_mutative is False, (
            f"--help must downgrade 'update' to non-mutative. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )

    def test_gaia_approvals_clean_help_is_not_mutative(self):
        """Regression: ghost P-0b06738b was created from this exact command."""
        result = detect_mutative_command(
            "/home/jorge/ws/me/gaia-ops-dev/bin/gaia approvals clean --help 2>&1"
        )
        assert result.is_mutative is False, (
            f"--help must downgrade 'clean' to non-mutative. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )

    def test_plain_update_still_mutative(self):
        """Sanity: without --help, 'update' remains mutative."""
        result = detect_mutative_command("kubectl update deployment myapp")
        assert result.is_mutative is True

    def test_plain_delete_still_mutative(self):
        """Sanity: without --help, 'delete' remains mutative."""
        result = detect_mutative_command("kubectl delete pod mypod")
        assert result.is_mutative is True

    def test_short_h_flag_exempts_delete(self):
        """-h should also exempt (common short form of --help)."""
        result = detect_mutative_command("kubectl delete -h")
        assert result.is_mutative is False, (
            f"-h must downgrade 'delete' to non-mutative. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_long_help_flag_exempts_push(self):
        """--help on git push is pure help output, no push occurs."""
        result = detect_mutative_command("git push --help")
        assert result.is_mutative is False

    def test_help_subcommand_exempts(self):
        """'help' as a standalone subcommand is read-only."""
        result = detect_mutative_command("kubectl help delete")
        assert result.is_mutative is False

    def test_help_with_stderr_redirect(self):
        """The 2>&1 redirect must not confuse the parser."""
        result = detect_mutative_command("helm uninstall --help 2>&1")
        assert result.is_mutative is False, (
            f"Redirect after --help must not re-enable mutation. "
            f"Got: reason={result.reason}"
        )


class TestHelpPositionalBoundaries:
    """--help must only exempt when it is in the primary position.

    When a mutative command has specific arguments/objects followed by --help,
    the CLI may still execute the mutation before seeing --help. Policy:
    only exempt when non_flag_tokens length is small (<=2 subcommands).
    """

    def test_git_push_with_args_and_help_stays_mutative(self):
        """git push origin main --help: args BEFORE --help -> real push risk -> T3."""
        result = detect_mutative_command("git push origin main --help")
        assert result.is_mutative is True, (
            f"git push with positional args BEFORE --help must stay T3: "
            f"git real-world parses args first and may push before honoring --help. "
            f"Got: is_mutative={result.is_mutative}, reason={result.reason}"
        )

    def test_rm_with_help_stays_mutative(self):
        """rm -rf /tmp/stuff --help: rm is a command alias (not in whitelist) -> T3."""
        result = detect_mutative_command("rm -rf /tmp/stuff --help")
        assert result.is_mutative is True, (
            f"rm is a command alias, not in the --help whitelist. "
            f"Must stay T3 regardless of --help presence. "
            f"Got: is_mutative={result.is_mutative}, reason={result.reason}"
        )

    def test_unknown_cli_with_help_stays_mutative(self):
        """Unknown CLI family -> no whitelist match -> T3.

        Uses a verb that WOULD be mutative to exercise the real exemption
        path. Family is unknown, so the whitelist must not match and the
        detector must stay T3.
        """
        result = detect_mutative_command("mystrange-cli delete something --help")
        assert result.is_mutative is True, (
            f"Unknown CLI (family=unknown) is not in --help whitelist. "
            f"Must stay T3 to be safe. "
            f"Got: is_mutative={result.is_mutative}, reason={result.reason}"
        )

    def test_kubectl_delete_object_with_help_stays_mutative(self):
        """kubectl delete pod mypod --help: 3 non-flag tokens -> T3."""
        result = detect_mutative_command("kubectl delete pod mypod --help")
        assert result.is_mutative is True, (
            f"kubectl delete with resource+name BEFORE --help has 3 non-flag "
            f"tokens; policy is to exempt only when <=2. "
            f"Got: is_mutative={result.is_mutative}, reason={result.reason}"
        )
