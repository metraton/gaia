#!/usr/bin/env python3
"""
Subagent stop hook for Claude Code Agent System.

Thin gate: parse stdin -> delegate to modules -> format response -> exit.

Business logic lives in hooks/modules/:
- modules.session.session_manager          : Session ID generation
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
from typing import Any, Dict, List, Optional

# Add hooks dir to path for adapter imports (matches pre_tool_use.py pattern)
sys.path.insert(0, str(Path(__file__).parent))

# Adapter layer
from adapters.claude_code import ClaudeCodeAdapter
from modules.core.stdin import has_stdin_data
from adapters.utils import warn_if_dual_channel

# --- Module imports (all business logic) ---
from modules.agents.contract_validator import (
    extract_commands_from_evidence,
    extract_exit_code_from_output,
    extract_plan_status_from_output,
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

# Configure structured logging with file handler (matching pre_tool_use.py pattern)
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
# Backward-compatible aliases (tests and e2e import these underscore names)
# ============================================================================

_extract_commands_from_evidence = extract_commands_from_evidence
_extract_exit_code_from_output = extract_exit_code_from_output
_extract_plan_status_from_output = extract_plan_status_from_output
_requires_consolidation_report = requires_consolidation_report
_read_transcript = read_transcript
_consume_approval_file = cleanup_approval
_process_context_updates = process_context_updates

# Backward-compatible aliases for old function names used in tests
capture_episodic_memory = write_episode
detect_anomalies = audit_workflow
capture_workflow_metrics = record_workflow
consume_approval_file = cleanup_approval


def _build_task_info_from_hook_data(
    hook_data: Dict[str, Any],
    agent_output: str = "",
) -> Dict[str, Any]:
    """Backward-compatible wrapper for build_task_info_from_hook_data."""
    return build_task_info_from_hook_data(hook_data, agent_output)


from modules.audit.workflow_recorder import get_workflow_memory_dir  # noqa: E402,F811



# ============================================================================
# Main processing chain
# ============================================================================

def subagent_stop_hook(task_info, agent_output):
    """
    Main subagent stop hook - validates contracts, captures metrics, detects anomalies, stores episodes.

    Execution order:
        1. contract_validator.validate() - structural contract check
        2. approval_cleanup.cleanup() - consume approval file
        3. context_writer.update() - progressive context enrichment
        4. workflow_recorder.record() - capture workflow metrics + telemetry
        5. workflow_auditor.audit() - detect anomalies
        6. episode_writer.write() - store episodic memory

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

        # Step 0: Parse json:contract once, share across validators
        parsed_contract = parse_contract(agent_output)

        # Step 1: Contract validation (early gate)
        contract_result = validate_contract(agent_output, task_info)
        if not contract_result.is_valid:
            logger.warning(
                "Contract validation failed for %s: %s",
                agent_type, contract_result.error_message,
            )

        # Step 2: Consume approval file if present for this agent
        cleanup_approval(agent_type)

        # Step 3: Extract command evidence and apply context updates first so
        # workflow telemetry can capture the additive outcome.
        commands_executed = extract_commands_from_evidence(agent_output)
        context_update_result = process_context_updates(agent_output, task_info)

        # Step 4: Capture workflow metrics
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

        # Step 4b: Validate deterministic response contract (reuse parsed contract)
        response_contract = validate_response_contract(
            agent_output,
            task_agent_id=resolve_agent_id(task_info),
            consolidation_required=requires_consolidation_report(task_info),
            parsed_contract=parsed_contract,
        )
        save_validation_result(task_info, response_contract)

        # Step 5: Check for anomalies (expanded auditor)
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

        # Step 6: Capture as episodic memory
        episode_id = write_episode(
            workflow_metrics,
            anomalies=anomalies if anomalies else None,
            commands_executed=commands_executed,
        )

        # Derive contract_attempts from response_contract repair state
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
    elif has_stdin_data():
        try:
            adapter = ClaudeCodeAdapter()
            warn_if_dual_channel()
            stdin_data = sys.stdin.read()

            try:
                event = adapter.parse_event(stdin_data)
            except ValueError as e:
                error_msg = str(e)
                logger.error(f"Adapter parse failed: {error_msg}")
                print(f"HOOK ERROR: {error_msg}", file=sys.stderr)
                if "Empty stdin" in error_msg:
                    print(f"Error: {error_msg}")
                sys.exit(1)

            hook_data = event.payload
            logger.info(
                f"Hook event: {hook_data.get('hook_event_name')}, "
                f"agent: {hook_data.get('agent_type', 'unknown')}"
            )

            completion = adapter.parse_agent_completion(hook_data)

            # Use last_assistant_message directly; fall back to transcript
            agent_output = completion.last_message
            if not agent_output:
                transcript_path = completion.transcript_path
                agent_output = read_transcript(transcript_path) if transcript_path else ""
                logger.info(f"Agent output: {len(agent_output)} chars from transcript (fallback)")
            else:
                logger.info(f"Agent output: {len(agent_output)} chars from last_assistant_message")

            task_info = build_task_info_from_hook_data(hook_data, agent_output)
            result = subagent_stop_hook(task_info, agent_output)

            if result.get("contract_rejected"):
                print(
                    f"HOOK ERROR: Contract rejected: {result.get('error', 'unknown')}",
                    file=sys.stderr,
                )
                print(json.dumps(result))
                sys.exit(2)

            print(json.dumps(result))
            sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            print(f"HOOK ERROR: Invalid JSON from stdin: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            print(f"HOOK ERROR: {str(e)}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage: python subagent_stop.py --test")
        print("       echo '{...}' | python subagent_stop.py  (stdin mode)")
        sys.exit(1)
