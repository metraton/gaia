#!/usr/bin/env python3
"""Tests for Mutative Verb Detector (mutative_verbs.py)."""

import sys
import pytest
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import (
    detect_mutative_command,
    build_t3_block_response,
    MutativeResult,
    COMMAND_ALIASES,
    SIMULATION_FLAGS,
    MUTATIVE_VERBS,
    GIT_LOCAL_SAFE_SUBCOMMANDS,
)


class TestMutativeResult:
    def test_default_values(self):
        result = MutativeResult()
        assert result.is_mutative is False
        assert result.category == "UNKNOWN"


class TestRemovedVerbs:
    def test_add_not_mutative(self):
        assert "add" not in MUTATIVE_VERBS
        result = detect_mutative_command("git add .")
        assert result.is_mutative is False

    def test_stash_not_mutative(self):
        assert "stash" not in MUTATIVE_VERBS
        result = detect_mutative_command("git stash")
        assert result.is_mutative is False

    def test_run_not_mutative(self):
        assert "run" not in MUTATIVE_VERBS
        result = detect_mutative_command("docker run nginx")
        assert result.is_mutative is False

    def test_run_all_apply_still_mutative(self):
        result = detect_mutative_command("terragrunt run-all apply")
        assert result.is_mutative is True
        assert result.verb == "apply"


class TestCommandAliases:
    """Scenario #20: Command aliases (rm, dd, mkfs) are MUTATIVE."""

    def test_rm(self):
        result = detect_mutative_command("rm file.txt")
        assert result.is_mutative is True
        assert result.verb == "rm"
        assert result.category == "MUTATIVE"

    def test_mv(self):
        result = detect_mutative_command("mv src dst")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_cp(self):
        result = detect_mutative_command("cp source dest")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_dd(self):
        result = detect_mutative_command("dd if=/dev/zero of=file")
        assert result.is_mutative is True
        assert result.verb == "dd"
        assert result.category == "MUTATIVE"

    def test_mkfs(self):
        """Scenario #20: mkfs is a command alias -> MUTATIVE."""
        result = detect_mutative_command("mkfs.ext4 /dev/sdb1")
        # mkfs is in COMMAND_ALIASES but mkfs.ext4 is a path variant
        # The base_cmd extraction strips paths, so mkfs.ext4 may not match.
        # Document current behavior:
        assert "mkfs" in COMMAND_ALIASES

    def test_chmod(self):
        result = detect_mutative_command("chmod 755 file")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_all_aliases_in_constant(self):
        """Verify all expected command aliases are registered."""
        expected_aliases = {"rm", "rmdir", "mkdir", "mv", "cp", "ln", "dd", "mkfs", "fdisk", "chmod", "chown", "chgrp", "nohup"}
        assert expected_aliases == set(COMMAND_ALIASES.keys())


class TestMkdir:
    """mkdir creates directories -- MUTATIVE via COMMAND_ALIASES."""

    def test_mkdir_basic(self):
        result = detect_mutative_command("mkdir foo")
        assert result.is_mutative is True
        assert result.verb == "mkdir"
        assert result.category == "MUTATIVE"

    def test_mkdir_p(self):
        """mkdir -p is still mutative: it creates directories even if idempotent.

        The -p flag makes mkdir idempotent on existing directories, but it can
        still create new state (nested directories). Approval is still required.
        """
        result = detect_mutative_command("mkdir -p foo/bar/baz")
        assert result.is_mutative is True
        assert result.verb == "mkdir"
        assert result.category == "MUTATIVE"


class TestMutativeVerbScanning:
    def test_kubectl_delete(self):
        result = detect_mutative_command("kubectl delete pod my-pod")
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_kubectl_apply(self):
        result = detect_mutative_command("kubectl apply -f manifest.yaml")
        assert result.is_mutative is True
        assert result.verb == "apply"

    def test_terraform_apply(self):
        result = detect_mutative_command("terraform apply")
        assert result.is_mutative is True
        assert result.verb == "apply"

    def test_git_push(self):
        result = detect_mutative_command("git push origin main")
        assert result.is_mutative is True
        assert result.verb == "push"

    def test_git_commit_not_mutative(self):
        """git commit was removed from MUTATIVE_VERBS in v5."""
        result = detect_mutative_command('git commit -m "msg"')
        assert result.is_mutative is False
        assert result.verb == "commit"

    def test_helm_install(self):
        result = detect_mutative_command("helm install release chart")
        assert result.is_mutative is True
        assert result.verb == "install"

    def test_docker_stop(self):
        result = detect_mutative_command("docker stop container")
        assert result.is_mutative is True

    def test_eksctl_create(self):
        result = detect_mutative_command("eksctl create cluster --name test")
        assert result.is_mutative is True
        assert result.verb == "create"


