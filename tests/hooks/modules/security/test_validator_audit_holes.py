"""The validator audit's holes and false refusals: reader options that write or run, sed scripts, and the Gaia CLI read at the program position."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO / "hooks"))

from modules.security.flag_classifiers import OUTCOME_MUTATIVE, OUTCOME_READ_ONLY, classify_by_flags  # noqa: E402
from modules.security.mutative_verbs import (  # noqa: E402
    READ_ONLY_BASE_CMDS, LocalWorkCaller, _is_inside_managed_worktrees_root, _rm_scratch_confined_targets,
    detect_mutative_command,
)
from modules.security.subagent_memory_write_guard import (  # noqa: E402
    COMPUTED_WORDS_REJECTION, IDENTITY_REJECTION, PROPOSAL_DECISION_REJECTION, check, rejection_message,
)


def _signs(command: str) -> bool:
    """The validator's own two steps: the verb detector, then the flag classifier."""
    if detect_mutative_command(command).is_mutative:
        return True
    flagged = classify_by_flags(command)
    return flagged is not None and flagged.outcome == OUTCOME_MUTATIVE


def _subagent(command: str):
    return check(command, is_subagent=True, agent_type="developer")


@pytest.mark.parametrize("command", [
    "sort -o /tmp/probe-sort.txt verb_lexicon.py",
    "sort --output=/tmp/out.txt in.txt",
    "sort --out /tmp/out.txt in.txt",
    "sort -uo out.txt in.txt",
    "sort -oout.txt in.txt",
    "sort --compress-program 'rm -rf build' in.txt",
    "tree -o listing.txt .",
    "tree -a -L 2 -o listing.txt",
    "tree -R -H . -L 1",
    "uniq in.txt out.txt",
    "uniq -c -f 2 in.txt out.txt",
    "xxd -r dump.hex out.bin",
    "xxd -l 16 in.bin out.hex",
    "fd -x rm",
    "fd -e log -X rm",
    "fd --exec-batch rm -- pattern",
    "rg --pre rm needle .",
    "rg --pre=rm needle",
    "rg --hostname-bin rm needle",
    "bat --pager 'rm -rf build' notes.md",
    "bat --pager='sh -c \"rm -rf build\"' notes.md",
    "ag --pager 'rm -rf build' needle",
    "find . -name '*.log' -fls /tmp/list.txt",
    "find . -fprint /tmp/list.txt",
    "less -o /tmp/log.txt notes.md",
    "less --log-file=/tmp/log.txt notes.md",
    "date -s '2020-01-01'",
    "date --set='2020-01-01'",
    "date 010112002020",
    "hostname build-box",
    "hostname -F /tmp/name",
    "file -C -m magic",
    "xargs sort -o out.txt",
    "find . -name '*.txt' -exec sort -o {} {} \\;",
])
def test_a_reader_option_that_writes_or_runs_signs(command):
    assert _signs(command)


@pytest.mark.parametrize("command", [
    "sort in.txt",
    "sort -k2,2 -t, in.txt",
    "sort -t o in.txt",
    "sort -T/tmp/foo -S1G in.txt",
    "sort -rn --key=2 in.txt",
    "tree -L 2 -I node_modules .",
    "tree -a --charset utf-8",
    "uniq -c in.txt",
    "uniq -f 2 -s 1 in.txt",
    "uniq --skip-fields 2 in.txt",
    "xxd in.bin",
    "xxd -l 64 -s 16 in.bin",
    "xxd -r dump.hex",
    "fd -e py pattern",
    "fd -x cat",
    "fd --type f --exclude node_modules pattern",
    "rg --pre-glob '*.pdf' needle",
    "rg -o -e needle -g '*.py'",
    "bat --paging=never notes.md",
    "bat --pager 'less -R' notes.md",
    "ag --passthrough needle",
    "find . -name '*.log' -ls",
    "find . -type f -newer ref -print0",
    "less -N notes.md",
    "date -Iseconds",
    "date +%s",
    "date -d tomorrow +%F",
    "date -u -r notes.md",
    "hostname",
    "hostname -f",
    "file -b notes.md",
    "grep -o -rn needle src",
    "cat -n notes.md",
])
def test_a_reader_that_only_reads_runs_free(command):
    assert not _signs(command)


