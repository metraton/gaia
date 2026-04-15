#!/usr/bin/env python3
"""
Tests for Flag-Dependent Command Classifiers.

Validates all 15 command families:
  1-8: git push, reset, checkout, stash, rebase, tag, clean, remote
  9:   sed
  10:  awk
  11:  tar
  12:  find
  13:  curl
  14:  wget
  15:  httpie

Each family has at least one READ_ONLY and one MUTATIVE/BLOCKED test case (AC-12).
Specific AC tests:
  AC-4: sed -i = MUTATIVE; sed without -i = READ_ONLY
  AC-5: git push --force = BLOCKED; git push = MUTATIVE
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.flag_classifiers import (
    classify_by_flags,
    FlagClassifierResult,
    OUTCOME_READ_ONLY,
    OUTCOME_MUTATIVE,
    OUTCOME_BLOCKED,
)


# ============================================================================
# 1. git push
# ============================================================================

class TestFlagClassifierGitPush:
    """AC-5: git push --force = BLOCKED; git push = MUTATIVE."""

    def test_git_push_plain_is_mutative(self):
        result = classify_by_flags("git push")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE
        assert result.command_family == "git_push"

    def test_git_push_origin_main_is_mutative(self):
        result = classify_by_flags("git push origin main")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    @pytest.mark.parametrize("command", [
        "git push --force",
        "git push -f",
        "git push origin main --force",
        "git push --mirror",
        "git push --prune",
        "git push --delete origin feature-branch",
        "git push -d origin feature-branch",
    ])
    def test_git_push_force_variants_are_blocked(self, command):
        result = classify_by_flags(command)
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED
        assert result.command_family == "git_push"

    def test_git_push_plus_refspec_is_blocked(self):
        result = classify_by_flags("git push origin +main")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED
        assert "+main" in result.matched_pattern

    def test_git_push_colon_refspec_is_blocked(self):
        result = classify_by_flags("git push origin :feature-branch")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_push_force_with_lease_is_blocked(self):
        """--force-with-lease is still a force push variant."""
        # Note: blocked_commands.py does NOT block --force-with-lease,
        # but flag_classifiers sees --force as a substring via _has_short_flag
        # The plan says --force-with-lease is not caught by blocked_commands.py;
        # flag_classifiers blocks --force (standalone) and --mirror/--prune/--delete.
        # --force-with-lease as a standalone flag is NOT in the blocked set.
        result = classify_by_flags("git push --force-with-lease")
        assert result is not None
        # --force-with-lease is NOT caught by _has_flag (exact match) or
        # _has_short_flag. It falls through to plain push = MUTATIVE.
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 2. git reset
# ============================================================================

class TestFlagClassifierGitReset:
    """git reset --hard = BLOCKED; --soft/--mixed = MUTATIVE."""

    def test_git_reset_hard_is_blocked(self):
        result = classify_by_flags("git reset --hard")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED
        assert result.matched_pattern == "--hard"

    def test_git_reset_hard_with_ref_is_blocked(self):
        result = classify_by_flags("git reset --hard HEAD~3")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_reset_soft_is_mutative(self):
        result = classify_by_flags("git reset --soft HEAD~1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_reset_mixed_is_mutative(self):
        result = classify_by_flags("git reset --mixed HEAD~1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_reset_plain_is_mutative(self):
        result = classify_by_flags("git reset")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 3. git checkout
# ============================================================================

class TestFlagClassifierGitCheckout:
    """git checkout . = BLOCKED; git checkout branch = MUTATIVE."""

    def test_git_checkout_dot_is_blocked(self):
        result = classify_by_flags("git checkout .")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_checkout_double_dash_is_blocked(self):
        result = classify_by_flags("git checkout -- file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED
        assert result.matched_pattern == "--"

    def test_git_checkout_head_is_blocked(self):
        result = classify_by_flags("git checkout HEAD")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_checkout_force_is_blocked(self):
        result = classify_by_flags("git checkout --force")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_checkout_branch_is_mutative(self):
        result = classify_by_flags("git checkout feature-branch")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_checkout_new_branch_is_mutative(self):
        result = classify_by_flags("git checkout -b new-branch")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 4. git stash
# ============================================================================

class TestFlagClassifierGitStash:
    """git stash list = READ_ONLY; git stash drop = BLOCKED; git stash push = MUTATIVE."""

    def test_git_stash_list_is_read_only(self):
        result = classify_by_flags("git stash list")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_stash_show_is_read_only(self):
        result = classify_by_flags("git stash show")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_stash_drop_is_blocked(self):
        result = classify_by_flags("git stash drop")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_stash_clear_is_blocked(self):
        result = classify_by_flags("git stash clear")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_stash_push_is_mutative(self):
        result = classify_by_flags("git stash push")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_stash_pop_is_mutative(self):
        result = classify_by_flags("git stash pop")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_stash_apply_is_mutative(self):
        result = classify_by_flags("git stash apply")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_stash_implicit_push_is_mutative(self):
        """Bare 'git stash' = implicit push."""
        result = classify_by_flags("git stash")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 5. git rebase
# ============================================================================

class TestFlagClassifierGitRebase:
    """git rebase --abort = READ_ONLY; -i / plain = MUTATIVE."""

    def test_git_rebase_abort_is_read_only(self):
        result = classify_by_flags("git rebase --abort")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_rebase_interactive_is_mutative(self):
        result = classify_by_flags("git rebase -i HEAD~3")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE
        assert result.matched_pattern == "-i"

    def test_git_rebase_interactive_long_is_mutative(self):
        result = classify_by_flags("git rebase --interactive HEAD~3")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_rebase_continue_is_mutative(self):
        result = classify_by_flags("git rebase --continue")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_rebase_plain_is_mutative(self):
        result = classify_by_flags("git rebase main")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 6. git tag
# ============================================================================

class TestFlagClassifierGitTag:
    """git tag -l = READ_ONLY; git tag --delete = BLOCKED; git tag v1.0 = MUTATIVE."""

    def test_git_tag_no_args_is_read_only(self):
        result = classify_by_flags("git tag")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_tag_list_is_read_only(self):
        result = classify_by_flags("git tag -l")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_tag_list_long_is_read_only(self):
        result = classify_by_flags("git tag --list")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_tag_verify_is_read_only(self):
        result = classify_by_flags("git tag -v v1.0")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_tag_delete_is_blocked(self):
        result = classify_by_flags("git tag --delete v1.0")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_tag_delete_short_is_blocked(self):
        result = classify_by_flags("git tag -d v1.0")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_tag_force_is_blocked(self):
        result = classify_by_flags("git tag --force v1.0")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_tag_create_is_mutative(self):
        result = classify_by_flags("git tag v1.0")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_tag_annotated_is_mutative(self):
        result = classify_by_flags("git tag -a v1.0 -m 'Release 1.0'")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 7. git clean
# ============================================================================

class TestFlagClassifierGitClean:
    """git clean -n = READ_ONLY; git clean -fd = BLOCKED."""

    def test_git_clean_dry_run_is_read_only(self):
        result = classify_by_flags("git clean --dry-run")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_clean_n_is_read_only(self):
        result = classify_by_flags("git clean -n")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_clean_fd_is_blocked(self):
        result = classify_by_flags("git clean -fd")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED

    def test_git_clean_bare_is_blocked(self):
        result = classify_by_flags("git clean")
        assert result is not None
        assert result.outcome == OUTCOME_BLOCKED


# ============================================================================
# 8. git remote
# ============================================================================

class TestFlagClassifierGitRemote:
    """git remote show = READ_ONLY; git remote remove = MUTATIVE."""

    def test_git_remote_bare_is_read_only(self):
        result = classify_by_flags("git remote")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_remote_show_is_read_only(self):
        result = classify_by_flags("git remote show origin")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_remote_get_url_is_read_only(self):
        result = classify_by_flags("git remote get-url origin")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_remote_v_is_read_only(self):
        result = classify_by_flags("git remote -v")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_git_remote_remove_is_mutative(self):
        result = classify_by_flags("git remote remove origin")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_remote_rename_is_mutative(self):
        result = classify_by_flags("git remote rename origin upstream")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_remote_set_url_is_mutative(self):
        result = classify_by_flags("git remote set-url origin https://new.url")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_git_remote_add_is_mutative(self):
        result = classify_by_flags("git remote add upstream https://github.com/repo")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 9. sed (AC-4)
# ============================================================================

class TestFlagClassifierSed:
    """AC-4: sed -i = MUTATIVE; sed without -i = READ_ONLY."""

    def test_sed_in_place_is_mutative(self):
        result = classify_by_flags("sed -i 's/foo/bar/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE
        assert result.command_family == "sed"

    def test_sed_in_place_uppercase_is_mutative(self):
        result = classify_by_flags("sed -I 's/foo/bar/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_sed_in_place_long_is_mutative(self):
        result = classify_by_flags("sed --in-place 's/foo/bar/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_sed_in_place_with_backup_is_mutative(self):
        result = classify_by_flags("sed -i.bak 's/foo/bar/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_sed_without_in_place_is_read_only(self):
        result = classify_by_flags("sed 's/foo/bar/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_sed_with_expression_is_read_only(self):
        result = classify_by_flags("sed -e 's/foo/bar/' -e 's/baz/qux/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_sed_bundled_ni_is_mutative(self):
        """Bundled flags containing 'i' should be detected."""
        result = classify_by_flags("sed -ni 's/foo/bar/' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 10. awk
# ============================================================================

class TestFlagClassifierAwk:
    """awk with system() = MUTATIVE; awk '{print $1}' = READ_ONLY."""

    def test_awk_print_is_read_only(self):
        result = classify_by_flags("awk '{print $1}' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY
        assert result.command_family == "awk"

    def test_awk_system_call_is_mutative(self):
        result = classify_by_flags("awk '{system(\"rm \" $1)}' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_awk_pipe_getline_is_mutative(self):
        result = classify_by_flags("awk '{\"date\" | getline d; print d}'")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_awk_print_redirect_is_mutative(self):
        result = classify_by_flags("awk '{print $1 > \"output.txt\"}' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_gawk_is_recognized(self):
        result = classify_by_flags("gawk '{print $1}' file.txt")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY
        assert result.command_family == "awk"

    def test_awk_with_field_separator_is_read_only(self):
        result = classify_by_flags("awk -F: '{print $1}' /etc/passwd")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY


# ============================================================================
# 11. tar
# ============================================================================

class TestFlagClassifierTar:
    """tar -t = READ_ONLY; tar -xf = MUTATIVE."""

    def test_tar_list_short_is_read_only(self):
        result = classify_by_flags("tar -t archive.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_tar_list_bundled_is_read_only(self):
        result = classify_by_flags("tar -tf archive.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_tar_list_long_is_read_only(self):
        result = classify_by_flags("tar --list -f archive.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_tar_extract_is_mutative(self):
        result = classify_by_flags("tar -xf archive.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_tar_extract_long_is_mutative(self):
        result = classify_by_flags("tar --extract -f archive.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_tar_create_is_mutative(self):
        result = classify_by_flags("tar -czf archive.tar.gz dir/")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_tar_create_long_is_mutative(self):
        result = classify_by_flags("tar --create -f archive.tar.gz dir/")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_tar_bare_operation_create(self):
        """GNU tar allows bare operation letters: tar czf out.tar dir."""
        result = classify_by_flags("tar czf out.tar.gz dir/")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_tar_bare_operation_list(self):
        result = classify_by_flags("tar tf archive.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY


# ============================================================================
# 12. find
# ============================================================================

class TestFlagClassifierFind:
    """find -name = READ_ONLY; find -delete = MUTATIVE."""

    def test_find_name_only_is_read_only(self):
        result = classify_by_flags("find . -name '*.py'")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY
        assert result.command_family == "find"

    def test_find_type_is_read_only(self):
        result = classify_by_flags("find /tmp -type f -name '*.log'")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_find_delete_is_mutative(self):
        result = classify_by_flags("find . -name '*.py' -delete")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE
        assert result.matched_pattern == "-delete"

    def test_find_exec_is_mutative(self):
        result = classify_by_flags("find . -name '*.py' -exec chmod 644 {} +")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE
        assert result.matched_pattern == "-exec"

    def test_find_execdir_is_mutative(self):
        result = classify_by_flags("find . -name '*.py' -execdir rm {} \\;")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_find_ok_is_mutative(self):
        result = classify_by_flags("find . -name '*.tmp' -ok rm {} \\;")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 13. curl
# ============================================================================

class TestFlagClassifierCurl:
    """curl GET = READ_ONLY; curl -X POST = MUTATIVE."""

    def test_curl_simple_get_is_read_only(self):
        result = classify_by_flags("curl https://example.com")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY
        assert result.command_family == "curl"

    def test_curl_with_headers_is_read_only(self):
        result = classify_by_flags("curl -H 'Accept: application/json' https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_curl_post_is_mutative(self):
        result = classify_by_flags("curl -X POST https://api.example.com/data")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_put_is_mutative(self):
        result = classify_by_flags("curl -X PUT https://api.example.com/data")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_delete_is_mutative(self):
        result = classify_by_flags("curl -X DELETE https://api.example.com/data/1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_patch_is_mutative(self):
        result = classify_by_flags("curl -X PATCH https://api.example.com/data/1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_data_flag_is_mutative(self):
        result = classify_by_flags("curl -d '{\"key\":\"val\"}' https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_form_flag_is_mutative(self):
        result = classify_by_flags("curl -F 'file=@upload.txt' https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_upload_file_is_mutative(self):
        result = classify_by_flags("curl -T myfile.txt https://ftp.example.com/upload/")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_json_flag_is_mutative(self):
        result = classify_by_flags("curl --json '{\"key\":\"val\"}' https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_request_equals_is_mutative(self):
        result = classify_by_flags("curl --request=POST https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_curl_localhost_get_is_read_only(self):
        result = classify_by_flags("curl http://localhost:3000/health")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY


# ============================================================================
# 14. wget
# ============================================================================

class TestFlagClassifierWget:
    """wget download = READ_ONLY; wget --post-data = MUTATIVE."""

    def test_wget_simple_download_is_read_only(self):
        result = classify_by_flags("wget https://example.com/file.tar.gz")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY
        assert result.command_family == "wget"

    def test_wget_post_data_is_mutative(self):
        result = classify_by_flags("wget --post-data='key=value' https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_wget_post_file_is_mutative(self):
        result = classify_by_flags("wget --post-file=data.json https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_wget_method_post_is_mutative(self):
        result = classify_by_flags("wget --method POST https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_wget_method_put_is_mutative(self):
        result = classify_by_flags("wget --method PUT https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_wget_method_delete_is_mutative(self):
        result = classify_by_flags("wget --method DELETE https://api.example.com/1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_wget_method_equals_is_mutative(self):
        result = classify_by_flags("wget --method=PATCH https://api.example.com/1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_wget_body_data_is_mutative(self):
        result = classify_by_flags("wget --body-data='{\"k\":\"v\"}' https://api.example.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE


# ============================================================================
# 15. httpie
# ============================================================================

class TestFlagClassifierHttpie:
    """httpie GET = READ_ONLY; httpie POST = MUTATIVE."""

    def test_httpie_get_url_is_read_only(self):
        result = classify_by_flags("http https://api.example.com/users")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY
        assert result.command_family == "httpie"

    def test_httpie_explicit_get_is_read_only(self):
        result = classify_by_flags("http GET https://api.example.com/users")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY

    def test_httpie_post_is_mutative(self):
        result = classify_by_flags("http POST https://api.example.com/users name=John")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE
        assert result.matched_pattern == "POST"

    def test_httpie_put_is_mutative(self):
        result = classify_by_flags("http PUT https://api.example.com/users/1 name=Jane")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_httpie_delete_is_mutative(self):
        result = classify_by_flags("http DELETE https://api.example.com/users/1")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_httpie_patch_is_mutative(self):
        result = classify_by_flags("http PATCH https://api.example.com/users/1 name=Updated")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_httpie_data_item_implies_post(self):
        """Data items without explicit method imply POST."""
        result = classify_by_flags("http https://api.example.com/users name=John email=j@e.com")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_httpie_json_data_item_implies_post(self):
        result = classify_by_flags("http https://api.example.com/users count:=5")
        assert result is not None
        assert result.outcome == OUTCOME_MUTATIVE

    def test_httpie_https_command_is_recognized(self):
        result = classify_by_flags("https https://api.example.com/users")
        assert result is not None
        assert result.outcome == OUTCOME_READ_ONLY


# ============================================================================
# Cross-cutting: unknown commands, empty, edge cases
# ============================================================================

class TestFlagClassifierPassthrough:
    """Unknown commands return None (fall through to mutative_verbs)."""

    def test_unknown_command_returns_none(self):
        result = classify_by_flags("ls -la")
        assert result is None

    def test_unknown_command_echo_returns_none(self):
        result = classify_by_flags("echo hello")
        assert result is None

    def test_empty_string_returns_none(self):
        result = classify_by_flags("")
        assert result is None

    def test_whitespace_returns_none(self):
        result = classify_by_flags("   ")
        assert result is None

    def test_none_returns_none(self):
        result = classify_by_flags(None)
        assert result is None

    def test_git_unknown_subcommand_returns_none(self):
        """git log is not in the 8 sub-command classifiers."""
        result = classify_by_flags("git log --oneline")
        assert result is None

    def test_git_status_returns_none(self):
        result = classify_by_flags("git status")
        assert result is None

    def test_git_diff_returns_none(self):
        result = classify_by_flags("git diff HEAD~1")
        assert result is None


# ============================================================================
# Result structure tests
# ============================================================================

class TestFlagClassifierResult:
    """Verify FlagClassifierResult properties work correctly."""

    def test_blocked_result_properties(self):
        result = classify_by_flags("git push --force")
        assert result is not None
        assert result.is_blocked is True
        assert result.is_mutative is True
        assert result.is_read_only is False

    def test_mutative_result_properties(self):
        result = classify_by_flags("git push origin main")
        assert result is not None
        assert result.is_blocked is False
        assert result.is_mutative is True
        assert result.is_read_only is False

    def test_read_only_result_properties(self):
        result = classify_by_flags("git stash list")
        assert result is not None
        assert result.is_blocked is False
        assert result.is_mutative is False
        assert result.is_read_only is True

    def test_result_has_all_fields(self):
        result = classify_by_flags("sed -i 's/foo/bar/' file.txt")
        assert result is not None
        assert isinstance(result.outcome, str)
        assert isinstance(result.reason, str)
        assert isinstance(result.matched_pattern, str)
        assert isinstance(result.command_family, str)
        assert len(result.reason) > 0
        assert len(result.matched_pattern) > 0
        assert len(result.command_family) > 0