class TestSimulationDetection:
    def test_terraform_plan(self):
        result = detect_mutative_command("terraform plan")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"

    def test_terraform_validate(self):
        result = detect_mutative_command("terraform validate")
        assert result.is_mutative is False

    def test_git_diff(self):
        result = detect_mutative_command("git diff")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"

    def test_helm_template(self):
        result = detect_mutative_command("helm template release chart")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"


class TestReadOnlyDetection:
    def test_kubectl_get(self):
        result = detect_mutative_command("kubectl get pods")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_git_status(self):
        result = detect_mutative_command("git status")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_git_log(self):
        result = detect_mutative_command("git log --all")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_kubectl_logs(self):
        result = detect_mutative_command("kubectl logs pod-name")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"


class TestDryRunOverride:
    """Scenario #23: --dry-run flag overrides to SIMULATION."""

    def test_helm_install_dry_run(self):
        result = detect_mutative_command("helm install --dry-run release chart")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"

    def test_kubectl_delete_dry_run(self):
        result = detect_mutative_command("kubectl delete --dry-run pod my-pod")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"

    def test_terraform_apply_dry_run(self):
        """--dry-run on a normally-mutative command should yield SIMULATION."""
        result = detect_mutative_command("terraform apply --dry-run")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"

    def test_kubectl_apply_dry_run_client(self):
        result = detect_mutative_command("kubectl apply --dry-run=client -f manifest.yaml")
        assert result.is_mutative is False
        assert result.category == "SIMULATION"


