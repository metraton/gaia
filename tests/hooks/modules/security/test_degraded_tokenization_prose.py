#!/usr/bin/env python3
"""Regression test: report prose must not be read as command syntax.

An agent that reports faithfully quotes the command it was blocked on. When
that quoted prose carries an apostrophe, `shlex.split` cannot resolve the
quoting and `tokenize_command` falls back. The old fallback -- a naive
whitespace split of the whole command -- exposed every word of the payload as
a standalone token, so a `--force` merely QUOTED registered as a real flag
(skipping the anchored read-only exception for `gaia contract`) and any prose
word in MUTATIVE_VERBS registered as a real verb. The conjunction turned an
honest report into a spurious T3.

The fix keeps the fallback best-effort but splits it where the ambiguity
starts: text before the first quote character is unambiguous syntax, the
remainder is one opaque datum. These tests pin BOTH directions -- the false
positive is gone, and nothing that was gated became ungated.
"""

from __future__ import annotations

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.command_semantics import (  # noqa: E402
    analyze_command,
    tokenize_command,
)
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402

# The reproduced conjunction: an apostrophe breaks shlex, and the same prose
# quotes a force flag and a mutative verb verbatim.
REPRO_COMMAND = (
    "gaia contract add evidence_report.verbatim_outputs "
    "'[hook] blocked: it's the push --force I did not run'"
)

# The same report written through the heredoc form agents use for JSON.
REPRO_HEREDOC = (
    'gaia contract fill --json "$(cat <<\'EOF\'\n'
    '{"verbatim_outputs": ["the hook\'s message named push --force"]}\n'
    'EOF\n)"'
)


class TestProseIsNotSyntax:
    """The false positive the fix removes."""

    def test_reproduced_case_is_allowed(self):
        result = detect_mutative_command(REPRO_COMMAND)
        assert result.is_mutative is False
        assert result.dangerous_flags == ()
        assert result.category == "READ_ONLY"

    def test_quoted_force_flag_is_not_a_flag(self):
        semantics = analyze_command(REPRO_COMMAND)
        assert "--force" not in semantics.flag_tokens

    def test_prose_words_are_not_tokens(self):
        tokens = tokenize_command(REPRO_COMMAND)
        assert "push" not in tokens
        assert "--force" not in tokens
        # The payload survives verbatim as ONE datum, so nothing is dropped and
        # the approval signature still binds the full command text.
        assert tokens[:4] == (
            "gaia",
            "contract",
            "add",
            "evidence_report.verbatim_outputs",
        )
        assert "push --force" in tokens[-1]

    def test_heredoc_report_is_allowed(self):
        result = detect_mutative_command(REPRO_HEREDOC)
        assert result.is_mutative is False
        assert result.dangerous_flags == ()

    def test_commit_message_quoting_a_force_flag_is_allowed(self):
        # Same class beyond `gaia contract`: a local-safe git subcommand whose
        # message quotes a force flag was escalated by the phantom flag alone.
        result = detect_mutative_command(
            "git commit -m 'it's a fix; the blocked command was push --force'"
        )
        assert result.is_mutative is False

    def test_distinct_payloads_stay_distinct(self):
        # One grant must never authorize a different payload: the opaque datum
        # is verbatim, so two reports do not collapse onto one signature.
        first = tokenize_command("gaia contract add x 'it's payload one")
        second = tokenize_command("gaia contract add x 'it's payload two")
        assert first != second


class TestGatesStayClosed:
    """No mutation that was gated before became ungated."""

    def test_real_force_flag_still_gates_gaia_contract(self):
        result = detect_mutative_command("gaia contract fill --json '{\"a\":1}' --force")
        assert result.is_mutative is True
        assert "--force" in result.dangerous_flags

    def test_real_force_flag_gates_even_when_tokenization_degrades(self):
        # The flag precedes the payload, so it stays in the unambiguous head:
        # a genuine force flag is caught even in a degraded parse.
        result = detect_mutative_command(
            "gaia contract fill --force '{\"note\": \"it's data\"}'"
        )
        assert result.is_mutative is True
        assert "--force" in result.dangerous_flags

    def test_destructive_verb_still_gated_in_excepted_group(self):
        result = detect_mutative_command("gaia contract delete --draft-id a7e4d20f1e2d3c4b5")
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_destructive_verb_gated_when_tokenization_degrades(self):
        result = detect_mutative_command("gaia contract delete --draft-id 'it's a7e4d20f1e2d3c4b5'")
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_mutative_head_survives_a_broken_payload(self):
        # Verb and flags precede quoted arguments, so classification anchors on
        # the head that shlex and bash read identically.
        result = detect_mutative_command(
            "kubectl delete namespace prod --now 'it's gone'"
        )
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_healthy_tokenization_is_untouched(self):
        assert tokenize_command("git push --force") == ("git", "push", "--force")
        assert detect_mutative_command("git push --force").is_mutative is True


class TestDegradedFallbackBoundaries:
    """Where nothing better is known, the conservative split is kept."""

    def test_no_quote_keeps_the_naive_split(self):
        # A trailing backslash also breaks shlex, but carries no payload to
        # protect -- the whole-command split stays.
        tokens = tokenize_command("kubectl delete ns prod \\")
        assert "delete" in tokens

    def test_quote_first_keeps_the_naive_split(self):
        # No unambiguous head can be established, so nothing is trusted and the
        # old conservative behaviour applies.
        tokens = tokenize_command("'kubectl delete namespace prod --now it's a mess'")
        assert "delete" in tokens

    def test_flag_glued_to_its_payload_stays_one_token(self):
        tokens = tokenize_command("gaia contract fill --json='{\"a\": \"it's\"}'")
        assert tokens[:3] == ("gaia", "contract", "fill")
        assert tokens[-1].startswith("--json=")
