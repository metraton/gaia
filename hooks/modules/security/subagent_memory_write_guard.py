"""
subagent_memory_write_guard.py -- subagent memory-write enforcement.

PreToolUse Bash guard that rejects direct curated-memory MUTATIONS
(`gaia memory add|append|reclassify|delete|link|checkpoint`) attempted from a
SUBAGENT dispatch context, EXCEPT for the sanctioned writer agents.

Why this exists
---------------
The `memory` skill declares the contract explicitly ("Who writes"):

    Only the orchestrator and `gaia-operator` mutate memory directly via
    the CLI. Subagents dispatched into a task do **not** call
    `gaia memory add` -- the writer hook rejects mutation from a dispatch
    context. Subagents propose impact with a versioned `memory_delta` in
    `agent_contract_handoff`; `memorialize_suggestions` is the legacy lane.

That contract was documented but NOT enforced at runtime: `gaia memory add`
(and `append` / `reclassify` / `link`) carry no verb in MUTATIVE_VERBS, so
they classified T0 "by elimination" and ran for ANY agent. This guard closes
the gap by scoping the block to dispatch context.

Scope of the block (categorical, NOT approvable)
------------------------------------------------
The block fires only when BOTH are true:
    - the command is a `gaia memory <write-verb>` invocation, AND
    - it runs in a subagent context (``is_subagent``) whose ``agent_type``
      is NOT in ``ALLOWED_AGENTS``.

The orchestrator (``is_subagent`` False) is never blocked here; ``gaia-operator``
(the sanctioned memory owner, dispatched as a subagent) is allow-listed. Like
``blocked_commands`` / ``gaia_db_write_guard`` this is a categorical deny with
no ``approval_id`` -- there is no T3 grant that lifts it, because the correct
path for a subagent is to PROPOSE, not to escalate. Read verbs
(``search``/``show``/``list``/``stats``/``get-relevant``/``conflicts``/
``episode-show``) are untouched: subagents read memory freely.

Detection
---------
Entry-point agnostic: each operator-separated component is tokenized and
every argv a Gaia entry point in it may receive (``gaia_cli_invocation``: the
launcher, a symlink or renamed copy, ``bin/cli/<group>.py``, ``-m cli.<group>``,
a shell-resolved word) is matched against the group and a write verb. No
identity a subagent's shell carries survives ``env -u``, so this is the boundary.

Public API:
    is_memory_write_attempt(command: str) -> bool
    is_proposal_decision_attempt(command: str) -> bool  (refused for every subagent)
    rejection_message(agent_type: str = "") -> str
    check(command, *, is_subagent, agent_type) -> tuple[bool, str | None]
"""

from __future__ import annotations

import shlex
from typing import Optional, Tuple

from .gaia_cli_invocation import gaia_cli_argvs

# Curated-memory MUTATING subcommands under `gaia memory`. Read verbs
# (search/show/list/stats/get-relevant/conflicts/episode-show) are absent by
# design -- subagents read memory freely.
MEMORY_WRITE_VERBS = frozenset(
    {"add", "append", "reclassify", "delete", "link", "checkpoint"}
)

# Subagents that ARE sanctioned to mutate memory directly. The orchestrator is
# handled by the ``is_subagent`` gate (it is never a subagent), so it does not
# appear here.
ALLOWED_AGENTS = frozenset({"gaia-operator"})

REJECTION_MESSAGE = (
    "Direct memory writes are not allowed from a subagent dispatch context. "
    "Subagents propose memory impact by emitting a versioned `memory_delta` "
    "block in their contract row (`memorialize_suggestions` is legacy); the orchestrator (or gaia-operator) "
    "persists it on user confirmation. Do NOT retry -- this is not approvable."
)


PROPOSAL_DECISION_VERBS = frozenset({"accept", "dismiss"})

PROPOSAL_DECISION_REJECTION = (
    "Accepting or dismissing a proposal is the orchestrator's decision, refused for "
    "every subagent. Propose in your contract row instead. Do NOT retry -- this is "
    "not approvable."
)


