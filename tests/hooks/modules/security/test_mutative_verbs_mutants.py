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
