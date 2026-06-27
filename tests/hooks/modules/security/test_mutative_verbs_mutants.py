#!/usr/bin/env python3
"""Mutation-survivor closure tests for mutative_verbs.py (GRIND-TOTAL).

This module exists to KILL the surviving mutants inventoried for
``hooks/modules/security/mutative_verbs.py`` (baseline 55.78% kill /
325 survivors over 735 specs). Each test targets the EXACT non-mutated
outcome of a code path so the corresponding mutant fails an assertion when it
lives.

The tests are honest: they assert specific values and branch directions
(category, verb, confidence, cli_family, reason substrings, dangerous_flags,
boundary indices, truthiness) — not merely ``is_mutative``. The dominant
survivor cause is that the legacy suite only asserts ``is_mutative`` and never
the rest of the MutativeResult, so operator/number/boolean mutants on the
*reason/verb/confidence/category* arms survive untouched. These tests pin
those fields.

Classes are grouped by function (mirrors the sibling
test_blocked_commands_mutants.py / test_approval_grants_mutants.py layout).
"""

import sys
from pathlib import Path

import pytest

# Add hooks to path (mirrors the sibling test modules).
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import modules.security.mutative_verbs as mv
from modules.security.mutative_verbs import (
    detect_mutative_command,
    MutativeResult,
)


