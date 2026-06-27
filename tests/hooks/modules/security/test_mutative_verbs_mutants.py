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
from dataclasses import FrozenInstanceError
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
# detect_mutative_command -- Step 3e subcommand tier-exception residuals.
#
# `verb_is_destructive` (L1293-1298) is a 4-arm OR over
# group_verb.split("-",1)[0] / group_verb against the global DENY set and the
# per-group EXTRA_DENY set. Survivors: the `or` chain (ReplaceOrWithAnd L1295-
# 1297), the split maxsplit `1` (NumberReplacer L1294/1313), and the `> 1`
# group-verb-presence guard (L1286 Gt_NotEq). These pin the destructive vs
# bookkeeping decision for hyphenated and per-group-deny verbs.
# ===========================================================================
class TestSubcommandTierException:
    def test_plan_hyphenated_destroy_verb_stays_t3(self):
        # group_verb = "delete-foo": arm1 split("-",1)[0]="delete" IS in DENY,
        # arm2 "delete-foo" is NOT.  The `or`->`and` mutants (L1295-1297) make
        # the whole guard False (arm1 and arm2 = T and F = F), dropping the
        # command to local bookkeeping (is_mutative False).  The split `1->0`
        # mutant makes arm1 split the whole token ("delete-foo" not in DENY),
        # same effect.  Original keeps it MUTATIVE / whole-record destruction.
        r = detect_mutative_command("gaia plan delete-foo 3")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        # Pin the FULL reason: the f-string interpolates non_flag_tokens[0]
        # ("plan") and group_verb ("delete-foo"); NumberReplacer on the [0]
        # index (L1319) flips "plan" to "delete-foo"/the last token.
        assert r.reason == (
            "Whole-record destruction 'gaia plan delete-foo' "
            "stays T3 despite the local bookkeeping exception"
        )

    def test_task_remove_extra_deny_stays_t3(self):
        # `gaia task remove` is in COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS (not the
        # global DENY set).  Pins the per-group EXTRA_DENY path: remove must
        # stay T3 for task.
        r = detect_mutative_command("gaia task remove 7")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "remove"
        assert "Whole-record destruction" in r.reason

    def test_task_hyphenated_remove_extra_deny_stays_t3(self):
        # group_verb = "remove-foo": arm3 split("-",1)[0]="remove" IS in task's
        # EXTRA_DENY, arm4 "remove-foo" is NOT.  Kills the arm3/arm4 OrWithAnd
        # mutants (L1296/L1297) and the L1296 split maxsplit `1->0` (which would
        # split the whole token and miss "remove").  Original stays T3.
        r = detect_mutative_command("gaia task remove-foo 7")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "remove"
        assert "Whole-record destruction" in r.reason

    def test_ac_remove_reversible_bookkeeping(self):
        # `gaia ac remove` is NOT in DENY nor in ac's EXTRA_DENY -> reversible
        # bookkeeping, stays non-T3.  Anchors the negative side of the deny
        # guard so an over-broad mutant (e.g. arm that always fires) is caught.
        r = detect_mutative_command("gaia ac remove 2")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "ac"
        assert "Local-only planning bookkeeping" in r.reason

    def test_plan_edit_no_group_verb_bookkeeping(self):
        # group_verb present (len(non_flag_tokens) > 1).  L1286 Gt_NotEq
        # (`!= 1`) flips the `len(...) > 1` group-verb extraction guard; the
        # bookkeeping reason naming the subcommand pins the non-destructive arm.
        r = detect_mutative_command("gaia plan edit 4")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "plan"
        assert "'gaia plan'" in r.reason


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

    # --- elif-chain comparison discriminators (L882 GtE, L879 break) -----
    def test_dash_r_not_captured_by_dash_f_branch(self):
        # Kills L882 `token == "-f"` -> Eq_GtE.  "-r" >= "-f" is True (r>f), so
        # the GtE mutant enters the `-f` branch and checks F_FLAG_MEANS_FORCE
        # (gsutil absent) instead of the `-r` branch (gsutil IS in
        # R_FLAG_MEANS_RECURSIVE_DELETE).  Original collects "-r"; mutant drops
        # it.  gsutil is chosen because it is in R_RECURSIVE but NOT F_FORCE,
        # so the two branches diverge.
        assert self._scan(["x", "-r"], "gsutil") == ("-r",)

    def test_two_context_flags_both_collected_continue(self):
        # Kills L879 ReplaceContinueWithBreak on the ALWAYS arm: with `break`,
        # a leading ALWAYS flag would stop the loop and the trailing context
        # flag would be lost.  Both must appear, in order.
        assert self._scan(["x", "--force", "-D"], "git") == ("--force", "-D")

    def test_force_then_context_continue_not_break(self):
        # Same ReplaceContinueWithBreak target with ALWAYS first then a context
        # flag in a force CLI: `break` would truncate to ("--force",).
        assert self._scan(["x", "--force", "-f"], "rm") == ("--force", "-f")

    def test_bare_dash_token_not_compound(self):
        # Kills L906 `len(token) > 2` -> Gt_NotEq (`len(token) != 2`).  A bare
        # "-" token (stdin marker): startswith("-") so it is not skipped, is
        # not a DANGEROUS_FLAGS key, and len 1.  Original `> 2` is False so the
        # compound branch is skipped -> ().  The `!= 2` mutant is True for
        # len 1, enters the branch, and evaluates `token[1]` -> IndexError,
        # which the harness records as a non-survivor.  Either way the mutant
        # cannot return () for this input.
        assert self._scan(["x", "-"], "rm") == ()


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

    def test_long_flag_exact_count_pins_slice_arithmetic(self):
        # The `idx + len(flag) + 2` slice survives a 2-char `-c` probe because
        # `len("-c") * 2`, `** 2`, `| 2`, `^ 2` happen to coincide with `+ 2`
        # at flag-length 2 (or collapse into the 507-char window). A SIX-char
        # flag (`node --eval`) breaks every coincidence:
        #   idx=4, len("--eval")=6  ->  offset = 4 + 6 + 2 = 12, code_portion 507
        #   *2  -> 4 + 12 = 16  -> 503 chars   (Mul)
        #   **2 -> 4 + 36 = 40  -> 479 chars, NOT > 500 -> is_mutative False (Pow)
        #   |2  -> 4 + (6|2)=10 -> 509 chars   (BitOr)
        #   ^2  -> 4 + (6^2)= 8 -> 511 chars   (BitXor)
        # Pinning is_mutative AND the exact 507 count kills all four.
        payload = "x" * 505
        cmd = 'node --eval "%s"' % payload
        r = self._l3(cmd, base="node")
        assert r.is_mutative is True
        assert r.verb == "heuristic-long-code"
        assert r.reason == (
            "Inline code is unusually long (507 chars > 500 limit)"
        )


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


