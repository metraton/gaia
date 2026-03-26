#!/usr/bin/env python3
"""
Subagent stop hook for Claude Code Agent System.

Thin gate: parse stdin -> delegate to adapter -> format response -> exit.

Architecture:
- Uses adapter layer to parse and process the full SubagentStop lifecycle
- All business logic lives in ClaudeCodeAdapter.adapt_subagent_stop()
- This file is stdin/stdout glue only

Business logic modules (called by the adapter):
- modules.agents.contract_validator        : Contract validation + evidence parsing
- modules.agents.transcript_reader         : Transcript I/O
- modules.agents.task_info_builder         : Hook payload -> task_info mapping
- modules.audit.workflow_recorder          : Workflow metrics capture
- modules.audit.workflow_auditor           : Anomaly detection + Gaia signaling
- modules.memory.episode_writer            : Episodic memory storage + session events
- modules.context.context_writer           : Progressive context enrichment
- modules.security.approval_cleanup        : Approval file consumption
- modules.agents.response_contract         : Response contract validation
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).parent))

# Adapter layer
from adapters.claude_code import ClaudeCodeAdapter
from modules.core.hook_entry import run_hook

# Configure structured logging with file handler
try:
    from modules.core.paths import get_logs_dir
    _log_dir = get_logs_dir()
except ImportError:
    _log_dir = Path.cwd() / ".claude" / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)

_log_file = _log_dir / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [subagent_stop] %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(_log_file),
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# Backward-compatible aliases (tests and e2e import these names)
# ============================================================================

from modules.agents.contract_validator import (
    extract_commands_from_evidence,
    extract_exit_code_from_output,
    parse_contract,
    requires_consolidation_report,
    validate as validate_contract,
)
from modules.agents.response_contract import (
    save_validation_result,
    validate_response_contract,
    resolve_agent_id,
)
from modules.agents.task_info_builder import build_task_info_from_hook_data
from modules.agents.transcript_reader import read_transcript
from modules.audit.workflow_auditor import audit as audit_workflow, signal_gaia_analysis
from modules.audit.workflow_recorder import record as record_workflow
from modules.context.context_writer import process_context_updates
from modules.memory.episode_writer import write as write_episode
from modules.security.approval_cleanup import cleanup as cleanup_approval
from modules.session.session_manager import get_or_create_session_id

_extract_commands_from_evidence = extract_commands_from_evidence
_extract_exit_code_from_output = extract_exit_code_from_output
_read_transcript = read_transcript
_process_context_updates = process_context_updates


def _build_task_info_from_hook_data(
    hook_data: Dict[str, Any],
    agent_output: str = "",
) -> Dict[str, Any]:
    """Backward-compatible wrapper for build_task_info_from_hook_data."""
    return build_task_info_from_hook_data(hook_data, agent_output)


# ============================================================================
# Backward-compatible main processing chain (used by tests directly)
# ============================================================================

def subagent_stop_hook(task_info, agent_output):
    """
    Main subagent stop hook - validates contracts, captures metrics, detects anomalies, stores episodes.

    This is the backward-compatible entry point. The adapter calls the same
    modules internally.

    Args:
        task_info: Task information including ID, description, agent, etc.
        agent_output: Complete output from agent execution

    Returns:
        Success confirmation with metrics info, or contract_rejected dict on validation failure.
    """
    try:
        from datetime import datetime as _dt
        session_id = get_or_create_session_id()
        agent_type = task_info.get("agent", "unknown")

        parsed_contract = parse_contract(agent_output)

        contract_result = validate_contract(agent_output, task_info)
        if not contract_result.is_valid:
            logger.warning(
                "Contract validation failed for %s: %s",
                agent_type, contract_result.error_message,
            )

        cleanup_approval(agent_type)

        commands_executed = extract_commands_from_evidence(agent_output)
        context_update_result = process_context_updates(agent_output, task_info)

        session_context = {
            "timestamp": _dt.now().isoformat(),
            "session_id": session_id,
            "task_id": task_info.get("task_id", "unknown"),
            "agent_id": task_info.get("agent_id", "unknown"),
            "agent": agent_type,
        }
        workflow_metrics = record_workflow(
            task_info,
            agent_output,
            session_context,
            commands_executed=commands_executed,
            context_update_result=context_update_result,
        )

        response_contract = validate_response_contract(
            agent_output,
            task_agent_id=resolve_agent_id(task_info),
            consolidation_required=requires_consolidation_report(task_info),
            parsed_contract=parsed_contract,
        )
        save_validation_result(task_info, response_contract)

        anomalies = audit_workflow(
            workflow_metrics,
            agent_output,
            task_info,
            rejected_sections=(context_update_result or {}).get("rejected", []),
        )
        if not response_contract.valid:
            missing = ", ".join(response_contract.missing) or "none"
            invalid = ", ".join(response_contract.invalid) or "none"
            anomalies.append({
                "type": "response_contract_violation",
                "severity": "critical",
                "message": (
                    f"Agent response contract invalid for {task_info.get('agent', 'unknown')}: "
                    f"missing=[{missing}] invalid=[{invalid}]"
                ),
            })

        if anomalies:
            logger.warning(f"{len(anomalies)} anomalies detected in workflow")
            signal_gaia_analysis(anomalies, workflow_metrics)

        workflow_metrics["anomalies_detected"] = len(anomalies)
        workflow_metrics["anomaly_types"] = [a.get("type", "") for a in anomalies]

        episode_id = write_episode(
            workflow_metrics,
            anomalies=anomalies if anomalies else None,
            commands_executed=commands_executed,
        )

        contract_attempts = 0
        if not response_contract.valid:
            try:
                repair_data = response_contract.to_dict()
                contract_attempts = int(repair_data.get("repair_attempts", 0))
            except Exception:
                contract_attempts = 0

        return {
            "success": True,
            "session_id": session_id,
            "status": "metrics_captured",
            "metrics_captured": True,
            "anomalies_detected": len(anomalies) if anomalies else 0,
            "episode_id": episode_id,
            "context_updated": context_update_result.get("updated", False) if context_update_result else False,
            "response_contract": response_contract.to_dict(),
            "contract_validated": contract_result.is_valid,
            "contract_attempts": contract_attempts,
        }

    except Exception as e:
        logger.error(f"Error in subagent_stop_hook: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "status": "partial_update"
        }


# ============================================================================
# Thin gate handler (stdin mode)
# ============================================================================

def _handle_subagent_stop(event) -> None:
    """Process a SubagentStop event.

    Delegates all business logic to the adapter.

    Args:
        event: Parsed HookEvent from the adapter layer.
    """
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_subagent_stop(event)

    if response.exit_code == 2:
        error_msg = response.output.get("error", "unknown") if isinstance(response.output, dict) else str(response.output)
        print(
            f"HOOK ERROR: Contract rejected: {error_msg}",
            file=sys.stderr,
        )

    print(json.dumps(response.output))
    sys.exit(response.exit_code)


# ============================================================================
# CLI interface
# ============================================================================

def main():
    """CLI interface for testing metrics capture."""
    if len(sys.argv) < 2:
        print("Usage: python subagent_stop.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        test_task_info = {
            "task_id": "T006",
            "description": "Terraform plan for infrastructure",
            "agent": "terraform-architect",
            "tier": "T1",
            "tags": ["#terraform", "#infrastructure"],
        }
        test_output = (
            "# Terraform Architect Execution Log\n\n"
            "## Task: T006 - Terraform plan for infrastructure\n\n"
            "### Results:\n"
            "- Configuration validated successfully\n"
            "- Plan generated with 12 resources\n"
            "- Duration: 45000 ms\n"
        )
        result = subagent_stop_hook(test_task_info, test_output)
        if result["success"]:
            print("Test completed successfully!")
            print(f"Session ID: {result['session_id']}")
            print(f"Anomalies: {result['anomalies_detected']}")
            print(f"Episode ID: {result.get('episode_id', 'none')}")
        else:
            print(f"Test failed: {result.get('error', 'Unknown error')}")
    else:
        print("Unknown command. Use --test to run test.")


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        run_hook(_handle_subagent_stop, hook_name="subagent_stop")