# ===========================================================================
# detect_mutative_command -- the dominant cluster (146 survivors).
#
# Root cause of the survivors: the legacy suite asserts only `is_mutative`,
# never the rest of the MutativeResult. So NumberReplacer/AddNot/operator/
# boolean mutants on the *category / verb / confidence / cli_family / reason*
# arms — and on the boundary/index expressions that feed them — survive.
# These tests pin the full structured result for one input per branch.
# ===========================================================================
class TestDetectMutativeCommand:
    # --- Edge cases: empty / no-tokens (lines 1013, 1025) ----------------
    def test_empty_string(self):
        r = detect_mutative_command("")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.reason == "Empty command"
        assert r.confidence == "high"

    def test_whitespace_only(self):
        # Kills ReplaceOrWithAnd on `not command or not command.strip()`:
        # with `and`, a whitespace-only string (not "   " -> False) would
        # short-circuit False and NOT take the empty branch.
        r = detect_mutative_command("   ")
        assert r.is_mutative is False
        assert r.reason == "Empty command"

    def test_redirect_only_no_tokens(self):
        # A command that is ONLY an output redirect strips to zero tokens
        # while being non-empty/non-whitespace -> "No tokens after parsing".
        # Kills ReplaceFalseWithTrue on is_mutative=False (line 1025).
        r = detect_mutative_command("2>&1")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.reason == "No tokens after parsing"
        assert r.confidence == "high"

    # --- Step 1: command alias fast-path (lines 1064-1072) ---------------
    def test_alias_rm_full_result(self):
        r = detect_mutative_command("rm file.txt")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "rm"
        assert r.cli_family == "system"
        assert r.confidence == "high"
        assert r.reason == "Command alias 'rm' is mutative"

    # --- mkdir path-sensitivity override (lines 1045-1062) ---------------
    def test_mkdir_working_tree_readonly(self):
        # path_tokens present AND not sensitive -> READ_ONLY override.
        r = detect_mutative_command("mkdir myproj/subdir")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "mkdir"
        assert r.cli_family == "system"
        assert r.confidence == "high"
        assert "working-tree paths only" in r.reason

    def test_mkdir_sensitive_stays_mutative(self):
        r = detect_mutative_command("mkdir /etc/cron.d/x")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.reason == "Command alias 'mkdir' is mutative"

    def test_mkdir_no_path_tokens_stays_mutative(self):
        # `path_tokens and not _mkdir_...` -> empty path_tokens -> falls
        # through to T3. Kills the AddNot/and mutants on the guard.
        r = detect_mutative_command("mkdir -p")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"

    def test_mkdir_double_dash_filtered(self):
        # The `t != "--"` filter (line 1048) drops the `--` separator so the
        # real path "subdir" is the only path_token. Working-tree -> READ_ONLY.
        # If `--` were NOT filtered it would still be a non-sensitive token,
        # so this also confirms the path-token list is non-empty (override
        # fires) rather than empty (fall-through to T3).
        r = detect_mutative_command("mkdir -- subdir")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert "working-tree paths only" in r.reason

    # --- Step 1b: read-only base cmd + find -delete (lines 1085-1105) ----
    def test_find_delete_mutative(self):
        r = detect_mutative_command("find . -name x -delete")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "find"
        assert r.cli_family == "system"
        assert r.confidence == "high"
        assert r.dangerous_flags == ("-delete",)
        assert r.reason == "`find -delete` removes matched files"

    def test_find_readonly_fast_path(self):
        r = detect_mutative_command("find . -name x")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "find"
        assert r.cli_family == "system"
        assert r.confidence == "high"
        assert "whitelist fast-path" in r.reason

    def test_grep_readonly_fast_path(self):
        # base_cmd in READ_ONLY_BASE_CMDS and NOT "find": exercises the
        # `base_cmd == "find"` Eq mutants by taking the other branch.
        r = detect_mutative_command('grep -rn "SessionStart" file.json')
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "grep"

    # --- Step 1c: capability-class (database) fast-path (lines 1115-1139)-
    def test_sqlite_readonly_select(self):
        r = detect_mutative_command('sqlite3 db.sqlite "SELECT 1"')
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "sqlite3"
        assert r.cli_family == "database"
        assert r.confidence == "high"

    def test_sqlite_mutative_delete(self):
        r = detect_mutative_command('sqlite3 db.sqlite "DELETE FROM t"')
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "sqlite3"
        assert r.cli_family == "database"
        assert r.confidence == "high"

    # --- Step 2: single-token command (lines 1154-1162) ------------------
    def test_single_token(self):
        r = detect_mutative_command("kubectl")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "kubectl"
        assert r.cli_family == "k8s"
        assert r.confidence == "low"
        assert "Single-token command" in r.reason

    # --- Step 3: simulation flag override (lines 1165-1175) --------------
    def test_simulation_flag(self):
        r = detect_mutative_command("kubectl apply -f x.yaml --dry-run")
        assert r.is_mutative is False
        assert r.category == "SIMULATION"
        assert r.confidence == "high"
        assert "Simulation flag detected" in r.reason

    # --- Step 3.5: --help exemption (lines 1191-1220) --------------------
    def test_help_verb_is_first_non_flag(self):
        # Kills NumberReplacer on semantic_non_flags[0] (line 1206): the verb
        # must be the FIRST non-flag token ("approvals"), not the second.
        r = detect_mutative_command("gaia approvals clean --help")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "approvals"
        assert r.confidence == "high"
        assert "non-flag tokens" in r.reason

    def test_help_no_non_flag_verb_help(self):
        # Empty semantic_non_flags -> verb literal "help".
        r = detect_mutative_command("gaia --help")
        assert r.is_mutative is False
        assert r.verb == "help"

    def test_help_single_non_flag(self):
        r = detect_mutative_command("gaia update --help")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "update"

    def test_help_three_non_flags_not_exempted(self):
        # Kills NumberReplacer/comparison on `<= 2` (line 1204): with 3
        # non-flag positional tokens the exemption must NOT fire, so the
        # mutative verb is detected and the command stays T3.
        r = detect_mutative_command("kubectl delete pod mypod --help")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"

    # --- Step 3b: inline code (python3 -c) (lines 1226-1228) -------------
    def test_inline_code_dangerous(self):
        r = detect_mutative_command("python3 -c \"import os; os.remove('x')\"")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "os-remove"

    def test_inline_code_safe(self):
        r = detect_mutative_command('python3 -c "print(1)"')
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.confidence == "medium"

    # --- Step 3c: heredoc (lines 1236-1242) ------------------------------
    def test_heredoc_dangerous(self):
        cmd = 'python3 - <<EOF\nimport os\nos.system("rm -rf /")\nEOF'
        r = detect_mutative_command(cmd)
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"

    # --- Step 3d: git local-only subcommand guard (lines 1251-1272) ------
    def test_git_commit_local_safe(self):
        r = detect_mutative_command('git commit -m "update create deploy"')
        assert r.is_mutative is False
        assert r.verb == "commit"
        assert r.cli_family == "git"
        assert r.confidence == "high"
        assert "Git local-only subcommand" in r.reason

    def test_git_branch_dangerous_flag(self):
        r = detect_mutative_command("git branch -D feature")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "branch"
        assert r.dangerous_flags == ("-D",)
        assert r.cli_family == "git"
        assert "dangerous flags" in r.reason

    # --- Step 3e: command+subcommand tier exception (lines 1282-1337) ----
    def test_gaia_brief_edit_local_bookkeeping(self):
        r = detect_mutative_command("gaia brief edit 5")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "brief"
        assert "Local-only planning bookkeeping" in r.reason

    def test_gaia_plan_add_local_bookkeeping(self):
        r = detect_mutative_command("gaia plan add foo")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "plan"

    def test_gaia_plan_delete_stays_t3(self):
        # Kills the destructive-verb guard mutants (lines 1294-1297 `or`
        # chains, split("-",1)[0]): delete is a whole-record destruction and
        # must stay T3 even inside the excepted group.
        r = detect_mutative_command("gaia plan delete 3")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert "Whole-record destruction" in r.reason
        assert "stays T3" in r.reason

    # --- Step 3f: consent-reducing operations (lines 1350-1372) ----------
    def test_approvals_revoke_not_t3(self):
        r = detect_mutative_command("gaia approvals revoke")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "revoke"
        assert "Consent-reducing operation" in r.reason

    def test_approvals_approve_stays_t3(self):
        # `approve` is deliberately absent from CONSENT_REDUCING_... and falls
        # through to Step 4 where it stays MUTATIVE.
        r = detect_mutative_command("gaia approvals approve P-1")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "approve"

    # --- Step 4: compound read-only subcommand (lines 1379-1387) ---------
    def test_compound_read_only_subcommand(self):
        r = detect_mutative_command("git merge-base a b")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "merge-base"
        assert "Compound read-only subcommand" in r.reason

    # --- Step 4: hyphen-split mutative verb (lines 1416-1419) ------------
    def test_hyphen_split_delete_stack(self):
        # "delete-stack" at subcommand position splits to "delete".
        r = detect_mutative_command("docker delete-stack mystack")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert r.cli_family == "docker"
        assert r.confidence == "high"

    # --- Step 4: verb+flag read-only override (lines 1445-1455) ----------
    def test_git_tag_list_override(self):
        r = detect_mutative_command("git tag -l")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "tag"
        assert "overridden to read-only by flag" in r.reason

    def test_git_tag_create_mutative(self):
        r = detect_mutative_command("git tag v1.0")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "tag"
        assert r.reason == "Mutative verb 'tag'"

    # --- Step 4: camelCase split (lines 1506-1550) -----------------------
    def test_camelcase_batch_delete(self):
        # Kills the camelCase-arm mutants: semantic_index == 1 (1509),
        # len(camel_parts) > 1 (1510), the raw-token index bound (1506),
        # and the result-arm fields.
        r = detect_mutative_command("mytool batchDelete")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert r.confidence == "high"
        assert "CamelCase verb 'delete'" in r.reason
        assert "batchDelete" in r.reason

    # --- Step 4b: api implicit GET (lines 1581-1595) ---------------------
    def test_gh_api_implicit_get(self):
        # Kills the api-arm mutants: len(...) > 1 (1586), [1] == "api" (1587),
        # and the NotEq on the MUTATIVE_VERBS membership scan (1584).
        r = detect_mutative_command("gh api repos/foo/bar")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "api"
        assert r.confidence == "high"
        assert "implicit GET" in r.reason

    def test_gh_api_explicit_post_mutative(self):
        r = detect_mutative_command("gh api repos/foo -X POST")
        assert r.is_mutative is True
        assert r.verb == "post"


