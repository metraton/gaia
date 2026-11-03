"""
Dispatch-side contract identity -- minted real, injected adoptable.

The born-at-dispatch path (``ToolPolicy._maybe_birth_dispatched_row`` in
``hooks/adapters/tool_policy.py``) stamps a nascent ``agent_contract_handoffs`` row before the subagent runs. This
module mints the identity that row is born under: REAL (it satisfies
``gaia.contract.validator.AGENT_ID_PATTERN_TEXT``, ``^a[0-9a-f]{16,}$``, which
rejects an agent NAME such as ``gaia-system``) and ADOPTABLE (its
``contract_id`` has the draft-id shape ``--draft-id`` addresses, so
``gaia.contract.drafts._agent_of``, which splits a draft id on its FIRST dot,
recovers the agent handle). The identity reaches the subagent through the
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

# Heading of the injected contract block. The dispatch kernel
# (modules/context/kernel_builder.py) renders the block under this same text
# as its own KERNEL_HEADING constant.
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

