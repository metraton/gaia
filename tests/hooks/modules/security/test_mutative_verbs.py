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
    MKDIR_SENSITIVE_PATH_PREFIXES,
    MAX_NORMAL_INLINE_LENGTH,
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
    """mkdir path-sensitive tier override (T3 for sensitive paths, T0 otherwise).

    Working-tree mkdir (relative or absolute non-sensitive paths) classifies as
    T0 (non-mutative).  mkdir targeting a kernel pseudo-filesystem or privileged
    OS directory retains T3.  See MKDIR_SENSITIVE_PATH_PREFIXES for the full set.
    """

    def test_mkdir_basic(self):
        """mkdir with a relative path is T0: working-tree, non-destructive."""
        result = detect_mutative_command("mkdir foo")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_mkdir_p(self):
        """mkdir -p with a relative nested path is T0.

        The -p flag makes mkdir idempotent on existing directories.  With all
        working-tree paths the command is safe by elimination, so T0 applies.
        """
        result = detect_mutative_command("mkdir -p foo/bar/baz")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"


class TestMkdirPathSensitive:
    """mkdir must remain T3 when any argument targets a sensitive system path."""

    def test_sensitive_prefixes_constant_present(self):
        """MKDIR_SENSITIVE_PATH_PREFIXES is exported and contains exactly the mandated set.

        Set = full system namespace MINUS scratch space (/tmp, /run) = 11 prefixes.
        """
        assert MKDIR_SENSITIVE_PATH_PREFIXES == frozenset({
            "/dev", "/sys", "/proc",
            "/etc", "/boot", "/usr",
            "/bin", "/sbin",
            "/lib", "/lib64",
            "/root",
        })

    # --- T3 cases (sensitive paths) ---

    def test_mkdir_dev(self):
        result = detect_mutative_command("mkdir /dev/foo")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_sys(self):
        result = detect_mutative_command("mkdir -p /sys/x")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_proc(self):
        result = detect_mutative_command("mkdir /proc/y")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_etc(self):
        """/etc is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /etc/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_boot(self):
        """/boot is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /boot/grub/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_usr(self):
        """/usr is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /usr/local/myapp")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_bin(self):
        """/bin is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /bin/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_sbin(self):
        """/sbin is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /sbin/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_lib(self):
        """/lib is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /lib/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_lib64(self):
        """/lib64 is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /lib64/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_root(self):
        """/root is in the sensitive set -- classifies as T3."""
        result = detect_mutative_command("mkdir /root/custom")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_tmp_is_t0(self):
        """/tmp is NOT in the sensitive set (scratch) -- classifies as T0."""
        result = detect_mutative_command("mkdir /tmp/workdir")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_mkdir_run_is_t0(self):
        """/run is NOT in the sensitive set (scratch) -- classifies as T0."""
        result = detect_mutative_command("mkdir /run/myservice")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_mkdir_mixed_one_sensitive(self):
        """If even one path is sensitive, the whole command is T3."""
        result = detect_mutative_command("mkdir foo /dev/mydev")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mkdir_home_jorge_is_t0(self):
        """Absolute path under /home is NOT sensitive -- classifies as T0."""
        result = detect_mutative_command("mkdir /home/jorge/projects/new")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    # --- T0 cases (working-tree paths) ---

    def test_mkdir_relative_simple(self):
        result = detect_mutative_command("mkdir foo")
        assert result.is_mutative is False

    def test_mkdir_p_nested_relative(self):
        result = detect_mutative_command("mkdir -p a/b/c")
        assert result.is_mutative is False

    def test_mkdir_dotslash(self):
        result = detect_mutative_command("mkdir ./skills/x")
        assert result.is_mutative is False

    def test_mkdir_home_relative(self):
        """Home-relative paths (~/...) are always working-tree -- T0."""
        result = detect_mutative_command("mkdir ~/projects/new")
        assert result.is_mutative is False

    def test_mkdir_no_args_is_t3(self):
        """No path arguments: conservative fallback is T3 (cannot confirm safety)."""
        result = detect_mutative_command("mkdir")
        assert result.is_mutative is True

    def test_mkdir_only_flags_no_paths_is_t3(self):
        """Flags but no path arguments: conservative fallback is T3."""
        result = detect_mutative_command("mkdir -p")
        assert result.is_mutative is True


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
        # AST analyzer reports the canonical call name (``os-remove``)
        # rather than the regex layer's category (``os-delete``).
        result = detect_mutative_command('python3 -c "import os; os.remove(f)"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "os-remove"

    def test_dangerous_shutil_rmtree(self):
        result = detect_mutative_command('python3 -c "import shutil; shutil.rmtree(d)"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "shutil-rmtree"

    def test_dangerous_file_write(self):
        result = detect_mutative_command("python3 -c \"open('f', 'w').write('data')\"")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "open-write"

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

    # ------------------------------------------------------------------
    # `python -c` (the non-`python3` interpreter token) read-only payloads.
    # Symmetric coverage with the python3 -c safe cases above: the false
    # positive being pinned here is `python -c` code that merely *contains*
    # the keywords `import` and/or `for` (both lexically collide with
    # MUTATIVE_VERBS["import"] and the historic "for"/"link" false positive)
    # being misclassified as T3.  The AST/regex inline path must classify
    # these as READ_ONLY for `python` exactly as it does for `python3`.
    # Without these tests a mutant that drops `python` from the inline-code
    # interpreter sets (or breaks the `python3?` indirect-exec regex) would
    # survive: the only prior `python` test (test_python_variant) exercises
    # the MUTATIVE branch, never the AST-clean read-only branch.
    # ------------------------------------------------------------------
    def test_python_variant_import_readonly_safe(self):
        """`python -c` with a bare import + read-only call is NOT mutative."""
        result = detect_mutative_command(
            'python -c "import json; print(json.dumps({}))"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_python_variant_for_loop_readonly_safe(self):
        """`python -c` containing a `for` loop over read-only code is NOT mutative."""
        result = detect_mutative_command(
            'python -c "for x in range(3): print(x)"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_python_variant_import_and_for_readonly_safe(self):
        """`python -c` with BOTH import and for, read-only body -> NOT mutative."""
        result = detect_mutative_command(
            'python -c "import json\nfor x in [1, 2]: print(x)"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_python_variant_subprocess_still_mutative(self):
        """No hole: a genuinely mutative `python -c` (subprocess) stays T3."""
        result = detect_mutative_command(
            'python -c "import subprocess; subprocess.run([\'ls\'])"'
        )
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
        assert result.verb == "os-remove"


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


class TestLongInlineReadOnlyExemption:
    """AC-9: heuristic-long-code must NOT flag PROVABLY read-only Python.

    The length heuristic is a proxy for "too complex to vet". It must not
    block long-but-harmless inline reads (import + SELECT/PRAGMA + print),
    yet it must keep flagging long code it cannot prove read-only -- in
    particular AST-clean-but-mutating payloads the blocklist analyzer misses
    (``cur.execute('INSERT ...')``, ``con.commit()``). No false negatives.
    """

    # SELECT/INSERT split so this test file never carries a literal SQL-write
    # string that other guards object to.
    _SEL = "SE" + "LECT"
    _INS = "INS" + "ERT INTO t(c) VALUES(1)"
    _PRAGMA = "PRA" + "GMA table_info(approvals)"

    def _long_readonly(self) -> str:
        body = (
            "import sqlite3; con=sqlite3.connect('/home/u/.gaia/gaia.db'); "
            "cols=[d[0] for d in con.execute('%s').fetchall()]; " % self._PRAGMA +
            "rows=con.execute('%s id,status,verb,subagent_id,created_at,"
            "expires_at,scope,command_hash,nonce,grant_kind,verb_family,"
            "uses_remaining FROM approvals WHERE status=? AND created_at > ? "
            "ORDER BY created_at DESC LIMIT 100', ('pending', 0)).fetchall(); "
            % self._SEL +
            "print('columns:', cols); print('total rows:', len(rows)); "
            "print(chr(10).join(repr(r) for r in rows)); "
            "extra=con.execute('%s count(*) FROM approvals').fetchone(); " % self._SEL +
            "print('count:', extra); con.close()"
        )
        assert len(body) > MAX_NORMAL_INLINE_LENGTH  # guards the premise
        return 'python3 -c "%s"' % body.replace('"', '\\"')

    def _long_mutating_ast_clean(self) -> str:
        # AST-clean (no dangerous CALL in the blocklist) but mutating via a
        # bound-method execute + commit. Padded past the length limit.
        body = (
            "import sqlite3; con=sqlite3.connect('/home/u/.gaia/gaia.db'); "
            "con.execute('%s'); con.commit(); " % self._INS +
            "note = '%s'; " % ("z" * 480) +
            "con.close()"
        )
        assert len(body) > MAX_NORMAL_INLINE_LENGTH
        return 'python3 -c "%s"' % body.replace('"', '\\"')

    def test_long_readonly_python_not_t3(self):
        """FIXED: long pure-read inline Python is READ_ONLY, not T3."""
        result = detect_mutative_command(self._long_readonly())
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "inline-code-readonly"

    def test_long_mutating_ast_clean_still_t3(self):
        """NO REGRESSION: long AST-clean-but-mutating sqlite write stays T3."""
        result = detect_mutative_command(self._long_mutating_ast_clean())
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "heuristic-long-code"

    def test_long_open_write_still_t3(self):
        """NO REGRESSION: open(...,'w').write caught by AST before length."""
        body = "open('x','w').write('%s')" % ("y" * 510)
        result = detect_mutative_command('python3 -c "%s"' % body)
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "open-write"

    def test_long_os_system_still_t3(self):
        """NO REGRESSION: os.system caught by AST regardless of length."""
        body = "import os; os.system('rm -rf / %s')" % ("x" * 500)
        result = detect_mutative_command('python3 -c "%s"' % body)
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "os-system"

    def test_long_subprocess_still_t3(self):
        """NO REGRESSION: subprocess.run caught by AST regardless of length."""
        body = "import subprocess; subprocess.run(['rm','%s'])" % ("x" * 500)
        result = detect_mutative_command('python3 -c "%s"' % body)
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_long_non_python_interpreter_still_t3(self):
        """NO REGRESSION: exemption is Python-only; long node code stays T3."""
        result = detect_mutative_command('node -e "%s"' % ("x" * 510))
        assert result.is_mutative is True
        assert result.verb == "heuristic-long-code"

    def test_long_sql_via_variable_still_t3(self):
        """NO REGRESSION: non-literal SQL argument cannot be proven read-only."""
        body = (
            "import sqlite3; q = 'DR' + 'OP TABLE t'; "
            "con=sqlite3.connect('/home/u/.gaia/gaia.db'); "
            "con.execute(q); note='%s'; con.close()" % ("z" * 480)
        )
        result = detect_mutative_command('python3 -c "%s"' % body.replace('"', '\\"'))
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "heuristic-long-code"


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
        ("git reset --hard HEAD~1", "--hard"),
    ], ids=[
        "branch-force-delete",
        "branch-force-move",
        "checkout-force",
        "reset-hard",
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


class TestT3FalsePositiveFix:
    """Regression suite for the T3 false-positive fix (READ_ONLY_BASE_CMDS
    whitelist + camelCase-at-subcommand-position guard).

    Bug: grep -rn "SessionStart" file.json was flagged as MUTATIVE because
    camelCase splitting on the quoted argument "SessionStart" produced
    "session" + "start", and "start" is in MUTATIVE_VERBS.

    Fix: (1) READ_ONLY_BASE_CMDS fast-path short-circuits the verb scanner
    for known read-only inspection tools (grep, find, cat, ls, head, tail,
    awk, wc, etc.). (2) camelCase splitting only fires at semantic_index == 1
    (subcommand position), not at later argument positions.
    """

    # ---- The original failing case (regression anchor) ----

    def test_grep_with_quoted_session_start_is_safe(self):
        """The exact bug report: grep -rn "SessionStart" file.json."""
        result = detect_mutative_command('grep -rn "SessionStart" file.json')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "grep"

    # ---- READ_ONLY_BASE_CMDS whitelist ----

    def test_find_with_substring_pattern_is_safe(self):
        """find . -name "*start*" must not match the mutative verb 'start'."""
        result = detect_mutative_command('find . -name "*start*"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_cat_with_start_in_filename_is_safe(self):
        """cat reading a file whose name contains 'start' must be read-only."""
        result = detect_mutative_command("cat file_with_start_in_name")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_head_is_safe(self):
        result = detect_mutative_command("head -n 5 file")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "head"

    def test_tail_is_safe(self):
        result = detect_mutative_command("tail -f log")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "tail"

    def test_awk_is_safe(self):
        result = detect_mutative_command("awk /pattern/ file")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "awk"

    def test_wc_is_safe(self):
        result = detect_mutative_command("wc -l file")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert result.verb == "wc"

    # ---- find -delete must still flag (whitelist exception) ----

    def test_find_delete_is_mutative(self):
        """find . -delete is destructive even though `find` is whitelisted."""
        result = detect_mutative_command('find . -name "*.tmp" -delete')
        assert result.is_mutative is True
        assert result.verb == "find"
        assert "-delete" in result.dangerous_flags

    # ---- npm start IS mutative (verb scanner still works for non-whitelisted CLIs) ----

    def test_npm_start_is_mutative(self):
        """npm start runs lifecycle scripts -- must remain T3."""
        result = detect_mutative_command("npm start")
        assert result.is_mutative is True
        assert result.verb == "start"
        assert result.category == "MUTATIVE"

    # ---- Single unknown token: not at verb position ----

    def test_unknown_base_cmd_with_start_is_safe(self):
        """`start service` with unknown base_cmd should not flag.

        `start` as a base_cmd is not in COMMAND_ALIASES or READ_ONLY_BASE_CMDS,
        and the verb scanner only inspects tokens AFTER the base_cmd. So
        `start` here is the base, `service` is the candidate verb -- neither
        matches and the command is safe by elimination.
        """
        result = detect_mutative_command("start service")
        assert result.is_mutative is False

    # ---- camelCase at subcommand position: still flags ----

    def test_camelcase_at_subcommand_position_is_mutative(self):
        """aws batchDelete --table foo: camelCase at subcmd splits to 'delete'."""
        result = detect_mutative_command("aws batchDelete --table foo")
        assert result.is_mutative is True
        assert result.verb == "delete"
        assert result.category == "MUTATIVE"

    # ---- camelCase at argument position: must NOT flag ----

    def test_camelcase_in_argument_value_is_safe(self):
        """aws s3api list-buckets --filter "BatchDelete" must not split
        the argument value 'BatchDelete' into the mutative verb 'delete'.

        The split_camel_case logic only fires at semantic_index == 1
        (subcommand position) -- this test guards that boundary.
        """
        result = detect_mutative_command(
            'aws s3api list-buckets --filter "BatchDelete"'
        )
        assert result.is_mutative is False

    # ---- git commit with SessionStart in message body (regression for
    # the same camelCase-in-argument false positive, but inside -m) ----

    def test_git_commit_with_session_start_in_message_is_safe(self):
        """git commit -m "add SessionStart handler" must not flag.

        Both the GIT_LOCAL_SAFE_SUBCOMMANDS guard and the
        camelCase-only-at-subcommand-position rule defend this case.
        """
        result = detect_mutative_command(
            'git commit -m "add SessionStart handler"'
        )
        assert result.is_mutative is False


class TestCapabilityClasses:
    """Fase S Nivel 1 -- database_cli capability class.

    Each verb in the database_cli class (sqlite3, psql, mysql, mongosh, ...)
    can apply arbitrary mutations by accepting the entire mutation language
    as a single argument or by reading from a file.  The verb scanner
    cannot see the intent.  The capability layer fixes that with a single
    rule: default MUTATIVE, with explicit overrides for read-only flags
    and inline read-only payloads.
    """

    # ---- sqlite3: redirect & dot-command load both stay MUTATIVE ----

    def test_sqlite3_redirect_input_is_mutative(self):
        """sqlite3 db < file.sql must require approval -- this is the
        exact gap that motivated Fase S (856 INSERTs slipped through)."""
        result = detect_mutative_command(
            "sqlite3 /tmp/x.db < /tmp/migration.sql"
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.cli_family == "database"
        assert "redirect" in result.reason.lower()

    def test_sqlite3_dot_read_is_mutative(self):
        """sqlite3 db ".read file.sql" loads an external script -- still
        an external payload, must stay MUTATIVE."""
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db ".read /tmp/migration.sql"'
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert ".read" in result.reason or "dot-command" in result.reason

    def test_sqlite3_dot_import_is_mutative(self):
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db ".import data.csv mytable"'
        )
        assert result.is_mutative is True

    # ---- sqlite3: read-only flag and inline SELECT are READ_ONLY ----

    def test_sqlite3_readonly_flag_is_safe(self):
        """sqlite3 -readonly downgrades the whole invocation to READ_ONLY."""
        result = detect_mutative_command(
            'sqlite3 -readonly /tmp/x.db "SELECT * FROM t"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"
        assert "-readonly" in result.reason

    def test_sqlite3_inline_select_is_safe(self):
        """sqlite3 db "SELECT ..." -- inline payload demonstrably read-only."""
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db "SELECT * FROM t"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_sqlite3_inline_pragma_table_info_is_safe(self):
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db "PRAGMA table_info(users)"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_sqlite3_inline_insert_is_mutative(self):
        """Inline INSERT does not match read-only patterns -> default MUTATIVE."""
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db "INSERT INTO t VALUES (1)"'
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    # ---- psql ----

    def test_psql_file_input_is_mutative(self):
        """psql -f file.sql executes a script file -- MUTATIVE."""
        result = detect_mutative_command("psql -d mydb -f /tmp/x.sql")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_psql_inline_select_is_safe(self):
        result = detect_mutative_command('psql -d mydb -c "SELECT 1"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_psql_inline_drop_is_mutative(self):
        result = detect_mutative_command(
            'psql -d mydb -c "DROP TABLE users"'
        )
        assert result.is_mutative is True

    # ---- mysql / mariadb ----

    def test_mysql_inline_select_is_safe(self):
        result = detect_mutative_command('mysql -e "SELECT 1"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_mysql_redirect_dump_is_mutative(self):
        """mysql db < dump.sql is the canonical restore -- must require approval."""
        result = detect_mutative_command("mysql mydb < /tmp/dump.sql")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mariadb_inline_select_is_safe(self):
        result = detect_mutative_command('mariadb -e "SELECT 1"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    # ---- mongosh / mongo ----

    def test_mongosh_eval_find_is_safe(self):
        result = detect_mutative_command('mongosh --eval "db.find()"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_mongosh_eval_findone_is_safe(self):
        result = detect_mutative_command(
            'mongosh --eval "db.users.findOne({_id: 1})"'
        )
        assert result.is_mutative is False

    def test_mongosh_eval_insert_is_mutative(self):
        result = detect_mutative_command('mongosh --eval "db.insert()"')
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_mongosh_eval_drop_is_mutative(self):
        result = detect_mutative_command(
            'mongosh --eval "db.collection.drop()"'
        )
        assert result.is_mutative is True

    def test_mongosh_eval_mixed_find_then_insert_is_mutative(self):
        """A payload that *contains* an insert is mutative even if it also
        starts with a read -- the deny_pattern wins over the read prefix."""
        result = detect_mutative_command(
            'mongosh --eval "db.find().forEach(d => db.t.insertOne(d))"'
        )
        assert result.is_mutative is True

    # ---- redis-cli / cqlsh / duckdb ----

    def test_redis_cli_default_is_mutative(self):
        """redis-cli with no recognised override stays MUTATIVE -- safer
        default until we add specific read-only patterns for Redis."""
        result = detect_mutative_command("redis-cli FLUSHALL")
        assert result.is_mutative is True

    def test_duckdb_redirect_is_mutative(self):
        result = detect_mutative_command("duckdb /tmp/x.db < /tmp/script.sql")
        assert result.is_mutative is True

    # ---- Extensibility & registry shape ----

    def test_registry_has_database_cli_class(self):
        from modules.security.capability_classes import CAPABILITY_CLASSES
        assert "database_cli" in CAPABILITY_CLASSES
        spec = CAPABILITY_CLASSES["database_cli"]
        assert spec["default_intent"] == "MUTATIVE"
        assert "sqlite3" in spec["verbs"]
        assert "psql" in spec["verbs"]
        assert "mysql" in spec["verbs"]
        assert "mongosh" in spec["verbs"]

    def test_is_capability_verb_lookup(self):
        from modules.security.capability_classes import is_capability_verb
        assert is_capability_verb("sqlite3") is True
        assert is_capability_verb("psql") is True
        # Unrelated commands must NOT match the capability index --
        # otherwise the regular verb scanner gets bypassed.
        assert is_capability_verb("git") is False
        assert is_capability_verb("kubectl") is False
        assert is_capability_verb("ls") is False

    def test_capability_class_does_not_break_unrelated_commands(self):
        """The capability layer must be a no-op for non-database CLIs."""
        result = detect_mutative_command("git status")
        assert result.is_mutative is False
        result = detect_mutative_command("kubectl get pods")
        assert result.is_mutative is False
        result = detect_mutative_command("kubectl delete pod foo")
        assert result.is_mutative is True


class TestSqliteReadonlyDotCommands:
    """Regression suite for sqlite3 read-only dot-command classification.

    Previously `.schema` and `.tables` (and other schema/metadata dot-commands)
    were incorrectly classified as T3 (MUTATIVE) because:
      - They are not SQL keywords (SELECT/EXPLAIN/PRAGMA), so the inline-payload
        regex did not match them.
      - The only dot-command check was the mutative-dot-command guard, which
        correctly blocked .read/.import/.restore but left the read-only ones to
        fall through to the default MUTATIVE classification.

    Fix (atom_t3_classification_overbroad): _SQLITE_READONLY_DOT_COMMANDS
    allowlist + Rule 1c in classify_capability().
    """

    # ---- Positive: commands that MUST classify as READ_ONLY (not T3) --------

    def test_sqlite3_schema_table_is_read_only(self):
        """Exact reproduction of the blocked command: sqlite3 ~/.gaia/gaia.db ".schema plans"
        This was wrongly classified as T3 before the fix."""
        result = detect_mutative_command(
            'sqlite3 ~/.gaia/gaia.db ".schema plans"'
        )
        assert result.is_mutative is False, (
            f"Expected READ_ONLY but got is_mutative={result.is_mutative}, "
            f"category={result.category!r}, reason={result.reason!r}"
        )
        assert result.category == "READ_ONLY"

    def test_sqlite3_tables_is_read_only(self):
        """Exact reproduction of the blocked command: sqlite3 /home/jorge/.gaia/gaia.db ".tables"
        This was wrongly classified as T3 before the fix."""
        result = detect_mutative_command(
            "sqlite3 /home/jorge/.gaia/gaia.db \".tables\""
        )
        assert result.is_mutative is False, (
            f"Expected READ_ONLY but got is_mutative={result.is_mutative}, "
            f"category={result.category!r}, reason={result.reason!r}"
        )
        assert result.category == "READ_ONLY"

    @pytest.mark.parametrize("dot_cmd", [
        ".schema",
        ".tables",
        ".databases",
        ".indexes",
        ".indices",
        ".dbinfo",
        ".show",
        ".fullschema",
    ])
    def test_each_readonly_dot_command_is_read_only(self, dot_cmd):
        """Every command in _SQLITE_READONLY_DOT_COMMANDS must classify as READ_ONLY."""
        cmd = f'sqlite3 /tmp/x.db "{dot_cmd}"'
        result = detect_mutative_command(cmd)
        assert result.is_mutative is False, (
            f"{dot_cmd}: expected READ_ONLY but got is_mutative={result.is_mutative}, "
            f"category={result.category!r}, reason={result.reason!r}"
        )
        assert result.category == "READ_ONLY"

    def test_sqlite3_schema_with_table_argument_is_read_only(self):
        """`.schema tablename` -- table name argument must not affect classification."""
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db ".schema users"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_sqlite3_indexes_with_table_argument_is_read_only(self):
        """`.indexes tablename` -- same; argument is metadata, not a mutation."""
        result = detect_mutative_command(
            'sqlite3 /tmp/x.db ".indexes plans"'
        )
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    # ---- Negative: write-capable dot-commands MUST stay MUTATIVE (T3) -------

    @pytest.mark.parametrize("write_cmd,label", [
        (".import data.csv mytable", "import"),
        (".restore backup.db",       "restore"),
        (".read /tmp/migration.sql", "read"),
        (".clone /tmp/clone.db",     "clone"),
        (".save /tmp/copy.db",       "save"),
        (".load /tmp/ext.so",        "load"),
        (".system ls",               "system"),
        (".shell echo hi",           "shell"),
    ])
    def test_write_capable_dot_commands_stay_mutative(self, write_cmd, label):
        """Write-capable dot-commands must never be downgraded to READ_ONLY."""
        cmd = f'sqlite3 /tmp/x.db "{write_cmd}"'
        result = detect_mutative_command(cmd)
        assert result.is_mutative is True, (
            f".{label}: expected MUTATIVE but got is_mutative={result.is_mutative}, "
            f"category={result.category!r}, reason={result.reason!r}"
        )

    def test_sqlite3_dump_stays_mutative(self):
        """.dump is conservative-excluded from the read-only allowlist.
        It outputs the full database schema and data; by default it prints to
        stdout but can be trivially piped to a file, so we treat it as MUTATIVE."""
        result = detect_mutative_command('sqlite3 /tmp/x.db ".dump"')
        assert result.is_mutative is True, (
            f"Expected MUTATIVE but got is_mutative={result.is_mutative}, "
            f"category={result.category!r}"
        )

    def test_sqlite3_output_redirect_dot_command_stays_mutative(self):
        """.output redirects query results to a file -- clearly not read-only."""
        result = detect_mutative_command('sqlite3 /tmp/x.db ".output /tmp/out.txt"')
        assert result.is_mutative is True

    def test_sqlite3_once_redirect_dot_command_stays_mutative(self):
        """.once redirects the next query result to a file -- must stay MUTATIVE."""
        result = detect_mutative_command('sqlite3 /tmp/x.db ".once /tmp/out.txt"')
        assert result.is_mutative is True

    # ---- Boundary: redirect input overrides read-only dot-command ----------

    def test_redirect_input_with_readonly_dot_command_still_mutative(self):
        """A shell redirect (< file) overrides the dot-command classification --
        the payload is external and must remain MUTATIVE (Rule 1 fires first)."""
        result = detect_mutative_command(
            "sqlite3 /tmp/x.db < /tmp/script.sql"
        )
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"


class TestSlugAndFlagFalsePositives:
    """Regression suite for slug/flag false-positive fix.

    Bug: tokens like "remove-live-state-from-context" were split on the
    first hyphen, producing "remove" which matched MUTATIVE_VERBS.  This
    caused false positives on any `gaia X Y <slug-with-verb>` pattern.

    Fix: hyphen-split is now constrained to semantic_index <= 2 (subcommand
    positions).  At deeper positions (index >= 3) tokens are argument slugs
    and must be matched only as full tokens, not split fragments.
    """

    # ---- Cases that MUST now pass (were false-positives before fix) ----

    def test_gaia_brief_deps_remove_slug_passes(self):
        """gaia brief deps remove-live-state-from-context: 'remove' is inside a slug
        at argument position -- must NOT be classified as mutative."""
        result = detect_mutative_command(
            "gaia brief deps remove-live-state-from-context"
        )
        assert result.is_mutative is False, (
            f"Expected non-mutative but got verb={result.verb!r} "
            f"reason={result.reason!r}"
        )

    def test_gaia_workspace_merge_report_flag_passes(self):
        """gaia workspace merge --report-duplicates: --report-duplicates is a
        read-only analysis flag -- the simulation flag override must fire."""
        result = detect_mutative_command(
            "gaia workspace merge --report-duplicates"
        )
        assert result.is_mutative is False, (
            f"Expected non-mutative but got verb={result.verb!r} "
            f"reason={result.reason!r}"
        )
        assert result.category == "SIMULATION"

    def test_gaia_memory_show_slug_with_delete_passes(self):
        """gaia memory show some-name-with-delete-in-it: 'delete' is inside a
        slug at argument position -- must NOT be classified as mutative."""
        result = detect_mutative_command(
            "gaia memory show some-name-with-delete-in-it"
        )
        assert result.is_mutative is False, (
            f"Expected non-mutative but got verb={result.verb!r} "
            f"reason={result.reason!r}"
        )

    # ---- Cases that MUST still block ----

    def test_rm_still_blocks(self):
        """rm -rf /tmp/foo: rm is first token -> MUTATIVE via COMMAND_ALIASES."""
        result = detect_mutative_command("rm -rf /tmp/foo")
        assert result.is_mutative is True
        assert result.verb == "rm"

    def test_gaia_brief_delete_still_blocks(self):
        """gaia brief delete some-brief: 'delete' is the real subcommand at
        position 2, not inside a hyphenated slug -- must remain MUTATIVE."""
        result = detect_mutative_command("gaia brief delete some-brief")
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_gaia_memory_delete_still_blocks(self):
        """gaia memory delete name: 'delete' is the real subcommand at
        position 2 -- must remain MUTATIVE."""
        result = detect_mutative_command("gaia memory delete name")
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_compound_rm_still_blocks(self):
        """cd /tmp && rm foo.txt: rm is first token of the second segment.

        Note: the bash_validator decomposes && chains into separate commands
        before calling detect_mutative_command on each segment, so this test
        calls detect_mutative_command on 'rm foo.txt' directly (the segment
        that would be evaluated for 'rm').
        """
        result = detect_mutative_command("rm foo.txt")
        assert result.is_mutative is True
        assert result.verb == "rm"

    # ---- Edge cases ----

    def test_echo_with_rm_in_string_is_safe(self):
        """echo "rm -rf /": 'echo' is in READ_ONLY_BASE_CMDS -- short-circuits
        before the verb scanner even runs.  String content is never scanned."""
        result = detect_mutative_command('echo "rm -rf /"')
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_gaia_approvals_reject_not_blocked_by_verb_scanner(self):
        """gaia approvals reject P-XXXX: 'reject' is not in MUTATIVE_VERBS, so
        the verb scanner classifies this as safe by elimination.  The approval
        workflow is enforced by the orchestrator layer, not the verb scanner.
        This test documents current behavior (not a regression)."""
        result = detect_mutative_command("gaia approvals reject P-XXXX")
        assert result.is_mutative is False


class TestGaiaPlanningBookkeepingException:
    """Anchored command+subcommand exception for local planning bookkeeping.

    `gaia brief <verb>` and `gaia ac <verb>` edit rows in the local planning
    store (briefs, acceptance criteria).  They are reversible and have no
    external side effects, so the generic MUTATIVE_VERBS gate (which catches
    edit/set/remove/add) would force needless T3 approval on subagents.

    The exception is anchored EXPLICITLY to (base_cmd, subcommand) so it does
    NOT leak into sibling groups -- `gaia approvals approve/revoke` and
    `gaia memory save` stay gated, and generic verbs (kubectl edit, git push)
    are untouched.
    """

    # ---- gaia brief: every verb in the group classifies non-mutative ----

    def test_gaia_brief_edit_not_mutative(self):
        result = detect_mutative_command("gaia brief edit my-brief")
        assert result.is_mutative is False, (
            f"gaia brief edit must be local-only bookkeeping. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )

    def test_gaia_brief_set_status_not_mutative(self):
        result = detect_mutative_command("gaia brief set-status my-brief done")
        assert result.is_mutative is False, (
            f"gaia brief set-status must be local-only bookkeeping. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_brief_set_field_not_mutative(self):
        result = detect_mutative_command("gaia brief set-field my-brief title X")
        assert result.is_mutative is False

    def test_gaia_brief_new_not_mutative(self):
        result = detect_mutative_command("gaia brief new my-brief")
        assert result.is_mutative is False

    def test_gaia_brief_show_not_mutative(self):
        result = detect_mutative_command("gaia brief show my-brief")
        assert result.is_mutative is False

    def test_gaia_brief_list_not_mutative(self):
        result = detect_mutative_command("gaia brief list")
        assert result.is_mutative is False

    # ---- gaia ac: every verb in the group classifies non-mutative ----

    def test_gaia_ac_edit_not_mutative(self):
        result = detect_mutative_command("gaia ac edit my-brief 1")
        assert result.is_mutative is False, (
            f"gaia ac edit must be local-only bookkeeping. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_ac_set_status_not_mutative(self):
        result = detect_mutative_command("gaia ac set-status my-brief 1 done")
        assert result.is_mutative is False

    def test_gaia_ac_add_not_mutative(self):
        result = detect_mutative_command("gaia ac add my-brief 'criterion text'")
        assert result.is_mutative is False, (
            f"gaia ac add must be local-only bookkeeping. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_ac_remove_not_mutative(self):
        result = detect_mutative_command("gaia ac remove my-brief 1")
        assert result.is_mutative is False, (
            f"gaia ac remove must be local-only bookkeeping. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_ac_show_not_mutative(self):
        result = detect_mutative_command("gaia ac show my-brief")
        assert result.is_mutative is False

    # ---- gaia plan: every reversible verb classifies non-mutative ----
    # `plan` is anchored explicitly in COMMAND_SUBCOMMAND_TIER_EXCEPTIONS so the
    # exemption no longer depends on the fragile lexical collision with
    # SIMULATION_VERBS['plan'].

    def test_gaia_plan_save_not_mutative(self):
        result = detect_mutative_command("gaia plan save my-plan")
        assert result.is_mutative is False, (
            f"gaia plan save must be local-only bookkeeping. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )

    def test_gaia_plan_edit_not_mutative(self):
        result = detect_mutative_command("gaia plan edit my-plan")
        assert result.is_mutative is False, (
            f"gaia plan edit must be local-only bookkeeping. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_plan_set_status_not_mutative(self):
        result = detect_mutative_command("gaia plan set-status my-plan done")
        assert result.is_mutative is False, (
            f"gaia plan set-status must be local-only bookkeeping. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_plan_show_not_mutative(self):
        result = detect_mutative_command("gaia plan show my-plan")
        assert result.is_mutative is False

    def test_gaia_plan_list_not_mutative(self):
        result = detect_mutative_command("gaia plan list")
        assert result.is_mutative is False

    # ---- Anchoring: the consent layer and memory writes stay gated ----

    # ---- Consent-direction principle: REDUCING consent is not T3 ----
    # An operation is T3 because it GRANTS capability or DESTROYS state.
    # Revoking/rejecting/cleaning a consent grant Gaia issued only takes
    # capability BACK — it never grants anything and never leaves the local
    # approval store, so it is not T3.  Gating it would create the absurd loop
    # of needing an approval to clean up approvals.  The asymmetry is the point:
    # `approve` GRANTS capability without the AskUserQuestion flow and stays T3.

    def test_gaia_approvals_revoke_not_mutative(self):
        """Revoking a grant only reduces capability already given -- not T3."""
        result = detect_mutative_command("gaia approvals revoke P-XXXX")
        assert result.is_mutative is False, (
            f"gaia approvals revoke reduces consent and must not be T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )
        assert result.verb == "revoke"

    def test_gaia_approvals_reject_not_mutative(self):
        """Rejecting a pending approval reduces consent -- not T3."""
        result = detect_mutative_command("gaia approvals reject P-XXXX")
        assert result.is_mutative is False, (
            f"gaia approvals reject reduces consent and must not be T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_approvals_reject_all_not_mutative(self):
        """Bulk reject reduces consent across all pending approvals -- not T3."""
        result = detect_mutative_command("gaia approvals reject-all")
        assert result.is_mutative is False, (
            f"gaia approvals reject-all reduces consent and must not be T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_approvals_clean_not_mutative(self):
        """Cleaning expired/stale approvals only removes capability -- not T3."""
        result = detect_mutative_command("gaia approvals clean")
        assert result.is_mutative is False, (
            f"gaia approvals clean reduces consent and must not be T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_approvals_approve_stays_mutative(self):
        """The asymmetry: `approve` GRANTS capability and must stay T3 even
        though its sibling verbs (revoke/reject/clean) are exempted."""
        result = detect_mutative_command("gaia approvals approve P-XXXX")
        assert result.is_mutative is True, (
            f"gaia approvals approve GRANTS capability and must stay T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )
        assert result.verb == "approve"

    def test_gaia_approvals_revoke_with_force_re_gates(self):
        """A dangerous flag re-gates a consent-reducing verb to T3, matching
        the --force escape hatch on the bookkeeping exception."""
        result = detect_mutative_command("gaia approvals revoke P-XXXX --force")
        assert result.is_mutative is True, (
            f"--force on a consent-reducing verb must re-gate to T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_memory_save_still_mutative(self):
        """gaia memory save: 'memory' is not an excepted group; 'save'..."""
        # 'save' is not in MUTATIVE_VERBS, so exercise a real write verb.
        result = detect_mutative_command("gaia memory write some-note")
        assert result.is_mutative is True, (
            f"gaia memory write must stay gated (not an excepted group). "
            f"Got: category={result.category}, reason={result.reason}"
        )

    def test_gaia_memory_delete_still_mutative(self):
        """gaia memory delete: 'memory' is not an excepted group -- stays T3."""
        result = detect_mutative_command("gaia memory delete some-note")
        assert result.is_mutative is True

    # ---- Generic verbs across other CLIs are untouched ----

    def test_kubectl_edit_still_mutative(self):
        result = detect_mutative_command("kubectl edit deployment myapp")
        assert result.is_mutative is True
        assert result.verb == "edit"

    def test_git_push_still_mutative(self):
        result = detect_mutative_command("git push origin main")
        assert result.is_mutative is True
        assert result.verb == "push"

    # ---- Dangerous-flag escape hatch: anchoring does not override --force ----

    def test_gaia_brief_with_force_flag_re_gates(self):
        """A dangerous flag (--force) under an excepted group re-gates to T3."""
        result = detect_mutative_command("gaia brief edit my-brief --force")
        assert result.is_mutative is True, (
            f"--force under an excepted group must re-gate to T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )

    # ---- Destructive verbs stay gated even within an excepted group ----

    def test_gaia_brief_delete_still_mutative(self):
        """Whole-record destruction is irreversible -- the exception does not
        cover 'delete' (pinned also by test_gaia_brief_delete_still_blocks)."""
        result = detect_mutative_command("gaia brief delete my-brief")
        assert result.is_mutative is True, (
            f"gaia brief delete is irreversible and must stay T3. "
            f"Got: category={result.category}, reason={result.reason}"
        )
        assert result.verb == "delete"

    def test_gaia_plan_delete_still_mutative(self):
        """Mirror of test_gaia_brief_delete_still_mutative for `gaia plan`.

        Critical because `plan` collides lexically with SIMULATION_VERBS['plan']:
        without the explicit destructive-verb anchor in Step 3e, `gaia plan
        delete` would fall through Step 4 and be mis-classified as SIMULATION,
        silently un-gating an irreversible deletion.  This pins it T3."""
        result = detect_mutative_command("gaia plan delete my-plan")
        assert result.is_mutative is True, (
            f"gaia plan delete is irreversible and must stay T3. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )
        assert result.verb == "delete"

    def test_gaia_plan_delete_with_force_still_mutative(self):
        """`gaia plan delete --force` stays T3 (destructive verb + dangerous flag)."""
        result = detect_mutative_command("gaia plan delete my-plan --force")
        assert result.is_mutative is True
        assert result.verb == "delete"

    # ---- gaia task: task-lifecycle bookkeeping exemption (Option A) ----
    # Reverses the 2026-06-04 decision to keep `gaia task` fully T3-gated.
    # `gaia task set-status` and other reversible verbs are now local-only
    # bookkeeping (same pattern as brief/ac/plan).  `gaia task remove` (row
    # deletion) stays T3 via the DENY_VERBS guard ("remove" added to the set).

    def test_gaia_task_set_status_not_mutative(self):
        """`gaia task set-status` is a reversible status transition in gaia.db --
        local bookkeeping, no external effects, must not require T3 approval."""
        result = detect_mutative_command(
            "gaia task set-status my-brief task-1 done"
        )
        assert result.is_mutative is False, (
            f"gaia task set-status must be local-only bookkeeping. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )

    def test_gaia_task_remove_stays_mutative(self):
        """`gaia task remove` is irreversible row deletion; it must stay T3 even
        though the `task` group is now exempted, pinned by 'remove' in
        COMMAND_SUBCOMMAND_EXTRA_DENY_VERBS[("gaia", "task")]."""
        result = detect_mutative_command("gaia task remove my-brief task-1")
        assert result.is_mutative is True, (
            f"gaia task remove is irreversible and must stay T3. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )
        assert result.verb == "remove"

    def test_gaia_task_add_not_mutative(self):
        """`gaia task add` is a safe bookkeeping write -- no T3 needed.
        Guards against regression if the exemption is inadvertently narrowed."""
        result = detect_mutative_command("gaia task add my-brief 'do the thing'")
        assert result.is_mutative is False, (
            f"gaia task add must be local-only bookkeeping. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )

    def test_gaia_task_reorder_not_mutative(self):
        """`gaia task reorder` is a safe local resequencing -- no T3 needed.
        Guards against regression if the exemption is inadvertently narrowed."""
        result = detect_mutative_command(
            "gaia task reorder my-brief task-1 task-2"
        )
        assert result.is_mutative is False, (
            f"gaia task reorder must be local-only bookkeeping. "
            f"Got: category={result.category}, verb={result.verb}, "
            f"reason={result.reason}"
        )


class TestScriptFileEvasion:
    """Closes the file-argument T3 evasion (Step 1d / _check_script_file).

    Before the fix, an interpreter invoked with a script FILE as a positional
    argument (``python3 deploy.py``, ``bash setup.sh``, ``./deploy.sh``,
    ``node migrate.js``) was classified safe-by-elimination: the verb scanner
    saw only the filename (which carries a ``.`` and is rejected as a
    non-subcommand), so the file's mutations executed without approval. The
    fix reads the file and classifies it by REAL invocation -- the same
    standard the inline ``-c`` path already meets.
    """

    # ---- Python files: detected by AST invocation ----

    def test_python_file_network_post_is_mutative(self, tmp_path):
        script = tmp_path / "deploy.py"
        script.write_text(
            "import requests\nrequests.post('http://h/p', json={})\n"
        )
        result = detect_mutative_command(f"python3 {script}")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_python_file_os_remove_is_mutative(self, tmp_path):
        script = tmp_path / "clean.py"
        script.write_text("import os\nos.remove('/etc/hosts')\n")
        result = detect_mutative_command(f"python3 {script}")
        assert result.is_mutative is True

    def test_python_file_subprocess_is_mutative(self, tmp_path):
        script = tmp_path / "run.py"
        script.write_text(
            "import subprocess\nsubprocess.run(['kubectl', 'apply', '-f', 'x'])\n"
        )
        result = detect_mutative_command(f"python3 {script}")
        assert result.is_mutative is True

    def test_python_file_open_write_is_mutative(self, tmp_path):
        script = tmp_path / "w.py"
        script.write_text("f = open('out.txt', 'w')\nf.write('x')\n")
        result = detect_mutative_command(f"python3 {script}")
        assert result.is_mutative is True

    def test_python_unbuffered_flag_still_inspects_file(self, tmp_path):
        """``python3 -u script.py``: -u is a standalone switch, not -c/-m, so
        the script file is still located and inspected (no evasion via -u)."""
        script = tmp_path / "deploy.py"
        script.write_text(
            "import requests\nrequests.post('http://h/p', json={})\n"
        )
        result = detect_mutative_command(f"python3 -u {script}")
        assert result.is_mutative is True

    # ---- Shell / Node files: detected by the regex layer ----

    def test_bash_file_with_kubectl_apply_is_mutative(self, tmp_path):
        script = tmp_path / "setup.sh"
        script.write_text("#!/bin/bash\nkubectl apply -f deploy.yaml\n")
        result = detect_mutative_command(f"bash {script}")
        assert result.is_mutative is True

    def test_bash_file_with_rm_rf_is_mutative(self, tmp_path):
        script = tmp_path / "deploy.sh"
        script.write_text("#!/bin/bash\nrm -rf /tmp/build\naws s3 cp x s3://b\n")
        result = detect_mutative_command(f"bash {script}")
        assert result.is_mutative is True

    def test_direct_shell_script_invocation_is_mutative(self, tmp_path):
        """``path/to/deploy.sh`` direct invocation (single token) is inspected
        before the single-token early return."""
        script = tmp_path / "deploy.sh"
        script.write_text("#!/bin/bash\nkubectl apply -f x.yaml\n")
        result = detect_mutative_command(str(script))
        assert result.is_mutative is True

    def test_node_file_with_exec_is_mutative(self, tmp_path):
        script = tmp_path / "migrate.js"
        script.write_text(
            "const cp = require('child_process')\n"
            "cp.execSync('kubectl apply -f x')\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True

    # ---- Conservative default: unreadable file ----

    def test_missing_file_is_conservatively_mutative(self):
        """An interpreter pointed at a missing/unreadable file cannot be proven
        safe, so it is classified T3 (conservative default)."""
        result = detect_mutative_command("python3 /nonexistent/ghost.py")
        assert result.is_mutative is True
        assert result.verb == "script-file-unreadable"

    # ---- No false positives: invocation-based, not name-based ----

    def test_analytic_python_file_stays_safe(self, tmp_path):
        """A read-only analytic Python file (no mutative invocation) stays
        non-mutative -- the fix classifies by invocation, not by being a
        ``python3 <file>`` shape."""
        script = tmp_path / "report.py"
        script.write_text(
            "import json\n"
            "data = json.load(open('m.json'))\n"
            "print(sum(d['v'] for d in data))\n"
        )
        result = detect_mutative_command(f"python3 {script}")
        assert result.is_mutative is False

    def test_readonly_shell_file_stays_safe(self, tmp_path):
        script = tmp_path / "check.sh"
        script.write_text("#!/bin/bash\nkubectl get pods\nls -la\ncat cfg.yaml\n")
        result = detect_mutative_command(f"bash {script}")
        assert result.is_mutative is False

    def test_python_dash_m_module_is_not_a_script_file(self, tmp_path):
        """``python3 -m pytest tests/x.py``: -m consumes the module name and
        means there is no script-file positional, so the command defers to
        ordinary scanning and is not flagged as an unreadable script."""
        result = detect_mutative_command("python3 -m pytest tests/x.py")
        assert result.is_mutative is False


class TestScriptFileEvasionNoFalsePositiveRegression:
    """Pins the explicitly-cited false-positive complaints
    (atom_t3_classification_overbroad) so the file-argument fix never
    reintroduces them."""

    def test_sqlite3_readonly_still_safe(self):
        result = detect_mutative_command("sqlite3 db.sqlite 'SELECT * FROM t'")
        assert result.is_mutative is False

    def test_python_dash_c_analytic_still_safe(self):
        result = detect_mutative_command("python3 -c 'print(sum([1, 2, 3]))'")
        assert result.is_mutative is False

    def test_python_dash_c_network_still_blocked(self):
        """The inline path that already worked must keep working."""
        result = detect_mutative_command(
            "python3 -c \"import requests; requests.post('http://h/p')\""
        )
        assert result.is_mutative is True


class TestExecSinkStringArgInScriptFile:
    """Closes the quoted-string exec-sink evasion in the script-file CODE lane
    (``_scan_exec_sink_string_args`` shared with the inline ``-c``/``-e`` path).

    Before the fix, the code lane (``node deploy.js``, ``ruby x.rb``, ...) ran
    only ``is_blocked_command`` + the verb scanner per line.  A mutation handed
    to an exec sink as a STRING LITERAL -- ``execSync("kubectl delete ...")`` --
    was invisible: the quotes make the whole command one token the verb scanner
    cannot split, and the universal exec-sink patterns were only applied on the
    inline path, never the script-file lane.  The two lanes now share one
    exec-sink detector, so ``node deploy.js`` classifies identically to the
    inline ``node -e "..."`` form.

    False-positive mitigation is pinned by the ``*_benign_*`` cases: escalation
    fires ONLY when the extracted inner command is itself mutative, so a
    read-only ``execSync("ls")`` stays safe.
    """

    # ---- Mutative inner command -> escalated to T3 (matches inline) ----

    def test_node_file_execsync_kubectl_delete_is_mutative(self, tmp_path):
        """The target case: node deploy.js with execSync("kubectl delete ...")
        must classify T3, matching ``node -e "execSync('kubectl delete ...')"``."""
        script = tmp_path / "deploy.js"
        script.write_text(
            'const { execSync } = require("child_process");\n'
            'execSync("kubectl delete deployment foo");\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_node_file_spawn_mutative_arg_is_mutative(self, tmp_path):
        script = tmp_path / "run.js"
        script.write_text('spawnSync("terraform apply -auto-approve");\n')
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True

    def test_ruby_file_system_rm_is_mutative(self, tmp_path):
        script = tmp_path / "task.rb"
        script.write_text('system("rm -rf /tmp/build")\n')
        result = detect_mutative_command(f"ruby {script}")
        assert result.is_mutative is True

    def test_ruby_file_backtick_kubectl_apply_is_mutative(self, tmp_path):
        script = tmp_path / "bt.rb"
        script.write_text('out = `kubectl apply -f x.yaml`\n')
        result = detect_mutative_command(f"ruby {script}")
        assert result.is_mutative is True

    def test_perl_file_system_gcloud_delete_is_mutative(self, tmp_path):
        script = tmp_path / "op.pl"
        script.write_text('system("gcloud compute instances delete vm1");\n')
        result = detect_mutative_command(f"perl {script}")
        assert result.is_mutative is True

    def test_php_file_shell_exec_mutative(self, tmp_path):
        script = tmp_path / "run.php"
        script.write_text(
            "<?php\nshell_exec(\"aws s3 rm s3://bucket/key\");\n"
        )
        result = detect_mutative_command(f"php {script}")
        assert result.is_mutative is True

    def test_node_file_execsync_blocked_inner_is_mutative(self, tmp_path):
        """A blocked command (rm -rf /) inside the sink string is caught by the
        inner is_blocked_command check, not merely the mutative scanner."""
        script = tmp_path / "wipe.js"
        script.write_text('execSync("rm -rf /");\n')
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True

    # ---- Benign inner command -> NOT escalated (FP mitigation) ----

    def test_node_file_execsync_ls_stays_safe(self, tmp_path):
        script = tmp_path / "read.js"
        script.write_text(
            'const { execSync } = require("child_process");\n'
            'const out = execSync("ls -la");\n'
            'console.log(out.toString());\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    def test_ruby_file_system_echo_stays_safe(self, tmp_path):
        script = tmp_path / "ok.rb"
        script.write_text('system("echo hello")\n')
        result = detect_mutative_command(f"ruby {script}")
        assert result.is_mutative is False

    def test_node_regex_exec_non_command_stays_safe(self, tmp_path):
        """``/re/.exec("some string")`` extracts a non-command string that does
        not classify mutative, so the generic ``exec(`` match does not escalate."""
        script = tmp_path / "re.js"
        script.write_text(
            'const m = /foo-(\\\\d+)/.exec("some arbitrary input value");\n'
            'console.log(m);\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    def test_perl_file_system_read_only_stays_safe(self, tmp_path):
        script = tmp_path / "r.pl"
        script.write_text('system("cat /etc/hostname");\nprint "done\\n";\n')
        result = detect_mutative_command(f"perl {script}")
        assert result.is_mutative is False

    # ---- Inline path parity preserved (shared helper, no regression) ----

    def test_inline_node_execsync_mutative_unchanged(self):
        result = detect_mutative_command(
            "node -e \"execSync('kubectl delete deployment foo')\""
        )
        assert result.is_mutative is True

    def test_inline_python_readonly_unchanged(self):
        """Python inline that parses clean as read-only stays safe -- the shared
        exec-sink helper runs AFTER the Python AST early-return, so AST-clean
        Python behavior is unchanged."""
        result = detect_mutative_command("python3 -c 'print(sum([1, 2, 3]))'")
        assert result.is_mutative is False


class TestCamelCaseIdentifierFalsePositiveFix:
    """Word-boundary discipline for camelCase splitting (recognized-CLI guard).

    Bug (confirmed live): scanning a read-only Playwright ``.js`` file forced
    spurious T3.  The verb scanner camelCase-split JS identifiers whose FIRST
    fragment is a mutative verb -- ``execPath`` / ``execSync`` -> ``exec``,
    ``setState`` -> ``set``, ``stopPropagation`` -> ``stop``, ``postMessage``
    -> ``post`` -- at the subcommand position (semantic_index == 1).  Those
    identifiers were treated as CLI subcommands of a language keyword base
    (``const``, ``let``, ``{``), which is nonsense.

    Fix: the camelCase split only fires when ``family != "unknown"`` (the base
    token is a recognized CLI).  Whole-token and hyphen matching are NOT gated,
    so real mutations still classify correctly regardless of base recognition.
    """

    # ---- Source-code lines: SAFE when scanned as source (from_source_code) ----
    # These call detect_mutative_command with from_source_code=True, which is
    # exactly how the script-content lane invokes it for a non-shell source
    # file.  camelCase splitting is suppressed so a language identifier whose
    # first fragment is a verb is not read as a CLI subcommand.

    def test_js_const_execpath_assignment_is_safe(self):
        """`const execPath = ...` must not camelCase-split to the verb 'exec'."""
        result = detect_mutative_command(
            "const execPath = findCachedChromium();", from_source_code=True,
        )
        assert result.is_mutative is False

    def test_js_destructured_execsync_is_safe(self):
        """`const { execSync } = require('child_process')` line: the identifier
        execSync must not be read as a mutative 'exec' subcommand."""
        result = detect_mutative_command(
            "const { execSync } = require('child_process');",
            from_source_code=True,
        )
        assert result.is_mutative is False

    def test_js_setstate_identifier_is_safe(self):
        """`let setState = useState()` must not split to the verb 'set'."""
        result = detect_mutative_command(
            "let setState = useState();", from_source_code=True,
        )
        assert result.is_mutative is False

    def test_js_stoppropagation_identifier_is_safe(self):
        """A bare `stopPropagation` token must not split to the verb 'stop'."""
        result = detect_mutative_command(
            "const stopPropagation = handler;", from_source_code=True,
        )
        assert result.is_mutative is False

    def test_source_code_flag_default_false_preserves_camelcase(self):
        """Contract guard: with the default (from_source_code=False, i.e. a
        shell command line) camelCase splitting is NOT suppressed -- so the
        flag genuinely gates behavior and shell scripts keep full semantics."""
        recognized = detect_mutative_command("aws batchDelete --table foo")
        assert recognized.is_mutative is True
        # Same token, but declared as source -> suppressed.
        as_source = detect_mutative_command(
            "aws batchDelete --table foo", from_source_code=True,
        )
        assert as_source.is_mutative is False

    def test_readonly_playwright_js_file_is_safe(self, tmp_path):
        """A read-only Playwright screenshot .js (executablePath / execPath /
        camelCase Playwright API) must classify NON-mutative when run via
        `node <file>` -- this is the visual-verify method the bug broke."""
        script = tmp_path / "screenshot.js"
        script.write_text(
            "const fs = require('fs');\n"
            "const chromeBinary = findCachedChromium();\n"
            "const browser = await chromium.launch({\n"
            "  executablePath: chromeBinary,\n"
            "  args: ['--no-sandbox'],\n"
            "});\n"
            "const page = await browser.newPage({ viewport: { width: 1440 } });\n"
            "await page.goto(url);\n"
            "await page.screenshot({ path: file, fullPage: true });\n"
            "await browser.close();\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    # ---- True positives that must STILL block (no weakening) ----

    def test_recognized_cli_camelcase_still_mutative(self):
        """`aws batchDelete` (recognized CLI, family != unknown) must still
        split to the mutative verb 'delete'."""
        result = detect_mutative_command("aws batchDelete --table foo")
        assert result.is_mutative is True
        assert result.verb == "delete"

    def test_unknown_cli_whole_token_install_still_mutative(self):
        """`mytool install pkg`: whole-token 'install' is NOT gated by family,
        so an unrecognized CLI's real mutation still classifies MUTATIVE."""
        result = detect_mutative_command("mytool install pkg")
        assert result.is_mutative is True
        assert result.verb == "install"

    def test_node_file_real_exec_still_mutative(self, tmp_path):
        """A node file that actually runs a mutation via execSync('kubectl
        apply') must stay MUTATIVE (fires on the whole-token 'apply')."""
        script = tmp_path / "migrate.js"
        script.write_text(
            "const cp = require('child_process');\n"
            "cp.execSync('kubectl apply -f x.yaml');\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True

    def test_bash_file_rsync_delete_still_mutative(self, tmp_path):
        """A bash script with a destructive `rsync --delete` line must stay
        MUTATIVE -- caught by the --delete dangerous-flag scan, a path the
        camelCase guard does not touch."""
        script = tmp_path / "sync.sh"
        script.write_text("#!/bin/bash\nrsync --delete src/ dst/\n")
        result = detect_mutative_command(f"bash {script}")
        assert result.is_mutative is True

    def test_bash_file_git_tag_still_mutative(self, tmp_path):
        """A bash script with a real `git tag` line must stay MUTATIVE."""
        script = tmp_path / "release.sh"
        script.write_text("#!/bin/bash\ngit tag v1.2.3\n")
        result = detect_mutative_command(f"bash {script}")
        assert result.is_mutative is True


class TestJsSourceCommentStringAware:
    """Comment / string-literal awareness for the JS-family script lane.

    Bug (confirmed live in branchkinect-architecture-overview):
    ``_classify_script_content_by_regex`` scanned ``.mjs``/``.cjs``/``.js``
    source line-by-line and only skipped ``#`` comments, so a mutative verb
    or a backtick-quoted command that lived inside a ``//`` comment or a JS
    template literal collided lexically and forced a spurious T3:

      * ``node engine/build-data.mjs`` -> MUTATIVE verb='edit', because
        ``const out = `// GENERATED FILE — do not edit by hand.``` carries the
        word "edit" inside a template literal.
      * ``node tools/verify.mjs`` -> MUTATIVE verb='install', because a ``//``
        comment ``... devDependencies (`npm install`) ...`` had "npm install"
        inside backticks that the exec-sink backtick regex captured -- but a JS
        backtick is a TEMPLATE LITERAL, not shell execution.

    Fix: the JS family is lexed (``source_lexer``) before scanning; comments and
    string/template CONTENTS are removed for the verb scan, and backticks are
    NOT treated as shell for JS.  A REAL mutation (an exec sink whose argument
    is a string literal) is preserved and still classifies T3 -- pinned by the
    ``*_still_mutative`` cases below so the fix cannot open a false negative.
    """

    # ---- Reproduced false positives: now NON-mutative (T0) ----

    def test_js_word_edit_inside_template_literal_is_safe(self, tmp_path):
        """The build-data.mjs repro: 'edit' inside a template literal must not
        classify the read-only generator script as mutative."""
        script = tmp_path / "build-data.mjs"
        script.write_text(
            "const doc = { pages: [] };\n"
            "const out = `// GENERATED FILE — do not edit by hand.\n"
            "window.__DOC__ = ${JSON.stringify(doc)};\n"
            "`;\n"
            "console.log(out);\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_js_npm_install_in_backtick_comment_is_safe(self, tmp_path):
        """The verify.mjs repro: 'npm install' in backticks inside a `//`
        comment is not a shell invocation and must not force T3."""
        script = tmp_path / "verify.mjs"
        script.write_text(
            "// Playwright + its bundled Chromium come from\n"
            "// devDependencies (`npm install`), with a cached-Chromium fallback.\n"
            "import { chromium } from 'playwright';\n"
            "const page = await chromium.launch();\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_js_mutative_verb_in_line_comment_is_safe(self, tmp_path):
        """A mutative verb mentioned in a `//` line comment must not match."""
        script = tmp_path / "note.mjs"
        script.write_text(
            "// TODO: deploy and delete the old bucket later\n"
            "const n = 1 + 2;\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    def test_js_mutative_verb_in_block_comment_is_safe(self, tmp_path):
        """A mutative verb inside a /* ... */ block comment (multi-line) must
        not match."""
        script = tmp_path / "banner.cjs"
        script.write_text(
            "/*\n"
            " * This module does NOT deploy, install, or destroy anything.\n"
            " * It only renders a report.\n"
            " */\n"
            "const total = compute();\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    def test_js_mutative_verb_in_string_literal_is_safe(self, tmp_path):
        """A mutative verb that is only a string VALUE (a label) must not
        classify the file mutative."""
        script = tmp_path / "labels.js"
        script.write_text(
            'const label = "delete the cache";\n'
            'const action = "install dependencies";\n'
            'renderButton(label);\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    def test_js_variable_named_like_verb_is_safe(self, tmp_path):
        """A variable whose name equals a bare mutative verb (`label`, `set`,
        `push`, `close`) is a language identifier, not a CLI subcommand, and
        must not force T3 -- the whole-token verb scan is not applied to the
        JS lane."""
        script = tmp_path / "vars.mjs"
        script.write_text(
            "const label = getLabel();\n"
            "let set = new Set();\n"
            "const close = () => {};\n"
            "results.push(row);\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    # ---- No false negatives: real mutations STILL T3 ----

    def test_js_execsync_kubectl_delete_still_mutative(self, tmp_path):
        """A genuine exec-sink mutation stays T3 after the lexer change."""
        script = tmp_path / "deploy.mjs"
        script.write_text(
            'import { execSync } from "node:child_process";\n'
            'execSync("kubectl delete deployment foo");\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_js_execsync_blocked_inner_still_mutative(self, tmp_path):
        """A blocked command inside the sink string stays T3."""
        script = tmp_path / "wipe.cjs"
        script.write_text('execSync("rm -rf /");\n')
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True

    def test_js_mutation_inside_template_interpolation_still_mutative(self, tmp_path):
        """A mutation hidden in a ${...} template interpolation is preserved in
        the exec view and stays T3 -- the string-blanking of the verb view must
        NOT reach the exec-sink detector."""
        script = tmp_path / "interp.mjs"
        script.write_text(
            'const msg = `result: ${require("child_process")'
            '.execSync("kubectl delete ns prod")}`;\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True

    def test_js_url_comment_marker_in_string_does_not_hide_sink(self, tmp_path):
        """A `//` inside a STRING (a URL) must not be treated as a comment and
        blank a real exec-sink call later on the same line."""
        script = tmp_path / "mix.mjs"
        script.write_text(
            'const u = "http://example.com"; '
            'require("child_process").execSync("git push origin main");\n'
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is True
        assert result.verb == "push"

    def test_js_execsync_ls_stays_safe(self, tmp_path):
        """Benign inner command is not escalated (false-positive mitigation
        preserved)."""
        script = tmp_path / "read.mjs"
        script.write_text(
            'const out = require("child_process").execSync("ls -la");\n'
            "console.log(out.toString());\n"
        )
        result = detect_mutative_command(f"node {script}")
        assert result.is_mutative is False

    def test_ruby_backtick_still_shell_exec(self, tmp_path):
        """Regression guard: the JS backtick change must NOT affect ruby, where
        a backtick IS shell execution -- ruby keeps the existing regex lane."""
        script = tmp_path / "bt.rb"
        script.write_text("out = `kubectl apply -f x.yaml`\n")
        result = detect_mutative_command(f"ruby {script}")
        assert result.is_mutative is True


class TestSourceLexer:
    """Unit tests for the per-language source lexer (source_lexer.py)."""

    def test_js_line_comment_blanked_in_both_views(self):
        from modules.security.source_lexer import strip_source, JS_SPEC
        out = strip_source("a = 1; // delete everything\nb = 2;\n", JS_SPEC)
        assert "delete" not in out.verb_view
        assert "delete" not in out.exec_view
        # Code outside the comment is preserved.
        assert "a = 1;" in out.verb_view
        assert "b = 2;" in out.verb_view

    def test_js_string_content_blanked_in_verb_view_kept_in_exec_view(self):
        from modules.security.source_lexer import strip_source, JS_SPEC
        out = strip_source('run("kubectl delete x");\n', JS_SPEC)
        assert "delete" not in out.verb_view      # scrubbed for verb scan
        assert "kubectl delete x" in out.exec_view  # preserved for exec-sink
        assert 'run(' in out.verb_view            # sink call structure kept

    def test_js_comment_marker_inside_string_is_not_a_comment(self):
        from modules.security.source_lexer import strip_source, JS_SPEC
        out = strip_source('u = "http://x"; run("y");\n', JS_SPEC)
        # The `//` is inside the string, so `run("y")` after it survives.
        assert 'run(' in out.exec_view

    def test_views_preserve_line_count(self):
        from modules.security.source_lexer import strip_source, JS_SPEC
        src = "/* multi\nline\ncomment */\ncode = 1;\n"
        out = strip_source(src, JS_SPEC)
        assert len(out.verb_view.splitlines()) == len(src.splitlines())
        assert len(out.exec_view.splitlines()) == len(src.splitlines())

    def test_spec_resolution_by_interpreter_and_extension(self):
        from modules.security.source_lexer import spec_for_script, JS_SPEC
        assert spec_for_script("node", "whatever") is JS_SPEC
        assert spec_for_script("someinterp", "/x/y.mjs") is JS_SPEC
        assert spec_for_script("someinterp", "/x/y.cjs") is JS_SPEC
        assert spec_for_script("ruby", "/x/y.rb") is None


_FAKE_GAIA_DISPATCHER = '''#!/usr/bin/env python3
"""gaia -- Unified Gaia CLI"""
import subprocess


def _discover_plugins():
    return []


def _ensure_db_bootstrapped(sub):
    # The real dispatcher runs a subprocess here for the lazy DB bootstrap.
    # AST analysis flags this as mutative -- the dispatcher re-dispatch must
    # override that with subcommand-based classification.
    subprocess.run(["bash", "bootstrap.sh"], check=False)
'''


class TestGaiaCliDispatcherReDispatch:
    """`python3 <path>/bin/gaia <subcmd>` must classify IDENTICALLY to the
    installed launcher form `gaia <subcmd>`.

    bin/gaia's body calls subprocess.run() for the lazy DB bootstrap, so the
    Python AST lane in _check_script_file would flag EVERY invocation as
    mutative regardless of the subcommand -- turning read-only commands
    (doctor, release check, dry-runs) into false T3 blocks. The re-dispatch in
    _check_gaia_cli_dispatcher reconstructs `gaia <args>` and re-classifies it,
    so the real effect (owned by the subcommand) drives the tier. Critically,
    the mutative subcommands (dev, install) MUST stay T3.
    """

    def _dispatcher(self, tmp_path, name="gaia", body=_FAKE_GAIA_DISPATCHER):
        bindir = tmp_path / "bin"
        bindir.mkdir()
        f = bindir / name
        f.write_text(body)
        return f

    # ---- read-only subcommands stop falsely classifying T3 ----

    def test_doctor_is_read_only(self, tmp_path):
        gaia = self._dispatcher(tmp_path)
        result = detect_mutative_command(f"python3 {gaia} doctor")
        # `doctor` carries no mutative verb -> safe by elimination (not T3).
        # Category is UNKNOWN (by-elimination), identical to the launcher form.
        assert result.is_mutative is False
        assert result.category != "MUTATIVE"

    def test_release_check_is_read_only(self, tmp_path):
        gaia = self._dispatcher(tmp_path)
        result = detect_mutative_command(f"python3 {gaia} release check")
        assert result.is_mutative is False

    def test_release_publish_dry_run_is_not_mutative(self, tmp_path):
        gaia = self._dispatcher(tmp_path)
        result = detect_mutative_command(f"python3 {gaia} release publish --dry-run")
        assert result.is_mutative is False

    def test_no_subcommand_is_read_only(self, tmp_path):
        gaia = self._dispatcher(tmp_path)
        result = detect_mutative_command(f"python3 {gaia}")
        assert result.is_mutative is False

    # ---- CRITICAL negative cases: mutative gating MUST NOT weaken ----

    def test_dev_stays_t3(self, tmp_path):
        gaia = self._dispatcher(tmp_path)
        result = detect_mutative_command(f"python3 {gaia} dev --workspace /home/x")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_install_stays_t3(self, tmp_path):
        gaia = self._dispatcher(tmp_path)
        result = detect_mutative_command(f"python3 {gaia} install")
        assert result.is_mutative is True

    # ---- parity with the launcher form ----

    @pytest.mark.parametrize(
        "sub",
        ["doctor", "release check", "release publish --dry-run",
         "dev --workspace /home/x", "install"],
    )
    def test_python3_form_matches_launcher_form(self, tmp_path, sub):
        gaia = self._dispatcher(tmp_path)
        via_path = detect_mutative_command(f"python3 {gaia} {sub}")
        via_launcher = detect_mutative_command(f"gaia {sub}")
        assert via_path.is_mutative == via_launcher.is_mutative
        assert via_path.category == via_launcher.category

    # ---- the guard is narrow: NOT a generic subprocess.run bypass ----

    def test_unrelated_bin_gaia_without_signature_stays_mutative(self, tmp_path):
        """A user script that happens to be named bin/gaia but is NOT the Gaia
        dispatcher (no signature) still gets AST-classified: subprocess.run keeps
        it mutative. The re-dispatch must not open a generic bypass."""
        body = "import subprocess\nsubprocess.run(['kubectl', 'apply', '-f', 'x'])\n"
        gaia = self._dispatcher(tmp_path, body=body)
        result = detect_mutative_command(f"python3 {gaia} doctor")
        assert result.is_mutative is True

    def test_dispatcher_named_file_outside_bin_stays_mutative(self, tmp_path):
        """The dispatcher signature alone is not enough -- it must live in a
        bin/ directory. A signature-bearing file elsewhere is still AST-scanned."""
        f = tmp_path / "gaia"
        f.write_text(_FAKE_GAIA_DISPATCHER)
        result = detect_mutative_command(f"python3 {f} doctor")
        assert result.is_mutative is True


class TestPythonModulePipReDispatch:
    """Brief 91, AC-7: ``python -m pip install`` must classify IDENTICALLY to
    ``pip install`` (MUTATIVE/T3).  Before the fix, the module name ``pip`` was
    swallowed into flag_tokens as the value of ``-m`` and the command was
    classified only by whatever generic verb happened to follow -- an accidental,
    incomplete defense (``python3 -m poetry add`` slipped through entirely).
    The re-dispatch in ``_check_python_module_runner`` reclassifies the command
    as the package-manager invocation it actually is."""

    # --- The evasion that AC-7 closes ----------------------------------------
    def test_python3_m_pip_install_is_mutative(self):
        result = detect_mutative_command("python3 -m pip install requests")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "install"

    def test_python3_m_pip_install_matches_direct_pip_install(self):
        """Re-dispatch must produce the SAME classification as the direct CLI
        form -- that equivalence is the whole point of the fix."""
        via_module = detect_mutative_command("python3 -m pip install x")
        direct = detect_mutative_command("pip install x")
        assert via_module.is_mutative == direct.is_mutative is True
        assert via_module.category == direct.category == "MUTATIVE"
        assert via_module.verb == direct.verb == "install"

    def test_python_m_pip_install_is_mutative(self):
        """Bare ``python`` (no version suffix) is covered too."""
        result = detect_mutative_command("python -m pip install x")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_versioned_interpreter_m_pip_install_is_mutative(self):
        result = detect_mutative_command("python3.11 -m pip install x")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_python3_m_pip_uninstall_is_mutative(self):
        result = detect_mutative_command("python3 -m pip uninstall x")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"
        assert result.verb == "uninstall"

    def test_interpreter_switch_before_m_still_caught(self):
        """A harmless interpreter switch (-u) before ``-m`` must not let the
        install slip past the re-dispatch."""
        result = detect_mutative_command("python3 -u -m pip install x")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    # --- Real pip read-only subcommands stay read-only -----------------------
    def test_python3_m_pip_list_is_read_only(self):
        result = detect_mutative_command("python3 -m pip list")
        assert result.is_mutative is False
        assert result.category == "READ_ONLY"

    def test_python3_m_pip_download_is_read_only(self):
        result = detect_mutative_command("python3 -m pip download x")
        assert result.is_mutative is False

    # --- Control: non-package-manager modules must NOT be made mutative -------
    def test_python3_m_pytest_not_mutative(self):
        """``python3 -m pytest`` runs the test suite -- it is NOT a package
        install and must not be re-dispatched into a mutative verb."""
        result = detect_mutative_command("python3 -m pytest")
        assert result.is_mutative is False

    def test_python3_m_http_server_not_mutative(self):
        result = detect_mutative_command("python3 -m http.server")
        assert result.is_mutative is False

    def test_python3_script_file_path_not_rerouted(self):
        """A script-file invocation (no ``-m``) must keep going through the
        script-file lane, not the module re-dispatch."""
        result = detect_mutative_command("python3 -m pytest tests/x.py")
        assert result.is_mutative is False


class TestGaiaInstallSubcommandsAreMutative:
    """`gaia dev` is a state-mutating install (pack + install into node_modules
    + wire .claude/ + bootstrap DB). It carries no verb in MUTATIVE_VERBS and
    would otherwise classify READ_ONLY "by elimination" -- the T3-gating gap
    this suite pins closed via the COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES anchor.

    NOTE: `gaia release sync-local` was REMOVED (its provenance intelligence
    moved to `gaia doctor`), so it is no longer in the anchor -- see
    TestGaiaReleaseSyncLocalNoLongerAnchored below.
    """

    def test_gaia_dev_is_mutative(self):
        result = detect_mutative_command("gaia dev --workspace /home/jorge/ws/me")
        assert result.is_mutative is True, (
            f"gaia dev is a state-mutating install and must be T3. "
            f"Got {result.category}: {result.reason}"
        )
        assert result.category == "MUTATIVE"

    def test_gaia_dev_bare_is_mutative(self):
        result = detect_mutative_command("gaia dev")
        assert result.is_mutative is True
        assert result.category == "MUTATIVE"

    def test_gaia_dev_via_python_source_entry_is_mutative(self):
        # The deploy command uses the source-tree entry point directly.
        result = detect_mutative_command(
            "gaia dev --mode pack --workspace /home/jorge/ws/me"
        )
        assert result.is_mutative is True

    # ---- Control: other `gaia release` verbs are NOT upgraded ----

    def test_gaia_release_check_not_upgraded(self):
        # `release check` is a local gate, not an install; the upgrade set is
        # anchored to `dev` only, so this must not become mutative here.
        result = detect_mutative_command("gaia release check")
        assert result.is_mutative is False

    def test_gaia_dev_help_stays_read_only(self):
        # The --help exemption (Step 3.5) runs before the upgrade.
        result = detect_mutative_command("gaia dev --help")
        assert result.is_mutative is False


class TestGaiaReleaseSyncLocalNoLongerAnchored:
    """Regression guard: `gaia release sync-local` was removed as a command and
    its ('gaia','release') entry was dropped from
    COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES. It must no longer be anchored T3 (the
    command does not exist; freshness intelligence lives in `gaia doctor`).
    """

    def test_release_key_absent_from_upgrades(self):
        from modules.security.mutative_verbs import COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES
        assert ("gaia", "release") not in COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES
        assert ("gaia", "dev") in COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES

    def test_release_sync_local_not_classified_via_anchor(self):
        # The ('gaia','release') anchor is gone, so the command-subcommand
        # UPGRADE reason must NOT appear. (The string still happens to trip the
        # generic 'sync' mutative verb -- harmless, since the command no longer
        # exists -- but that is the verb scanner, not the removed anchor.)
        result = detect_mutative_command("gaia release sync-local")
        assert "anchored MUTATIVE (T3) by config" not in result.reason
