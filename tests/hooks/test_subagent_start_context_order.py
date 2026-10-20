"""A dispatched subagent's injected context starts with its kernel, not the events digest.

What the user feels: when the host cuts an over-long hook context it keeps the
head, so the kernel (identity, goal, the user's rows) must come first and the
session-events digest last -- the digest is the first thing to lose.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402

DIGEST = "# Recent Session Events (last 24h)\nNo recent events"
KERNEL = "# Your Contract\n\ncontract_id: a0123456789abcdef.beefcafe0123"


def test_the_kernel_precedes_the_cached_events_digest(monkeypatch):
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(
        adapter, "_read_cached_context",
        lambda *args, **kwargs: {"context": DIGEST, "agent_type": "developer"},
    )
    monkeypatch.setattr(adapter, "_maybe_claim_dispatch_kernel", lambda raw: KERNEL)

    result = adapter.adapt_subagent_start(
        {"session_id": "sess-order", "agent_type": "developer"},
    )

    assert result.additional_context == f"{KERNEL}\n\n{DIGEST}"


def test_a_failed_claim_leaves_only_the_digest(monkeypatch):
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(
        adapter, "_read_cached_context",
        lambda *args, **kwargs: {"context": DIGEST, "agent_type": "developer"},
    )
    monkeypatch.setattr(adapter, "_maybe_claim_dispatch_kernel", lambda raw: None)

    result = adapter.adapt_subagent_start(
        {"session_id": "sess-order", "agent_type": "developer"},
    )

    assert result.additional_context == DIGEST
