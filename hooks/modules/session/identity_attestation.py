"""Which agent the host really started a session as, and whether Gaia lets it work.

The configured identity (the ``agent`` setting) is what the host should run; the
agent the host names at session start is what it did run, and only that can
reveal a package that shipped without the setting. Gaia's approvals, routing and
delegation belong to the orchestrator, so a main thread started as anything else
has every prompt refused until a session starts with the right identity.

A session with no attestation is not refused: it started before this check was
installed, and refusing it would lock out sessions nothing has judged.

``GAIA_ALLOW_NON_ORCHESTRATOR=1`` in the host's environment lifts the refusal and
keeps the warning. It is TEMPORARY: it stays only until a live Claude Code
session confirms that ``agent_type`` reaches SessionStart when the agent comes
from the ``agent`` setting rather than ``--agent``, and is removed after that.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from gaia.agent_identity import ORCHESTRATOR

ALLOW_ENV = "GAIA_ALLOW_NON_ORCHESTRATOR"


def attest(agent_type: str, *, build: str, channel: str, workspace: str) -> dict:
    """Return the attestation of a session whose host named *agent_type* ("" for none)."""
    return {
        "agent_type": agent_type,
        "build": build,
        "channel": channel,
        "workspace": workspace,
        "attested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def is_orchestrator(attestation: dict) -> bool:
    """Whether the attested main thread is the orchestrator."""
    return attestation.get("agent_type") == ORCHESTRATOR


def _enforced() -> bool:
    return os.environ.get(ALLOW_ENV) != "1"


def _ran_as(attestation: dict) -> str:
    return attestation.get("agent_type") or "no agent"


def status_line(attestation: dict, version: str) -> str:
    """The one line the user sees at session start."""
    if is_orchestrator(attestation):
        return f"Gaia {version} · {ORCHESTRATOR} · build {attestation.get('build')}"
    effect = "prompts are blocked" if _enforced() else f"allowed by {ALLOW_ENV}"
    return (f"Gaia {version} · WARNING: session started as {_ran_as(attestation)}, "
            f"not {ORCHESTRATOR}; {effect} -- run `gaia doctor`")


def prompt_block_reason(attestation: Optional[dict]) -> Optional[str]:
    """Why a prompt in this session is refused, or None when it may proceed."""
    if attestation is None or is_orchestrator(attestation) or not _enforced():
        return None
    return (
        f"Gaia refused this prompt: the host started this session as "
        f"{_ran_as(attestation)}, not {ORCHESTRATOR}, so Gaia's approvals and "
        f"routing are not in charge. Run `gaia doctor` in a terminal to see which "
        f"install lacks the identity, reinstall (`gaia install`, or `gaia dev` for "
        f"a dev build), then start a new session."
    )
