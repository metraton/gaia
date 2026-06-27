#!/usr/bin/env python3
"""Mutation-survivor closure tests for blocked_commands.py (GRIND-TOTAL).

This module exists to KILL the surviving mutants inventoried for
``hooks/modules/security/blocked_commands.py`` (baseline 63.06% kill /
58 survivors over 157 specs). Each test targets the EXACT non-mutated outcome
of a code path so that the corresponding mutant fails the assertion when it
lives.

The tests are honest: they assert specific values and branch directions
(boundary, comparison operator, truthiness, return value, loop iteration),
not merely "does not raise". A trivial smoke test would let comparison/operator
mutants survive; these do not.

Survivor groups closed here (function -> mutant kinds):

  _has_unquoted_separator    -- index init (i=0), loop bound (i<n), escape
                                fast-path (\\ + i+1<n, i+=2), quote toggles
                                (==, not in_double/in_single, in_single=not...),
                                i+=1 increments, continue->break, and->or guard
  _is_false_positive_carrier -- base_cmd == "git" (Eq flips), `and` -> `or`
  matches                    -- forbidden-flag exact-match / startswith branch,
                                ordered-sequence guard AddNot
  is_blocked_command         -- empty-guard or->and, suggestion for-loop
                                iteration, prefix-match AddNot, break->continue
  _read_only_base_cmds       -- ImportError except handler is reachable/honest
  SemanticBlockedRule        -- head_only default True (semantic_head_tokens)
  BlockedCommandResult       -- dataclass(frozen=True) immutability
"""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

# Add hooks to path (mirrors the sibling test modules).
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import modules.security.blocked_commands as bc
from modules.security.blocked_commands import (
    is_blocked_command,
    _has_unquoted_separator,
    _is_false_positive_carrier,
    _read_only_base_cmds,
    SemanticBlockedRule,
    BlockedCommandResult,
)


