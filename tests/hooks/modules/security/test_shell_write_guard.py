"""Boundary tests for the shell-authored file-write guard.

The guard is only worth having if it holds in BOTH directions, so the two
classes are given equal weight here: writes the shell authors into a working
tree must be refused, and the legitimate uses that surround them every day --
reads, scratch redirection, /dev/null, and tools that write as their own
effect -- must keep passing untouched. A guard verified only on its rejections
is a guard whose false-positive rate is unmeasured, and a false positive is
what teaches agents to route around it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.shell_write_guard import (  # noqa: E402
    check,
    rejection_message,
    targets_working_tree,
)


@pytest.fixture
def repo(tmp_path):
    """A real git working tree, since the guard keys on the .git marker."""
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "node_modules" / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.fixture
def outside():
    """A scratch directory genuinely outside any working tree.

    Deliberately NOT tmp_path: pytest's tmp root can itself sit inside a
    repository (it did on the machine this was written on, which is how the
    negative direction caught it), and a fixture that is secretly in-tree
    proves nothing about the outside-the-tree case it claims to cover.
    """
    with tempfile.TemporaryDirectory(dir="/tmp") as path:
        yield Path(path)


# ---------------------------------------------------------------------------
# Direction 1 -- shell-authored writes into the working tree are REFUSED
# ---------------------------------------------------------------------------

def test_redirect_into_tree_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, reason = check(f"echo hello > {target}", cwd=str(repo))
    assert allowed is False
    assert str(target) in reason


def test_append_redirect_into_tree_is_refused(repo):
    target = repo / "README.md"
    allowed, _ = check(f"echo hello >> {target}", cwd=str(repo))
    assert allowed is False


def test_heredoc_redirected_to_tree_file_is_refused(repo):
    target = repo / "src" / "generated.py"
    command = f"cat <<EOF > {target}\nprint(1)\nEOF"
    allowed, _ = check(command, cwd=str(repo))
    assert allowed is False


def test_tee_into_tree_is_refused(repo):
    """The measured hole: tee was classified T0 and the write executed."""
    target = repo / "notes.md"
    allowed, _ = check(f"echo hi | tee {target}", cwd=str(repo))
    assert allowed is False


def test_tee_append_into_tree_is_refused(repo):
    target = repo / "notes.md"
    allowed, _ = check(f"echo hi | tee -a {target}", cwd=str(repo))
    assert allowed is False


def test_sed_in_place_into_tree_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, _ = check(f"sed -i 's/a/b/' {target}", cwd=str(repo))
    assert allowed is False


def test_sed_in_place_with_backup_suffix_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, _ = check(f"sed -i.bak 's/a/b/' {target}", cwd=str(repo))
    assert allowed is False


def test_perl_in_place_into_tree_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, _ = check(f"perl -i -pe 's/a/b/' {target}", cwd=str(repo))
    assert allowed is False


def test_dd_output_file_into_tree_is_refused(repo):
    target = repo / "src" / "blob.bin"
    allowed, _ = check(f"dd if=/dev/zero of={target}", cwd=str(repo))
    assert allowed is False


def test_relative_destination_resolves_against_cwd(repo):
    allowed, reason = check("echo x > src/app.py", cwd=str(repo))
    assert allowed is False
    assert str(repo / "src" / "app.py") in reason


def test_redirect_behind_a_cd_in_a_chain_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, _ = check(f"cd /tmp && echo x > {target}", cwd="/tmp")
    assert allowed is False


def test_write_hidden_in_command_substitution_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, _ = check(f"echo $(echo hi | tee {target})", cwd=str(repo))
    assert allowed is False


def test_write_in_a_later_chain_component_is_refused(repo):
    target = repo / "src" / "app.py"
    allowed, _ = check(f"ls -la && echo x > {target}", cwd=str(repo))
    assert allowed is False


# ---------------------------------------------------------------------------
# Direction 2 -- legitimate uses must keep passing
# ---------------------------------------------------------------------------

def test_reading_with_sed_n_is_allowed(repo):
    """sed -n prints and writes nothing; a read must not be reachable here."""
    allowed, reason = check(f"sed -n '1,40p' {repo / 'README.md'}", cwd=str(repo))
    assert allowed is True
    assert reason is None


def test_cat_head_and_grep_are_allowed(repo):
    for command in (
        f"cat {repo / 'README.md'}",
        f"head -20 {repo / 'README.md'}",
        f"grep -rn needle {repo}",
        f"find {repo} -name '*.py'",
    ):
        allowed, _ = check(command, cwd=str(repo))
        assert allowed is True, command


def test_redirect_to_dev_null_is_allowed(repo):
    allowed, _ = check("make build > /dev/null", cwd=str(repo))
    assert allowed is True


def test_stderr_to_dev_null_is_allowed(repo):
    allowed, _ = check("ls /nope 2>/dev/null", cwd=str(repo))
    assert allowed is True


def test_file_descriptor_duplication_is_allowed(repo):
    allowed, _ = check("make build 2>&1", cwd=str(repo))
    assert allowed is True


def test_redirect_to_a_temp_outside_the_tree_is_allowed(repo, outside):
    allowed, _ = check(f"echo hi > {outside / 'dump.txt'}", cwd=str(repo))
    assert allowed is True


def test_redirect_to_gaia_scratch_is_allowed(repo):
    scratch = os.path.expanduser("~/.gaia/scratch/probe.json")
    allowed, _ = check(f"echo hi > {scratch}", cwd=str(repo))
    assert allowed is True


def test_gaia_substrate_is_allowed_even_inside_a_repository(repo, monkeypatch):
    """The canonical scratch dir stays writable wherever it is rooted.

    command-execution rule 7 sends every probe and throwaway dump to
    ~/.gaia/scratch, so a guard that blocked it whenever GAIA_DATA_DIR happened
    to resolve inside a repository would forbid the very location the norm
    mandates.
    """
    substrate = repo / ".gaia"
    (substrate / "scratch").mkdir(parents=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(substrate))
    allowed, _ = check(
        f"echo hi > {substrate / 'scratch' / 'probe.json'}", cwd=str(repo)
    )
    assert allowed is True


def test_tee_to_a_temp_outside_the_tree_is_allowed(repo, outside):
    allowed, _ = check(f"echo hi | tee {outside / 'dump.txt'}", cwd=str(repo))
    assert allowed is True


def test_pipe_that_never_touches_disk_is_allowed(repo):
    allowed, _ = check(f"cat {repo / 'README.md'} | grep needle | wc -l", cwd=str(repo))
    assert allowed is True


def test_tools_that_write_as_their_own_effect_are_allowed(repo):
    """A tool's own output is not a shell-authored write."""
    for command in (
        f"pytest --junitxml={repo / 'report.xml'}",
        f"terraform -chdir={repo} apply",
        f"git -C {repo} commit -m 'x'",
        f"npm --prefix {repo} run build",
        f"go build -o {repo / 'bin' / 'app'} ./...",
    ):
        allowed, _ = check(command, cwd=str(repo))
        assert allowed is True, command


