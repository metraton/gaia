"""The comment counter against throwaway repositories with known comment content."""

import pytest

from gaia.comment_lint import is_measured_path, measure, scan_text
from tests.fixtures.comment_repo import code, commit_files, git, interleave, make_change

BASE = {"a.py": "VALUE = 0\n"}

DOCSTRINGS = (
    '"""Module summary."""\n\n\n'
    'def public():\n    """One line."""\n    return 1\n\n\n'
    'def _private():\n    """One line."""\n    return 2\n\n\n'
    'def documented():\n    """Summary.\n\n    More detail.\n    """\n    return 3\n'
)


def run(tmp_path, change, base=BASE):
    return measure(str(make_change(tmp_path, base, change)), head="feat")


def test_a_comment_corrected_in_place_costs_nothing(tmp_path):
    base = {"a.py": "# old claim\nVALUE = 0\n"}
    report = run(tmp_path, {"a.py": "# corrected claim\nVALUE = 0\n"}, base)
    assert (report.net, report.blocked) == (0, False)


def test_over_the_limit_lists_every_added_comment_with_its_line(tmp_path):
    report = run(tmp_path, {"a.py": interleave(40, 6)})
    assert (report.net, report.limit, report.over_limit) == (6, 2, True)
    assert [(path, mark.line) for path, mark in report.offenders][:2] == [("a.py", 1), ("a.py", 8)]
    assert "a.py:1: # fact 0" in report.render()


@pytest.mark.parametrize("comments,blocked", [(5, False), (6, True)])
def test_limit_is_two_percent_of_added_code(tmp_path, comments, blocked):
    report = run(tmp_path, {"a.py": interleave(250, comments)})
    assert (report.limit, report.blocked) == (5, blocked)


def test_one_line_public_docstrings_are_free_and_the_rest_count(tmp_path):
    report = run(tmp_path, {"a.py": DOCSTRINGS})
    assert sorted(mark.text for _, mark in report.offenders) == [
        '"""One line."""', '"""Summary.', "More detail."]


def test_a_banner_is_refused_whatever_the_count(tmp_path):
    report = run(tmp_path, {"a.py": code(300) + "# ==== Section ====\n"})
    assert (report.net, report.over_limit, report.blocked) == (1, False, True)
    assert [mark.line for _, mark in report.banners] == [301]


@pytest.mark.parametrize("path,line", [
    ("a.py", "# --- Helpers ---"),
    ("a.py", "#### Helpers ####"),
    ("a.py", "# ****"),
    ("a.py", "# ~~~ notes ~~~"),
    ("a.sh", "# ___"),
    ("a.ts", "// ===== X ====="),
    ("a.ts", "/*****/"),
    ("a.ts", "/* *** */"),
])
def test_a_line_that_is_mostly_decoration_is_a_banner(path, line):
    assert scan_text(path, f"{line}\nvalue = 1\n").counted[1].banner


@pytest.mark.parametrize("path,line", [
    ("a.py", "# a -> b"),
    ("a.py", "# TODO(x): handle the empty case..."),
    ("a.py", "# see https://my-host.example.com/a-b-c for details"),
    ("a.py", "# a -- b"),
    ("a.py", "# **bold** claim"),
    ("a.ts", "// wait for it..."),
])
def test_arrows_ellipses_and_urls_with_dashes_are_not_banners(path, line):
    assert not scan_text(path, f"{line}\nvalue = 1\n").counted[1].banner


@pytest.mark.parametrize("line", ["# --- Helpers ---", "#### Helpers ####"])
def test_a_short_banner_alone_blocks_the_change(tmp_path, line):
    report = run(tmp_path, {"a.py": code(300) + line + "\n"})
    assert (report.over_limit, report.blocked, len(report.banners)) == (False, True, 1)


@pytest.mark.parametrize("lines,blocked", [(2, False), (3, True)])
def test_a_comment_block_over_two_lines_is_refused(tmp_path, lines, blocked):
    report = run(tmp_path, {"a.py": "# why\n" * lines + code(300)})
    assert (report.over_limit, report.blocked) == (False, blocked)


EXISTING_BLOCK = "# first fact\n# second fact\n# third fact\n" + code(300)


@pytest.mark.parametrize("edited,blocked", [
    ("# first fact, corrected\n# second fact\n# third fact, corrected\n", False),
    ("# first fact\n# third fact\n", False),
    ("# first fact\n# second fact\n# third fact\n# fourth fact\n", True),
    ("# first fact\n# second fact\n# third fact\n# fourth\n# fifth\n", True),
])
def test_only_a_block_the_change_creates_or_lengthens_is_refused(tmp_path, edited, blocked):
    base = {"a.py": EXISTING_BLOCK}
    report = run(tmp_path, {"a.py": edited + code(300)}, base)
    assert (bool(report.runs), report.over_limit) == (blocked, False)