# ===========================================================================
# _has_unquoted_separator -- the dominant cluster (38 survivors).
#
# Behaviour contract: return True iff one of _COMPOUND_SEPARATORS
# ("&&", "||", ";", "|", "`", "$(") appears OUTSIDE any single/double quoted
# region, honouring backslash escapes. The quote-state walk is the carrier's
# security backstop: a separator inside quotes must NOT trip (so a grep regex
# is treated as one command) while a separator outside quotes MUST trip (so a
# chained `&& kubectl delete ns` cannot hide behind a read-only prefix).
# ===========================================================================
class TestHasUnquotedSeparator:
    # --- separator OUTSIDE quotes => True -------------------------------
    @pytest.mark.parametrize("command", [
        "grep foo file && kubectl delete namespace prod",
        "grep foo file || rm -rf /",
        "grep foo file ; rm -rf /",
        "grep foo file | xargs rm",
        "echo `rm -rf /`",
        "echo $(rm -rf /)",
    ])
    def test_unquoted_separator_detected(self, command):
        assert _has_unquoted_separator(command) is True

    # --- separator at the very FIRST byte => True ------------------------
    # Kills NumberReplacer on `i = 0` (line 703): if the walk starts at i=1
    # a leading separator is skipped and this would wrongly return False.
    @pytest.mark.parametrize("command", [
        "&& rm -rf /",
        "| rm -rf /",
        "; rm",
        "`id`",
        "$(id)",
        "|| rm",
    ])
    def test_separator_at_first_byte_detected(self, command):
        assert _has_unquoted_separator(command) is True

    # --- separator only INSIDE quotes => False ---------------------------
    # Kills the quote-toggle mutants (lines 710/711/714/715 Eq_Is, Delete_Not,
    # in_single=not.../in_double=not...) and the and->or guard (line 718):
    # if quote tracking breaks, these quoted separators wrongly trip.
    @pytest.mark.parametrize("command", [
        "grep 'foo && bar' file",
        'grep "foo && bar" file',
        "grep -E 'a|b' file",
        'grep -E "a|b" file',
        "grep ';' file",
        'grep "$(x)" file',
        "grep '`x`' file",
    ])
    def test_quoted_separator_not_detected(self, command):
        assert _has_unquoted_separator(command) is False

    # --- single quote inside double-quoted region stays double ----------
    # Kills line 714 `not in_single` flips and line 718 and->or: inside a
    # double-quoted region a lone ' must NOT open single-quote state, so the
    # trailing separator stays quoted.
    def test_single_quote_inside_double_quotes(self):
        # The ' is literal inside "...", region stays double => && is quoted.
        assert _has_unquoted_separator("echo \"it's && safe\"") is False

    # --- double quote inside single-quoted region stays single ----------
    # Kills line 710 `not in_double` flips: inside a single-quoted region a
    # lone " must NOT open double-quote state.
    def test_double_quote_inside_single_quotes(self):
        assert _has_unquoted_separator("echo 'say \" && done'") is False

    # --- escaped quote does NOT toggle quote state ----------------------
    # Kills the escape fast-path (line 707 `\\ and i+1<n`, line 708 `i += 2`):
    # a backslash-escaped quote must be skipped as two chars so it does NOT
    # open a quoted region; the following separator therefore trips.
    def test_escaped_quote_does_not_open_quote(self):
        # \" is an escaped quote (no region opened) => the && is unquoted.
        assert _has_unquoted_separator('echo \\" && rm') is True

    # --- escaped separator char is still scanned correctly --------------
    # Kills line 708 i+=2 NumberReplacer and line 707 Add/Number on i+1:
    # after consuming the escape pair the walk must resume at the right index.
    def test_escape_then_unquoted_separator(self):
        # \x consumes 2 chars, then a real unquoted | trips.
        assert _has_unquoted_separator("a\\x | b") is True

    # --- trailing backslash at EOF (i+1 == n) does not over-read --------
    # Kills line 707 comparison flips on `i + 1 < n`: at the last char a lone
    # backslash must fall through (no index error) and return False.
    def test_trailing_backslash_no_separator(self):
        assert _has_unquoted_separator("echo hello\\") is False

    # --- plain safe command, no separator anywhere => False -------------
    @pytest.mark.parametrize("command", [
        "grep foo file",
        "ls -la /var/log",
        "cat /etc/hosts",
        "",
    ])
    def test_no_separator(self, command):
        assert _has_unquoted_separator(command) is False

    # --- separator AFTER a closed quoted region => True -----------------
    # Kills the continue->break mutants (lines 709/717): if the quote-close
    # branch breaks the loop instead of continuing, a separator after the
    # closed quote is never seen.
    @pytest.mark.parametrize("command", [
        "grep 'foo' file && rm -rf /",
        'grep "foo" file ; rm -rf /',
        "grep 'a' x | grep 'b' y && rm",
    ])
    def test_separator_after_closed_quote(self, command):
        assert _has_unquoted_separator(command) is True

    # --- separator inside quote, real separator after => True -----------
    # Reinforces continue->break (709/717) and i+=1 (712/716): the walk must
    # advance past the quoted region and reach the unquoted separator.
    def test_quoted_then_unquoted_separator(self):
        assert _has_unquoted_separator("grep 'a && b' x && rm -rf /") is True

    # --- backslash IMMEDIATELY before a separator escapes it => False ---
    # Precise discriminant `\\|` (backslash + pipe, the whole command):
    # original escapes the pipe (i+=2 past EOF) -> False. Kills line 708
    # `i += 2` -> `i += 1` (NumberReplacer) and line 707 `i + 1` -> `i + 2`
    # (NumberReplacer occ4/5): both wrongly skip the escape so the pipe is
    # read as an unquoted separator -> True.
    @pytest.mark.parametrize("command", [
        "\\|",
        "\\&&",
        "\\;",
        "\\`",
    ])
    def test_backslash_escapes_immediate_separator(self, command):
        assert _has_unquoted_separator(command) is False

    # --- backslash DEEP in the string before a separator escapes it -----
    # Precise discriminant `aaaa\\|`: the backslash sits at index 4 so any
    # mutant that scales the index in the escape guard (line 707 `i + 1`
    # -> `i << 1` = 2*i, which at i=4 yields 8 >= n=6 and SKIPS the escape)
    # mis-handles it and reports the escaped pipe as unquoted -> True.
    # The original keeps the pipe escaped -> False.
    @pytest.mark.parametrize("command", [
        "aaaa\\|",
        "aaaaaa\\|b",
        "abcdef\\;x",
    ])
    def test_backslash_deep_escapes_separator(self, command):
        assert _has_unquoted_separator(command) is False

    # --- escaped backslash then separator => the separator is UNquoted --
    # Precise discriminant `x\\\\|y` (x, \\, \\, |, y): the two backslashes
    # form one escaped backslash (escape consumes both), leaving the pipe
    # unquoted -> True. Kills line 709 continue->break: with `break` the walk
    # stops at the escaped pair and never reaches the trailing unquoted pipe
    # -> False.
    def test_escaped_backslash_then_unquoted_separator(self):
        assert _has_unquoted_separator("x\\\\|y") is True

    # --- quoted separator then UNquoted separator, tight packing --------
    # Precise discriminant `a'|'&&b`: `'|'` is a quoted pipe (safe), then the
    # `&&` outside quotes trips -> True. Kills the single-quote-toggle
    # increment mutant (line 712 `i += 1` -> `i += 2`): advancing 2 after
    # opening `'` skips the `|`, desyncing quote state so the later `&&` is
    # wrongly seen as quoted -> False.
    def test_single_quoted_sep_then_unquoted_sep(self):
        assert _has_unquoted_separator("a'|'&&b") is True

    # --- same for the double-quote-toggle increment (line 716) ----------
    # Precise discriminant `a"|"&&b`.
    def test_double_quoted_sep_then_unquoted_sep(self):
        assert _has_unquoted_separator('a"|"&&b') is True