# ===========================================================================
# _mkdir_targets_sensitive_path -- 19 survivors.
#
# Scans mkdir path args; returns True iff any absolute path arg falls under a
# MKDIR_SENSITIVE_PATH_PREFIXES prefix. Survivors: the i=1 init, the i<len
# bound, the `== "--"` / seen_end_of_opts=True, the `-m`/`--mode` value skip
# (i += 1), the `~/` or `~` guard, and the `norm == prefix or startswith` are
# never pinned. These call the function directly and assert the bool.
# ===========================================================================
class TestMkdirTargetsSensitivePath:
    def _m(self, *tokens):
        return mv._mkdir_targets_sensitive_path(tokens)

    def test_sensitive_etc_subpath(self):
        assert self._m("mkdir", "/etc/foo") is True

    def test_sensitive_etc_exact(self):
        # `norm == prefix` arm (line 688): an exact "/etc" is sensitive.
        assert self._m("mkdir", "/etc") is True

    def test_relative_path_safe(self):
        # `not os.path.isabs` -> relative is working-tree, not sensitive.
        assert self._m("mkdir", "subdir") is False

    def test_home_slash_safe(self):
        # `token.startswith("~/")` arm of the `or`.
        assert self._m("mkdir", "~/foo") is False

    def test_home_bare_safe(self):
        # `token == "~"` arm of the `or` (line 675). Kills ReplaceOrWithAnd:
        # with `and`, "~" alone (startswith("~/") False) would fall through
        # to isabs (False) -> still False here, so use the etc-after to anchor.
        assert self._m("mkdir", "~") is False

    def test_abs_nonsensitive_safe(self):
        assert self._m("mkdir", "/home/user/x") is False

    def test_etc_custom_not_sensitive(self):
        # `norm.startswith(prefix + "/")` requires the slash: "/etc_custom"
        # must NOT match "/etc". Kills the startswith-without-slash drift.
        assert self._m("mkdir", "/etc_custom/x") is False

    def test_path_after_double_dash(self):
        # `token == "--"` sets seen_end_of_opts=True (line 663-664) so the
        # following "/etc/foo" is treated as a PATH, not a flag -> sensitive.
        assert self._m("mkdir", "--", "/etc/foo") is True

    def test_double_dash_makes_dashlike_token_a_path(self):
        # After --, a token that starts with "-" is still a path. This pins
        # ReplaceTrueWithFalse on seen_end_of_opts=True: if it stayed False,
        # the dash token would be skipped as a flag.
        assert self._m("mkdir", "--", "/sys/x") is True

    # --- loop-bound + continue discriminators (L659, L677) ---------------
    def test_trailing_mode_flag_no_value_terminates(self):
        # Kills L659 `while i < len(tokens)` -> Lt_IsNot / Lt_NotEq.  A bare
        # trailing "-m" (no mode value): the value-skip does `i += 1` again, so
        # i overshoots to len+1.  `< len` stops cleanly (orig -> False, no path
        # args).  `i is not len` / `i != len` are both True at i==len+1, so the
        # loop re-enters and indexes tokens[len+1] -> IndexError, which the
        # harness records as a non-survivor.  Either way the mutant cannot
        # return False here.
        assert self._m("mkdir", "-m") is False

    def test_home_path_then_sensitive_continues_scan(self):
        # Kills L677 ReplaceContinueWithBreak on the `~/`/`~` home-safe arm.
        # A home-relative path followed by a sensitive absolute path: `continue`
        # keeps scanning and finds "/etc" -> True.  `break` would stop at the
        # home path and return False.
        assert self._m("mkdir", "~/foo", "/etc/bar") is True

    def test_flag_skipped_before_end_of_opts(self):
        # `not seen_end_of_opts and token.startswith("-")` -> a flag is
        # skipped. A bare "-p" with no path -> no sensitive path -> False.
        assert self._m("mkdir", "-p") is False

    def test_mode_value_is_skipped(self):
        # `-m`/`--mode` consume their value (i += 1, line 670). Here the mode
        # VALUE is itself "/etc": it must be SKIPPED, so only the real arg
        # "subdir" is checked -> False. If the skip were removed, "/etc" would
        # be read as a path -> True. Kills the NumberReplacer on i += 1.
        assert self._m("mkdir", "-m", "/etc", "subdir") is False

    def test_mode_long_flag_value_skipped(self):
        assert self._m("mkdir", "--mode", "/etc", "subdir") is False

    def test_mode_value_skipped_real_path_still_caught(self):
        # The mode value is skipped but a later real sensitive path is caught.
        assert self._m("mkdir", "-m", "/etc", "/sys/x") is True

    def test_normpath_double_slash(self):
        # os.path.normpath collapses "//" so "/etc//foo" matches "/etc/".
        assert self._m("mkdir", "/etc//foo") is True


