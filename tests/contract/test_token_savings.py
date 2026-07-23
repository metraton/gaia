"""
AC-15 -- M6 token measurement (task T14).

"En un escenario fijo, el flujo CLI por-valor consume MENOS tokens de contrato
que re-emitir el bloque completo; baseline medido, asercion de reduccion
(umbral a fijar tras baseline)."

The by-value contract flow (M2) keeps the contract state OUT of the prompt: it
lives on disk as a draft and the agent mutates it with small CLI calls. On a
resume, only a MINIMAL pointer + status hint travels back into the prompt --
never the whole envelope. This suite proves, on a FIXED, genuinely-valid
scenario, that this costs strictly FEWER contract tokens than the pre-by-value
model where the agent re-emits the full ``agent_contract_handoff`` block every
turn.

Methodology
-----------
* Token proxy: ``gaia.contract.view.estimate_tokens`` -- a deterministic,
  tokenizer-agnostic ``ceil(len/4)`` proxy. No third-party tokenizer, so the
  number is reproducible in any environment and the threshold below is stable.
  The claim is RELATIVE and the proxy is monotone in text length, so the
  verdict is robust to the exact tokenizer (a real BPE tokenizer counts the
  punctuation-dense full-JSON re-emit as even MORE tokens -- the proxy is
  conservative for our claim).
* Baseline (full re-emit): each turn, one ``render_full_reemit(envelope)`` --
  the whole fenced block, exactly the form the legacy parser recognizes.
* By-value: each turn, one ``render_resume_hint(draft_id, envelope)`` -- the
  minimal injected view.
* Reduction ratio is measured FIRST (documented below); the asserted threshold
  is fixed strictly BELOW the measured value, with margin.

Measured baseline (this fixed scenario, ceil(len/4) proxy):
    baseline  = 369 tokens/turn   by-value = 142 tokens/turn
    reduction = 0.615  ->  THRESHOLD fixed at 0.50 (below measured, with margin)
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.contract.validator import validate_form  # noqa: E402
from gaia.contract.view import (  # noqa: E402
    estimate_tokens,
    measure_token_savings,
    render_full_reemit,
    render_resume_hint,
)

# --------------------------------------------------------------------------- #
# The FIXED scenario: a realistically-populated, genuinely-VALID COMPLETE
# contract (validated below so the baseline is a real block, not a strawman).
# --------------------------------------------------------------------------- #
DRAFT_ID = "a1b2c3d4e.deadbeefcafe"

FIXED_ENVELOPE = {
    "agent_status": {
        "agent_state": "COMPLETE",
        "agent_id": "a1b2c3d4e",
        "pending_steps": [],
        "next_action": "done",
    },
    "evidence_report": {
        "patterns_checked": [
            "existing plugin pattern in bin/cli/*.py",
            "gaia.contract.drafts addressing",
            "T6 resume hint builder",
        ],
        "files_checked": [
            "bin/cli/contract.py",
            "gaia/contract/drafts.py",
            "hooks/adapters/claude_code.py",
            "gaia/contract/view.py",
            "tests/contract/test_orchestrator_reads_in_progress.py",
            "gaia/contract/validator.py",
        ],
        "commands_run": [
            "pytest tests/contract/test_token_savings.py -q",
            "pytest tests/contract/test_cache_safe_view.py -q",
            "gaia contract view --draft-id a1b2c3d4e.deadbeef",
        ],
        "key_outputs": [
            "by-value view is a minimal pointer plus a one-line status summary",
            "full re-emit repeats the entire envelope every turn",
        ],
        "verbatim_outputs": [
            "reduction_ratio measured at baseline; threshold fixed below it with margin",
            "the invariant prefix is byte-identical across resumes; only the volatile line changes",
            "pending_steps count is surfaced, not the step contents",
        ],
        "cross_layer_impacts": [
            "adapter _build_resume_draft_context delegates to gaia.contract.view.render_resume_hint",
        ],
        "open_gaps": [],
        "verification": {
            "method": "test",
            "result": "pass",
            "details": "pytest tests/contract/test_token_savings.py -q green",
        },
    },
    "consolidation_report": None,
    "approval_request": None,
}

# Threshold fixed AFTER measuring the baseline (0.615), below it with margin.
REDUCTION_THRESHOLD = 0.50


def test_fixed_scenario_is_a_genuinely_valid_contract():
    """The baseline must re-emit a REAL contract, not a strawman -- otherwise
    'fewer tokens than the full block' would be comparing against nonsense."""
    result = validate_form(FIXED_ENVELOPE)
    assert result.ok, f"fixed scenario is not a valid contract: {[str(e) for e in result.errors]}"


def test_by_value_consumes_strictly_fewer_contract_tokens_than_full_reemit():
    """Core AC-15 assertion: reduction measured, reduction asserted."""
    m = measure_token_savings(DRAFT_ID, FIXED_ENVELOPE, turns=1)

    # 1. Strict reduction (never merely equal).
    assert m["byvalue_tokens_total"] < m["baseline_tokens_total"], m

    # 2. The measured reduction clears the post-baseline threshold.
    assert m["reduction_ratio"] >= REDUCTION_THRESHOLD, (
        f"reduction {m['reduction_ratio']:.3f} fell below fixed threshold "
        f"{REDUCTION_THRESHOLD}: {m}"
    )

    # 3. The baseline was genuinely measured (non-trivial token counts on both
    #    sides -- guards against a degenerate empty-view false pass).
    assert m["baseline_tokens_per_turn"] > 0 and m["byvalue_tokens_per_turn"] > 0, m


def test_savings_accumulate_every_turn_the_task_resumes():
    """The recurring per-turn injection is what the by-value flow shrinks, so
    the absolute savings grow with each resume while the RATIO stays fixed
    (both sides scale linearly in turns)."""
    one = measure_token_savings(DRAFT_ID, FIXED_ENVELOPE, turns=1)
    five = measure_token_savings(DRAFT_ID, FIXED_ENVELOPE, turns=5)

    saved_1 = one["baseline_tokens_total"] - one["byvalue_tokens_total"]
    saved_5 = five["baseline_tokens_total"] - five["byvalue_tokens_total"]
    assert saved_5 == saved_1 * 5, (saved_1, saved_5)
    assert abs(five["reduction_ratio"] - one["reduction_ratio"]) < 1e-9


def test_reduction_grows_as_the_contract_grows():
    """The by-value view is decoupled from the contract's bulk, so a RICHER
    contract yields an even LARGER reduction -- the fixed scenario is a floor,
    not a ceiling. This is what makes the threshold conservative for real,
    heavier contracts."""
    lean = {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c3d4e",
            "pending_steps": [],
            "next_action": "starting",
        },
        "evidence_report": {
            k: []
            for k in (
                "patterns_checked", "files_checked", "commands_run",
                "key_outputs", "verbatim_outputs", "cross_layer_impacts",
                "open_gaps",
            )
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    lean_ratio = measure_token_savings(DRAFT_ID, lean, turns=1)["reduction_ratio"]
    rich_ratio = measure_token_savings(DRAFT_ID, FIXED_ENVELOPE, turns=1)["reduction_ratio"]
    assert rich_ratio > lean_ratio, (lean_ratio, rich_ratio)


def test_full_reemit_baseline_is_the_recognizable_fence_block():
    """Sanity: the baseline really is the full fenced block (the thing the
    old model re-emitted), and the by-value view really is not."""
    baseline = render_full_reemit(FIXED_ENVELOPE)
    hint = render_resume_hint(DRAFT_ID, FIXED_ENVELOPE)
    assert baseline.startswith("```agent_contract_handoff\n")
    assert baseline.rstrip().endswith("```")
    # The by-value hint is NOT a fenced contract re-emit.
    assert "```agent_contract_handoff" not in hint
    assert estimate_tokens(hint) < estimate_tokens(baseline)
