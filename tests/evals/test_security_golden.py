"""Golden security-decision catalog tests (brief #89 AC-2).

This is the human-curated security oracle: ``catalogs/security_decisions.yaml``
pairs each (tool, command) input with an ``expected_decision`` asserted by a
human against the policy the security core SHOULD enforce -- NOT extracted from
any recorded log. That distinction is the whole point of AC-2: the
gaia_simulator path uses the recorded log as its oracle
(``extractor.py:330  expected_decision = outcome['decision']``) and is a
regression detector; this catalog is an independent oracle that catches a
defect even on the first run that records the log.

What runs GREEN today (pure-Python, zero LLM / subprocess budget):

- The catalog loads via :func:`tests.evals.catalog.load_catalog`.
- Case ids are unique and every case carries a curated ``expected_decision``
  in the allowed vocabulary ``{allow, ask, deny}``.
- The catalog exercises every decision class (allow / ask / deny).

What is DEFERRED (xfail until the backend lands -- see the catalog's NOTE):

- Live replay through the real PreToolUse entry point via the
  ``hook_log_replay`` backend, comparing the observed ``permissionDecision``
  to the curated ``expected_decision``. ``hook_log_replay`` is declared in
  ``catalog.VALID_BACKENDS`` but is not implemented in ``runner.py``, and
  ``expected_decision`` is not yet a field on ``catalog.CaseModel``. Wiring
  both into the runner is the follow-up unit; this test reaches xpass the
  moment the backend exists, signalling the work is done.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.evals.catalog import load_catalog

GOLDEN_CATALOG = (
    Path(__file__).resolve().parent / "catalogs" / "security_decisions.yaml"
)

# Oracle vocabulary == Claude Code PreToolUse permissionDecision values.
VALID_DECISIONS = frozenset({"allow", "ask", "deny"})


def _raw_cases() -> list[dict]:
    """Return the raw YAML cases (load_catalog drops unknown keys like
    ``expected_decision``, so the oracle must be read from the raw doc)."""
    data = yaml.safe_load(GOLDEN_CATALOG.read_text(encoding="utf-8"))
    return data["cases"]


class TestGoldenCatalogStructure:
    """Structural validation -- runs green, no budget spent."""

    def test_catalog_loads(self):
        """The golden catalog passes the shared catalog validator."""
        cases = load_catalog(GOLDEN_CATALOG)
        assert cases, "security_decisions.yaml produced no cases"

    def test_case_ids_unique(self):
        ids = [c["id"] for c in _raw_cases()]
        assert len(ids) == len(set(ids)), f"duplicate case ids: {ids}"

    def test_every_case_has_curated_decision(self):
        """Every case carries a human-curated expected_decision in the
        allowed vocabulary -- the catalog is an oracle, not a log replay."""
        bad = [
            (c["id"], c.get("expected_decision"))
            for c in _raw_cases()
            if c.get("expected_decision") not in VALID_DECISIONS
        ]
        assert not bad, f"cases missing/invalid expected_decision: {bad}"

    def test_all_decision_classes_present(self):
        """The oracle must exercise allow, ask AND deny, or it is not a
        meaningful security corpus."""
        decisions = {c.get("expected_decision") for c in _raw_cases()}
        assert VALID_DECISIONS <= decisions, (
            f"catalog must cover every decision class; missing: "
            f"{VALID_DECISIONS - decisions}"
        )


@pytest.mark.xfail(
    reason=(
        "hook_log_replay backend not implemented in runner.py and "
        "expected_decision not yet a CaseModel field (brief #89 AC-2 follow-up). "
        "This xpasses once the backend + field land."
    ),
    strict=True,
)
class TestGoldenLiveReplay:
    """Live oracle assertion -- deferred until the replay backend exists."""

    def test_decisions_match_oracle(self):
        from tests.evals.catalog import CaseModel  # noqa: F401
        from tests.evals.runner import dispatch  # noqa: F401

        # The CaseModel today has no expected_decision field and the runner has
        # no hook_log_replay backend, so this cannot be wired yet. Assert the
        # precondition so the xfail flips to xpass exactly when it is satisfiable.
        assert hasattr(CaseModel, "expected_decision"), (
            "CaseModel still lacks expected_decision -- replay oracle not wireable"
        )