def test_generated_artifact_directories_are_allowed(repo):
    target = repo / "node_modules" / "pkg" / "index.js"
    allowed, _ = check(f"echo x > {target}", cwd=str(repo))
    assert allowed is True


def test_quoted_redirect_operator_is_a_mention_not_a_use(repo):
    """shlex strips quotes, so a quoted '>' must not read as a redirect."""
    allowed, _ = check(f"grep '>' {repo / 'README.md'}", cwd=str(repo))
    assert allowed is True


def test_comparison_operator_in_a_quoted_string_is_allowed(repo):
    allowed, _ = check(f"awk '$1 > 5' {repo / 'data.txt'}", cwd=str(repo))
    assert allowed is True


def test_write_outside_any_repository_is_allowed(outside):
    allowed, _ = check(f"sed -i 's/a/b/' {outside / 'notes.txt'}", cwd=str(outside))
    assert allowed is True


# ---------------------------------------------------------------------------
# The message an agent actually sees
# ---------------------------------------------------------------------------

def test_rejection_message_names_the_alternative_and_the_reason():
    message = rejection_message("/repo/src/app.py")
    assert "/repo/src/app.py" in message
    assert "Write" in message and "Edit" in message
    assert "not approvable" in message
    assert "scratch" in message


def test_targets_working_tree_returns_the_resolved_path(repo):
    hit = targets_working_tree("echo x > src/app.py", cwd=str(repo))
    assert hit == str(repo / "src" / "app.py")


def test_empty_command_is_allowed():
    allowed, reason = check("", cwd=None)
    assert allowed is True
    assert reason is None