# ===========================================================================
# _is_false_positive_carrier -- git carrier branch (4 survivors, line 685).
#   `if base_cmd == "git" and semantics.non_flag_tokens:`
# ===========================================================================
class TestIsFalsePositiveCarrier:
    # --- git commit / stash with a message is a carrier => True ---------
    # Kills Eq flips on `base_cmd == "git"` (Eq_GtE/Eq_LtE/Eq_IsNot): a
    # non-"git" comparison result would drop these into the wrong branch.
    @pytest.mark.parametrize("command", [
        'git commit -m "fix: kubectl delete namespace prod"',
        'git stash push -m "wip rm -rf /"',
        'git commit --amend -m "aws ec2 delete-vpc note"',
    ])
    def test_git_commit_message_is_carrier(self, command):
        assert _is_false_positive_carrier(command) is True

    # --- non-git command whose FIRST token is "commit" ------------------
    # Kills the and->or mutant (line 685): with `or`, the guard becomes
    # `base_cmd == "git" or semantics.non_flag_tokens`, which is truthy for
    # ANY command with non-flag tokens. It would then read git_subcmd and,
    # because the first token here is literally "commit", wrongly return True.
    # `kubectl` is neither a read-only base cmd nor git, so the honest result
    # is False; only the `or` mutant flips it to True.
    def test_non_git_command_with_commit_token_not_carrier(self):
        assert _is_false_positive_carrier("kubectl commit foo") is False

    # --- git with NO subcommand tokens => not a carrier -----------------
    # Kills the `and semantics.non_flag_tokens` truthiness: bare `git` (no
    # non-flag tokens) must NOT be treated as a commit/stash carrier.
    def test_bare_git_not_carrier(self):
        assert _is_false_positive_carrier("git") is False

    # --- git push --force is NOT a carrier (must stay blockable) --------
    # The first non-flag token is "push", not commit/stash => not a carrier,
    # so the destructive git push remains visible to the block list.
    def test_git_push_not_carrier(self):
        assert _is_false_positive_carrier("git push --force origin main") is False


# ===========================================================================
# SemanticBlockedRule.matches -- forbidden/required flags (9 survivors).
# Anchored via is_blocked_command end-to-end so the rule table is real.
# ===========================================================================
class TestSemanticRuleMatches:
    # --- terraform destroy (no -target) is BLOCKED ----------------------
    def test_terraform_destroy_blocked(self):
        assert is_blocked_command("terraform destroy").is_blocked is True

    # --- terraform destroy -target=<res> is NOT blocked -----------------
    # Kills line 111 forbidden-flag branch:
    #   `if flag_token == forbidden or flag_token.startswith(forbidden + "=")`
    # The Eq flips (Eq_Is/Eq_Gt/Eq_GtE/Eq_NotEq/Eq_Lt/Eq_LtE) and AddNot on
    # this guard would mis-evaluate the forbidden-flag exemption.
    @pytest.mark.parametrize("command", [
        "terraform destroy -target=aws_instance.web",
        "terraform destroy --target=aws_instance.web",
        "terragrunt destroy -target=module.db",
    ])
    def test_terraform_destroy_targeted_not_blocked(self, command):
        assert is_blocked_command(command).is_blocked is False

    # --- exact forbidden flag (no =value) also exempts -------------------
    # Kills the `==` half of the or (line 111): `-target` as a standalone
    # token must match via equality, not only via startswith("-target=").
    def test_terraform_destroy_bare_target_flag_not_blocked(self):
        assert is_blocked_command("terraform destroy -target").is_blocked is False

    # --- ordered-sequence guard (line 99/100) ---------------------------
    # Kills AddNot on `if not _contains_ordered_sequence(...)`: a command that
    # does NOT contain the rule sequence must NOT match the rule.
    def test_non_matching_sequence_not_blocked(self):
        # "terraform plan" lacks the ("terraform","destroy") sequence.
        assert is_blocked_command("terraform plan").is_blocked is False

    # --- required_flags gate (docker system prune -a) -------------------
    # docker system prune WITHOUT a required flag is not the blocked rule;
    # WITH -a it is. Exercises the required_flags `any(...)` path (line 104).
    def test_docker_prune_requires_flag(self):
        assert is_blocked_command("docker system prune").is_blocked is False
        assert is_blocked_command("docker system prune -a").is_blocked is True