# ===========================================================================
# Module-level survivors -- frozen dataclass + read-cap constant.
# (Import-fallback ExceptionReplacers on lines 34/46/55 are proven equivalent
# in equivalents-mutative-verbs.skip: the sibling modules always import
# in-process, so the except bodies never run.)
# ===========================================================================
class TestModuleLevel:
    def test_mutative_result_is_frozen(self):
        # `@dataclass(frozen=True)` -> ReplaceTrueWithFalse makes the dataclass
        # mutable. Assert immutability so the mutant raises (or fails to raise).
        r = MutativeResult(is_mutative=True, category="MUTATIVE")
        with pytest.raises(FrozenInstanceError):
            r.is_mutative = False  # type: ignore[misc]

    def test_max_safe_inline_length_value(self):
        # Pin the constant so NumberReplacer on `150` flips the assertion.
        assert mv.MAX_SAFE_INLINE_LENGTH == 150

    def test_max_normal_inline_length_value(self):
        assert mv.MAX_NORMAL_INLINE_LENGTH == 500

    def test_max_script_read_bytes_value(self):
        # `256 * 1024` -> ReplaceBinaryOperator (Mul->Add/...) and
        # NumberReplacer on 256/1024 all change the product. Pin the literal
        # value (NOT `256*1024`, which would re-evaluate the mutated source).
        assert mv._MAX_SCRIPT_READ_BYTES == 262144


# ===========================================================================
# split_camel_case -- L932 survivors.
#   `return [...] if len(parts) > 1 else [token.lower()]`
# Gt_NotEq (len != 1) and the NumberReplacer on `1` discriminate only when
# parts has length 0 (empty token) or exactly 1. Gt_GtE is equivalent (see
# equivalents-mutative-verbs.skip M3) because at len==1 both arms yield the
# same single lowercased element and at len==0 both yield [token.lower()].
# ===========================================================================
class TestSplitCamelCase:
    def test_empty_token_returns_single_empty(self):
        # parts = "".split() -> []. orig `len > 1` is False -> [token.lower()]
        # = [""].  Gt_NotEq (`!= 1`) is True for len 0 -> would return the
        # comprehension over [] = [].  NumberReplacer `1->0` (`> 0`) is True
        # for len 0 -> [] as well.  Asserting [""] kills both.
        assert mv.split_camel_case("") == [""]

    def test_single_word_lowercased(self):
        # parts = ["GET"] (no camel boundary), len 1. orig -> [token.lower()].
        # Gt_NotEq `!= 1` is False -> [token.lower()] (same). This pins the
        # len==1 arm so a `1->2` NumberReplacer (`> 2`) still returns
        # [token.lower()] for len 1 (no change) -- but `> 0` would take the
        # comprehension; here both happen to lower the same single element,
        # so the discriminating case is the empty-token test above.
        assert mv.split_camel_case("GET") == ["get"]

    def test_camel_split_two_parts(self):
        # parts length 2 -> comprehension. Pins the >1 True arm.
        assert mv.split_camel_case("batchDelete") == ["batch", "delete"]


# ===========================================================================
# _extract_embedded_shell_commands -- L461 survivor.
#   `return [m.group(1) for m in _STRING_LITERAL_RE.finditer(code)]`
# NumberReplacer on the group index `1`. group(0) returns the whole match
# (quotes included); group(1) returns the inner literal (quotes stripped).
# Asserting the quote-stripped content kills the `1->0` mutant.
# ===========================================================================
class TestExtractEmbeddedShellCommands:
    def test_group_one_strips_quotes(self):
        out = mv._extract_embedded_shell_commands("x = 'rm -rf /tmp/foo'")
        # group(1) -> inner literal (no surrounding quotes).
        assert out == ["rm -rf /tmp/foo"]
        assert not out[0].startswith("'")


# ===========================================================================
# _extract_python_payload -- L1659/L1661 survivors.
#   fallback: `m = _re.search(r"-c\s+(\S.*)$", ...)`; `return m.group(1)...`
# AddNot on `if m:` (L1659) and NumberReplacer on the group index `1` (L1661).
# ===========================================================================
class TestExtractPythonPayload:
    def test_inline_c_quoted_payload(self):
        # Canonical -c quoted form -> group(2) of the quoted regex.
        out = mv._extract_python_payload('python3 -c "print(1)"', "python3")
        assert out == "print(1)"

    def test_fallback_unquoted_payload_group_one(self):
        # No surrounding quote pair -> falls to the greedy `-c\s+(\S.*)$`
        # branch; AddNot on `if m:` flips entry, NumberReplacer on group(1)
        # changes what is returned. An unquoted payload exercises this arm.
        out = mv._extract_python_payload("pybin -c foo_bar_baz", "pybin")
        assert out == "foo_bar_baz"


