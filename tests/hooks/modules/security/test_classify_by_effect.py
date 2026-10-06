"""A mutation reaches consent or the permanent block by the shape of its effect.

Each row is a command a subagent could type, fed to the public Bash boundary
(``BashValidator.validate``) and judged only by the outcome a caller sees:

- ``CONSENT`` -- denied and carrying an ``approval_id``: the user can sign it.
- ``FLOOR``   -- denied with no ``approval_id``: categorical, no grant lifts it.
- ``FREE``    -- allowed without asking anyone.

``DENIED`` accepts either of the first two; it is used where the brief asks
only that the mutation stop running free, not which of the two stops it.

Every anchor that closes a gap carries its benign twin in the same table: the
nearest command with the same spelling and no destructive effect. Without the
twin a rule widened to "anything that starts with ``git checkout``" would pass
the table while charging every branch switch for a signature.
"""

from __future__ import annotations

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

CONSENT = "consent"
FLOOR = "floor"
DENIED = "denied"
FREE = "free"

_DB = "/home/someone/.gaia/gaia.db"

# A wrapper around gh, declared the way a user declares one.
GH_WRAPPER = "ghwrap"

# (case_id, command, expected_outcome)
CASES = [
    # -- Rewriting a branch that may already exist --------------------------
    ("checkout-B-resets-existing-branch", "git checkout -B main origin/main", CONSENT),
    ("switch-C-resets-existing-branch", "git switch -C main origin/main", CONSENT),
    ("twin-checkout-b-new-branch", "git checkout -b feature/new-work", FREE),
    ("twin-switch-c-new-branch", "git switch -c feature/new-work", FREE),
    ("twin-checkout-existing-branch", "git checkout main", FREE),
    # -- Moving a pull request's remote branch -------------------------------
    ("gh-pr-update-branch", "gh pr update-branch 42", CONSENT),
    ("wrapper-pr-update-branch", f"{GH_WRAPPER} pr update-branch 42 --rebase", CONSENT),
    ("twin-gh-pr-view", "gh pr view 42", FREE),
    ("twin-wrapper-pr-view", f"{GH_WRAPPER} pr view 42", FREE),
    # -- Provoking CI through a gh wrapper declared in GAIA_CLI_ALIASES ------
    ("wrapper-workflow-run", f"{GH_WRAPPER} workflow run ci.yml --ref main", CONSENT),
    ("wrapper-run-rerun", f"{GH_WRAPPER} run rerun 123456", CONSENT),
    ("twin-wrapper-run-view", f"{GH_WRAPPER} run view 123456", FREE),
    ("twin-wrapper-workflow-list", f"{GH_WRAPPER} workflow list", FREE),
    # -- Deleting a Cloud Storage bucket -------------------------------------
    ("gcloud-storage-buckets-delete", "gcloud storage buckets delete gs://prod-data", CONSENT),
    ("gcloud-storage-rm-recursive", "gcloud storage rm -r gs://prod-data", CONSENT),
    ("twin-gcloud-storage-ls", "gcloud storage ls gs://prod-data", FREE),
    ("twin-gcloud-storage-buckets-describe",
     "gcloud storage buckets describe gs://prod-data", FREE),
    # -- Any shell writer over the Gaia database file ------------------------
    ("db-cp-over", f"cp /tmp/restore.db {_DB}", FLOOR),
    ("db-mv-over", f"mv /tmp/restore.db {_DB}", FLOOR),
    ("db-redirect-over", f"echo junk > {_DB}", FLOOR),
    ("db-tee-over", f"tee {_DB}", FLOOR),
    ("db-dd-over", f"dd if=/dev/zero of={_DB} bs=1k count=1", FLOOR),
    ("db-truncate", f"truncate -s 0 {_DB}", FLOOR),
    ("db-python-interpreter",
     "python3 -c \"import sqlite3; c=sqlite3.connect('" + _DB + "'); "
     "c.execute('DELETE FROM handoffs'); c.commit()\"", FLOOR),
    ("db-python-open-write",
     "python3 -c \"open('" + _DB + "', 'w').write('')\"", FLOOR),
    ("db-sudo-cp-over", f"sudo cp /tmp/restore.db {_DB}", FLOOR),
    ("twin-db-mentioned-in-quoted-text",
     f"echo 'never run: cp /tmp/x.db {_DB}'", FREE),
    ("twin-db-listing", f"ls -l {_DB}", FREE),
    ("twin-db-select", f"sqlite3 {_DB} \"SELECT count(*) FROM handoffs\"", FREE),
    ("twin-db-copied-out", f"cp {_DB} /tmp/snapshot.db", CONSENT),
    # -- Executors whose payload travels quoted ------------------------------
    ("xargs-sh-c-push", "xargs sh -c 'git push origin main'", DENIED),
    ("find-exec-sh-c-rm", "find /srv -name '*.log' -exec sh -c 'rm -rf \"$1\"' _ {} \\;", DENIED),
    ("watch-quoted-delete", "watch 'kubectl delete pod web-1'", DENIED),
    ("ssh-quoted-delete", "ssh prod-host 'rm -rf /srv/data'", DENIED),
    ("flock-c-push", "flock /tmp/lockfile -c 'git push origin main'", DENIED),
    ("su-c-delete", "su -c 'rm -rf /srv/data'", DENIED),
    ("script-c-push", "script -qc 'git push origin main' /dev/null", DENIED),
    ("twin-watch-quoted-read", "watch 'kubectl get pods'", FREE),
    ("twin-xargs-sh-c-echo", "xargs sh -c 'echo hello'", FREE),
    # -- A wrapper in front of a floor command leaves it blocked -------------
    ("sudo-rm-root", "sudo rm -rf /", FLOOR),
    ("doas-rm-root", "doas rm -rf /", FLOOR),
    ("timeout-dd-disk", "timeout 10 dd if=/dev/zero of=/dev/sda", FLOOR),
    ("nice-mkfs", "nice -n 10 mkfs.ext4 /dev/sda1", FLOOR),
    ("ionice-dd-disk", "ionice -c3 dd if=/dev/zero of=/dev/sda", FLOOR),
    ("nohup-rm-root", "nohup rm -rf /", FLOOR),
    ("env-mkfs", "env mkfs.ext4 /dev/sda1", FLOOR),
    ("stdbuf-dd-disk", "stdbuf -oL dd if=/dev/zero of=/dev/sda", FLOOR),
    ("time-mkfs", "time mkfs.ext4 /dev/sda1", FLOOR),
    ("stacked-sudo-timeout-rm-root", "sudo -u root timeout -s KILL 5 rm -rf /", FLOOR),
    # -- ...and in front of a T3 command leaves it at consent ----------------
    ("sudo-git-push", "sudo git push origin main", CONSENT),
    ("timeout-gh-workflow-run", "timeout 30 gh workflow run ci.yml", CONSENT),
    ("nice-checkout-B", "nice git checkout -B main origin/main", CONSENT),
    ("twin-timeout-read", "timeout 30 git status", FREE),
    ("twin-time-read", "time ls -la", FREE),
    # -- Retiring a workspace re-keys its rows in gaia.db ---------------------
    ("gaia-workspace-retire-apply", "gaia workspace retire me --into ws --yes", CONSENT),
    ("gaia-workspace-retire-bare", "gaia workspace retire me --into ws", CONSENT),
    ("gaia-workspace-retire-undo",
     "gaia workspace retire --undo /home/someone/.gaia/backups/retire-ledger.json",
     CONSENT),
    ("twin-gaia-workspace-retire-dry-run",
     "gaia workspace retire me --into ws --dry-run", FREE),
    # -- Curating retires phantoms and deletes dangling facets ---------------
    ("gaia-workspace-curate-apply", "gaia workspace curate --yes", CONSENT),
    ("twin-gaia-workspace-curate-dry-run", "gaia workspace curate --dry-run", FREE),
]