def is_memory_write_attempt(
    command: str, verbs: frozenset[str] = MEMORY_WRITE_VERBS,
) -> bool:
    """Return True iff ``command`` invokes a `gaia memory <verb>` for one of ``verbs``.

    Invocation-form agnostic (installed launcher, `python3 .../bin/gaia`,
    compound chains). Flags between `memory` and the verb are skipped.

    Args:
        command: The Bash command line (may include operators, wrappers).
        verbs: The write verbs that count; every mutation verb by default.

    Returns:
        True if one of ``verbs`` follows `gaia memory` anywhere in the
        command's token stream.
    """
    return _invokes(command, ("memory",), verbs)


def is_proposal_decision_attempt(command: str) -> bool:
    """Return True iff ``command`` invokes `gaia contract proposals accept|dismiss`."""
    return _invokes(command, ("contract", "proposals"), PROPOSAL_DECISION_VERBS)


def _invokes(command: str, group: tuple, verbs: frozenset[str]) -> bool:
    if not command or group[-1] not in command:
        return False

    # A help-only invocation exits after printing usage and performs no write.
    # The exemption is judged per operator-separated component, so a help
    # piped to `head` or with stderr folded in (`2>&1`) is still help while a
    # write chained after it is still a write. A substitution runs a command
    # inside the component that the exemption would otherwise speak for
    # (``gaia memory add --help $(gaia memory add ...)``), so its presence
    # anywhere withdraws the exemption from the whole line.
    from ..tools.shell_parser import parse_command

    help_exempt = not any(marker in command for marker in ("$(", "`", "<(", ">("))
    return any(
        _component_invokes(component, group=group, help_exempt=help_exempt, verbs=verbs)
        for component in parse_command(command)
    )


def _component_invokes(
    component: str, *, group: tuple, help_exempt: bool, verbs: frozenset[str],
) -> bool:
    """True iff one operator-free component may invoke `gaia <group...> <verb>`, verb in ``verbs``.

    The candidates are every argv a Gaia entry point in the component may
    receive (``gaia_cli_invocation.gaia_cli_argvs``). A multi-word group is
    also matched from its second word on wherever it appears, because a
    renamed copy of ``bin/cli/<group>.py`` takes that tail under any name.
    """
    try:
        tokens = shlex.split(component)
    except ValueError:
        # Unbalanced quotes etc. -- fall back to a naive split so a
        # partially-parseable command is still inspected conservatively.
        tokens = component.split()

    candidates = gaia_cli_argvs(tokens)
    if len(group) > 1:
        candidates += [[group[0], *tokens[i:]] for i, tok in enumerate(tokens) if tok == group[1]]
    return any(_argv_invokes(argv, group, verbs, help_exempt) for argv in candidates)


def _argv_invokes(argv: list, group: tuple, verbs: frozenset[str], help_exempt: bool) -> bool:
    k = _skip_flags(argv, 0)
    for word in group:
        if k >= len(argv) or argv[k] != word:
            return False
        k = _skip_flags(argv, k + 1)
    if k >= len(argv) or argv[k] not in verbs:
        return False
    tail = argv[k + 1:]
    return not (help_exempt and tail and tail[0] in {"--help", "-h", "-?", "--usage"})


def _skip_flags(tokens: list, index: int) -> int:
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 1
    return index


def rejection_message(agent_type: str = "") -> str:
    """Return the canonical rejection message (optionally naming the agent)."""
    if agent_type:
        return (
            f"Agent '{agent_type}' attempted a direct memory write. "
            + REJECTION_MESSAGE
        )
    return REJECTION_MESSAGE


def check(
    command: str,
    *,
    is_subagent: bool = False,
    agent_type: str = "",
) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash guard integration.

    Args:
        command: The Bash command line.
        is_subagent: True when running in a subagent dispatch context.
        agent_type: The originating agent name (e.g. "developer").

    Returns:
        (allowed, reason)
        - (True, None)  when the command is not a subagent memory write,
          or the subagent is an allow-listed writer (gaia-operator), or the
          caller is the orchestrator (``is_subagent`` False).
        - (False, msg)  when a non-sanctioned subagent attempts a memory write.
    """
    if not is_subagent:
        return True, None
    if is_proposal_decision_attempt(command):
        return False, PROPOSAL_DECISION_REJECTION
    if agent_type in ALLOWED_AGENTS:
        return True, None
    if is_memory_write_attempt(command):
        return False, rejection_message(agent_type)
    return True, None
