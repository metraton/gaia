"""
gaia.contract.view -- the minimal, cache-safe injected VIEW of a contract
draft, plus the token-cost measurement that proves the by-value flow is
cheaper than re-emitting the full block (T14, AC-15 / AC-16).

Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli (M6).

WHY THIS MODULE EXISTS
----------------------
The by-value contract flow (M2) keeps the agent's contract state OUT of the
prompt: it lives on disk as a draft (``gaia.contract.drafts``) and the agent
mutates it with small ``gaia contract set/add`` calls. The only thing that
needs to travel back INTO the prompt on a resume is a *pointer* to that draft
plus a minimal status summary -- NOT the whole envelope. This module owns the
one renderer for that injected view, so the hook adapter (T6,
``ClaudeCodeAdapter._build_resume_draft_context``) and the measurement here
render from a SINGLE source of truth instead of diverging (mirroring the T6
test philosophy: the orchestrator read and the resume-injection read are two
callers of the same source, never two copies).

Harness-agnostic by construction (decisions #1 / #3): stdlib only; imports
nothing under ``hooks/`` and no third party. The hook adapter (harness-specific)
imports THIS module, never the reverse. NOT re-exported by
``gaia.contract.__init__`` -- keeping it off that surface preserves the layer-1
portability boundary (AC-2), since importing ``gaia.contract.validator`` must
never transitively pull anything else in.

THE TWO REGIONS OF A RESUMED SUBAGENT PROMPT (AC-16, cache-safety)
------------------------------------------------------------------
A resumed subagent's prompt has two distinct regions from two distinct paths:

  1. IDENTITY / SKILL prompt -- the agent's ``.md`` definition + its declared
     skills. Injected by the HARNESS's own system-prompt assembly (a DIFFERENT
     path than this hook), from STATIC on-disk artifacts. It does NOT read the
     draft, so it is BYTE-STABLE across resumes -- this is the large,
     KV-cache-worthy prefix, and the byte-stability the cache actually depends
     on. (T14 carry-forward from T6: do NOT claim the volatile hint below is
     the byte-stable part; the identity/skill prompt is.)

  2. RESUME HINT -- what THIS module renders and the hook injects as
     ``additionalContext``, BELOW the identity prompt. It is VOLATILE BY DESIGN
     (T6): it reflects the draft's current ``agent_state`` / ``next_action`` /
     ``pending_steps``, which change between messages. Its whole job is to be
     MINIMAL -- a pointer + a status line -- so the full variable contract is
     NEVER re-injected atop the prompt.

Within the hint we still separate an INVARIANT PREFIX (instructions + the
resumed draft_id + the CLI command template -- all constant for a *fixed* draft
across its resumes) from a VOLATILE TAIL (the one status line whose values move
message-to-message). Ordering the invariant content FIRST and the volatile line
LAST maximizes the byte-stable prefix a KV cache can reuse; the volatile tail
is deliberately last and is NOT claimed stable.

TOKEN MEASUREMENT (AC-15)
-------------------------
``estimate_tokens`` is a deterministic, tokenizer-agnostic proxy
(``ceil(len/4)`` -- the standard chars-per-token rule of thumb). No third-party
tokenizer is assumed, so the measurement is reproducible in any environment and
the fixed threshold is stable. The claim is RELATIVE (by-value << full re-emit)
and the proxy is monotone in text size, so the exact tokenizer does not change
the verdict -- if anything a real BPE tokenizer counts the punctuation-dense
full-JSON re-emit as MORE tokens, making the proxy conservative for our claim.
"""

from __future__ import annotations

import json
import math
from typing import Tuple

# The line that separates the byte-stable invariant prefix (above) from the
# volatile status tail (below) inside the resume hint. Exposed so tests and
# callers can split the two regions deterministically.
VOLATILE_MARKER = "-- current draft state (volatile) --"


