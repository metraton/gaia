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

What runs GREEN now that the replay backend landed (brief #89 AC-2 close):

- Live replay through the real PreToolUse entry point via the
  ``hook_log_replay`` backend (:class:`tests.evals.runner.HookLogReplayBackend`),
  comparing the observed ``permissionDecision`` to the curated
  ``expected_decision``. ``hook_log_replay`` is declared in
  ``catalog.VALID_BACKENDS`` and implemented in ``runner.py``;
  ``expected_decision`` is now a field on ``catalog.CaseModel``. The backend
  shells out to ``hooks/pre_tool_use.py`` (subprocess, never an import -- the
  runner's "MUST NOT import from hooks/" contract holds) and reports the
  literal allow/ask/deny decision for each case.
"""

from __future__ import annotations

import json
from pathlib import Path

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


class TestGoldenLiveReplay:
    """Live oracle assertion -- replays every case through the real
    PreToolUse hook and compares the observed decision to the curated
    ``expected_decision``.

    This is the AC-2 close: the ``hook_log_replay`` backend is wired and
    ``expected_decision`` is a ``CaseModel`` field, so the deferred
    assertion now runs for real instead of being an xfail placeholder.
    """

    def test_wiring_preconditions(self):
        """The two follow-up blockers are gone: the field exists and the
        backend is exported."""
        from tests.evals.catalog import CaseModel
        from tests.evals.runner import HookLogReplayBackend  # noqa: F401

        assert hasattr(CaseModel, "expected_decision"), (
            "CaseModel must carry expected_decision for the replay oracle"
        )

    def test_decisions_match_oracle(self):
        """Replay each curated case through ``pre_tool_use.py`` and assert
        the observed allow/ask/deny matches the human oracle.

        The ``contract_grader`` validates response *shape* and is exercised
        by the broader eval suite; here the load-bearing assertion is the
        decision comparison itself -- a single mismatch is a real security
        defect (the core enforced something other than the curated policy).
        """
        from tests.evals.runner import HookLogReplayBackend, dispatch

        cases = load_catalog(GOLDEN_CATALOG)
        backend = HookLogReplayBackend()

        observed: dict[str, str] = {}
        mismatches: list[str] = []
        for case in cases:
            assert case.backend == "hook_log_replay", (
                f"{case.id}: golden catalog must use hook_log_replay backend"
            )
            assert case.expected_decision in VALID_DECISIONS, (
                f"{case.id}: expected_decision not loaded onto CaseModel"
            )
            result = dispatch(
                agent_type=case.agent,
                task=case.task,
                backend=backend,
            )
            payload = json.loads(result.stdout)
            decision = payload.get("decision")
            observed[case.id] = decision
            if decision != case.expected_decision:
                mismatches.append(
                    f"{case.id}: expected {case.expected_decision!r}, "
                    f"observed {decision!r} (raw={payload.get('raw_decision')!r}, "
                    f"reason={payload.get('reason', '')[:80]!r})"
                )

        assert not mismatches, (
            "live security decisions diverge from the curated oracle:\n"
            + "\n".join(mismatches)
        )

        # Sanity: the corpus actually exercised every decision class live.
        assert set(observed.values()) >= VALID_DECISIONS, (
            f"replay did not cover every decision class; observed: "
            f"{sorted(set(observed.values()))}"
        )
