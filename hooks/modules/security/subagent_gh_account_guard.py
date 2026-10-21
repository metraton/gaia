"""
subagent_gh_account_guard.py -- gh's account credentials are not a subagent's to touch.

PreToolUse Bash guard that refuses `gh auth switch|login|refresh|logout|
setup-git|token`, spelled `gh` or as a wrapper declared for it in
GAIA_CLI_ALIASES (`ghx`), from a SUBAGENT dispatch context. gh keeps one active
account per host, shared with every concurrent session on the machine: switch,
login, refresh and logout rewrite or empty it, `setup-git` rewires git's
credential helper, and `token` prints a secret into the transcript. The account
for a command is chosen per process, by running gh through ghx.

Categorical, not approvable, like `subagent_resume_point_guard`: a signature
for a switch was granted five times after the rule existed and the account
still drifted under the other sessions. Reads stay free (`gh auth status`, any
`--help`). The orchestrator never reaches gh at all (`gaia_cli_only_guard`).

Decided on the shell's words per command position: the first word once
wrappers and assignments are peeled, in every operator-split component and in
every `$( )`/backtick body. A wrapper script nobody declared keeps its
ordinary tier: nothing static sees inside it.

Public API:
    ACCOUNT_ACTIONS: the `gh auth` actions refused
    touches_account_credentials(command: str) -> bool
    check(command, *, is_subagent) -> tuple[bool, str | None]
"""

from __future__ import annotations

import os
from typing import Optional, Sequence, Tuple

ACCOUNT_ACTIONS = frozenset({"switch", "login", "refresh", "logout", "setup-git", "token"})

REJECTION_MESSAGE = (
    "A subagent does not switch, add, refresh, remove or read GitHub account "
    "credentials (`gh auth switch|login|refresh|logout|setup-git|token`, also "
    "through ghx): the active account is shared with every session on this "
    "machine. Run gh through ghx, which picks the account from the repository's "
    "origin for that one process: `ghx -C <repo> <gh arguments>`. `gh auth "
    "status` lists the accounts. A missing account is the user's to add -- "
    "report it in your contract. Do NOT retry -- this is not approvable."
)


def _runs_gh(word: str) -> bool:
    from .cli_aliases import is_cli

    return is_cli(os.path.basename(word), "gh")


def _auth_action_refused(argv: Sequence[str]) -> bool:
    """True when *argv*, the words after a gh program, run a refused `auth` action."""
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        index += 2 if argv[index] == "-C" else 1
    if argv[index:index + 1] != ["auth"] or "--help" in argv:
        return False
    actions = [w for w in argv[index + 1:] if not w.startswith("-")]
    if not actions:
        return False
    from .subagent_resume_point_guard import is_shell_resolved

    return actions[0] in ACCOUNT_ACTIONS or is_shell_resolved(actions[0])


def _component_refused(component: str) -> bool:
    from ..tools.shell_parser import shell_words
    from .mutative_verbs import _peel_leading_command_wrappers

    words = shell_words(_peel_leading_command_wrappers(component)[0])
    return bool(words) and _runs_gh(words[0]) and _auth_action_refused(words[1:])


def touches_account_credentials(command: str) -> bool:
    """True iff some command *command* runs is a refused `gh auth` action."""
    if not command or "auth" not in command:
        return False

    from ..tools.shell_parser import parse_command
    from .shell_substitution import extract_substitutions

    return any(
        _component_refused(component)
        for text in (command, *extract_substitutions(command))
        for component in parse_command(text)
    )


def check(command: str, *, is_subagent: bool = False) -> Tuple[bool, Optional[str]]:
    """(True, None) unless a subagent touches gh's account credentials, then (False, message)."""
    if is_subagent and touches_account_credentials(command):
        return False, REJECTION_MESSAGE
    return True, None
