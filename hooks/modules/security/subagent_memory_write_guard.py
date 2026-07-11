"""
subagent_memory_write_guard.py -- subagent memory-write enforcement.

PreToolUse Bash guard that rejects direct curated-memory MUTATIONS
(`gaia memory add|edit|append|reclassify|delete|link`) attempted from a
SUBAGENT dispatch context, EXCEPT for the sanctioned writer agents.

Why this exists
---------------
The `memory` skill declares the contract explicitly ("Who writes"):

    Only the orchestrator and `gaia-operator` mutate memory directly via
    the CLI. Subagents dispatched into a task do **not** call
    `gaia memory add` -- the writer hook rejects mutation from a dispatch
    context. Subagents instead propose new memory by emitting a
    `memorialize_suggestions` block in their `agent_contract_handoff`.

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
Invocation-form agnostic: matches the installed launcher (`gaia memory add`),
the re-dispatched form (`python3 <path>/bin/gaia memory add`), and compound
chains (`cd /x && gaia memory add ...`). Tokenizes the full command and scans
for a `gaia`-basename token followed (skipping flags) by the `memory`
subcommand and then a write verb.

Public API:
    is_memory_write_attempt(command: str) -> bool
    rejection_message(agent_type: str = "") -> str
    check(command, *, is_subagent, agent_type) -> tuple[bool, str | None]
"""

from __future__ import annotations

import shlex
from typing import Optional, Tuple

# Curated-memory MUTATING subcommands under `gaia memory`. Read verbs
# (search/show/list/stats/get-relevant/conflicts/episode-show) are absent by
# design -- subagents read memory freely.
MEMORY_WRITE_VERBS = frozenset(
    {"add", "edit", "append", "reclassify", "delete", "link"}
)

# Subagents that ARE sanctioned to mutate memory directly. The orchestrator is
# handled by the ``is_subagent`` gate (it is never a subagent), so it does not
# appear here.
ALLOWED_AGENTS = frozenset({"gaia-operator"})

REJECTION_MESSAGE = (
    "Direct memory writes are not allowed from a subagent dispatch context. "
    "Subagents propose new memory by emitting a `memorialize_suggestions` "
    "block in their agent_contract_handoff; the orchestrator (or gaia-operator) "
    "persists it on user confirmation. Do NOT retry -- this is not approvable."
)


def _basename(token: str) -> str:
    """Return the last path component of a token ('/a/bin/gaia' -> 'gaia')."""
    # Handle both '/' and trailing separators without importing os for a hot path.
    return token.rsplit("/", 1)[-1]


def is_memory_write_attempt(command: str) -> bool:
    """Return True iff ``command`` invokes a `gaia memory <write-verb>`.

    Invocation-form agnostic (installed launcher, `python3 .../bin/gaia`,
    compound chains). Flags between `memory` and the verb are skipped.

    Args:
        command: The Bash command line (may include operators, wrappers).

    Returns:
        True if a `gaia memory` mutation verb is present anywhere in the
        command's token stream.
    """
    if not command or "memory" not in command:
        return False

    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes etc. -- fall back to a naive split so a
        # partially-parseable command is still inspected conservatively.
        tokens = command.split()

    n = len(tokens)
    for i, tok in enumerate(tokens):
        if _basename(tok) != "gaia":
            continue
        # Next non-flag token must be the `memory` subcommand.
        j = i + 1
        while j < n and tokens[j].startswith("-"):
            j += 1
        if j >= n or tokens[j] != "memory":
            continue
        # Next non-flag token after `memory` must be a write verb.
        k = j + 1
        while k < n and tokens[k].startswith("-"):
            k += 1
        if k < n and tokens[k] in MEMORY_WRITE_VERBS:
            return True
    return False


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
    if agent_type in ALLOWED_AGENTS:
        return True, None
    if is_memory_write_attempt(command):
        return False, rejection_message(agent_type)
    return True, None
