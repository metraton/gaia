"""The change-request shape of a forge CLI, read from the command path whatever the binary is called."""

from __future__ import annotations

from typing import Optional, Sequence

CHANGE_REQUEST_NOUNS = frozenset({"pr", "mr", "pulls", "pull-request", "merge-request"})

# `az repos pr ...` puts the noun one level down. Only a known group word may
# precede it: `npm run pr create` and `docker run pr create` must not read as a PR.
_NOUN_GROUPS = frozenset({"repos"})


def change_request_action(path: Sequence[str]) -> Optional[str]:
    """The action word that follows a change-request noun at the head of *path*, or None."""
    start = 1 if path[:1] and path[0] in _NOUN_GROUPS else 0
    if len(path) > start + 1 and path[start] in CHANGE_REQUEST_NOUNS:
        return path[start + 1]
    return None
