"""The closed change-verb lexicon (D198 point 1): where an ambiguous word counts, the exceptions, and their snapshot."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.security.mutative_verbs import detect_mutative_command

AMBIGUOUS_SNAPSHOT = {
    "set": "gcloud config set project p signs; yarn set version fetches and signs",
    "copy": "rclone copy ./a remote:b reaches a remote from an unmodelled CLI",
    "start": "docker start web stays on this machine",
    "tag": "docker tag app:1 app:latest is a local image name; docker push publishes it",
    "label": "kubectl label pod web tier=api changes shared state",
    "annotate": "kubectl annotate pod web note=x changes shared state",
}
EXCEPTION_SNAPSHOT = {
    "own-branch-push": "git push -u origin task/1128-verb-lexicon",
    "managed-worktree-ff-merge": "git -C <managed worktree> merge --ff-only task/x",
    "local-merge": "git merge feature-x (curated case merge-local-branch; Jorge 2026-11-05)",
    "own-upstream-ff-merge": "git merge --ff-only origin/main",
    "change-request-open": "ghx pr create --repo o/r --head feat/x --base main --body-file x.md",
    "consent-reducing-gaia": "gaia approvals reject P-1",
}
LOCAL_FAMILIES_SNAPSHOT = {"docker": "docker tag / docker start act on the local daemon"}


def test_lexicon_matches_its_snapshot():
    from modules.security import verb_lexicon

    assert set(verb_lexicon.AMBIGUOUS_VERBS) == set(AMBIGUOUS_SNAPSHOT)
    assert set(verb_lexicon.EFFECT_OVER_VERB_EXCEPTIONS) == set(EXCEPTION_SNAPSHOT)
    assert set(verb_lexicon.LOCAL_FAMILIES) == set(LOCAL_FAMILIES_SNAPSHOT)
    assert verb_lexicon.AMBIGUOUS_VERBS <= frozenset().union(*verb_lexicon.CHANGE_VERB_CLASSES.values())


@pytest.mark.parametrize("command", [
    "docker tag app:1 app:latest",
    "docker start web",
    "gh pr create --repo o/r --head feat/x --base main --body-file /tmp/x.md",
    "ghx pr create --repo o/r --head feat/x --base main --body-file /tmp/x.md",
    "git merge feature-x",
    "git merge --no-ff -m 'merge x' feature-x",
    "git merge --squash feature-x",
    "git merge --abort",
])
def test_local_or_own_work_runs_free(command):
    assert detect_mutative_command(command, cwd="/").is_mutative is False


@pytest.mark.parametrize("command", [
    "git tag v1.0.0",
    "kubectl label pod web tier=api",
    "kubectl annotate pod web note=x",
    "gcloud config set project p",
    "git config set core.hooksPath /x",
    "rclone copy ./a remote:b",
    "yarn set version stable",
    "make start",
    "docker push app:latest",
    "gh pr edit 5 --repo o/r --title t",
    "gh pr merge 5 --squash",
    "ghx pr merge 5",
    "glab mr merge 5",
    "git merge -s ours feature-x",
    "git merge $BRANCH",
])
def test_shared_state_or_unproven_local_signs(command):
    assert detect_mutative_command(command, cwd="/").is_mutative is True