# ---------------------------------------------------------------------------
# Token estimation (proxy; see module docstring)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Deterministic, tokenizer-agnostic token-count proxy.

    Uses the standard ~4-chars-per-token heuristic (``ceil(len/4)``). Chosen so
    the measurement needs no third-party tokenizer and yields the SAME number
    in every environment -- which is what lets AC-15 pin a fixed threshold. The
    metric is only ever used for a RELATIVE comparison, and it is monotone in
    text length, so the reduction verdict is robust to the exact tokenizer.
    """
    if not text:
        return 0
    return math.ceil(len(text) / 4)


# ---------------------------------------------------------------------------
# The full re-emit BASELINE (what the old model paid every turn)
# ---------------------------------------------------------------------------

def render_full_reemit(envelope: dict) -> str:
    """Render the FULL fenced ``agent_contract_handoff`` block an agent would
    re-emit every turn in the pre-by-value model (the AC-15 baseline).

    Matches the canonical fence the legacy parser recognizes
    (``hooks.modules.agents.contract_validator._RE_HANDOFF``:
    ``\\n```agent_contract_handoff\\n<json>\\n```\\n``) so the baseline is a
    faithful representation of the block that used to travel through the prompt
    in full, not a strawman.
    """
    body = json.dumps(envelope, indent=2, sort_keys=False)
    return f"```agent_contract_handoff\n{body}\n```"


# ---------------------------------------------------------------------------
# The minimal, cache-safe resume hint (what the by-value model injects)
# ---------------------------------------------------------------------------

def render_resume_hint_invariant(draft_id: str) -> str:
    """The BYTE-STABLE prefix of the resume hint for a fixed ``draft_id``.

    Instructions + the resumed draft_id + the CLI command template. Every token
    here is constant across that draft's resumes (the draft_id does not change
    while a single conversation resumes the same agent), so this whole block is
    part of the cache-reusable prefix. It carries NO field that changes between
    messages -- those live only in the volatile tail below.
    """
    return "\n".join(
        [
            "# Contract Draft (resumed)",
            "Your in-progress contract draft was NOT reset by this resume.",
            "Continue writing it BY VALUE via the gaia contract CLI --",
            "do NOT re-emit the full agent_contract_handoff block from memory:",
            f"  gaia contract view     --draft-id {draft_id}",
            f"  gaia contract set      --draft-id {draft_id} FIELD VALUE",
            f"  gaia contract add      --draft-id {draft_id} FIELD VALUE",
            f"  gaia contract finalize --draft-id {draft_id}",
        ]
    )


def render_resume_hint_volatile(envelope: dict) -> str:
    """The VOLATILE tail of the resume hint (T6: volatile by design).

    A single status line summarizing the draft's current state. It reads ONLY
    the three summary fields (``agent_state``, ``next_action``, and the COUNT of
    ``pending_steps``) -- never the bulky evidence payload -- so its size does
    not scale with the contract body. This is what keeps the injected view
    minimal and decoupled from the full variable contract.
    """
    agent_status = envelope.get("agent_status") or {}
    agent_state = agent_status.get("agent_state", "?")
    next_action = agent_status.get("next_action", "?")
    pending_steps = agent_status.get("pending_steps") or []
    return (
        f"{VOLATILE_MARKER}\n"
        f"agent_state={agent_state} next_action={next_action!r} "
        f"pending_steps={len(pending_steps)}"
    )


def render_resume_hint(draft_id: str, envelope: dict) -> str:
    """The complete resume hint: byte-stable invariant prefix, then the one
    volatile status line last (cache-optimal ordering).

    This is the single renderer the hook adapter's resume injection calls, so
    the injected view and any measurement of it never diverge.
    """
    return f"{render_resume_hint_invariant(draft_id)}\n{render_resume_hint_volatile(envelope)}"


def split_resume_hint(hint: str) -> Tuple[str, str]:
    """Split a rendered hint into (invariant_prefix, volatile_tail).

    Deterministic split on ``VOLATILE_MARKER``. If the marker is absent the whole
    hint is treated as the invariant prefix (defensive; the real renderer always
    emits the marker).
    """
    idx = hint.find(VOLATILE_MARKER)
    if idx == -1:
        return hint, ""
    return hint[:idx].rstrip("\n"), hint[idx:]


# ---------------------------------------------------------------------------
# Token-savings measurement (AC-15)
# ---------------------------------------------------------------------------

def measure_token_savings(draft_id: str, envelope: dict, turns: int = 1) -> dict:
    """Measure the contract-token cost of the by-value flow vs full re-emit.

    Fixed-scenario model of a resumed task spanning ``turns`` messages:

    * FULL RE-EMIT (baseline): each turn the agent re-emits the whole current
      ``agent_contract_handoff`` block into the prompt -> ``turns`` copies of
      ``render_full_reemit(envelope)``.
    * BY-VALUE: each turn only the minimal resume hint is injected ->
      ``turns`` copies of ``render_resume_hint(draft_id, envelope)``. The
      agent's own incremental CLI writes are small per-field deltas written
      ONCE, never re-injected atop the prompt each turn, so the recurring
      prompt cost is just the hint.

    Returns a dict with per-turn and cumulative token counts plus the reduction
    ratio, so a test can assert ``by_value < baseline`` and a fixed threshold.
    """
    baseline_view = render_full_reemit(envelope)
    byvalue_view = render_resume_hint(draft_id, envelope)

    baseline_per_turn = estimate_tokens(baseline_view)
    byvalue_per_turn = estimate_tokens(byvalue_view)

    baseline_total = baseline_per_turn * turns
    byvalue_total = byvalue_per_turn * turns

    reduction_ratio = (
        1.0 - (byvalue_total / baseline_total) if baseline_total else 0.0
    )
    return {
        "turns": turns,
        "baseline_view_chars": len(baseline_view),
        "byvalue_view_chars": len(byvalue_view),
        "baseline_tokens_per_turn": baseline_per_turn,
        "byvalue_tokens_per_turn": byvalue_per_turn,
        "baseline_tokens_total": baseline_total,
        "byvalue_tokens_total": byvalue_total,
        "reduction_ratio": reduction_ratio,
    }