def test_every_reader_declares_its_effect_surface():
    from modules.security.flag_classifiers import READER_GRAMMARS

    assert set(READER_GRAMMARS) <= READ_ONLY_BASE_CMDS
    assert {"sort", "tree", "uniq", "xxd", "fd", "rg", "bat", "find"} <= set(READER_GRAMMARS)


@pytest.mark.parametrize("command", [
    "sed -n '1e true' CHANGELOG.md",
    "sed e notes.md",
    "sed -n 'w /tmp/out.txt' notes.md",
    "sed -n '/re/W /tmp/out.txt' notes.md",
    "sed 's/a/b/w /tmp/out.txt' notes.md",
    "sed 's/.*/rm -rf build/e' notes.md",
    "sed 's|a|b|ge' notes.md",
    "sed -e 's/a/b/' -e '$e date' notes.md",
    "sed -ne '1e id' notes.md",
    "sed --expression='1e id' notes.md",
    "sed '/x/{s/a/b/;e id\n}' notes.md",
    "sed '1!e true' notes.md",
    "sed -f script.sed notes.md",
    "sed -n \"${LINE}e true\" notes.md",
    "sed \"s/a/$B/e\" notes.md",
    "sed --in-place=.bak 's/a/b/' notes.md",
    "sed --in-pl 's/a/b/' notes.md",
    "sed 's/a/b/' notes.md --unknown-option",
    "find . -name '*.md' -exec sed -n '1e id' {} \\;",
])
def test_a_sed_script_that_writes_runs_or_is_not_shown_signs(command):
    assert _signs(command)


@pytest.mark.parametrize("command", [
    "sed -n '1,40p' notes.md",
    "sed 's/hello/world/g' notes.md",
    "sed -e 's/foo/bar/' -e 's/baz/qux/' notes.md",
    "sed -n '/start/,/end/p' notes.md",
    "sed '$d' notes.md",
    "sed -n '$p' notes.md",
    "sed '1d;$d' notes.md",
    "sed -E 's/(we|eww)e+/x/gI' notes.md",
    "sed 's/w e/e w/' notes.md",
    "sed -n \"${START},${END}p\" notes.md",
    "sed \"s/foo/$BAR/\" notes.md",
    "sed -n \"$((LINE - 2)),$((LINE + 2))p\" notes.md",
    "sed -n $RANGE notes.md",
    "sed \"$SCRIPT\" notes.md",
    "sed -n \"${RANGE}$CMD\" notes.md",
    "sed '/^#/d;/^$/d' notes.md",
    "sed 'y/abc/xyz/' notes.md",
    "sed -n '5{p;q}' notes.md",
    "sed '1a text with e and w' notes.md",
    "sed ':a;N;$!ba;s/\\n/ /g' notes.md",
    "sed -n '10,+5p;0~2p' notes.md",
    "sed -n '\\%re%p' notes.md",
    "sed 's/a/b/2' notes.md",
    "sed -z 's/\\n/,/g' notes.md",
    "sed -s -n '$=' a.txt b.txt",
    "sed '/a/!d' notes.md",
    "sed 's/x/y/;# e w comment' notes.md",
    "sed -n '2q;p' notes.md",
    "sed --sandbox '1e true' notes.md",
    "sed -n -e '/we/p' -- notes.md",
])
def test_a_sed_filter_shown_on_the_line_runs_free(command):
    assert classify_by_flags(command).outcome == OUTCOME_READ_ONLY
    assert not _signs(command)


_WRITE = "memory add --type project --slug x --body y"