# ===========================================================================
# _scan_dangerous_flags -- 63 survivors.
#
# Returns the tuple of dangerous flags present, honouring per-CLI context.
# Survivors: the legacy suite never asserts the EXACT returned tuple, so the
# `token == "-f"` / `cli in X` / `len(token) > 2` / `token[1] != "-"` /
# `"r" in flag_chars and "f" in flag_chars` mutants survive. These tests pin
# the tuple for each flag in both its dangerous-CLI and inert-CLI context, so
# every comparison/membership/and/length/index mutant flips an assertion.
# ===========================================================================
class TestScanDangerousFlags:
    def _scan(self, *args):
        return mv._scan_dangerous_flags(*args)

    # --- non-flag tokens are skipped (line 870 `not startswith("-")`) ----
    def test_non_flag_tokens_skipped(self):
        # Kills AddNot on the `if not token.startswith("-")` guard: a bare
        # positional arg must NOT be collected.
        assert self._scan(["rm", "file"], "rm") == ()

    # --- ALWAYS flags (line 877 == "ALWAYS") -----------------------------
    def test_always_force(self):
        assert self._scan(["x", "--force"], "anything") == ("--force",)

    def test_always_no_preserve_root(self):
        assert self._scan(["x", "--no-preserve-root"], "anything") == (
            "--no-preserve-root",
        )

    # --- -f context (lines 882-884) --------------------------------------
    def test_f_force_cli(self):
        # token == "-f" AND cli in F_FLAG_MEANS_FORCE -> collected.
        assert self._scan(["x", "-f"], "rm") == ("-f",)

    def test_f_inert_cli(self):
        # cli NOT in F_FLAG_MEANS_FORCE -> NOT collected. Kills the
        # `cli in F_FLAG_MEANS_FORCE` membership AddNot.
        assert self._scan(["x", "-f"], "ls") == ()

    # --- -r / -R context (lines 885-887) ---------------------------------
    def test_r_recursive_cli(self):
        assert self._scan(["x", "-r"], "rm") == ("-r",)

    def test_R_recursive_cli(self):
        # token in ("-r", "-R") tuple membership.
        assert self._scan(["x", "-R"], "rm") == ("-R",)

    def test_r_inert_cli(self):
        assert self._scan(["x", "-r"], "ls") == ()

    # --- -D context (lines 888-890) --------------------------------------
    def test_D_force_delete_git(self):
        assert self._scan(["x", "-D"], "git") == ("-D",)

    def test_D_inert_cli(self):
        assert self._scan(["x", "-D"], "ls") == ()

    # --- -M context (lines 891-893) --------------------------------------
    def test_M_force_move_git(self):
        assert self._scan(["x", "-M"], "git") == ("-M",)

    def test_M_inert_cli(self):
        assert self._scan(["x", "-M"], "ls") == ()

    # --- --delete context (lines 894-896) --------------------------------
    def test_delete_destructive_git(self):
        assert self._scan(["x", "--delete"], "git") == ("--delete",)

    def test_delete_inert_cli(self):
        assert self._scan(["x", "--delete"], "ls") == ()

    # --- --recursive context (lines 897-899) -----------------------------
    def test_recursive_destructive_cli(self):
        assert self._scan(["x", "--recursive"], "rm") == ("--recursive",)

    def test_recursive_inert_cli(self):
        assert self._scan(["x", "--recursive"], "ls") == ()

    # --- --hard context (lines 900-902) ----------------------------------
    def test_hard_destructive_git(self):
        assert self._scan(["x", "--hard"], "git") == ("--hard",)

    def test_hard_inert_cli(self):
        assert self._scan(["x", "--hard"], "ls") == ()

    # --- compound short flags (lines 906-913) ----------------------------
    def test_compound_rf_always(self):
        # `len(token) > 2 and token[0] == "-" and token[1] != "-"` then
        # `"r" in flag_chars and "f" in flag_chars`. -rf is also an exact
        # ALWAYS match, so use a non-listed compound to exercise the elif.
        assert self._scan(["x", "-rfi"], "anything") == ("-rfi",)

    def test_compound_f_only_force_cli(self):
        # elif `"f" in flag_chars and cli in F_FLAG_MEANS_FORCE`.
        assert self._scan(["x", "-fv"], "mv") == ("-fv",)

    def test_compound_f_only_inert_cli(self):
        assert self._scan(["x", "-fv"], "ls") == ()

    def test_compound_r_only_recursive_cli(self):
        # elif `"r" in flag_chars and cli in R_FLAG_MEANS_RECURSIVE_DELETE`.
        assert self._scan(["x", "-rv"], "rm") == ("-rv",)

    def test_compound_r_only_inert_cli(self):
        assert self._scan(["x", "-rv"], "ls") == ()

    def test_compound_length_boundary(self):
        # `len(token) > 2`: a 2-char short flag "-v" is NOT a compound and
        # (not being in DANGEROUS_FLAGS) must yield (). Kills the `> 2`
        # NumberReplacer/comparison mutants.
        assert self._scan(["x", "-v"], "rm") == ()

    def test_long_flag_not_treated_as_compound(self):
        # `token[1] != "-"`: a long flag like "--verbose" has token[1]=="-"
        # so the compound branch must be skipped -> ().
        assert self._scan(["x", "--verbose"], "rm") == ()

    # --- ordering / multiplicity -----------------------------------------
    def test_multiple_flags_in_order(self):
        # ReplaceContinueWithBreak (line 879) and append ordering: both flags
        # must be collected, in encounter order.
        assert self._scan(["x", "-D", "--force"], "git") == ("-D", "--force")


