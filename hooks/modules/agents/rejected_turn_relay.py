"""
Preservation and relay of the substantive text of a turn the contract gate
rejected.

The defect this exists for: when ``adapt_subagent_stop`` returns exit_code=2
because the final message carried no valid fenced ``agent_contract_handoff``,
the harness feeds the rejection back to the SUBAGENT, which produces one more
turn -- and that repair turn's message REPLACES the rejected one in everything
the orchestrator receives. Measured: an agent emitted a full diagnosis at
20:08:10, the hook rejected it 0.386s later, and the orchestrator saw only the
20:09:17 re-emission ("the contract is already finalized, this only adds the
envelope"). The work existed and was lost in the relay, not in the DB.

The gate is deliberately unchanged -- a turn without a contract is still
rejected. What changes is that the rejection no longer costs the work:

  * PERSIST -- the substantive text (everything that is not the contract fence)
    is written under ``<data_dir>/rejected_turns/<key>.txt``, so it survives the
    turn regardless of what the agent does next.
  * REINJECT -- that same text is appended VERBATIM to the rejection message
    the harness delivers to the subagent, with an explicit instruction to
    reproduce it in the repair message. This is the only in-band route back to
    the orchestrator, which reads the subagent's final message and nothing else.
  * CARRY FORWARD -- a second rejection of the same turn re-preserves the
    ORIGINAL text (the repair attempt is usually thinner than what it replaced),
    so repeated rejections cannot erode it. What is bounded is the REINJECTION,
    never the preserved copy -- see the note on ``_MAX_INLINE_CHARS`` below.

Key space: one file per (session, harness agent id) turn -- the harness id is
the right key here precisely because this module keys the HARNESS turn, not a
CLI draft (see ``handoff_persister.resolve_minted_agent_id`` for why those two
identifier spaces must not be conflated).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Fenced blocks that ARE the contract, not the agent's substantive prose. Both
# spellings occur in practice: an ``agent_contract_handoff`` info string, and a
# plain ``json`` block whose body is the envelope.
_FENCE_RE = re.compile(r"```[^\n`]*\n.*?\n?```", re.DOTALL)
_CONTRACT_MARKERS = ("agent_contract_handoff", '"agent_status"', "'agent_status'")

# Cap on how much text is inlined into the rejection message. The file keeps
# the full text; the notice keeps the message deliverable. Generous on purpose:
# truncating the relay to a summary would reintroduce the very loss this module
# exists to prevent.
_MAX_INLINE_CHARS = 20000

# THE PRESERVED FILE HAS NO CEILING, and that is deliberate. An earlier
# revision capped it, which destroyed evidence outright: a SINGLE 30030-char
# turn with no accumulation at all was stored truncated to 20107 -- 9923
# characters of the agent's own work gone, in the one module whose entire
# purpose is that a rejection must not cost the work. What made the loop
# expensive was never the file; it was RE-READING it on every pass, and that is
# bounded where it belongs -- ``_MAX_INLINE_CHARS`` and the caller's shrinking
# per-attempt budget cap what is REINJECTED. The number of passes is itself now
# bounded by the rejection circuit breaker, so accumulation cannot run away.

_SUBDIR = "rejected_turns"
_SUFFIX = ".txt"
_KEY_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _relay_dir() -> Path:
    from gaia.paths import data_dir

    directory = Path(data_dir()) / _SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def preservation_key(session_id: Optional[str], task_info: Dict[str, Any]) -> str:
    """Stable per-turn key: the harness session plus the harness agent id."""
    raw = f"{session_id or 'nosession'}.{task_info.get('agent_id') or task_info.get('agent') or 'unknown'}"
    return _KEY_SAFE_RE.sub("-", raw)[:120]


def substantive_text(agent_output: str) -> str:
    """The agent's own prose: the message with contract fences removed.

    Only fences that ARE the contract are stripped -- a code block the agent
    included as evidence is substantive and stays.
    """
    if not agent_output:
        return ""

    def _drop(match: re.Match) -> str:
        block = match.group(0)
        return "" if any(m in block for m in _CONTRACT_MARKERS) else block

    return _FENCE_RE.sub(_drop, agent_output).strip()


def load(key: str) -> Optional[str]:
    """The text preserved for ``key`` on a previous rejection, if any."""
    try:
        path = _relay_dir() / f"{key}{_SUFFIX}"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("Rejected-turn relay: load failed for %s: %s", key, exc)
        return None


def save(key: str, text: str) -> Optional[Path]:
    """Atomically persist ``text`` for ``key``; None when the write failed."""
    try:
        directory = _relay_dir()
        target = directory / f"{key}{_SUFFIX}"
        tmp = directory / f".{key}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
        return target
    except Exception as exc:
        logger.warning("Rejected-turn relay: could not persist %s: %s", key, exc)
        return None


def clear(key: str) -> None:
    """Drop the preserved text once its turn is over."""
    try:
        (_relay_dir() / f"{key}{_SUFFIX}").unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("Rejected-turn relay: clear failed for %s: %s", key, exc)


def build_relay_notice(
    text: str,
    path: Optional[Path] = None,
    max_inline_chars: Optional[int] = None,
) -> str:
    """The block appended to the rejection message the subagent receives.

    It names the loss (the orchestrator never saw the rejected message), the
    obligation (reproduce it verbatim), and the anti-pattern that was actually
    measured (replacing it with a thin "this only adds the envelope" line).

    ``max_inline_chars`` lets the caller spend a smaller inline budget on a
    later attempt -- the text has already been delivered once and the file path
    is named, so re-shipping the whole ceiling every pass is what made the retry
    loop expensive. Defaults to :data:`_MAX_INLINE_CHARS`.
    """
    budget = _MAX_INLINE_CHARS if max_inline_chars is None else max(1, int(max_inline_chars))
    body = text
    if len(body) > budget:
        # The marker states the true cause -- this excerpt is short because the
        # INLINE budget for this attempt is, not because anything was discarded
        # -- and points at the complete copy, which is never truncated.
        omitted = len(body) - budget
        body = (
            body[:budget]
            + f"\n[... {omitted} more chars not inlined here: this attempt's "
            f"inline budget is {budget} chars. NOTHING was discarded"
            + (f"; the complete text is preserved at {path}" if path else "")
            + "]"
        )
    where = f"\nThe full text is also preserved at: {path}" if path else ""
    return (
        "\n\n=== PRESERVED OUTPUT OF THE REJECTED TURN -- RELAY REQUIRED ===\n"
        "The message you just sent was NOT relayed to the orchestrator: this "
        "turn was rejected for a missing/invalid agent_contract_handoff fence, "
        "and your repair message REPLACES it in everything the orchestrator "
        "receives. The text below is therefore lost unless you reproduce it.\n\n"
        "Your repair message MUST reproduce the text below VERBATIM, before the "
        "fenced agent_contract_handoff block. Do NOT summarize it and do NOT "
        "replace it with a thin note such as \"the contract is already "
        "finalized, this only adds the envelope\"."
        f"{where}\n"
        "--- BEGIN PRESERVED OUTPUT ---\n"
        f"{body}\n"
        "--- END PRESERVED OUTPUT ---\n"
    )


def on_rejection(
    agent_output: str,
    *,
    key: str,
    rejection_reason: str,
    max_inline_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """Preserve the rejected turn's prose and reinject it into the rejection.

    Returns a dict with the augmented ``reason`` plus provenance
    (``path``, ``chars``, ``carried_forward``). When there is no substantive
    text to preserve at all, ``reason`` comes back unchanged -- an empty relay
    notice would only add noise to the repair message.
    """
    result: Dict[str, Any] = {
        "reason": rejection_reason,
        "path": None,
        "chars": 0,
        "carried_forward": False,
        "inline_truncated": False,
    }
    try:
        text = substantive_text(agent_output)
        # A repair attempt that already echoes the earlier text keeps it; one
        # that dropped it must not overwrite the original with its own thinner
        # message -- that erosion is the defect itself, one turn later.
        previous = load(key)
        if previous and previous.strip() and previous.strip() not in text:
            text = previous if not text else f"{previous}\n\n{text}"
            result["carried_forward"] = True
        if not text.strip():
            return result
        path = save(key, text)
        result["path"] = str(path) if path else None
        result["chars"] = len(text)
        notice = build_relay_notice(text, path, max_inline_chars=max_inline_chars)
        result["inline_truncated"] = len(text) > (
            _MAX_INLINE_CHARS if max_inline_chars is None else max_inline_chars
        )
        result["reason"] = rejection_reason + notice
        logger.warning(
            "Rejected-turn relay: preserved %d chars of substantive output for "
            "%s and reinjected it into the repair message.",
            len(text), key,
        )
    except Exception as exc:
        logger.warning("Rejected-turn relay: preservation failed for %s: %s", key, exc)
    return result


def on_accepted(agent_output: str, *, key: str) -> Optional[Dict[str, Any]]:
    """Close out a turn that finally passed the gate.

    Reports whether the repair message actually carried the preserved text
    back, then drops the file. ``relayed=False`` is the honest signal that the
    substantive work reached the orchestrator only through the persisted copy.
    """
    preserved = load(key)
    if not preserved:
        return None
    relayed = bool(preserved.strip()) and preserved.strip()[:400] in (agent_output or "")
    clear(key)
    if not relayed:
        logger.warning(
            "Rejected-turn relay: the repair message for %s did NOT reproduce "
            "the preserved output (%d chars); it survives only in the "
            "preserved copy.",
            key, len(preserved),
        )
    return {"relayed": relayed, "chars": len(preserved)}