@pytest.mark.parametrize("command, refusal", [
    (f'env -S "gaia {_WRITE}"', rejection_message("developer")),
    (f"flock /tmp/gaia.lock -c 'gaia {_WRITE}'", rejection_message("developer")),
    (f"timeout 5 flock /tmp/gaia.lock -c 'gaia {_WRITE}'", rejection_message("developer")),
    (f"script -qc 'gaia {_WRITE}' /dev/null", rejection_message("developer")),
    (f"watch -n 5 'gaia {_WRITE}'", rejection_message("developer")),
    (f"flock /tmp/gaia.lock -c 'cd /tmp && gaia {_WRITE}'", rejection_message("developer")),
    ("flock /tmp/gaia.lock -c 'gaia contract proposals accept 1'", PROPOSAL_DECISION_REJECTION),
    ('env -S "-u GAIA_DISPATCH_AGENT gaia memory search x"', IDENTITY_REJECTION),
    ("flock /tmp/gaia.lock -c 'env -i gaia memory search x'", IDENTITY_REJECTION),
    ("flock /tmp/gaia.lock -c 'gaia memory search $TERM_NAME'", COMPUTED_WORDS_REJECTION),
    (f"uv run gaia {_WRITE}", rejection_message("developer")),
    (f"find . -maxdepth 0 -exec gaia {_WRITE} \\;", rejection_message("developer")),
    (f"some-runner --flag gaia {_WRITE}", rejection_message("developer")),
    (f"python3 -W ignore bin/gaia {_WRITE}", rejection_message("developer")),
    ("env -i gaia memory search x", IDENTITY_REJECTION),
    ("timeout 5 env -u GAIA_DISPATCH_AGENT gaia memory search x", IDENTITY_REJECTION),
    ("gaia memory search $(cat words.txt)", COMPUTED_WORDS_REJECTION),
    ("echo x | xargs gaia memory search", COMPUTED_WORDS_REJECTION),
])
def test_a_gaia_call_a_carrier_runs_meets_the_role_guards(command, refusal):
    assert _subagent(command) == (False, refusal)


@pytest.fixture
def audit_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "CHANGELOG.md").write_text("gaia\n")
    cli = tmp_path / "full" / "bin" / "cli"
    cli.mkdir(parents=True)
    for name in ("metrics.py", "doctor.py"):
        (cli / name).write_text("env = 1\n")
    return tmp_path


@pytest.mark.parametrize("command", [
    "grep -c gaia {root}/src/CHANGELOG.m*",
    "grep -c x {root}/full/bin/cli/metrics.py --include=*.py",
    "grep -n env -i {root}/full/bin/cli/doctor.py",
    "rg -n gaia {root}/src --glob *.md",
    "git -C {root} log --oneline -- gaia *.py",
    "git -C {root} grep -n gaia -- $PATHSPEC",
    "ls gaia bin/cli/memory.py *.md",
    "cat bin/cli/memory.py $EXTRA",
    "wc -l {root}/full/bin/cli/*.py gaia",
    "python3 tools/report.py gaia *.md",
    "env -i grep -c gaia {root}/src/CHANGELOG.md",
    "env -i ls gaia",
    "diff env -i",
    "grep -n 'gaia memory add' notes.md $EXTRA",
    "echo 'run gaia memory add to save it'",
    "git commit -m 'gaia memory add is refused for a subagent'",
    "GAIA_DB=$HOME/.gaia/scratch/x.db gaia paths",
])
def test_a_word_that_is_only_an_argument_is_not_a_gaia_call(command, audit_tree):
    assert _subagent(command.format(root=audit_tree)) == (True, None)


def test_two_dots_inside_a_name_are_not_parent_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import scratch_dir, worktrees_dir

    entry = Path(scratch_dir()) / "a1b2c3.token"
    entry.mkdir(parents=True)
    (entry / "report..draft.txt").write_text("x")
    (entry / "sub").mkdir()
    caller = LocalWorkCaller(agent_id="a1b2c3", cwd=str(entry))
    tree = Path(worktrees_dir()) / "gaia" / "feature..draft"
    tree.mkdir(parents=True)

    assert _rm_scratch_confined_targets(("rm", str(entry / "report..draft.txt")), caller)
    assert _rm_scratch_confined_targets(("rm", str(entry / "sub" / ".." / "report..draft.txt")), caller) is None
    assert _is_inside_managed_worktrees_root(str(tree))
    assert not _is_inside_managed_worktrees_root(str(tree / ".." / "feature..draft"))
