"""
Dispatch lifecycle -- host-neutral claim -> stamp -> render sequence for a
starting subagent turn (plan 65 T5, move-only extraction out of a specific
host adapter's own module; T7 threads the exact-callID correlation key
through as a fifth neutral argument -- the ladder itself lives in
``claim_dispatch_row``, this facade only forwards what it is given).

This is the CLAIM seam, not the birth one (see ``dispatch_binding``): the
nascent ``agent_contract_handoffs`` row was already born earlier in the
turn's dispatch. When a host reports that a dispatched turn has actually
started, the host holds its own per-run identifier for that turn while the
born row only knows the CLI-minted ``contract_id`` -- the claim is where the
two identifier spaces first meet, which is also where the recovery join used
after an unfinalized cut is stamped.

Kept free of any host-specific vocabulary on purpose: a caller translates its
own event shape into the five neutral arguments below and reads back either
the rendered kernel text or ``None``. That translation -- and everything that
knows what a particular host's start event looks like -- stays entirely on
the caller's side.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def claim_dispatch_kernel(
    *,
    agent_name: Optional[str],
    dispatch_prompt_id: Optional[str],
    dispatch_description: Optional[str],
    host_agent_id: Optional[str],
    dispatch_tool_use_id: Optional[str] = None,
) -> Optional[str]:
    """Claim the born row a turn-start correlates to, stamp it, render its kernel.

    ``dispatch_prompt_id`` / ``dispatch_description`` are the correlation keys
    against the row's own columns of the same name, scoped by ``agent_name``;
    ``dispatch_tool_use_id`` is the exact-callID layer 0 key, forwarded
    unchanged -- a caller with no such coordinate (today's Claude Code) omits
    it and the ladder behaves exactly as before this parameter existed.
    ``claim_dispatch_row`` owns the ladder and the divergent-signature guard.

    ``host_agent_id`` is stamped onto the claimed row as its
    ``harness_agent_id`` right after the claim resolves, because the claim is
    where both identifier spaces first meet: the row carries the CLI-minted
    ``contract_id`` and the caller carries the host-assigned run id. Stamping
    at the claim (rather than later, from whatever identity a context cache
    might carry) covers every start lane, cache hit or miss, since a
    cache-borne stamp would silently lose the cache-miss lane -- exactly the
    cut-turn traceability the stamp exists for. A later stop-style seam cannot
    substitute for this one: it never fires on a harness cut. The stamp runs
    before rendering so a render failure can never lose it; both the stamp
    and the render are best-effort and never block the start.

    Returns the joined kernel blocks, or ``None`` when nothing was claimed or
    rendering failed -- callers read ``None`` as "keep the legacy path",
    never as an error.
    """
    try:
        from gaia.store.writer import claim_dispatch_row, stamp_harness_agent_id
        from ..context.kernel_builder import build_kernel_context

        row = claim_dispatch_row(
            agent_name=agent_name or None,
            dispatch_prompt_id=dispatch_prompt_id or None,
            dispatch_description=dispatch_description or None,
            dispatch_tool_use_id=dispatch_tool_use_id or None,
        )
        if row is None:
            return None
        try:
            _stamp = stamp_harness_agent_id(
                row.get("contract_id") or None,
                host_agent_id or None,
            )
            if _stamp.get("status") == "applied":
                logger.info(
                    "Harness agent id stamped: contract_id=%s harness_agent_id=%s",
                    row.get("contract_id"), host_agent_id,
                )
        except Exception as exc:
            logger.debug("Harness agent id stamp failed (non-fatal): %s", exc)
        kernel = build_kernel_context(row, agent_name=agent_name)
        if kernel:
            logger.info(
                "Dispatch kernel injected (contract_id=%s, agent=%s)",
                row.get("contract_id"), agent_name or "unknown",
            )
        return kernel
    except Exception as exc:
        logger.debug("dispatch claim/kernel failed (non-fatal): %s", exc)
        return None