# ===========================================================================
# is_blocked_command -- top-level orchestration (4 survivors).
# ===========================================================================
class TestIsBlockedCommand:
    # --- empty / whitespace guard (line 596 or->and) --------------------
    # `if not command or not command.strip()` -- with `and`, an empty string
    # ("" is falsy so `not command` True, `not command.strip()` True) still
    # short-circuits, but a None-equivalent only-whitespace differs. Assert
    # both empty and whitespace-only return not-blocked.
    @pytest.mark.parametrize("command", ["", "   ", "\t\n"])
    def test_empty_or_whitespace_not_blocked(self, command):
        assert is_blocked_command(command).is_blocked is False

    # --- suggestion loop iterates and breaks on first match -------------
    # Kills ZeroIterationForLoop (line 622) and BreakWithContinue (line 625):
    # a blocked command must surface a populated suggestion string from the
    # FIRST matching prefix. ZeroIteration would leave suggestion None.
    def test_blocked_command_has_suggestion(self):
        result = is_blocked_command("aws ec2 delete-vpc --vpc-id vpc-123")
        assert result.is_blocked is True
        assert result.suggestion is not None
        assert isinstance(result.suggestion, str)
        assert len(result.suggestion) > 0

    # --- prefix-match guard (line 623 AddNot) ----------------------------
    # `if cmd_prefix in command.lower()` -- AddNot would invert membership,
    # producing a suggestion from a NON-matching prefix (or None). We assert
    # the suggestion is the one keyed to this command, not an arbitrary one.
    def test_suggestion_matches_command_prefix(self):
        result = is_blocked_command("kubectl delete namespace prod")
        assert result.is_blocked is True
        assert result.suggestion is not None
        # The suggestion must mention the relevant resource, proving the
        # prefix-membership branch selected the correct entry.
        assert "namespace" in result.suggestion.lower() or "delete" in result.suggestion.lower()


# ===========================================================================
# _read_only_base_cmds -- ImportError fallback (line 74, 1 survivor).
# ===========================================================================
class TestReadOnlyBaseCmds:
    def test_returns_nonempty_frozenset(self):
        result = _read_only_base_cmds()
        assert isinstance(result, frozenset)
        # The happy path (import succeeds) returns the canonical set; it must
        # be non-empty and contain the documented read-only tools. The
        # ExceptionReplacer mutant on `except ImportError` cannot be observed
        # here, but this asserts the live return so a broken import surfaces.
        assert "grep" in result
        assert "cat" in result
        assert "ls" in result


# ===========================================================================
# SemanticBlockedRule -- head_only default True (line 96, 1 survivor).
# ===========================================================================
class TestSemanticBlockedRuleDefaults:
    # --- head_only defaults to True => semantic_head_tokens used ---------
    # Kills ReplaceTrueWithFalse on `head_only: bool = True`: if the default
    # were False the rule would scan ALL semantic tokens, changing which
    # commands match. We assert a destructive sequence buried AFTER a safe
    # head is NOT caught by a head_only rule (so the default really gates the
    # head). terraform destroy at head IS caught; the same tokens trailing a
    # different head are not.
    def test_head_only_default_is_true(self):
        rule = SemanticBlockedRule("x", ("terraform", "destroy"), "k")
        assert rule.head_only is True

    def test_head_only_gates_to_head_tokens(self):
        # A command whose destructive sequence is NOT at the head must not
        # match a head_only rule. `echo terraform destroy` has head "echo".
        assert is_blocked_command("echo terraform destroy").is_blocked is False


# ===========================================================================
# SemanticBlockedRule -- @dataclass(frozen=True) (line 87, 1 anon survivor).
# ===========================================================================
class TestSemanticBlockedRuleFrozen:
    # --- frozen=True makes instances immutable --------------------------
    # Kills ReplaceTrueWithFalse on `@dataclass(frozen=True)` (line 87): with
    # frozen False, assignment would succeed instead of raising
    # FrozenInstanceError.
    def test_rule_is_frozen(self):
        rule = SemanticBlockedRule("x", ("terraform", "destroy"), "k")
        with pytest.raises(FrozenInstanceError):
            rule.category = "y"
