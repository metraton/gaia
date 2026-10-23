"""The change-request shape of a forge CLI, read from the command path whatever the binary is called."""

from __future__ import annotations

from typing import Optional, Sequence

CHANGE_REQUEST_NOUNS = frozenset({"pr", "mr", "pulls", "pull-request", "merge-request"})

_GROUPS_ABOVE_THE_NOUN = frozenset({"repos"})


def change_request_action(path: Sequence[str]) -> Optional[str]:
    """The action word that follows a change-request noun at the head of *path*, or None."""
    start = 1 if path[:1] and path[0] in _GROUPS_ABOVE_THE_NOUN else 0
    if len(path) > start + 1 and path[start] in CHANGE_REQUEST_NOUNS:
        return path[start + 1]
    return None
