"""Reading or quoting a command as data never asks for a signature.

A fixed, versioned corpus of commands that only READ, or that carry another
command's text as data -- a search pattern, a contract value, a heredoc body,
a help page -- fed to the public Bash boundary (``BashValidator.validate``).
Every row must run free: a denial here is a false positive, whether it carries
an ``approval_id`` (a signature the user is asked for nothing) or not (a
categorical block on prose).

The corpus is the unit of measurement, so rows are added, never edited to make
the rate pass. Its origin is the set of false positives measured in real turns
plus the one the classifier truth table recorded as open
(``mention-force-push-escalates``). The mirror property -- that the same
spellings still stop when they RUN -- is held by
``test_classify_by_effect.py`` and ``test_classifier_truth_table.py``; the
controls at the bottom pin the nearest executing twin of each data form here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for entry in (str(_REPO_ROOT), str(_HOOKS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

_DB = "/home/someone/.gaia/gaia.db"

# (case_id, command) -- every one must be allowed without a signature.
CORPUS = [
    # -- A search whose PATTERN is a mutative word or a whole command ---------
    ("git-grep-uninstall", 'git grep -n "uninstall" -- hooks/'),
    ("git-grep-uninstall-plugin", "git grep -n 'plugin uninstall' -- skills/ bin/"),
    ("git-grep-rm-rf", 'git grep -n "rm -rf" -- hooks/'),
    ("git-grep-kubectl-delete", 'git grep -nE "kubectl (delete|apply)" -- skills/'),
    ("git-grep-force-push", 'git grep -n "git push --force"'),
    ("git-C-grep-uninstall", 'git -C /home/someone/repo grep -n "uninstall"'),
    ("git-grep-sqlite-db", f'git grep -n "sqlite3 {_DB}"'),
    # -- A node invocation that only reads ------------------------------------
    ("node-version", "node --version"),
    ("node-e-read-file",
     "node -e \"console.log(require('fs').readFileSync('package.json', 'utf8'))\""),
    ("node-p-read-version", "node -p \"require('./package.json').version\""),
    ("node-e-list-scripts",
     "node -e \"const p = require('./package.json'); "
     "console.log(Object.keys(p.scripts || {}).join(' '))\""),
    # -- A heredoc body handed to the Gaia CLI as data ------------------------
    ("heredoc-plan-save-names-mutations",
     "gaia plan save --brief=b --content-file=- <<'PLAN'\n"
     "Never run git push --force origin main.\n"
     f"Do not open sqlite3 {_DB} by hand; rm -rf the scratch dir instead.\n"
     "PLAN"),
    ("heredoc-cmdsub-contract-add",
     "gaia contract add evidence_report.key_outputs \"$(cat <<'EOF'\n"
     f"sqlite3 {_DB} 'DELETE FROM handoffs' was never run\n"
     "EOF\n)\""),
    # -- A Gaia CLI value that merely NAMES the SQL shell and the DB file -----
    ("gaia-set-value-names-sql-shell-and-db",
     "gaia contract set --draft-id a1234567890abcdef.x evidence_report.key_outputs "
     f"'[\"sqlite3 {_DB} was never opened\"]'"),
    ("gaia-fill-json-names-sql-shell-and-db",
     "gaia contract fill --draft-id a1234567890abcdef.x --json "
     f"'{{\"evidence_report\": {{\"open_gaps\": [\"read {_DB} via sqlite3 is refused\"]}}}}'"),
    ("gaia-add-value-names-db-redirect",
     "gaia contract add evidence_report.key_outputs "
     f"\"cp /tmp/restore.db {_DB} is a floor write\""),
    # -- pytest's report flag spells -rf ---------------------------------------
    ("pytest-rf", "pytest -rf tests/hooks -q"),
    ("pytest-rfE", "pytest -rfE tests/hooks"),
    ("python-m-pytest-rf", "python3 -m pytest tests/hooks -rf -q -p no:cacheprovider"),
    # -- A help page of a subcommand whose name is a mutative verb ------------
    ("claude-plugin-help", "claude plugin --help"),
    ("claude-plugin-uninstall-help", "claude plugin uninstall --help"),
    ("claude-plugin-install-h", "claude plugin install -h"),
    # -- Open in the classifier truth table (mention-force-push-escalates) ----
    ("gaia-add-value-names-force-push",
     "gaia contract add evidence_report.key_outputs "
     '"git push --force origin main is forbidden"'),
    ("gaia-add-value-names-reset-hard",
     "gaia contract add evidence_report.open_gaps "
     '"git reset --hard was not used"'),
    # -- Measured live while task 779 ran ------------------------------------
    ("heredoc-gate-add-evidence-shape-names-floor",
     "gaia task gate add my-brief 7 --verification-type command "
     "--evidence-shape-file - <<'GATE'\n"
     "kubectl delete namespace prod must stay on the permanent floor;\n"
     "flux uninstall and git push --force origin main are refused.\n"
     "Writes to .claude/settings.json and hooks/ are protected.\n"
     "GATE"),
    ("gaia-plan-change-propose-names-cluster-delete",
     "gaia plan change propose my-brief --reason "
     '"The gate must keep kubectl delete namespace prod and kubectl drain node-1 '
     "refused while text naming them, and edits to .claude/settings.json or "
     '.claude/settings.local.json, is only stored as data."'),
    ("gaia-evidence-add-names-cluster-delete",
     "gaia evidence add --task 779 --summary "
     "\"Verified that kubectl delete ns prod --all stays blocked when run and that "
     'a note naming ~/.claude/settings.json is accepted as data."'),
    ("gaia-contract-fill-json-names-flux-uninstall",
     "gaia contract fill --draft-id a1234567890abcdef.x --json "
     "'{\"evidence_report\": {\"patterns_checked\": "
     "[\"grep uninstall -> the flux uninstall floor rule\"]}}'"),
]

# The executing twin of each data form: same words, and this time they run.
CONTROLS = [
    ("control-git-push-force", "git push --force origin main"),
    ("control-sqlite-write-db", f"sqlite3 {_DB} \"DELETE FROM handoffs\""),
    ("control-node-e-rm", "node -e \"require('fs').rmSync('build', {recursive: true})\""),
    ("control-claude-plugin-uninstall", "claude plugin uninstall gaia"),
    ("control-python-c-rmtree", "python3 -c \"import shutil; shutil.rmtree('/srv/data')\""),
    ("control-cmdsub-runs-push",
     'gaia contract add evidence_report.key_outputs "$(git push origin main)"'),
    ("control-cmdsub-runs-force-push",
     'gaia contract add evidence_report.key_outputs "$(git push --force origin main)"'),
    ("control-gaia-then-newline-force-push",
     'gaia contract add evidence_report.key_outputs "x"\ngit push --force origin main'),
    ("control-cmdsub-runs-sql-write",
     "gaia contract add evidence_report.key_outputs "
     f"\"$(sqlite3 {_DB} 'DELETE FROM handoffs')\""),
    ("control-unquoted-heredoc-expands-sql-write",
     "gaia contract add evidence_report.key_outputs \"$(cat <<EOF\n"
     f"$(sqlite3 {_DB} 'DELETE FROM handoffs')\n"
     "EOF\n)\""),
    ("control-git-grep-pager-runs", "git grep -Orm -e secret"),
    ("control-claude-plugin-install", "claude plugin install gaia@gaia-marketplace"),
    ("control-kubectl-delete-namespace", "kubectl delete namespace prod"),
    ("control-interpreter-heredoc-runs-rmtree",
     "python3 - <<'PY'\nimport shutil\nshutil.rmtree('/srv/data')\nPY"),
    ("control-shell-heredoc-runs-cluster-delete",
     "bash -s <<'SH'\nkubectl delete namespace prod\nSH"),
]


def _clear_classifier_caches():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_checkout(tmp_path, monkeypatch):
    """Run inside a throwaway git checkout, as agents do, with cold caches."""
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)
    _clear_classifier_caches()
    yield root
    _clear_classifier_caches()


def _gated(command: str):
    result = BashValidator().validate(
        command, is_subagent=True, session_id="s-768", agent_type="developer",
    )
    if result.allowed:
        return None
    kind = "consent" if result.approval_id else "floor"
    return f"{kind}: {result.reason[:240]}"


def test_corpus_gates_none():
    """The whole corpus, measured as a rate: gated/total must be 0/N."""
    gated = {}
    for case_id, command in CORPUS:
        _clear_classifier_caches()
        verdict = _gated(command)
        if verdict:
            gated[case_id] = verdict
    report = "\n".join(f"  {cid}: {why}" for cid, why in gated.items())
    assert not gated, f"gated {len(gated)}/{len(CORPUS)} of the data corpus:\n{report}"


@pytest.mark.parametrize("case_id,command", CONTROLS, ids=[c[0] for c in CONTROLS])
def test_executing_twin_still_stops(case_id, command):
    assert _gated(command), f"{case_id}: {command!r} ran free"
