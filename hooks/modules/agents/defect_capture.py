"""Route a contract-reported failure into the raw defect floor.

A subagent that emits a ``failure_report`` in its contract has stated a
concrete defect -- what it attempted, what broke, and the observed proof.
This module turns that statement into one ``episode_anomalies`` row so the
defect survives the turn and shows up in the existing anomaly aggregation
(``gaia metrics``) alongside every other anomaly type.

The capture is unrequested (SubagentStop reads the contract it already
parsed) and non-blocking: every failure mode here resolves to ``None`` (no
anomaly appended), ``EpisodicMemory.store_episode`` logs and continues past a
rejected ``insert_episode_anomaly``, and ``episode_writer.write`` swallows the
whole episode write.

``parse_failure_report`` is the single read seam for the block: it applies the
same FAILURE_REPORT_SHAPE check the validator and the SubagentStop gate apply.

The anomaly's ``severity`` passes through from the report verbatim
(``VALID_FAILURE_SEVERITIES``: ``info``/``warning``/``error``).
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# A type distinct from the runtime-inferred anomalies keeps an agent-stated
# defect separable; ``episode_anomalies.type`` is plain TEXT with no CHECK.
DEFECT_ANOMALY_TYPE = "agent_reported_defect"

# Used when the agent omits the optional ``severity``.
DEFAULT_DEFECT_SEVERITY = "warning"

# Evidence is serialized into the single ``payload`` column; the cap bounds the row.
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
        logger.debug("Defect capture failed (non-fatal): %s", exc)
        return None