# ===========================================================================
# _layer3_length_check -- 36 survivors.
#
# Extracts the code portion after the inline flag, then flags it MUTATIVE when
# longer than MAX_NORMAL_INLINE_LENGTH (unless skip_length_check). Survivors:
# the `idx + len(flag) + 2` slice arithmetic, the `> MAX` boundary, the
# `not skip_length_check and ...` guard, the `idx != -1` check, and the
# break/zero-iteration loop mutants are never pinned because the legacy suite
# only checks is_mutative. These tests pin the exact reported char-count
# (which the slice arithmetic determines) and both boundary sides.
# ===========================================================================
class TestLayer3LengthCheck:
    def _l3(self, command, base="python3", family="unknown", skip=False):
        return mv._layer3_length_check(command, base, family, skip)

    def test_short_code_safe(self):
        r = self._l3('python3 -c "print(1)"')
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "inline-code"
        assert r.confidence == "medium"
        assert "no dangerous patterns" in r.reason

    def test_long_code_flagged_with_exact_count(self):
        # code_portion = command[idx + len("-c") + 2:] -> the substring after
        # ' -c ' is the quoted payload. For a 505-char payload the quoted
        # portion is 507 chars. Asserting the EXACT count kills every
        # ReplaceBinaryOperator/NumberReplacer mutant on the slice arithmetic
        # (idx + len(flag) + 2) -- any change shifts the reported length.
        payload = "x" * 505
        cmd = 'python3 -c "%s"' % payload
        r = self._l3(cmd)
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "heuristic-long-code"
        assert r.confidence == "low"
        # 505 payload + 2 surrounding quote chars = 507.
        assert r.reason == (
            "Inline code is unusually long (507 chars > 500 limit)"
        )

    def test_skip_length_check_never_flags(self):
        # `not skip_length_check and len > MAX` -> skip=True forces the safe
        # arm even for an over-length payload. Kills the AddNot on the guard
        # and the ReplaceTrueWithFalse upstream that feeds skip_length_check.
        payload = "x" * 505
        cmd = 'python3 -c "%s"' % payload
        r = self._l3(cmd, skip=True)
        assert r.is_mutative is False
        assert r.verb == "inline-code"

    def test_boundary_at_limit_not_flagged(self):
        # `> MAX_NORMAL_INLINE_LENGTH` is strict: a code_portion of EXACTLY
        # 500 chars must NOT flag. Kills the Gt_GtE / Gt_Eq comparison mutants
        # and the NumberReplacer on 500. Payload 498 + 2 quotes = 500.
        payload = "x" * 498
        cmd = 'python3 -c "%s"' % payload
        r = self._l3(cmd)
        assert r.is_mutative is False
        assert r.verb == "inline-code"

    def test_boundary_one_over_limit_flagged(self):
        # 501 chars (payload 499 + 2 quotes) -> just over -> flagged.
        payload = "x" * 499
        cmd = 'python3 -c "%s"' % payload
        r = self._l3(cmd)
        assert r.is_mutative is True
        assert r.reason == (
            "Inline code is unusually long (501 chars > 500 limit)"
        )

    def test_flag_not_found_uses_whole_command(self):
        # When the inline flag is absent (idx == -1), code_portion stays the
        # whole command. Kills the `idx != -1` comparison + break mutants:
        # build a long command with NO ' -c ' so the loop never assigns, and
        # the whole (long) command is measured.
        long_cmd = "node " + ("y" * 600)  # base node, but no ' -e '/'--eval '
        r = self._l3(long_cmd, base="node")
        assert r.is_mutative is True
        assert r.verb == "heuristic-long-code"
        # whole command measured: len("node " + 600 y) = 5 + 600 = 605
        assert "605 chars" in r.reason