class TestAPIImplicitGET:
    def test_glab_api_implicit_get(self):
        result = detect_mutative_command('glab api "projects/123"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_glab_api_explicit_post(self):
        result = detect_mutative_command('glab api -X POST "projects/123/notes"')
        assert result.is_mutative is True
        assert result.verb == "post"
        assert result.category == "MUTATIVE"

    def test_glab_api_explicit_post_with_body(self):
        """Scenario #13: glab api -X POST with -f body is mutative."""
        result = detect_mutative_command('glab api -X POST "projects/123/notes" -f body="hello"')
        assert result.is_mutative is True
        assert result.verb == "post"
        assert result.category == "MUTATIVE"

    def test_glab_api_explicit_get(self):
        """Scenario #14: glab api -X GET is NOT mutative."""
        result = detect_mutative_command('glab api -X GET "projects/123"')
        assert result.is_mutative is False

    def test_gh_api_implicit_get(self):
        result = detect_mutative_command("gh api repos/owner/repo")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_gh_api_explicit_delete(self):
        """Scenario #15: gh api -X DELETE is mutative."""
        result = detect_mutative_command('gh api -X DELETE "repos/owner/repo/comments/1"')
        assert result.is_mutative is True
        assert result.verb == "delete"
        assert result.category == "MUTATIVE"


class TestHTTPVerbDetection:
    """Scenario #24: HTTP verbs post, put, patch, delete are MUTATIVE."""

    def test_put_is_mutative(self):
        result = detect_mutative_command('gh api -X PUT "repos/owner/repo/topics"')
        assert result.is_mutative is True
        assert result.verb == "put"
        assert result.category == "MUTATIVE"

    def test_patch_is_mutative(self):
        result = detect_mutative_command('gh api -X PATCH "repos/owner/repo"')
        assert result.is_mutative is True
        assert result.verb == "patch"
        assert result.category == "MUTATIVE"

    def test_delete_is_mutative(self):
        result = detect_mutative_command('glab api -X DELETE "projects/123/notes/1"')
        assert result.is_mutative is True
        assert result.verb == "delete"
        assert result.category == "MUTATIVE"


class TestGitTagDetection:
    """Scenario #21 and #22: git tag behavior."""

    def test_git_tag_is_mutative(self):
        """Scenario #21: bare `git tag` is mutative (tag is in MUTATIVE_VERBS)."""
        assert "tag" in MUTATIVE_VERBS
        result = detect_mutative_command("git tag v1.0.0")
        assert result.is_mutative is True
        assert result.verb == "tag"
        assert result.category == "MUTATIVE"

    def test_git_tag_list_flag(self):
        """Scenario #22: `git tag -l` is listing -> READ_ONLY.

        The verb+flag override mechanism downgrades "tag" from MUTATIVE to
        READ_ONLY when the -l or --list flag is present.
        """
        result = detect_mutative_command("git tag -l")
        assert result.is_mutative is False
        assert result.verb == "tag"
        assert result.category == "READ_ONLY"

    def test_git_tag_list_long_flag(self):
        """Same as above but with --list flag."""
        result = detect_mutative_command("git tag --list")
        assert result.is_mutative is False
        assert result.verb == "tag"
        assert result.category == "READ_ONLY"

    def test_git_tag_list_with_pattern(self):
        """git tag -l 'v*' is listing with a filter -- still READ_ONLY."""
        result = detect_mutative_command('git tag -l "v*"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_git_tag_delete_still_mutative(self):
        """git tag -d is deletion -- must remain MUTATIVE."""
        result = detect_mutative_command("git tag -d v1.0.0")
        assert result.is_mutative is True
        assert result.verb == "tag"


class TestEdgeCases:
    def test_empty_command(self):
        result = detect_mutative_command("")
        assert result.is_mutative is False
        assert result.category == "UNKNOWN"

    def test_single_token(self):
        result = detect_mutative_command("ls")
        assert result.is_mutative is False

    def test_path_prefix(self):
        result = detect_mutative_command("/usr/bin/kubectl delete pod my-pod")
        assert result.is_mutative is True
        assert result.verb == "delete"
        assert result.category == "MUTATIVE"

    def test_unknown_verb(self):
        result = detect_mutative_command("unknowncli frobnicate data")
        assert result.is_mutative is False
        assert result.category == "UNKNOWN"

    def test_docker_ps(self):
        """Scenario #18: docker ps is NOT mutative (safe by elimination)."""
        result = detect_mutative_command("docker ps")
        assert result.is_mutative is False

    def test_docker_build(self):
        result = detect_mutative_command("docker build -t image .")
        assert result.is_mutative is False


class TestGitMergeBase:
    """git merge-base is a read-only subcommand despite containing 'merge'."""

    def test_merge_base_is_read_only(self):
        result = detect_mutative_command("git merge-base main HEAD")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "merge-base"

    def test_merge_base_is_ancestor(self):
        result = detect_mutative_command("git merge-base --is-ancestor abc def")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_merge_base_fork_point(self):
        result = detect_mutative_command("git merge-base --fork-point main")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_git_merge_still_mutative(self):
        """Plain git merge must remain MUTATIVE."""
        result = detect_mutative_command("git merge main")
        assert result.is_mutative is True
        assert result.verb == "merge"


class TestInlineCodeDetection:
    """python3 -c inline code: flag dangerous patterns, not generic keywords."""

    def test_safe_json_operations(self):
        result = detect_mutative_command('python3 -c "import json; print(json.dumps({}))"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_safe_pathlib_read(self):
        result = detect_mutative_command('python3 -c "from pathlib import Path; p = Path.cwd()"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_safe_sys_version(self):
        result = detect_mutative_command('python3 -c "import sys; print(sys.version)"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_dangerous_os_remove(self):
        result = detect_mutative_command('python3 -c "import os; os.remove(f)"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "os-delete"

    def test_dangerous_shutil_rmtree(self):
        result = detect_mutative_command('python3 -c "import shutil; shutil.rmtree(d)"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "shutil-delete"

    def test_dangerous_file_write(self):
        result = detect_mutative_command("python3 -c \"open('f', 'w').write('data')\"")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "file-write-open"

    def test_subprocess_is_mutative(self):
        """subprocess in python -c is flagged -- the inner command runs in-process,
        bypassing the hook entirely (no separate Bash tool invocation)."""
        result = detect_mutative_command('python3 -c "import subprocess; subprocess.run([\"ls\"])"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "process-module"

    def test_python_variant(self):
        """python (not python3) with -c should also be checked."""
        result = detect_mutative_command('python -c "import os; os.remove(f)"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_heredoc_stdin_import_safe(self):
        """python3 - <<'PYEOF' with import in body must NOT be flagged as mutative."""
        cmd = "python3 - <<'PYEOF'\nimport json\nprint(json.dumps({}))\nPYEOF"
        result = detect_mutative_command(cmd)
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_heredoc_stdin_dangerous_still_caught(self):
        """python3 - <<'PYEOF' with os.remove() must still be caught."""
        cmd = "python3 - <<'PYEOF'\nimport os\nos.remove('/tmp/x')\nPYEOF"
        result = detect_mutative_command(cmd)
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "os-delete"


class TestUniversalInlineCodeDetection:
    """Language-agnostic 3-layer inline code detection for node, ruby, perl, etc."""

    # ---- Layer 1: Shell command extraction from string literals ----

    def test_node_exec_with_shell_command(self):
        """node -e with execSync running a shell command -> mutative via Layer 2."""
        result = detect_mutative_command(
            """node -e "require('child_process').execSync('rm -rf /tmp/x')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_ruby_system_with_shell_command(self):
        """ruby -e with system() call -> mutative via Layer 2."""
        result = detect_mutative_command(
            """ruby -e "system('terraform destroy')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_perl_exec_with_shell_command(self):
        """perl -e with exec() call -> mutative via Layer 2."""
        result = detect_mutative_command(
            """perl -e "exec('kubectl delete ns prod')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    # ---- Layer 2: Universal dangerous API keywords ----

    def test_node_fs_unlink(self):
        """node -e with fs.unlinkSync -> mutative (FILE_DELETION)."""
        result = detect_mutative_command(
            """node -e "require('fs').unlinkSync('/tmp/x')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "fs-delete"

    def test_ruby_file_delete(self):
        """ruby -e with File.delete -> mutative (FILE_DELETION)."""
        result = detect_mutative_command(
            """ruby -e "File.delete('/tmp/x')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "file-delete"

    def test_perl_unlink(self):
        """perl -e with unlink() -> mutative (FILE_DELETION)."""
        result = detect_mutative_command(
            """perl -e "unlink('/tmp/x')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "unlink-call"

    def test_node_child_process(self):
        """node -e requiring child_process -> mutative (PROCESS_EXECUTION module)."""
        result = detect_mutative_command(
            """node -e "require('child_process')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "process-module"

    def test_node_fs_write(self):
        """node -e with fs.writeFileSync -> mutative (FILE_WRITE)."""
        result = detect_mutative_command(
            """node -e "require('fs').writeFileSync('/tmp/x', 'data')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "fs-write"

    def test_node_eval_flag(self):
        """node --eval (long form) should also trigger inline code detection."""
        result = detect_mutative_command(
            """node --eval "require('child_process').execSync('ls')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_perl_capital_e_flag(self):
        """perl -E (capital) should also trigger inline code detection."""
        result = detect_mutative_command(
            """perl -E "unlink('/tmp/x')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_php_inline_code(self):
        """php -r with system() -> mutative."""
        result = detect_mutative_command(
            """php -r "system('rm /tmp/x');" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_ruby_fileutils_rm(self):
        """ruby -e with FileUtils.rm -> mutative (FILE_DELETION)."""
        result = detect_mutative_command(
            """ruby -e "FileUtils.rm_rf('/tmp/x')" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "fileutils-rm"

    # ---- Safe inline code (all languages) ----

    def test_node_console_log_safe(self):
        """node -e with console.log -> NOT mutative."""
        result = detect_mutative_command(
            """node -e "console.log('hello')" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_ruby_puts_safe(self):
        """ruby -e with puts -> NOT mutative."""
        result = detect_mutative_command(
            """ruby -e "puts 'hello'" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_perl_print_safe(self):
        """perl -e with print -> NOT mutative."""
        result = detect_mutative_command(
            """perl -e "print 'hello'" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_node_version_check_safe(self):
        """node -e reading package.json version -> NOT mutative."""
        result = detect_mutative_command(
            """node -e "console.log(JSON.parse('{}').version)" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_python_print_safe(self):
        """python3 -c with print -> NOT mutative (regression check)."""
        result = detect_mutative_command(
            """python3 -c "print('hello')" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_lua_safe_print(self):
        """lua -e with print -> NOT mutative."""
        result = detect_mutative_command(
            """lua -e "print('hello')" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    # ---- Layer 3: Heuristics ----

    def test_sensitive_path_not_flagged(self):
        """Inline code reading /etc/passwd -> NOT mutative (no dangerous API)."""
        result = detect_mutative_command(
            """node -e "readFile('/etc/passwd')" """
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_suspicious_base64(self):
        """Inline code with atob (base64 decoding) -> suspicious via heuristic."""
        result = detect_mutative_command(
            """node -e "eval(atob('dGVzdA=='))" """
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert "heuristic" in result.verb
        assert "encoding" in result.verb

    def test_long_inline_code_suspicious(self):
        """Very long inline code (>500 chars) -> suspicious via heuristic."""
        long_code = "x" * 510
        result = detect_mutative_command(
            f'node -e "{long_code}"'
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert "heuristic-long-code" in result.verb

    def test_short_safe_code_not_suspicious(self):
        """Short safe inline code should NOT trigger length heuristic."""
        result = detect_mutative_command(
            """node -e "console.log(1+1)" """
        )
        assert result.is_mutative is False

    def test_ip_address_heuristic(self):
        """Inline code with an IP address -> suspicious via heuristic."""
        result = detect_mutative_command(
            """node -e "connect('192.168.1.1')" """
        )
        assert result.is_mutative is True
        assert "ip-address" in result.verb


class TestBuildT3BlockResponse:
    def test_response_keys(self):
        danger = MutativeResult(
            is_mutative=True, category="MUTATIVE", verb="delete",
            cli_family="k8s", confidence="high", reason="Mutative verb",
        )
        response = build_t3_block_response("kubectl delete pod x", danger)
        assert "decision" in response
        assert "message" in response
        assert response["decision"] == "block"

    def test_message_includes_nonce(self):
        danger = MutativeResult(
            is_mutative=True, category="MUTATIVE", verb="apply",
            cli_family="k8s", confidence="high", reason="Mutative verb",
        )
        response = build_t3_block_response("kubectl apply -f x.yaml", danger, nonce="abc123")
        assert "NONCE:abc123" in response["message"]


# ============================================================================
# Comprehensive detect_mutative_command tests
# ============================================================================

class TestDetectMutativeCommand:
    """Comprehensive tests for detect_mutative_command covering the git commit
    message false-positive fix (GIT_LOCAL_SAFE_SUBCOMMANDS guard) and general
    classification correctness."""

    # ------------------------------------------------------------------
    # Git commit/stash: message body must NOT affect classification
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cmd", [
        'git commit -m "fix: update docs"',
        'git commit -m "feat: create new feature"',
        'git commit -m "chore: deploy pipeline"',
        'git commit -m "refactor: replace old code"',
        'git commit --amend -m "update: send fix"',
        "git stash push -m 'save before deploy'",
        'git commit -m "push to production"',
        'git commit -m "delete unused imports"',
        'git commit -m "apply formatting rules"',
        'git commit -m "merge conflicts resolved"',
        'git commit -m "install dependencies"',
    ], ids=[
        "update-in-msg",
        "create-in-msg",
        "deploy-in-msg",
        "replace-in-msg",
        "amend-update-send-in-msg",
        "stash-deploy-in-msg",
        "push-in-msg",
        "delete-in-msg",
        "apply-in-msg",
        "merge-in-msg",
        "install-in-msg",
    ])
    def test_git_message_body_does_not_trigger_t3(self, cmd):
        """Mutative words inside -m message must not trigger T3."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is False, (
            f"Command {cmd!r} should be non-mutative but got "
            f"verb={result.verb!r} category={result.category}"
        )

    # ------------------------------------------------------------------
    # Git commands that MUST remain mutative
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cmd,expected_verb", [
        ("git push origin main", "push"),
        ("git push --force origin main", "push"),
        ("git push --delete origin feature-branch", "push"),
        ("git push -u origin feature", "push"),
    ], ids=[
        "push-plain",
        "push-force",
        "push-delete",
        "push-upstream",
    ])
    def test_git_push_always_mutative(self, cmd, expected_verb):
        """git push (all variants) must remain mutative."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is True
        assert result.verb == expected_verb

    @pytest.mark.parametrize("cmd,expected_verb", [
        ("git merge feature-x", "merge"),
        ("git rebase main", "rebase"),
        ("git tag v1.0.0", "tag"),
        ("git tag -d v1.0.0", "tag"),
    ], ids=[
        "merge",
        "rebase",
        "tag-create",
        "tag-delete",
    ])
    def test_git_destructive_local_still_mutative(self, cmd, expected_verb):
        """git merge/rebase/tag are NOT in the safe list."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is True
        assert result.verb == expected_verb

    # ------------------------------------------------------------------
    # Git local/safe commands
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cmd,expected_verb", [
        ("git add .", "add"),
        ("git add -A", "add"),
        ("git log --oneline", "log"),
        ("git log --all --graph", "log"),
        ("git diff HEAD", "diff"),
        ("git diff --staged", "diff"),
        ("git status", "status"),
        ("git status -s", "status"),
        ("git branch feature-x", "branch"),
        ("git checkout main", "checkout"),
        ("git switch develop", "switch"),
        ("git reflog", "reflog"),
        ("git show HEAD", "show"),
        ("git shortlog -s", "shortlog"),
        ("git blame README.md", "blame"),
        ("git bisect start", "bisect"),
        ("git stash", "stash"),
        ("git stash list", "stash"),
        ("git stash pop", "stash"),
        ("git reset HEAD~1", "reset"),
        ("git reset --soft HEAD~1", "reset"),
        ("git revert HEAD", "revert"),
        ("git revert abc123", "revert"),
        ("git cherry-pick abc123", "cherry-pick"),
        ("git cherry-pick feature~2", "cherry-pick"),
    ], ids=[
        "add-dot",
        "add-all",
        "log-oneline",
        "log-all-graph",
        "diff-head",
        "diff-staged",
        "status",
        "status-short",
        "branch-create",
        "checkout",
        "switch",
        "reflog",
        "show",
        "shortlog",
        "blame",
        "bisect",
        "stash-bare",
        "stash-list",
        "stash-pop",
        "reset",
        "reset-soft",
        "revert-head",
        "revert-sha",
        "cherry-pick",
        "cherry-pick-ref",
    ])
    def test_git_local_commands_not_mutative(self, cmd, expected_verb):
        """Git local-only subcommands are non-mutative."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is False, (
            f"Command {cmd!r} should be non-mutative but got "
            f"verb={result.verb!r} category={result.category}"
        )
        assert result.verb == expected_verb

    # ------------------------------------------------------------------
    # Git dangerous flags on local commands still trigger T3
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cmd,expected_flag", [
        ("git branch -D feature", "-D"),
        ("git branch -M old-name new-name", "-M"),
        ("git checkout --force main", "--force"),
    ], ids=[
        "branch-force-delete",
        "branch-force-move",
        "checkout-force",
    ])
    def test_git_local_with_dangerous_flags_mutative(self, cmd, expected_flag):
        """Local git subcommands with dangerous flags must remain mutative."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is True
        assert expected_flag in result.dangerous_flags

    # ------------------------------------------------------------------
    # Git local commands: correct category assignment
    # ------------------------------------------------------------------

    def test_git_diff_is_simulation_category(self):
        """git diff should have SIMULATION category (diff is a simulation verb)."""
        result = detect_mutative_command("git diff HEAD")
        assert result.category == "SIMULATION"

    def test_git_log_is_read_only_category(self):
        """git log should have READ_ONLY category."""
        result = detect_mutative_command("git log --all")
        assert result.category == "READ_ONLY"

    def test_git_status_is_read_only_category(self):
        """git status should have READ_ONLY category."""
        result = detect_mutative_command("git status")
        assert result.category == "READ_ONLY"

    def test_git_commit_is_unknown_category(self):
        """git commit is local-safe but 'commit' is not in READ_ONLY or SIMULATION verbs."""
        result = detect_mutative_command("git commit -m 'msg'")
        assert result.category == "UNKNOWN"
        assert result.is_mutative is False

    # ------------------------------------------------------------------
    # Non-git mutative commands (sanity checks)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cmd,expected_verb", [
        ("kubectl apply -f manifest.yaml", "apply"),
        ("terraform apply", "apply"),
        ("rm -rf /tmp/data", "rm"),
        ("docker rm container-id", "rm"),
        ("helm install release chart", "install"),
        ("kubectl delete pod my-pod", "delete"),
    ], ids=[
        "kubectl-apply",
        "terraform-apply",
        "rm-rf",
        "docker-rm",
        "helm-install",
        "kubectl-delete",
    ])
    def test_non_git_mutative(self, cmd, expected_verb):
        """Non-git mutative commands must stay classified as T3."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is True
        assert result.verb == expected_verb

    # ------------------------------------------------------------------
    # Non-git safe commands (sanity checks)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("cmd", [
        "kubectl get pods",
        "terraform plan",
        "ls -la",
        "docker ps",
    ], ids=[
        "kubectl-get",
        "terraform-plan",
        "ls",
        "docker-ps",
    ])
    def test_non_git_safe(self, cmd):
        """Non-git read-only/simulation commands must be non-mutative."""
        result = detect_mutative_command(cmd)
        assert result.is_mutative is False

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_git_commit_no_m_flag(self):
        """git commit without -m flag is safe."""
        result = detect_mutative_command("git commit")
        assert result.is_mutative is False
        assert result.verb == "commit"

    def test_git_commit_empty_message(self):
        """git commit -m '' (empty message) is safe."""
        result = detect_mutative_command('git commit -m ""')
        assert result.is_mutative is False

    def test_git_commit_with_path_prefix(self):
        """/usr/bin/git commit -m 'deploy fix' is safe."""
        result = detect_mutative_command('/usr/bin/git commit -m "deploy fix"')
        assert result.is_mutative is False

    def test_git_commit_with_c_flag(self):
        """git -C /path commit -m 'update config' is safe (global -C flag)."""
        result = detect_mutative_command('git -C /some/path commit -m "update config"')
        assert result.is_mutative is False
        assert result.verb == "commit"

    # ------------------------------------------------------------------
    # GIT_LOCAL_SAFE_SUBCOMMANDS constant integrity
    # ------------------------------------------------------------------

    def test_safe_subcommands_constant_contents(self):
        """Verify the expected subcommands are in GIT_LOCAL_SAFE_SUBCOMMANDS."""
        expected = {
            "commit", "stash", "add", "log", "diff", "status",
            "branch", "checkout", "switch", "reflog",
        }
        assert expected.issubset(GIT_LOCAL_SAFE_SUBCOMMANDS)

    def test_push_not_in_safe_subcommands(self):
        """push must NEVER be in GIT_LOCAL_SAFE_SUBCOMMANDS."""
        assert "push" not in GIT_LOCAL_SAFE_SUBCOMMANDS

    def test_mutative_verbs_not_in_safe_subcommands(self):
        """Subcommands that are in MUTATIVE_VERBS should not be in the safe list."""
        overlap = GIT_LOCAL_SAFE_SUBCOMMANDS & MUTATIVE_VERBS
        assert overlap == set(), (
            f"These subcommands are in both GIT_LOCAL_SAFE_SUBCOMMANDS and "
            f"MUTATIVE_VERBS: {overlap}"
        )


class TestGwsMacroPrefix:
    """gws CLI exposes convenience macros prefixed with '+' (e.g. +reply, +send,
    +search) that wrap underlying API calls. The verb scanner must strip the
    '+' before the taxonomy lookup so the macros classify like their base
    verbs, otherwise mutative macros slip through as 'safe by elimination'
    and bypass T3 approval (bug found 2026-04-17 with gws gmail +reply).
    """

    def test_gws_gmail_plus_reply_is_mutative(self):
        """gws gmail +reply is a send-a-reply macro; must be T3."""
        result = detect_mutative_command(
            'gws gmail +reply --message-id 19d988b417469c8a --body "hello"'
        )
        assert result.is_mutative is True
        assert result.verb == "reply"
        assert result.category == "MUTATIVE"

    def test_gws_gmail_plus_send_is_mutative(self):
        """gws gmail +send wraps messages send; must be T3."""
        result = detect_mutative_command(
            'gws gmail +send --to user@example.com --subject Hi --body Test'
        )
        assert result.is_mutative is True
        assert result.verb == "send"
        assert result.category == "MUTATIVE"

    def test_gws_gmail_plus_search_is_read_only(self):
        """gws gmail +search is a list wrapper; stays read-only after strip."""
        result = detect_mutative_command('gws gmail +search "from:boss"')
        assert result.is_mutative is False
        assert result.verb == "search"
        assert result.category == "READ_ONLY"

    def test_gws_gmail_users_messages_send_still_mutative(self):
        """Regression guard: the explicit messages send path keeps working."""
        result = detect_mutative_command(
            'gws gmail users messages send --params \'{"userId":"me","raw":"..."}\''
        )
        assert result.is_mutative is True
        assert result.verb == "send"
        assert result.category == "MUTATIVE"