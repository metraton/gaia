"""
Dispatch-side contract identity -- minted real, injected adoptable.

The born-at-dispatch path (``ClaudeCodeAdapter._maybe_birth_dispatched_row``)
stamps a nascent ``agent_contract_handoffs`` row before the subagent runs. That
row used to be born under a SYNTHETIC key -- ``dispatch.{sid}.{agent}.{key}``
for ``contract_id`` and the agent NAME for ``agent_id`` -- which no consumer of
the contract substrate can adopt:

  * ``gaia.contract.drafts._agent_of`` recovers the agent handle by splitting a
    draft id on its FIRST dot, so the synthetic key yielded the literal
    ``"dispatch"`` instead of a handle.
  * ``gaia.contract.validator.AGENT_ID_PATTERN_TEXT`` (``^a[0-9a-f]{16,}$``)
    rejects an agent NAME such as ``gaia-system`` outright.

So the row existed but nothing could converge onto it: the agent minted its own
unrelated identity via ``gaia contract init`` and finalized a SECOND row. This
module owns the other half: minting an identity that is REAL (it satisfies the
validator) and ADOPTABLE (its ``contract_id`` has the draft-id shape the CLI's
``--draft-id`` addresses). The identity reaches the subagent through the
dispatch kernel rendered from the claimed row (``modules/context/kernel_builder``),
not through a block rendered here.

Uniqueness over derivability -- and why:
    The identity is minted from ``secrets``, never DERIVED from the dispatch
    coordinates. A derived identity (a hash of session + agent + task, say) would
    hand the SAME id to two concurrent dispatches of the same agent type against
    the same task, and both agents would then adopt one row -- precisely the
    binding corruption the born-at-dispatch row exists to prevent. Uniqueness is
    the property worth keeping; per-key idempotency is not, because a re-dispatch
    is a genuinely new turn that deserves its own row. The writer's
    ``ON CONFLICT(contract_id) DO NOTHING`` still makes ONE dispatch's birth
    idempotent, which is the scope that matters.
"""

from __future__ import annotations

import pathlib as _pl
import sys as _sys

# Heading of the injected contract block. Consumers (tests, and the agent
# reading its own context) locate the identity by this marker, never by
# position in the payload. The dispatch kernel (modules/context/
# kernel_builder.py, KERNEL_HEADING) renders the block under this same
# marker; the constant survives here as the marker's protocol-side name now
# that the legacy identity-block renderer is retired.
IDENTITY_BLOCK_HEADING = "# Your Contract"


def _import_drafts():
    """Import ``gaia.contract.drafts`` from an installed package or the repo.

    Mirrors the resolution ``dispatch_binding`` uses for the writer, so the hook
    layer stays agnostic to whether ``gaia`` is pip-installed or a sibling in
    the repo tree.
    """
    try:
        from gaia.contract import drafts as _drafts  # noqa: WPS433 (local import)
    except ImportError:
        _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from gaia.contract import drafts as _drafts  # noqa: WPS433
    return _drafts


def mint_dispatch_identity() -> dict:
    """Mint the two halves of a dispatch identity.

    Returns ``{"agent_id": ..., "contract_id": ...}`` where ``agent_id``
    satisfies ``AGENT_ID_PATTERN_TEXT`` and ``contract_id`` is
    ``mint_draft_id(agent_id)`` -- so ``_agent_of(contract_id) == agent_id`` and
    the id is directly addressable as ``--draft-id`` on every ``gaia contract``
    call.
    """
    drafts = _import_drafts()
    agent_id = drafts.mint_agent_id()
    return {"agent_id": agent_id, "contract_id": drafts.mint_draft_id(agent_id)}