def _worktree_unlock_cases():
    """Unlocking a worktree Gaia retains, beside unlocking one it does not.

    Gaia locks every worktree it creates (``gaia.worktree.lock_reason``) so the
    retention collector can tell an owned worktree from an abandoned one;
    unlocking it is what lets the reclaimer, or a plain ``git worktree prune``,
    delete work nobody captured. A worktree outside Gaia's managed roots
    carries no such retention and stays the user's own business.
    """
    from gaia.worktree import worktrees_dir

    retained = Path(worktrees_dir()) / "some-contract-worktree"
    return [
        ("worktree-unlock-retained", f"git worktree unlock {retained}", CONSENT),
        ("twin-worktree-unlock-unmanaged",
         "git worktree unlock /srv/checkouts/my-own-worktree", FREE),
    ]


def _clear_classifier_caches():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def fresh_classifier(monkeypatch):
    monkeypatch.setenv("GAIA_CLI_ALIASES", f"{GH_WRAPPER}=gh")
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


def _outcome(command: str) -> tuple:
    result = BashValidator().validate(
        command, is_subagent=True, session_id="s-767", agent_type="developer",
    )
    if result.allowed:
        return FREE, result.reason
    return (CONSENT if result.approval_id else FLOOR), result.reason


def _assert_outcome(case_id: str, command: str, expected: str) -> None:
    got, reason = _outcome(command)
    if expected == DENIED:
        assert got in (CONSENT, FLOOR), f"{case_id}: {command!r} ran free"
    else:
        assert got == expected, (
            f"{case_id}: {command!r} -> {got}, expected {expected} ({reason[:300]})"
        )


@pytest.mark.parametrize("case_id,command,expected", CASES, ids=[c[0] for c in CASES])
def test_mutation_is_decided_by_its_effect(case_id, command, expected):
    _assert_outcome(case_id, command, expected)


def test_worktree_unlock_is_decided_by_retention():
    for case_id, command, expected in _worktree_unlock_cases():
        _clear_classifier_caches()
        _assert_outcome(case_id, command, expected)
