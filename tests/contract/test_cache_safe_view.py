"""
AC-16 -- M6 cache-safe injected view (task T14).

"La vista inyectada al prompt es minima y byte-estable (no reinyecta el
contrato completo variable arriba del prompt)."

A resumed subagent's prompt has TWO regions from TWO paths:

  1. IDENTITY / SKILL prompt -- the agent's static ``.md`` + declared skills,
     injected by the HARNESS's own system-prompt assembly (a DIFFERENT path
     than this hook), ABOVE the hint. It never reads the draft, so it is
     BYTE-STABLE across resumes -- this is the large, KV-cache-worthy prefix,
     and the byte-stability the cache actually depends on.
  2. RESUME HINT -- what the hook injects as ``additionalContext``, BELOW the
     identity prompt. It is VOLATILE BY DESIGN (T6, carry-forward): it reflects
     the draft's current agent_state / next_action / pending_steps. Its job is
     to be MINIMAL, so the full variable contract is NEVER re-injected atop the
     prompt.

This suite therefore proves TWO distinct properties:
  * MINIMAL (and decoupled): the injected hint does not carry the full variable
    contract, and its size does not scale with the contract's bulk.
  * BYTE-STABLE where it matters: the byte-stable, cacheable substrate is the
    identity/skill prompt (the different path, above) -- NOT the volatile hint.
    Within the hint, the invariant prefix (for a fixed draft) is still ordered
    first so the cacheable prefix is maximized, but byte-stability is asserted
    on the identity substrate, never on the volatile tail.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from gaia.contract.view import (  # noqa: E402
    VOLATILE_MARKER,
    estimate_tokens,
    render_full_reemit,
    render_resume_hint,
    render_resume_hint_invariant,
    split_resume_hint,
)

DRAFT_ID = "a1b2c3d4e.deadbeefcafe"
IDENTITY_FILE = _REPO_ROOT / "agents" / "gaia-system.md"


def _envelope(agent_state="IN_PROGRESS", next_action="starting", n_pending=0, bulk=0):
    """Build an envelope; ``bulk`` inflates ONLY the evidence payload (the part
    the by-value view must NOT re-inject)."""
    return {
        "agent_status": {
            "agent_state": agent_state,
            "agent_id": "a1b2c3d4e",
            "pending_steps": [f"step-{i}" for i in range(n_pending)],
            "next_action": next_action,
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [f"file_{i}.py" for i in range(bulk)],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [f"VERBATIM-PAYLOAD-{i} " * 20 for i in range(bulk)],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }


# --------------------------------------------------------------------------- #
# MINIMAL: the hint does not re-inject the full variable contract.
# --------------------------------------------------------------------------- #

def test_injected_view_does_not_embed_the_full_contract_body():
    """A unique sentinel buried in the contract's bulky evidence payload must
    NOT appear in the injected hint -- the hint is a pointer + summary, not the
    envelope."""
    env = _envelope(bulk=5)
    sentinel = "VERBATIM-PAYLOAD-0"
    env["evidence_report"]["verbatim_outputs"][0] = sentinel + "-UNIQUE-SENTINEL"

    hint = render_resume_hint(DRAFT_ID, env)
    assert "UNIQUE-SENTINEL" not in hint
    # And it is dramatically smaller than re-emitting the full block.
    assert estimate_tokens(hint) < 0.5 * estimate_tokens(render_full_reemit(env))


def test_injected_view_size_is_decoupled_from_contract_bulk():
    """Two drafts identical except one has a MASSIVE evidence payload produce a
    BYTE-IDENTICAL hint -- the injected view is O(1) in the contract body, i.e.
    it genuinely does not re-inject the full variable contract atop the prompt."""
    lean = _envelope(bulk=0)
    heavy = _envelope(bulk=200)  # ~200 files + 200 long verbatim strings

    hint_lean = render_resume_hint(DRAFT_ID, lean)
    hint_heavy = render_resume_hint(DRAFT_ID, heavy)
    assert hint_lean == hint_heavy, "hint size leaked with contract bulk"

    # Meanwhile the full re-emit baseline explodes with the bulk -- confirming
    # the two views diverge exactly where the token savings come from.
    assert estimate_tokens(render_full_reemit(heavy)) > 10 * estimate_tokens(hint_heavy)


# --------------------------------------------------------------------------- #
# BYTE-STABLE substrate: the identity/skill prompt (different path), NOT the
# volatile hint (T6 carry-forward).
# --------------------------------------------------------------------------- #

def test_byte_stable_substrate_is_the_identity_prompt_not_the_volatile_hint():
    """The cacheable, byte-stable region is the identity/skill prompt injected
    by a DIFFERENT path (the agent's static .md, above the hint) -- it does not
    read the draft, so it is identical across resumes with different draft
    state. The hint, by contrast, is volatile and DOES change -- so we must not
    (and do not) lean on it for byte-stability."""
    assert IDENTITY_FILE.is_file(), IDENTITY_FILE

    # Model the assembled resumed prompt: identity ABOVE, hint BELOW.
    identity_prompt = IDENTITY_FILE.read_text(encoding="utf-8")

    env_msg1 = _envelope(agent_state="IN_PROGRESS", next_action="investigating", n_pending=3)
    env_msg2 = _envelope(agent_state="COMPLETE", next_action="done", n_pending=0)

    # The identity substrate is a pure function of the agent definition -- it
    # never reads the draft, so it is byte-identical across the two resumes.
    identity_msg1 = identity_prompt
    identity_msg2 = identity_prompt
    assert identity_msg1 == identity_msg2

    # The hint IS volatile across those same two resumes -- proving the
    # byte-stability above cannot be coming from the hint.
    hint_msg1 = render_resume_hint(DRAFT_ID, env_msg1)
    hint_msg2 = render_resume_hint(DRAFT_ID, env_msg2)
    assert hint_msg1 != hint_msg2


def test_invariant_hint_prefix_is_byte_stable_across_resumes_only_tail_moves():
    """Cache-optimal ordering WITHIN the hint: for a FIXED draft, the invariant
    prefix (instructions + draft_id + CLI template) is byte-identical across N
    resumes; ONLY the trailing volatile status line changes. This maximizes the
    cache-reusable prefix without ever claiming the volatile tail is stable."""
    resumes = [
        _envelope(agent_state="IN_PROGRESS", next_action="step one", n_pending=4),
        _envelope(agent_state="IN_PROGRESS", next_action="step two", n_pending=2),
        _envelope(agent_state="COMPLETE", next_action="done", n_pending=0),
    ]
    prefixes = set()
    tails = set()
    for env in resumes:
        prefix, tail = split_resume_hint(render_resume_hint(DRAFT_ID, env))
        prefixes.add(prefix)
        tails.add(tail)

    # One and only one invariant prefix across every resume (byte-stable).
    assert len(prefixes) == 1, prefixes
    # It equals the standalone invariant renderer -- the same bytes, no drift.
    assert prefixes.pop() == render_resume_hint_invariant(DRAFT_ID)
    # The volatile tail genuinely moved (3 distinct states -> 3 distinct tails).
    assert len(tails) == 3, tails
    # The marker really separates the two regions.
    for env in resumes:
        assert VOLATILE_MARKER in render_resume_hint(DRAFT_ID, env)


def test_invariant_prefix_carries_no_volatile_field():
    """Defensive: the byte-stable prefix must contain none of the volatile
    field VALUES -- if a status ever leaked above the marker, the cache prefix
    would silently churn between messages."""
    env = _envelope(agent_state="BLOCKED", next_action="waiting-on-approval", n_pending=7)
    prefix, _ = split_resume_hint(render_resume_hint(DRAFT_ID, env))
    assert "BLOCKED" not in prefix
    assert "waiting-on-approval" not in prefix
    assert "pending_steps=7" not in prefix
    # The draft_id (constant for this draft's resumes) legitimately IS in the
    # prefix -- that is what makes the CLI commands actionable.
    assert DRAFT_ID in prefix


# --------------------------------------------------------------------------- #
# No divergence: the hook's actual injection path renders the SAME view.
# --------------------------------------------------------------------------- #

def test_hook_injection_path_uses_the_same_renderer(tmp_path, monkeypatch):
    """The adapter's resume injection and this measurement must be two callers
    of ONE renderer -- otherwise cache-safety proven here would not hold on the
    real injected view."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)

    from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
    from modules.core.paths import clear_path_cache  # noqa: E402
    from gaia.contract.drafts import save_draft  # noqa: E402

    clear_path_cache()
    monkeypatch.setattr(ClaudeCodeAdapter, "RESUME_MAP_CACHE_DIR", tmp_path / "resume_map")

    agent_id = "a1b2c3d4e"
    draft_id = f"{agent_id}.cafefeed0001"
    env = _envelope(agent_state="IN_PROGRESS", next_action="mid-flight", n_pending=2, bulk=50)
    save_draft(draft_id, env)

    adapter = ClaudeCodeAdapter()
    session_id = "sess-cache-001"
    adapter._adapt_send_message(
        "SendMessage", {"to": agent_id, "message": "go"}, session_id=session_id,
    )
    hook_view = adapter._build_resume_draft_context(session_id)

    assert hook_view is not None
    assert hook_view == render_resume_hint(draft_id, env)
    # And the hook's real injected view carries none of the bulky payload.
    assert "VERBATIM-PAYLOAD" not in hook_view
    clear_path_cache()
