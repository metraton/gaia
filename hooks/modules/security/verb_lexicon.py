"""The closed change-verb lexicon (D198 point 1); test_verb_lexicon.py snapshots it with the real case behind each entry."""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

# One class per change verb; a new synonym joins the class whose effect it names. Free by
# elimination: add/commit/stash (local), release (`gh release view`), link, unlink (`npm unlink`).
CHANGE_VERB_CLASSES: Dict[str, FrozenSet[str]] = {
    "create": frozenset({
        "create", "put", "insert", "register", "import", "install", "provision",
        "attach", "bind", "connect", "mount", "tag", "label", "annotate",
    }),
    "update": frozenset({
        "update", "patch", "set", "modify", "edit", "configure", "replace", "overwrite",
        "write", "upsert", "mutate", "upgrade", "downgrade", "scale", "resize", "autoscale",
        "move", "rename", "copy", "migrate", "transfer", "rebase", "rollback", "reconcile",
        "rsync", "grant", "assign", "select", "get-credentials", "enable", "disable",
        "suspend", "unsuspend", "pause", "resume", "cordon", "uncordon", "seal", "unseal",
        "approve", "close", "reopen",
    }),
    # "cleanup" is its own word: `gaia cleanup` has no hyphen for the split to reach "clean".
    "delete": frozenset({
        "delete", "destroy", "remove", "drop", "purge", "wipe", "clean", "cleanup",
        "trash", "shred", "srm", "truncate", "kill", "terminate", "uninstall", "unpublish",
        "drain", "evict", "deregister", "detach", "disconnect", "unbind", "force-delete",
        "force-remove", "erase", "revoke", "stop", "shutdown", "halt", "poweroff",
        "nuke", "obliterate", "zap", "annihilate", "expunge", "eradicate", "decommission",
        "teardown", "deprovision",
    }),
    "publish": frozenset({
        "apply", "deploy", "publish", "promote", "push", "merge", "sync", "export",
        "send", "reply", "post", "comment", "login", "upload", "submit", "ship",
    }),
    # "replay" re-runs a stored command (`gaia approvals replay`), the same effect as its first run;
    # run/build/test/make stay out until the exec class gets its own lexicon.
    "exec": frozenset({
        "exec", "execute", "invoke", "trigger", "replay", "rerun", "dispatch", "cancel",
        "start", "restart", "reboot", "reload", "refresh", "abort", "up",
    }),
}

# Only a family proven local relieves these words; an unmodelled CLI (`rclone copy`) is not proven local.
AMBIGUOUS_VERBS: FrozenSet[str] = frozenset({"set", "copy", "start", "tag", "label", "annotate"})
LOCAL_FAMILIES: FrozenSet[str] = frozenset({"docker"})

CHANGE_REQUEST_OWN_WORK: FrozenSet[str] = frozenset({"create", "edit", "update"})
CONSENT_REDUCING_VERBS: Dict[Tuple[str, str], FrozenSet[str]] = {
    ("gaia", "approvals"): frozenset({"revoke", "reject", "reject-all", "clean"}),
}

EFFECT_OVER_VERB_EXCEPTIONS: Dict[str, Tuple[str, str]] = {
    "own-branch-push": (
        "git push [-u] <remote> <own non-default branch>",
        "mutative_verbs._check_git_publish_relief",
    ),
    "managed-worktree-ff-merge": (
        "git -C <managed worktree> merge --ff-only <rev>",
        "mutative_verbs._check_git_publish_relief",
    ),
    "local-merge": (
        "git merge [--no-ff|--squash|--ff-only|-m <msg>] <ref>, git merge --abort|--continue|--quit",
        "mutative_verbs._git_merge_is_local",
    ),
    "own-upstream-ff-merge": (
        "git merge --ff-only <remote>/<checked-out branch>",
        "mutative_verbs._git_local_sync",
    ),
    "change-request-open": (
        "<any forge binary> pr|mr create|edit|update, any --repo for create",
        "mutative_verbs._detect_mutative_command, CHANGE_REQUEST_OWN_WORK",
    ),
    "consent-reducing-gaia": (
        "gaia approvals revoke|reject|reject-all|clean",
        "mutative_verbs.CONSENT_REDUCING_SUBCOMMAND_EXCEPTIONS",
    ),
}


def ambiguous_verb_relieved(verb: str, family: str) -> bool:
    """True when *verb* is an ambiguous word on a family proven local."""
    return verb in AMBIGUOUS_VERBS and family in LOCAL_FAMILIES