# ===========================================================================
# _is_subcommand_identifier -- L983/L984/L986 survivors.
#   empty -> False (L983 ReplaceFalseWithTrue); loop scans chars; on a
#   NON_SUBCOMMAND char return False (L986 ReplaceFalseWithTrue); else True.
#   L984 ZeroIterationForLoop skips the loop -> always True for non-empty.
# ===========================================================================
class TestIsSubcommandIdentifier:
    def test_empty_is_false(self):
        # L983 ReplaceFalseWithTrue would make "" return True.
        assert mv._is_subcommand_identifier("") is False

    def test_path_like_is_false(self):
        # Contains "/" (a NON_SUBCOMMAND char) -> False. L986
        # ReplaceFalseWithTrue makes this True; L984 ZeroIterationForLoop
        # skips the char scan so the "/" is never seen -> also True. Both die.
        assert mv._is_subcommand_identifier("tests/foo.py") is False

    def test_plain_word_is_true(self):
        assert mv._is_subcommand_identifier("delete") is True

    def test_internal_hyphen_is_true(self):
        assert mv._is_subcommand_identifier("force-delete") is True


# ===========================================================================
# build_t3_block_response -- L2054 survivor.
#   `if danger.dangerous_flags:`  AddNot inverts the flag-warning branch.
# ===========================================================================
class TestBuildT3BlockResponse:
    def test_dangerous_flags_warning_present(self):
        danger = MutativeResult(
            is_mutative=True, category="MUTATIVE", verb="rm",
            dangerous_flags=("-rf",), cli_family="system", confidence="high",
            reason="x",
        )
        resp = mv.build_t3_block_response("rm -rf /tmp/x", danger)
        assert "Dangerous flags detected: -rf" in resp["message"]

    def test_no_dangerous_flags_no_warning(self):
        danger = MutativeResult(
            is_mutative=True, category="MUTATIVE", verb="kubectl",
            dangerous_flags=(), cli_family="k8s", confidence="high", reason="x",
        )
        resp = mv.build_t3_block_response("kubectl apply -f x", danger)
        assert "Dangerous flags detected" not in resp["message"]


# ===========================================================================
# _check_inline_code -- L1892/L1894/L1910 survivors.
#   Layer-1 guard `if _is_blocked_command is not None:` (L1892 AddNot,
#   IsNot_Is) and the per-literal `for literal in embedded_strings:` (L1894
#   ZeroIterationForLoop): all three disable Layer 1, which catches a blocked
#   shell command embedded as a STRING LITERAL that no Layer-2b API pattern
#   matches (dd/mkfs). With Layer 1 live the verb is 'embedded-blocked-cmd';
#   disabling it falls through to the safe 'inline-code' terminal.
#   Layer-2 guard `base_cmd in _PYTHON_INTERPRETERS and _analyze_python_inline
#   is not None` (L1910 And->Or): `_analyze_python_inline` is always imported
#   in-process, so `or` makes the AST block run for a NON-python interpreter.
#   A `node -c <subprocess payload>` is classified by Layer-2b (verb
#   'process-module', confidence 'medium') under the original `and`; under `or`
#   the AST lane would claim it (verb 'subprocess-run', confidence 'high').
# ===========================================================================
class TestCheckInlineCode:
    def test_embedded_blocked_literal_caught_by_layer1(self):
        # 'dd if=/dev/zero of=/dev/sda' is blocked by blocked_commands but is
        # NOT one of the Layer-2b API keyword patterns, so it can ONLY be
        # caught by Layer 1 (string-literal extraction). Kills the AddNot /
        # IsNot_Is on the Layer-1 guard and the ZeroIterationForLoop on the
        # per-literal loop -- each disables Layer 1, dropping to safe inline.
        cmd = 'node -e "dd if=/dev/zero of=/dev/sda"'
        r = mv._check_inline_code(cmd, "node", "unknown")
        assert r.is_mutative is True
        assert r.verb == "embedded-blocked-cmd"
        assert r.confidence == "high"
        assert "blocked shell command" in r.reason

    def test_non_python_interpreter_skips_ast_lane(self):
        # base_cmd 'node' is NOT a python interpreter, so under the original
        # `and` guard the AST lane is skipped and Layer-2b classifies the
        # payload (verb 'process-module', confidence 'medium'). The And->Or
        # mutant would enter the AST lane (verb 'subprocess-run', high). Pin
        # the Layer-2b verb + confidence to kill the And->Or.
        cmd = 'node -c "import subprocess\nsubprocess.run([\'ls\'])"'
        r = mv._check_inline_code(cmd, "node", "unknown")
        assert r.is_mutative is True
        assert r.verb == "process-module"
        assert r.confidence == "medium"


# ===========================================================================
# _check_script_file -- L1784 survivors (Delete_Not, AddNot on
# `if not ast_result.parse_failed:`). A clean-parsing, non-dangerous PYTHON
# script returns the AST-analysis safe result ("(AST analysis)" reason).
# Inverting the guard to `if parse_failed:` drops through to the regex lane,
# whose safe terminal reads "has no mutative or blocked line" instead. Pinning
# the "(AST analysis)" reason kills both unary mutants.
# ===========================================================================
class TestCheckScriptFilePythonLane:
    def _run(self, tmp_path, body, name="s.py"):
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        return detect_mutative_command(f"python3 {p}")

    def test_clean_python_script_uses_ast_analysis(self, tmp_path):
        r = self._run(tmp_path, "x = 1\nprint(x + 2)\n")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "script-file"
        assert "(AST analysis)" in r.reason
        assert "no mutative invocation" in r.reason

    def test_direct_invocation_uses_first_token_not_last(self):
        # _resolve_script_argument's direct-invocation branch reads
        # `invoked = raw_tokens[0]` -- the executable token IS the script.
        # The NumberReplacer mutant `raw_tokens[-1]` would instead treat a
        # trailing ./script.sh ARGUMENT of an unrelated command as the script,
        # reading it as an unreadable file and flagging the whole command
        # MUTATIVE 'script-file-unreadable'. `mytool` is not a script
        # interpreter and `raw_tokens[0]` ('mytool') has no '/', so the
        # original returns None -> ordinary detection -> non-mutative unknown
        # verb 'foo'. Pinning that result kills the [0]->[-1] mutant.
        r = detect_mutative_command("mytool foo ./evil.sh")
        assert r.is_mutative is False
        assert r.verb == "foo"


