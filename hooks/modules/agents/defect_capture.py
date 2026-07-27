"""Route a contract-reported failure into the raw defect floor.

A subagent that emits a ``failure_report`` in its contract has stated a
concrete defect -- what it attempted, what broke, and the observed proof.
This module turns that statement into one ``episode_anomalies`` row so the
defect survives the turn and shows up in the existing anomaly aggregation
(``gaia metrics``) alongside every other anomaly type.

Two properties are load-bearing and are why this lives in the hook rather
than in an agent's own tooling:

- **Unrequested.** No agent opts in and none can. SubagentStop reads the
  contract it already parsed; emitting a well-formed ``failure_report`` is
  the whole of the agent's participation.
- **Non-blocking.** The capture is advisory telemetry. A malformed report,
  a parse failure, or a rejected DB write must leave the subagent's turn
  byte-identical to a turn with no report at all. Every failure mode here
  resolves to ``None`` (no anomaly appended); the persistence layer below
  -- ``EpisodicMemory.store_episode`` -- already logs and continues past a
  rejected ``insert_episode_anomaly``, and ``episode_writer.write`` swallows
  the whole episode write. A telemetry channel that can fell the work it
  observes is worse than no channel.

Normalization is NOT redone here. ``parse_failure_report`` is the single
read seam for the block: it applies the same FAILURE_REPORT_SHAPE check the
validator and the SubagentStop gate apply, so a block malformed enough to be
rejected at write time can never be read here as clean.

The anomaly's ``severity`` passes through from the report verbatim. The
report's enum (``info``/``warning``/``error``, ``VALID_FAILURE_SEVERITIES``)
is already a subset of the ranks ``gaia metrics`` sorts by, so no
translation is needed -- and translating would let the two drift.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# The anomaly type is free-form at the storage layer (``episode_anomalies.type``
# is a plain TEXT column with no enum or CHECK), so a new type costs nothing and
# is aggregated by ``gaia metrics`` on arrival. Naming it distinctly is what
# keeps an agent-STATED defect separable from the anomalies the runtime infers
# on its own.
DEFECT_ANOMALY_TYPE = "agent_reported_defect"

# Used when the agent omits the optional ``severity``. The report is a defect
# the agent chose to state, so the floor is a warning, not info -- but it is not
# an error either, since the agent declined to grade it.
DEFAULT_DEFECT_SEVERITY = "warning"

# Evidence is copied into the anomaly payload, which is JSON-serialized into a
# single column. Cap it so one runaway report cannot bloat the row.
MAX_EVIDENCE_ITEMS = 20


def build_defect_anomaly(
    parsed_contract: Any,
    *,
    agent: str = "unknown",
) -> Optional[Dict[str, Any]]:
    """Build the anomaly dict for a contract-reported defect, or None.

    Args:
        parsed_contract: The parsed ``agent_contract_handoff`` envelope, or
            any other value -- a non-dict is simply "no report".
        agent: Emitting agent name, recorded on the anomaly for attribution.

    Returns:
        An anomaly dict in the shape the episode writer consumes (``type``,
        ``severity``, ``message``, plus extra keys preserved verbatim into
        ``episode_anomalies.payload``), or ``None`` when there is no
        well-formed report to record. Never raises.
    """
    try:
        if not isinstance(parsed_contract, dict):
            return None

        from modules.agents.contract_validator import parse_failure_report

        report = parse_failure_report(parsed_contract)
        if not report:
            return None

        attempted = report["attempted"]
        symptom = report["symptom"]
        component = report.get("component")
        severity = report.get("severity") or DEFAULT_DEFECT_SEVERITY
        evidence = list(report.get("evidence") or [])[:MAX_EVIDENCE_ITEMS]

        where = f" in {component}" if component else ""
        return {
            "type": DEFECT_ANOMALY_TYPE,
            "severity": severity,
            "message": (
                f"{agent} reported a defect{where}: attempted {attempted} -- {symptom}"
            ),
            "agent": agent,
            "attempted": attempted,
            "symptom": symptom,
            "component": component,
            "evidence": evidence,
        }
    except Exception as exc:
        # Advisory channel: a defect in the defect capture never costs the
        # turn. Debug level, matching the other non-fatal SubagentStop
        # enrichment steps.
        logger.debug("Defect capture failed (non-fatal): %s", exc)
        return None