# ===========================================================================
# _find_first_non_flag -- 10 survivors.
#
# Returns (first_truthy_token_after_index_0, its_index) or ("", -1).
# Survivors: the `range(1, len)` bounds, the `if tokens[i]` truthiness guard,
# and the ("", -1) sentinel are never pinned. These assert the exact tuple.
# ===========================================================================
class TestFindFirstNonFlag:
    def _ff(self, tokens):
        return mv._find_first_non_flag(tokens)

    def test_first_token_returned_with_index(self):
        # range starts at 1 (skip tokens[0]); first truthy is index 1.
        assert self._ff(["cmd", "verb", "x"]) == ("verb", 1)

    def test_empty_token_skipped(self):
        # `if tokens[i]` truthiness: an empty string at index 1 is skipped,
        # the real verb at index 2 is returned. Kills the AddNot on the guard.
        assert self._ff(["cmd", "", "verb"]) == ("verb", 2)

    def test_no_non_flag_returns_sentinel(self):
        # Single token -> loop body never runs -> ("", -1) sentinel.
        assert self._ff(["cmd"]) == ("", -1)

    def test_all_empty_returns_sentinel(self):
        # All-empty tail -> sentinel. Kills the NumberReplacer on -1.
        assert self._ff(["cmd", "", ""]) == ("", -1)

    def test_index_zero_token_not_returned(self):
        # `range(1, len)` must NOT return tokens[0] even when it is the only
        # truthy token. Kills the range-start NumberReplacer (1 -> 0).
        assert self._ff(["onlybase", "", ""]) == ("", -1)