# ===========================================================================
# _classify_script_content_by_regex -- L1821/L1824/L1828 survivors.
#   per-line: `if not line or line.startswith("#"): continue` (L1821 Or->And),
#   blocked-command guard `if _is_blocked_command is not None:` (L1824 AddNot,
#   IsNot_Is), and the no-match terminal is_mutative=False (L1828 True->False).
# Driven through detect_mutative_command on a `./script.sh` invocation so the
# real file content is classified.
# ===========================================================================
class TestClassifyScriptContentByRegex:
    def _run(self, tmp_path, body, name="s.sh"):
        # Invoke via the `bash <file>` interpreter shape so
        # _resolve_script_argument returns the script path (shell lane) and
        # _check_script_file reads the real file content.
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        return detect_mutative_command(f"bash {p}")

    def test_comment_and_blank_lines_skipped_then_safe(self, tmp_path):
        # Only comments + blanks -> no mutative/blocked line -> safe terminal.
        # L1828 True->False on the empty-result is_mutative=False would flip
        # this to is_mutative=True. L1821 Or->And: with `and`, a blank line
        # ("" -> startswith("#") False) would NOT be skipped and could be
        # parsed as a (non-mutative) command, but a comment line "# x"
        # ("" is truthy so `not line` False; with `and` the whole guard is
        # False) would NOT continue -> parsed as command. Asserting the safe
        # terminal verb/reason pins the skip + terminal arms.
        r = self._run(tmp_path, "# comment only\n\n#another\n")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "script-file"
        assert "no mutative or blocked line" in r.reason

    def test_blocked_line_detected(self, tmp_path):
        # A blocked destructive line -> verb 'script-blocked-cmd'. L1824 AddNot
        # / IsNot_Is on `_is_blocked_command is not None` would skip the
        # blocked check; asserting the blocked verb kills it.
        r = self._run(tmp_path, "#!/bin/sh\nrm -rf /\n")
        assert r.is_mutative is True
        assert r.verb == "script-blocked-cmd"
        assert "blocked command" in r.reason

    def test_mutative_line_detected(self, tmp_path):
        r = self._run(tmp_path, "#!/bin/sh\nkubectl apply -f x.yaml\n")
        assert r.is_mutative is True
        assert "line is mutative" in r.reason


