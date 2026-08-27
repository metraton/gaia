#!/usr/bin/env python3
"""The three consent levels of a command substitution, read end to end.

The truth table beside this file asserts ``(is_mutative, tier)`` from the
classifier. That is the right instrument for classification and the wrong one
for CONSENT, because it cannot see the difference that matters most here:

    FREE         the command runs with no consent at all
    APPROVABLE   denied, WITH a consent path -- an approval can unblock it
    CATEGORICAL  denied, with NO consent path -- permanently forbidden

Those three come out of ``BashValidator.validate``, not out of the classifier,
and a change that demotes a hard deny to an approvable one -- or promotes an
ordinary mutation to permanently forbidden -- moves between them while leaving
both halves of the classifier pair untouched. Reading all three in ONE
invocation is what makes over-escalation as loud as under-escalation.

The read-only corpus here is deliberately large. A gate around command
substitution is cheap to make sound and expensive to keep usable: the
approvable tier covers the commonest verbs there are, and a substitution used
as an ARGUMENT -- to get the current directory, list files, count lines, resolve
a branch -- is a constant idiom. Every row below must cost nothing, forever.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
REPO_ROOT = HOOKS_DIR.parent
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from modules.tools.bash_validator import BashValidator  # noqa: E402

FREE = "FREE"
APPROVABLE = "APPROVABLE"
CATEGORICAL = "CATEGORICAL"

# A project path and a user path that are NOT the filesystem root: the permanent
# floor owns the root, and the point of these rows is the tier below it -- the
# one where somebody's actual working tree lives.
PROJECT = "/home/jorge/ws/me/gaia/tools"
USERDIR = "/home/jorge/projects/app"

# (case_id, expected_level, command)
MUTATION_THROUGH_SUBSTITUTION = [
    ("project-dir-delete", APPROVABLE, f"ls $(rm -rf {PROJECT})"),
    ("user-dir-delete", APPROVABLE, f"echo $(rm -rf {USERDIR})"),
    ("cluster-delete", APPROVABLE, "echo $(kubectl delete deployment web -n prod)"),
    ("iac-apply", APPROVABLE, "cat $(terraform apply -auto-approve)"),
    ("iac-destroy", APPROVABLE, "printf '%s' $(terraform destroy -auto-approve)"),
    ("cloud-write-gcloud", APPROVABLE, "echo $(gcloud storage rm -r gs://bucket/data)"),
    ("cloud-write-aws", APPROVABLE, "wc -l $(aws s3 rm s3://bucket/key --recursive)"),
    ("package-publish", APPROVABLE, "echo $(npm publish --access public)"),
    ("release-create", APPROVABLE, "echo $(gh release create v9.9.9)"),
    ("helm-uninstall-double-quoted", APPROVABLE, 'echo "$(helm uninstall app -n prod)"'),
    ("process-substitution", APPROVABLE, "diff /tmp/a <(kubectl delete deploy web)"),
    ("mid-chain", APPROVABLE, "git status && echo $(flux delete kustomization apps)"),
    ("param-expansion-default", APPROVABLE, "echo ${FOO:-$(kubectl delete deploy web)}"),
    ("nested", APPROVABLE, f"echo $(echo $(rm -rf {USERDIR}))"),
    ("body-leading-cd", APPROVABLE, "ls $(cd /tmp && rm -rf /tmp/junk-dir)"),
    ("body-compound", APPROVABLE, "ls $(true && kubectl delete deploy web -n prod)"),
    ("after-ansi-c-word", APPROVABLE, r"echo $'it\'s' $(docker rm -f web)"),
    ("read-carrier-double-quoted", APPROVABLE, f'grep -rn "$(rm -rf {USERDIR})" .'),
    # A mutation handed to an executor AS A STRING. This axis was missing while
    # the code was wrong on it: the validator unwraps a shell payload in an
    # earlier phase, on the command as written, so the bare form looked gated
    # while the detector itself reported nothing -- and every re-dispatched
    # route (a substitution body, a line in a script) was therefore free.
    ("executor-bash-c", APPROVABLE, 'bash -c "helm uninstall app -n prod"'),
    ("executor-in-substitution", APPROVABLE,
     'echo $(bash -c "helm uninstall app -n prod")'),
    ("executor-in-substitution-backtick", APPROVABLE,
     'echo `bash -c "kubectl delete deployment web -n prod"`'),
    ("executor-in-process-substitution", APPROVABLE,
     'diff /tmp/a <(bash -c "terraform apply -auto-approve")'),
    ("executor-in-double-quoted-substitution", APPROVABLE,
     'echo "$(sh -c "helm uninstall app -n prod")"'),
    ("executor-eval", APPROVABLE, 'eval "kubectl delete deployment web -n prod"'),
    ("executor-find-exec", APPROVABLE,
     "find . -name '*.tf' -exec terraform apply -auto-approve {} ;"),
    # The BOUNDARY of the body-count cap, the other axis the corpus missed. The
    # cap is a scan condition, so one substitution past it is never looked at.
    ("body-count-past-the-cap", APPROVABLE,
     "echo " + " ".join(["$(pwd)"] * 64) + " $(rm -rf /home/jorge/projects/app)"),
]

# The floor and the .claude boundary. These were closed BEFORE the mutative lane
# existed and must not soften as it widens: a permanently forbidden operation
# that becomes merely approvable has lost its guarantee just as surely as one
# that becomes free.
PERMANENTLY_FORBIDDEN = [
    ("floor-rm-root", CATEGORICAL, "echo $(rm -rf /)"),
    ("floor-rm-root-backtick", CATEGORICAL, "echo `rm -rf /`"),
    ("floor-device-write", CATEGORICAL, "ls $(dd if=/dev/zero of=/dev/sda)"),
    ("floor-mkfs", CATEGORICAL, "echo $(mkfs.ext4 /dev/sda1)"),
    ("floor-ansi-c", CATEGORICAL, r"echo $'it\'s' $(rm -rf /)"),
    ("floor-deep-nesting", CATEGORICAL, "echo $(" * 11 + "rm -rf /" + ")" * 11),
    ("hooks-overwrite", CATEGORICAL,
     "echo $(cp payload.py .claude/hooks/pre_tool_use.py)"),
    ("hooks-overwrite-quoted", CATEGORICAL,
     'echo "$(cp payload.py .claude/hooks/pre_tool_use.py)"'),
    ("hooks-git-mv", CATEGORICAL,
     "ls $(git mv a.py .claude/hooks/pre_tool_use.py)"),
    # A heredoc body carrying one ordinary apostrophe used to desynchronise the
    # quoting state for every later line, so a write into the protected tree on
    # a line AFTER the heredoc classified as a read -- at level zero, free. The
    # level is the assertion: this must be permanently forbidden, not merely
    # approvable.
    ("heredoc-apostrophe-then-hooks-write", CATEGORICAL,
     "cat <<EOF\nok it's fine\nEOF\necho $(cp payload.py "
     ".claude/hooks/pre_tool_use.py)"),
    ("heredoc-apostrophe-then-root-delete", CATEGORICAL,
     "cat <<EOF\nit's fine\nEOF\necho $(rm -rf /)"),
    ("heredoc-dash-form-apostrophe", CATEGORICAL,
     "cat <<-EOF\n\tit's fine\n\tEOF\necho $(cp payload.py "
     ".claude/hooks/pre_tool_use.py)"),
    ("heredoc-unquoted-body-executes", CATEGORICAL,
     "cat <<EOF\n$(cp payload.py .claude/hooks/pre_tool_use.py)\nEOF"),
    ("here-string-executes", CATEGORICAL,
     'cat <<< "$(cp payload.py .claude/hooks/pre_tool_use.py)"'),
]

# Read-only substitutions in their real idiomatic use. This is the half that
# decides whether the system stays usable.
READ_ONLY_IDIOMS = [
    ("pwd", "echo $(pwd)"),
    ("repo-root", "cd $(git rev-parse --show-toplevel)"),
    ("count-files", "wc -l $(ls)"),
    ("date", "echo $(date +%Y-%m-%d)"),
    ("basename", "echo $(basename /a/b/c.txt)"),
    ("dirname-nested", "echo $(dirname $(pwd))"),
    ("find-then-grep", "grep -rn TODO $(find . -name '*.py')"),
    ("current-branch", 'echo "branch: $(git branch --show-current)"'),
    ("head-sha", "echo $(git rev-parse HEAD)"),
    ("cat-file", 'echo "$(cat /etc/hostname)"'),
    ("which-nested", "ls -la $(dirname $(which python3))"),
    ("whoami", "echo $(whoami)"),
    ("uname", "echo $(uname -sr)"),
    ("hostname", "echo $(hostname)"),
    ("two-process-substitutions", "diff <(sort a.txt) <(sort b.txt)"),
    ("python-version", "echo $(python3 --version)"),
    ("porcelain-count", "echo $(git status --porcelain | wc -l)"),
    ("kubectl-get", "echo $(kubectl get pods -o name)"),
    ("terraform-output", "echo $(terraform output -raw ip)"),
    ("aws-identity",
     "echo $(aws sts get-caller-identity --query Account --output text)"),
    ("gh-pr-number", "echo $(gh pr view --json number -q .number)"),
    ("npm-view", "echo $(npm view react version)"),
    ("git-describe", "echo $(git describe --tags --abbrev=0)"),
    ("sed-print", "echo $(sed -n '1p' README.md)"),
    ("arithmetic", "echo $((2 + 2))"),
    ("param-default-read", 'echo "${NOPE:-$(pwd)}"'),
    ("commit-message-date", 'git commit -m "chore: release $(date +%F)"'),
    ("ls-as-arg", "ls -la $(pwd)"),
    ("ansi-c-then-read", r"printf $'a\tb\t%s\n' $(pwd)"),
    ("git-log-format", "echo $(git log -1 --format=%H)"),
    ("tail-log", "echo $(tail -1 /var/log/syslog)"),
    ("helm-list", "echo $(helm list -n prod -o json)"),
    ("read-body-at-the-descent-bound", "echo $(" * 12 + "pwd" + ")" * 12),
    # Exactly the body-count cap and nothing beyond it. The scan finished; it
    # did not stop. Reporting that as truncated made a long-but-honest command
    # ask for consent to have been read completely.
    ("body-count-exactly-at-the-cap", "echo " + " ".join(["$(pwd)"] * 64)),
    # An interpreter payload that only READS, reached the way the payload lane
    # actually governs it -- through a substitution. This is the row that would
    # turn red if the lane adopted a payload's verdict unconditionally instead
    # of only when it comes back mutative.
    #
    # The BARE spellings (`bash -c "ls -la"`, `eval "echo hi"`, `find -exec
    # grep`) are deliberately NOT here: they resolve APPROVABLE, and they did so
    # before this lane existed. That prompt belongs to the validator's
    # indirect-execution phase, which asks for confirmation on any shell wrapper
    # regardless of payload. Measured both ways -- with the lane live and
    # neutralized -- those rows are identical, so listing them as free controls
    # would assert something the tree has never done and blame this lane for a
    # prompt that is not its.
    ("executor-read-in-substitution", 'echo $(bash -c "git rev-parse HEAD")'),
    ("executor-read-nested-in-substitution",
     'echo $(echo $(sh -c "git branch --show-current"))'),
    ("executor-version-flag", "bash --version"),
    ("executor-no-payload", "sh -c"),
    # Heredocs doing what heredocs are for. The body of a QUOTED delimiter is
    # literal -- naming a substitution there is writing it down, and the shell
    # only ever prints it.
    ("heredoc-plain-text", "cat <<EOF\nhello world\nEOF"),
    ("heredoc-apostrophe-plain", "cat <<EOF\nit's a normal sentence\nEOF"),
    ("heredoc-apostrophe-then-read",
     "cat <<EOF\nit's fine\nEOF\necho $(pwd)"),
    ("heredoc-read-substitution-in-body",
     "cat <<EOF\ncurrent dir: $(pwd)\nEOF"),
    ("heredoc-two-on-one-line",
     "diff <(cat <<A\nit's one\nA\n) <(cat <<B\nit's two\nB\n)"),
    ("heredoc-dash-form-plain", "cat <<-EOF\n\tit's indented\n\tEOF"),
    ("heredoc-then-read-command",
     "cat <<'EOF'\nliteral text\nEOF\ngit status --porcelain"),
    ("here-string-read", 'cat <<< "$(pwd)"'),
]

# A destructive command WRITTEN DOWN rather than run. The shell decides which of
# these is which, and so must the classifier: one character of quoting is the
# whole difference, and getting it wrong here taxes every attempt to report on
# this boundary -- including the reports this very work had to write.
QUOTED_MENTIONS = [
    ("grep-single-quoted", "grep -rn 'echo $(kubectl delete deploy web)' hooks/"),
    ("echo-single-quoted", f"echo '$(rm -rf {USERDIR})'"),
    ("escaped-dollar", 'echo "\\$(terraform apply -auto-approve)"'),
    ("trailing-comment", "echo hello # $(npm publish --access public)"),
    ("commit-message-mention", "git commit -m 'fix: gate $(kubectl delete deploy web)'"),
    ("contract-write", "gaia contract add key 'the form ls $(rm -rf dir) was FREE'"),
    ("awk-field", "awk '{print $(NF)}' report.txt"),
    ("git-log-grep", "git log --grep='$(terraform destroy)' --oneline"),
    ("memory-search", "gaia memory search 'echo $(gcloud storage rm -r gs://b)'"),
    ("ansi-c-mention", r"echo $'a substitution $(npm publish) mentioned'"),
    ("rg-search", "rg -n 'diff <(kubectl delete deploy web)' hooks/"),
    ("param-expansion-paren", "echo $(grep -rn ${x//)/y} /tmp)"),
]


def consent_level(command: str) -> str:
    """Return which of the three consent levels *command* resolves to."""
    result = BashValidator().validate(command, is_subagent=True)
    if result.allowed:
        return FREE
    return APPROVABLE if result.block_response is not None else CATEGORICAL


@pytest.mark.parametrize(
    "case_id,expected,command",
    MUTATION_THROUGH_SUBSTITUTION + PERMANENTLY_FORBIDDEN,
    ids=[c[0] for c in MUTATION_THROUGH_SUBSTITUTION + PERMANENTLY_FORBIDDEN],
)
def test_gated_forms_resolve_at_their_exact_level(case_id, expected, command):
    """Gated is not enough -- the LEVEL is the assertion.

    Under-escalation lets a mutation run unasked; over-escalation turns an
    ordinary mutation into a permanent prohibition, which is just as wrong and
    far easier to ship by accident, since it still looks like "blocked".
    """
    assert consent_level(command) == expected


@pytest.mark.parametrize(
    "case_id,command", READ_ONLY_IDIOMS + QUOTED_MENTIONS,
    ids=[c[0] for c in READ_ONLY_IDIOMS + QUOTED_MENTIONS],
)
def test_reading_and_quoting_never_cost_consent(case_id, command):
    """The usability half, and the one a soundness fix quietly erodes."""
    assert consent_level(command) == FREE


def test_the_corpus_carries_every_direction():
    """A census that lost one of its halves would still pass every row above."""
    assert len(READ_ONLY_IDIOMS) >= 30
    assert len(QUOTED_MENTIONS) >= 10
    assert len(MUTATION_THROUGH_SUBSTITUTION) >= 15
    assert len(PERMANENTLY_FORBIDDEN) >= 8
