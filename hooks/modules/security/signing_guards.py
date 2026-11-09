"""The signatures a command's raw text asks for before the verb scan reads it, shared by the Bash validator and request-set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .composition_rules import redirected_into_sender
from .creation_place_guard import GUIDANCE as STRANDED_GUIDANCE, stranded_creation
from .data_heredoc import data_heredoc_header
from .gaia_db_write_guard import sqlite_unproven_effect
from .mutative_verbs import account_path_redirect_target
from .protected_path_guard import install_root_change, unprovable_write_target
from .sensitive_read_guard import remote_copy_destination
from .shell_write_guard import system_path_target


@dataclass(frozen=True)
class GuardSignature:
    """The lane that asks: its verb, the rule it names when the verb does not, and the guidance the denial carries."""

    verb: str
    rule: str = ""
    guidance: str = ""


def signing_guard(command: str, cwd: Optional[str], is_subagent: bool) -> Optional[GuardSignature]:
    """The first guard lane that asks a signature for *command*, else None.

    request-set marks a step signed from this same answer, so a lane added here
    is one the sanctioned request route can sign: a lane the validator asks on
    and request-set did not see would seal the step unsigned, and no grant
    could ever reach it.
    """
    scope = data_heredoc_header(command) or command
    if account_path_redirect_target(scope):
        return GuardSignature("account-path-write")
    if system_path_target(scope, cwd):
        return GuardSignature("system-path-write", rule="sign.system-path")
    changed_install = install_root_change(scope, cwd)
    if changed_install:
        return GuardSignature("install-root-change", guidance=(
            f"It changes the installed Gaia the host runs ({changed_install}); name that "
            f"install in --does. Changing Gaia itself is an edit to its source, propagated "
            f"by `gaia install` or `gaia dev`."
        ))
    stranded = stranded_creation(scope, cwd, is_subagent)
    if stranded:
        return GuardSignature("stranded-creation", guidance=STRANDED_GUIDANCE.format(path=stranded))
    if unprovable_write_target(scope, cwd):
        return GuardSignature("unprovable-write-target")
    sqlite_guidance = sqlite_unproven_effect(scope, cwd)
    if sqlite_guidance:
        return GuardSignature("sqlite-reach", guidance=sqlite_guidance)
    if redirected_into_sender(scope) or remote_copy_destination(scope):
        return GuardSignature("egress")
    return None