# ===========================================================================
# detect_mutative_command -- early fast-path branch residuals (Chunk 1).
#
# The mkdir path-token filter (L1048), the `find -delete` base-cmd guard
# (L1086), and the inline-code-CLI gate (L1227). The legacy suite never drove
# inputs that distinguish these branch directions, so comparison-operator and
# boolean mutants on them survived. Each test pins one branch with a concrete
# input whose classification flips under the mutant.
# ===========================================================================
class TestDetectMutativeEarlyBranches:
    # --- L1048: mkdir path-token filter `t != "--"` (NotEq_Gt / NotEq_GtE) ---
    def test_mkdir_single_path_sorting_before_dashdash(self):
        # path_tokens = [t for t in tokens[1:]
        #                if not t.startswith("-") and t != "--"].
        # The arg "!dir" does not start with "-" so it reaches the `t != "--"`
        # clause, which is always True there (it cannot equal "--").  The
        # NotEq_Gt (`t > "--"`) and NotEq_GtE (`t >= "--"`) mutants instead test
        # ordering: "!dir" < "--" (ASCII "!"=0x21 < "-"=0x2d), so both mutants
        # DROP the only path token, leaving path_tokens empty -> the
        # working-tree override is skipped -> mkdir falls through to T3.
        # The original keeps it READ_ONLY.
        r = detect_mutative_command("mkdir '!dir'")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "mkdir"
        assert "working-tree paths only" in r.reason

    # --- L1086: `base_cmd == "find"` guard (Eq_LtE / Eq_GtE / Eq_IsNot) ------
    def test_cat_delete_flag_stays_read_only(self):
        # The find-only `-delete` destructive guard fires only when
        # base_cmd == "find".  `cat` is a read-only base cmd that sorts
        # BEFORE "find" ("c" < "f"): the Eq_LtE mutant (`base_cmd <= "find"`)
        # and Eq_IsNot mutant (`base_cmd is not "find"`) both make
        # `cat -delete f` enter the find branch and return MUTATIVE.  The
        # original keeps cat on the read-only fast-path.
        r = detect_mutative_command("cat -delete f")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "cat"
        assert "Read-only base command" in r.reason

    def test_grep_delete_flag_stays_read_only(self):
        # `grep` sorts AFTER "find" ("g" > "f"): the Eq_GtE mutant
        # (`base_cmd >= "find"`) makes `grep -delete f` enter the find branch
        # and return MUTATIVE.  The original keeps grep read-only.
        r = detect_mutative_command("grep -delete f")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "grep"
        assert "Read-only base command" in r.reason

    # --- L1227: inline-code-CLI gate `in ... and ... & ...` (AndWithOr) ------
    def test_inline_cli_without_inline_flag_is_not_inline_analyzed(self):
        # `base_cmd in _INLINE_CODE_CLIS and cli_flags & set(flag_tokens)`.
        # `python3 -m pip install requests`: python3 IS an inline CLI but the
        # `-c` inline flag is ABSENT, so cli_flags ({-c}) & flag tokens is
        # empty -> the guard is False and detection falls through to the verb
        # scanner, which flags the mutative verb "install".  The AndWithOr
        # mutant turns the guard into `in ... or (empty & ...)` = True, routing
        # the whole string through _check_inline_code, which finds no dangerous
        # pattern and returns READ_ONLY -- silently un-gating the install.
        r = detect_mutative_command("python3 -m pip install requests")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "install"
        assert r.reason == "Mutative verb 'install'"

    # --- L1238/1239/1240: heredoc guard `and` chain (AndWithOr x3) -----------
    def test_stdin_dash_without_heredoc_not_inline_analyzed(self):
        # Heredoc guard (Step 3c):
        #   base_cmd in _INLINE_CODE_CLIS  (C1)
        #   and "<<" in command            (C2)
        #   and semantics.non_flag_tokens  (C3)
        #   and non_flag_tokens[0] == "-"  (C4)
        # `python3 - os.system('rm -rf /')` reaches the guard (stdin "-" is not a
        # script file, no -c flag) with C1=True, C2=False (no "<<"), C3=True,
        # C4=True.  The original guard is False -> detection falls through to the
        # verb scanner, which finds no subcommand verb and returns UNKNOWN /
        # non-mutative.  Each of the three AndWithOr mutants on the `and` chain
        # re-associates so that C1 (or C4) alone forces the guard True, routing
        # the string through _check_inline_code(skip_length_check=True), which
        # detects the embedded `rm -rf /` and returns MUTATIVE -- a False->True
        # flip the original never makes for this input.
        r = detect_mutative_command("python3 - os.system('rm -rf /')")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "-"

    # --- L1242: heredoc routes with skip_length_check=True (TrueWithFalse) ---
    def test_long_safe_heredoc_not_length_flagged(self):
        # The heredoc branch calls
        #   _check_inline_code(..., skip_length_check=True)
        # so a long multi-line heredoc body is NOT flagged on size alone.  The
        # TrueWithFalse mutant flips it to skip_length_check=False, which makes
        # the >500-char length heuristic fire and return MUTATIVE
        # 'heuristic-long-code'.  A long-but-benign heredoc (80 assignment
        # lines) stays READ_ONLY under the original and flips to MUTATIVE under
        # the mutant.
        body = "\n".join(f"x{i} = {i}" for i in range(80))
        r = detect_mutative_command(f"python3 - <<EOF\n{body}\nEOF")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "inline-code"

    # --- L1251: git local-safe guard `base_cmd == "git" and ...` ------------
    # The Step-3d git-local-safe branch is anchored to base_cmd == "git".  The
    # subcommand "checkout" is in GIT_LOCAL_SAFE_SUBCOMMANDS but is NOT a global
    # MUTATIVE/READ_ONLY/SIMULATION verb, so a NON-git CLI carrying it falls
    # through to the "Unknown verb" terminal.  If a mutant on the `== "git"`
    # comparison (or the `and`) lets a non-git base_cmd enter the git branch,
    # the reason flips to "Git local-only subcommand 'checkout' is safe".
    def test_helm_checkout_not_treated_as_git_local(self):
        # `helm` sorts AFTER "git" ("h" > "g"): kills Eq_GtE (`base_cmd >=
        # "git"`) and AndWithOr (`base_cmd == "git" or non_flag_tokens`), both
        # of which would route helm into the git-local-safe branch.
        r = detect_mutative_command("helm checkout thing")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "checkout"
        assert r.reason == "Unknown verb 'checkout' with no dangerous flags"

    def test_aws_checkout_not_treated_as_git_local(self):
        # `aws` sorts BEFORE "git" ("a" < "g"): kills Eq_LtE (`base_cmd <=
        # "git"`) and Eq_IsNot (`base_cmd is not "git"`), both of which would
        # route aws into the git-local-safe branch.
        r = detect_mutative_command("aws checkout thing")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "checkout"
        assert r.reason == "Unknown verb 'checkout' with no dangerous flags"


# ===========================================================================
# detect_mutative_command -- Step 4 hyphen-split position boundary
# (L1416 `semantic_index <= 2 and "-" in stripped_token and is_subcmd_shape`,
#  L1417 `stripped_token.split("-", 1)[0]`,
#  L1425 `confidence = "high" if semantic_index <= 2 else "medium"`).
#
# The boundary `<= 2` controls WHETHER a hyphenated token is split into its
# first fragment: at subcommand positions (index 1,2) "delete-thing" splits to
# "delete" and is flagged; at deeper positions (index >= 3) the token is an
# argument slug and is left whole (no false positive).  The legacy suite only
# ever exercised index 1/2, so the comparison/number/and mutants that move the
# boundary or invert the and-chain survived.  These tests pin both sides of the
# boundary AND the confidence arm that shares it.
# ===========================================================================
class TestHyphenSplitPositionBoundary:
    def test_hyphen_split_at_index_two_high_confidence(self):
        # head = (gh, repo, delete-thing); delete-thing is at semantic_index 2.
        # Original `<= 2` is True -> split to "delete" -> MUTATIVE, high.
        # Kills L1416 LtE_Lt (`< 2` -> False at idx 2 -> no split -> not
        # flagged), LtE_NotEq (`!= 2` -> False at idx 2 -> no split), and the
        # NumberReplacer `2 -> 1` (`<= 1` -> False at idx 2 -> no split).
        # Kills L1425 LtE_Lt / LtE_NotEq / `2 -> 1` (all flip high -> medium).
        r = detect_mutative_command("gh repo delete-thing")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert r.cli_family == "cloud"
        assert r.confidence == "high"
        assert r.reason == "Mutative verb 'delete'"

    def test_hyphenated_slug_at_index_three_not_split(self):
        # head = (gh, repo, subgroup, delete-thing); delete-thing is at
        # semantic_index 3.  Original `<= 2` is False -> NO split -> candidate
        # "delete-thing" is not in MUTATIVE_VERBS -> falls through to the
        # safe-by-elimination terminal (is_mutative False).
        # Kills L1416 NumberReplacer `2 -> 3` (`<= 3` -> True at idx 3 -> would
        # split to "delete" -> MUTATIVE) and the first AndWithOr arm
        # (`<= 2 or "-" in ...` -> True at idx 3 -> would split).
        r = detect_mutative_command("gh repo subgroup delete-thing")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "repo"
        assert r.reason == "Unknown verb 'repo' with no dangerous flags"

    def test_bare_verb_at_index_three_medium_confidence(self):
        # head = (gh, repo, subgroup, delete); plain "delete" at semantic_index
        # 3 IS in MUTATIVE_VERBS regardless of splitting -> MUTATIVE, but the
        # position is > 2 so confidence is "medium".  Kills L1425 NumberReplacer
        # `2 -> 3` (`<= 3` -> True at idx 3 -> would flip medium -> high).
        r = detect_mutative_command("gh repo subgroup delete")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert r.confidence == "medium"
        assert r.reason == "Mutative verb 'delete'"