def test_an_existing_block_survives_an_edit_that_shifts_it(tmp_path):
    base = {"a.py": EXISTING_BLOCK}
    report = run(tmp_path, {"a.py": "VALUE = 1\n" + EXISTING_BLOCK.replace("second", "2nd")}, base)
    assert not report.runs


def test_a_new_block_beside_an_existing_one_is_refused(tmp_path):
    base = {"a.py": EXISTING_BLOCK}
    report = run(tmp_path, {"a.py": EXISTING_BLOCK + "# new one\n# new two\n# new three\n"}, base)
    assert [len(block) for _, block in report.runs] == [3]


def test_directives_and_generated_files_are_not_counted(tmp_path):
    change = {
        "a.py": "#!/usr/bin/env python3\n# noqa: E501\nVALUE = 1  # type: ignore\n",
        "gen.py": "# @generated\n# one\n\n# two\n\n# three\n\n# four\n",
    }
    assert run(tmp_path, change).net == 0


def test_trailing_comments_count_in_python_and_javascript_not_in_shell_or_yaml():
    python = scan_text("a.py", "x = 1  # trailing\n# full\n")
    assert sorted(python.counted) == [1, 2] and python.noncode == {2}
    ts = scan_text("a.ts", 'const u = "http://x.com";\n// one\nconst a = 1; // two\n'
                           "/* block\n   more */\nconst b = `\n// text\n`;\n")
    assert sorted(ts.counted) == [2, 3, 4, 5]
    assert sorted(scan_text("a.sh", "#!/bin/sh\n# note\necho hi # not counted\n").counted) == [2]
    assert sorted(scan_text("a.yaml", "# note\nkey: v # not counted\n").counted) == [1]


def test_measured_paths_skip_vendored_and_lock_output():
    assert is_measured_path("src/a.py") and is_measured_path("infra/main.tf")
    assert not any(map(is_measured_path, [
        "pnpm-lock.yaml", "vendor/x.py", "web/node_modules/y.js", "a.min.js", "README.md"]))


def test_base_is_the_nearest_remote_branch_that_lacks_the_change(tmp_path):
    repo = make_change(tmp_path, BASE, {"a.py": interleave(40, 6)}, branch="feat-a")
    git(repo, "update-ref", "refs/remotes/origin/feat-a", "HEAD")
    commit_files(repo, {"b.py": "# one fact\nVALUE = 1\n"})
    git(repo, "checkout", "-q", "-b", "feat")
    report = measure(str(repo), head="feat")
    assert report.base == git(repo, "rev-parse", "feat-a~1")
    assert report.net == 1


def squash_of_pushed_topic(tmp_path, extra=None):
    repo = make_change(tmp_path, BASE, {"a.py": interleave(40, 3)}, branch="topic")
    commit_files(repo, {"b.py": interleave(40, 3)})
    git(repo, "update-ref", "refs/remotes/origin/topic", "HEAD")
    git(repo, "checkout", "-q", "-b", "squash", "main")
    git(repo, "merge", "--squash", "topic")
    commit_files(repo, extra or {}, "squash")
    return repo


def test_a_squash_of_already_pushed_commits_is_not_counted_again(tmp_path):
    repo = squash_of_pushed_topic(tmp_path)
    assert measure(str(repo), head="squash", base="origin/main").blocked
    report = measure(str(repo), head="squash")
    assert (report.excluded, report.net, report.blocked) == (1, 0, False)


def test_a_squash_that_adds_new_comments_still_blocks(tmp_path):
    repo = squash_of_pushed_topic(tmp_path, {"c.py": interleave(40, 6)})
    report = measure(str(repo), head="squash")
    assert (report.excluded, report.blocked) == (0, True)


def test_commits_the_branch_already_pushed_are_not_recounted(tmp_path):
    repo = make_change(tmp_path, BASE, {"a.py": interleave(40, 6)})
    git(repo, "update-ref", "refs/remotes/origin/feat", "HEAD")
    commit_files(repo, {"b.py": "# one fact\nVALUE = 1\n"})
    report = measure(str(repo), head="feat")
    assert (report.excluded, report.net, report.blocked) == (1, 1, False)
    assert [(path, mark.line) for path, mark in report.offenders] == [("b.py", 1)]


def test_merge_commits_are_left_out(tmp_path):
    repo = make_change(tmp_path, BASE, {"a.py": interleave(40, 1)})
    git(repo, "checkout", "-q", "main")
    commit_files(repo, {"m.py": interleave(40, 6)}, "main moves")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "main")
    report = measure(str(repo), head="feat")
    assert report.net == 1 and not report.blocked


def test_nothing_to_compare_without_a_remote_branch_is_not_a_block(tmp_path):
    repo = make_change(tmp_path, BASE, {"a.py": interleave(40, 6)})
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    report = measure(str(repo), head="feat")
    assert (report.base, report.blocked) == (None, False)