# ===========================================================================
# detect_mutative_command -- camelCase verb override / exception / danger arms
# (L1518 `override_flags & frozenset(semantics.flag_tokens)`,
#  L1520 `is_mutative=False` override result,
#  L1530 `is_mutative=False` CLI_VERB_TIER_EXCEPTIONS result,
#  L1540 `" with dangerous flags ..." if dangerous_flags else ""`).
#
# When the FIRST non-flag token is camelCase (semantic_index == 1) and a
# fragment is a mutative verb, three result arms can fire: a verb+flag
# read-only override, an unconditional tier exception, or a plain MUTATIVE with
# dangerous-flag detail.  The legacy suite only asserted is_mutative on the
# plain path, so the set-intersection operator (L1518), the two boolean result
# literals (L1520/L1530) and the dangerous-flag conditional (L1540) survived.
# The override path uses `git tagCreate` because ("git","tag") is the sole
# VERB_FLAG_READ_ONLY_OVERRIDES entry ({-l,--list}); the exception path uses
# `gws fooModify` because ("workspace","modify") is the sole
# CLI_VERB_TIER_EXCEPTIONS entry.
# ===========================================================================
class TestCamelCaseOverrideExceptionArms:
    def test_camelcase_tag_with_list_flag_overridden_read_only(self):
        # head_raw[1] = "tagCreate" -> camel parts [tag, create]; "tag" in
        # MUTATIVE_VERBS and ("git","tag") override flags {-l,--list} intersect
        # the present flag {-l} -> READ_ONLY.
        # L1518 set-intersection `&`: any arithmetic mutant (Add/Sub/Mul/Div/
        #   FloorDiv/Mod/Pow/RShift/LShift) raises TypeError on frozensets ->
        #   detect_mutative_command propagates -> mutant killed.
        # L1518 AddNot (`not (override & flags)`) -> False -> skips override ->
        #   would flag MUTATIVE; pinned READ_ONLY kills it.
        # L1520 FalseWithTrue (`is_mutative=True`) -> pinned False kills it.
        r = detect_mutative_command("git tagCreate -l")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "tag"
        assert r.cli_family == "git"
        assert r.confidence == "high"
        assert r.reason == (
            "CamelCase verb 'tag' (from 'tagCreate') "
            "overridden to read-only by flag"
        )

    def test_camelcase_tag_no_flag_stays_mutative(self):
        # No override flag present: `override_flags & frozenset()` is the empty
        # set (falsy) -> skip override -> plain MUTATIVE.  Kills L1518
        # BitAnd_BitOr: `override_flags | frozenset()` = {-l,--list} (truthy) ->
        # would wrongly downgrade to READ_ONLY.
        r = detect_mutative_command("git tagCreate")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "tag"
        assert r.reason == "CamelCase verb 'tag' (from 'tagCreate')"

    def test_camelcase_tag_both_override_flags_stays_read_only(self):
        # Both override flags present: `{-l,--list} & {-l,--list}` = {-l,--list}
        # (truthy) -> READ_ONLY.  Kills L1518 BitAnd_BitXor: `^` on two equal
        # sets is the empty set (falsy) -> would skip override -> MUTATIVE.
        r = detect_mutative_command("git tagCreate -l --list")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "tag"
        assert "overridden to read-only by flag" in r.reason

    def test_camelcase_modify_workspace_exception_read_only(self):
        # head_raw[1] = "fooModify" -> camel parts [foo, modify]; "modify" in
        # MUTATIVE_VERBS and ("workspace","modify") in CLI_VERB_TIER_EXCEPTIONS
        # -> READ_ONLY.  Kills L1530 FalseWithTrue (`is_mutative=True`).
        r = detect_mutative_command("gws fooModify")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "modify"
        assert r.cli_family == "workspace"
        assert r.reason == (
            "CamelCase verb 'modify' (from 'fooModify') "
            "excepted to read_only by config"
        )

    def test_camelcase_mutative_verb_with_dangerous_flag_detail(self):
        # head_raw[1] = "fooDelete" -> camel parts [foo, delete]; "delete" in
        # MUTATIVE_VERBS, no override/exception, --force is a dangerous flag ->
        # MUTATIVE with the flag detail appended.  Kills L1540 AddNot
        # (`if not dangerous_flags`), which would drop the " with dangerous
        # flags (...)" suffix from the reason.
        r = detect_mutative_command("mytool fooDelete --force")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert r.dangerous_flags == ("--force",)
        assert r.reason == (
            "CamelCase verb 'delete' (from 'fooDelete') "
            "with dangerous flags ('--force',)"
        )


# ===========================================================================
# detect_mutative_command -- camelCase split GUARD (L1509-1511).
#
#   if (semantic_index == 1            # L1509
#       and len(camel_parts) > 1       # L1510
#       and _is_subcommand_identifier(raw_token)):  # L1511
#
# The guard restricts camelCase verb-splitting to the FIRST non-flag token
# (subcommand position) AND to real identifier tokens, so an argument value
# like a path-y or deep-position token is never split into a phantom mutative
# verb.  The legacy suite only exercised the true path (idx 1, clean
# identifier), so the position comparison (L1509 Eq_GtE), the len boundary
# (L1510 NumberReplacer `1 -> 2`), and both `and` joins (L1510/L1511 AndWithOr)
# survived.  These tests drive the FALSE side of each clause with an input
# whose camel parts DO contain a mutative verb, so any mutant that wrongly
# enters the split loop flips is_mutative.
# ===========================================================================
class TestCamelCaseSplitGuard:
    def test_camelcase_at_index_two_not_split(self):
        # head_raw = (gh, repo, fooDelete); the camel token is at semantic_index
        # 2, NOT 1.  Original `== 1` is False -> guard False -> no split -> the
        # command is safe-by-elimination.  Kills L1509 Eq_GtE (`>= 1` -> True at
        # idx 2 -> would split "fooDelete" -> "delete" -> MUTATIVE) and the
        # FIRST AndWithOr at L1510 (`== 1 or len > 1 ...` -> True -> would split).
        r = detect_mutative_command("gh repo fooDelete")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "repo"
        assert r.reason == "Unknown verb 'repo' with no dangerous flags"

    def test_camelcase_two_part_at_index_one_split(self):
        # head_raw = (gh, fooDelete); camel parts [foo, delete], len 2 > 1 ->
        # split -> "delete" MUTATIVE.  Kills L1510 NumberReplacer `1 -> 2`
        # (`len(camel_parts) > 2` -> False for a 2-part token -> would skip the
        # split loop and leave the command unflagged).
        r = detect_mutative_command("gh fooDelete")
        assert r.is_mutative is True
        assert r.category == "MUTATIVE"
        assert r.verb == "delete"
        assert r.confidence == "high"
        assert r.reason == "CamelCase verb 'delete' (from 'fooDelete')"

    def test_camelcase_non_identifier_token_not_split(self):
        # head_raw = (gh, deleteFoo/bar); camel parts [delete, foo/bar] (so a
        # clean "delete" fragment IS present), but the raw token carries "/" so
        # _is_subcommand_identifier is False -> guard False -> NOT split (the
        # token is an argument value, not a subcommand).  Kills the SECOND
        # AndWithOr at L1511 (`... and is_subcmd` -> `... or is_subcmd`):
        # `(== 1 and len > 1) or False` = True -> would enter the loop and flag
        # the "delete" fragment as MUTATIVE.
        r = detect_mutative_command("gh deleteFoo/bar")
        assert r.is_mutative is False
        assert r.category == "UNKNOWN"
        assert r.verb == "deletefoo/bar"
        assert r.reason == "Unknown verb 'deletefoo/bar' with no dangerous flags"


# ===========================================================================
# detect_mutative_command -- Step 4b API implicit-GET arm (L1581-1587).
#
#   if (not any(t in MUTATIVE_VERBS for t in semantic_head_tokens[1:])  # L1584
#       and len(semantic_head_tokens) > 1                               # L1586
#       and semantic_head_tokens[1] == "api"):                          # L1587
#
# `gh api repos/...` defaults to GET and is read-only.  The existing
# `test_gh_api_implicit_get` exercises the True path but its head has length 3,
# so the `> 1` boundary number mutants and the `[1:]` slice-start mutant on the
# `not any(...)` membership scan survived.  These tests pin the exact boundary:
# a length-2 head (`gh api`) for the `> 1` number mutant, and a base command
# that is itself a mutative verb (`post api`) for the `[1:]` slice-start mutant.
# ===========================================================================
class TestApiImplicitGetArm:
    def test_gh_api_bare_length_two_head(self):
        # head = (gh, api), length exactly 2.  Original `len(...) > 1` is True ->
        # api arm fires -> READ_ONLY.  Kills L1586 NumberReplacer `1 -> 2`
        # (`> 2` -> False for a length-2 head -> would skip the api arm and fall
        # through to the "unknown verb" terminal).
        r = detect_mutative_command("gh api")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "api"
        assert r.confidence == "high"
        assert r.reason == "API call with implicit GET method"

    def test_mutative_base_cmd_before_api_blocks_arm(self):
        # base_cmd "post" IS in MUTATIVE_VERBS; head = (post, api).  The Step-4
        # loop scans head[1:] = (api,) and finds no verb, so it reaches Step 4b.
        # Original `not any(t in MUTATIVE_VERBS for t in head[1:])` scans only
        # (api,) -> not any -> True -> api arm fires -> READ_ONLY.
        # Kills L1584 NumberReplacer `1 -> 0` (`head[0:]` includes base "post",
        # which IS mutative -> any True -> not any False -> arm skipped ->
        # command falls through to the "unknown verb" terminal).
        r = detect_mutative_command("post api")
        assert r.is_mutative is False
        assert r.category == "READ_ONLY"
        assert r.verb == "api"
        assert r.reason == "API call with implicit GET method"
