#!/usr/bin/env python3
"""
Pre-tool use hook v2 - Modular Architecture (Optimized)

Thin entry point that uses the new modular hook system.
Maintains backward compatibility with Claude Code hook interface.

Features:
- Modular validation (security, tools, workflow modules)
- State sharing with post-hook
- Auto-approval for read-only commands (handled in BashValidator)
- LRU cache for tier classification (60-70% faster for repeated commands)
- Fast-path detection for ultra-common commands
- Lazy parsing (only parse compound commands with operators)
"""

import sys
import json
import logging
import os
import re
import select
import subprocess
from pathlib import Path
from datetime import datetime

# Add modules to path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "modules" / "skills"))

from modules.core.paths import get_logs_dir

# Import skill loader
try:
    from skill_loader import load_skills_for_task
except ImportError:
    # Fallback if skill_loader not available
    def load_skills_for_task(prompt: str, subagent_type: str) -> str:
        return ""

# Import context exhaustion detector
try:
    from modules.context.exhaustion_detector import check_context_health
except ImportError:
    # Fallback if exhaustion_detector not available
    def check_context_health(session_id: str):
        return None

from modules.core.state import create_pre_hook_state, save_hook_state
from modules.security.tiers import SecurityTier, classify_command_tier
from modules.tools.bash_validator import BashValidator, create_permission_allow_response
from modules.tools.task_validator import TaskValidator

# Configure logging
log_file = get_logs_dir() / f"pre_tool_use_v2-{os.getenv('USER', 'unknown')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# PROJECT CONTEXT INJECTION
# ============================================================================

PROJECT_AGENTS = [
    "terraform-architect",
    "gitops-operator",
    "cloud-troubleshooter",
    "devops-developer"
]


def _load_skills_for_task(prompt: str, subagent_type: str) -> str:
    """
    Load skills on-demand for task

    Args:
        prompt: Task prompt
        subagent_type: Agent type

    Returns:
        Formatted skills content (empty string if loader unavailable)
    """
    try:
        return load_skills_for_task(prompt, subagent_type)
    except Exception as e:
        logger.warning(f"Failed to load skills: {e}")
        return ""



def _should_inject_on_resume(parameters: dict) -> bool:
    """
    Determine if context should be injected on a resume operation.
    
    By default, resume operations skip context injection because the agent
    already has context from phase 1. However, in some cases we need fresh context:
    
    Rules:
    1. If prompt contains "User approved" -> NO inject (simple execution)
    2. If prompt has substantial new information (>100 words) -> YES inject
    3. If prompt mentions new resources/scope -> YES inject  
    4. Default: NO inject (trust existing context)
    
    Args:
        parameters: Task tool parameters with 'prompt' key
    
    Returns:
        True if context should be injected, False otherwise
    """
    prompt = parameters.get("prompt", "")
    prompt_lower = prompt.lower()
    
    # Case 1: Post-approval execution - NO injection needed
    # These are simple "go ahead" instructions
    approval_indicators = [
        "user approved",
        "approved. execute",
        "approved, execute", 
        "approval confirmed",
        "proceed with execution",
        "go ahead",
        "confirmed. proceed"
    ]
    if any(indicator in prompt_lower for indicator in approval_indicators):
        logger.debug("Resume with approval indicator - skipping context injection")
        return False
    
    # Case 2: Substantial new information - YES inject
    # If user is providing a lot of new context, we should refresh
    word_count = len(prompt.split())
    if word_count > 100:
        logger.info(f"Resume with substantial new info ({word_count} words) - injecting context")
        return True
    
    # Case 3: New scope/resources mentioned - YES inject
    # These words indicate the user is expanding the task scope
    scope_expansion_indicators = [
        "also",
        "additionally", 
        "another",
        "new ",
        "different",
        "and also",
        "as well as",
        "in addition",
        "plus",
        "moreover",
        "furthermore",
        "besides that"
    ]
    if any(indicator in prompt_lower for indicator in scope_expansion_indicators):
        logger.info("Resume with scope expansion - injecting context")
        return True
    
    # Default: Trust existing context
    logger.debug("Standard resume - skipping context injection")
    return False

def _inject_project_context(parameters: dict) -> dict:
    """
    Inject project context for project agents.

    Automatically provisions context from project-context.json for agents that need it.
    This makes the orchestrator lightweight - it only routes, the hook injects context.

    Args:
        parameters: Original Task tool parameters

    Returns:
        Modified parameters with context injected into prompt
    """
    subagent_type = parameters.get("subagent_type", "")

    # Only inject for project agents (not for generic agents like Explore, general-purpose, etc.)
    if subagent_type not in PROJECT_AGENTS:
        logger.debug(f"Skipping context injection for non-project agent: {subagent_type}")
        return parameters

    # Conditional context injection for resume operations (Phase 4 enhancement)
    # By default, skip injection for resume (context from phase 1)
    # But inject if: new info (>100 words), scope expansion, or new resources
    if parameters.get("resume"):
        if _should_inject_on_resume(parameters):
            logger.info(f"Resume with new context detected, injecting for: {parameters.get('resume')}")
            # Continue to context injection below
        else:
            logger.debug(f"Standard resume, skipping context injection: {parameters.get('resume')}")
            return parameters

    prompt = parameters.get("prompt", "")
    if not prompt:
        logger.warning(f"No prompt provided for {subagent_type}, skipping context injection")
        return parameters

    try:
        # Find context_provider.py
        context_provider_paths = [
            Path(".claude/tools/context/context_provider.py"),
            Path("node_modules/@jaguilar87/gaia-ops/tools/context/context_provider.py"),
            Path(__file__).parent.parent / "tools" / "context" / "context_provider.py"
        ]

        context_provider = None
        for path in context_provider_paths:
            if path.exists():
                context_provider = path
                break

        if not context_provider:
            logger.warning("context_provider.py not found, skipping context injection")
            return parameters

        # Execute context_provider.py to get filtered context
        logger.info(f"Injecting context for {subagent_type}...")
        result = subprocess.run(
            ["python3", str(context_provider), subagent_type, prompt],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=os.getcwd()
        )

        if result.returncode != 0:
            logger.error(f"context_provider.py failed: {result.stderr}")
            # Don't block - let agent proceed without context
            return parameters

        # Parse context JSON
        try:
            context_payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse context JSON: {e}")
            return parameters

        # Load skills on-demand based on prompt
        skills_content = _load_skills_for_task(prompt, subagent_type)

        # Inject context + skills into prompt
        enriched_prompt = f"""# Project Context (Auto-Injected)

{json.dumps(context_payload, indent=2)}

{skills_content}
---

# User Task

{prompt}
"""

        # Modify parameters
        parameters["prompt"] = enriched_prompt

        # Add metadata for TaskValidator to know the original user task
        # This prevents T3 keyword detection in injected context
        parameters["_original_user_task"] = prompt

        context_level = context_payload.get("metadata", {}).get("context_level", "unknown")
        standards_count = context_payload.get("metadata", {}).get("standards_count", 0)

        logger.info(
            f"✅ Context injected for {subagent_type} "
            f"(level={context_level}, standards={standards_count})"
        )

        return parameters

    except subprocess.TimeoutExpired:
        logger.error("context_provider.py timed out (15s)")
        return parameters
    except Exception as e:
        logger.error(f"Error injecting context: {e}", exc_info=True)
        return parameters


def _filter_events_for_agent(events: list, agent: str) -> list:
    """
    Filter events relevant to agent domain.

    Args:
        events: List of critical events from session
        agent: Agent type (e.g., "gitops-operator")

    Returns:
        Filtered list of events relevant to agent
    """
    # Define which events each agent should see
    filters = {
        "terraform-architect": ["git_commit", "infrastructure_change"],
        "gitops-operator": ["git_commit", "git_push", "infrastructure_change"],
        "devops-developer": ["git_commit", "file_modifications"],
        "cloud-troubleshooter": "*",  # All events (needs full history for diagnosis)
        "gaia": "*"  # All events (workflow analysis)
    }

    agent_filter = filters.get(agent, [])

    # Return all events for wildcard agents
    if agent_filter == "*":
        return events[-10:]  # Last 10 events

    # Filter by event type and return max 10
    filtered = [
        e for e in events[-20:]  # Search last 20
        if e.get("event_type") in agent_filter
    ]

    return filtered[:10]  # Return max 10


def _format_events_summary(events: list) -> str:
    """
    Format events as readable summary for agent context.

    Args:
        events: List of filtered events

    Returns:
        Formatted markdown string
    """
    if not events:
        return "No recent events"

    lines = []

    for event in events:
        etype = event.get("event_type", "")
        ts = event.get("timestamp", "")[:16]  # YYYY-MM-DDTHH:MM

        if etype == "git_commit":
            msg = event.get("commit_message", "")
            hash_val = event.get("commit_hash", "")[:7]
            if hash_val and msg:
                lines.append(f"- [{ts}] Commit {hash_val}: {msg}")

        elif etype == "git_push":
            branch = event.get("branch", "")
            if branch:
                lines.append(f"- [{ts}] Pushed to {branch}")

        elif etype == "file_modifications":
            count = event.get("modification_count", 0)
            if count:
                lines.append(f"- [{ts}] Modified {count} files")

        elif etype == "infrastructure_change":
            cmd = event.get("command", "")
            if cmd:
                lines.append(f"- [{ts}] Infrastructure: {cmd}")

    return "\n".join(lines) if lines else "No recent events"


def _inject_session_events(parameters: dict) -> dict:
    """
    Inject relevant session events for agent context.

    Filters events by agent domain to avoid noise.
    Only injects for project agents on new tasks (not resume).

    Args:
        parameters: Task tool parameters

    Returns:
        Modified parameters with events injected into prompt
    """
    subagent_type = parameters.get("subagent_type", "")

    # Only inject for project agents
    if subagent_type not in PROJECT_AGENTS:
        logger.debug(f"Skipping session events for non-project agent: {subagent_type}")
        return parameters

    # Skip for resume operations (already has context from phase 1)
    if parameters.get("resume"):
        logger.debug(f"Skipping session events for resume: {parameters.get('resume')}")
        return parameters

    # Get session events
    context_path = Path(".claude/session/active/context.json")
    if not context_path.exists():
        logger.debug("No session context file found")
        return parameters

    try:
        with open(context_path, 'r') as f:
            context = json.load(f)

        events = context.get("critical_events", [])
        if not events:
            logger.debug("No critical events in session")
            return parameters

        # Filter by agent domain
        filtered = _filter_events_for_agent(events, subagent_type)

        if not filtered:
            logger.debug(f"No relevant events for {subagent_type}")
            return parameters

        # Format events summary
        events_summary = _format_events_summary(filtered)

        # Inject into prompt
        prompt = parameters.get("prompt", "")

        enriched_prompt = f"""{prompt}

# Recent Session Events (Auto-Injected, Last 24h)
{events_summary}
"""

        parameters["prompt"] = enriched_prompt
        logger.info(f"✅ Session events injected for {subagent_type} ({len(filtered)} events)")

        return parameters

    except Exception as e:
        logger.warning(f"Failed to inject session events: {e}")
        return parameters


# ============================================================================
# MAIN HOOK LOGIC
# ============================================================================

def pre_tool_use_hook(tool_name: str, parameters: dict) -> str | dict | None:
    """
    Pre-tool use hook implementation.

    Args:
        tool_name: Name of the tool being invoked
        parameters: Tool parameters

    Returns:
        None: allowed (no modification)
        str: blocked (error message, triggers exit 2)
        dict: allowed with modification (JSON with updatedInput, exit 0)
    """
    logger.info(f"Hook invoked: tool={tool_name}, params={json.dumps(parameters)[:200]}")

    try:
        # Validate inputs
        if not isinstance(tool_name, str):
            return "Error: Invalid tool name"
        if not isinstance(parameters, dict):
            return "Error: Invalid parameters"

        # ====================================================================
        # CONTEXT EXHAUSTION CHECK (Phase 4)
        # ====================================================================
        # Check if context is approaching limits
        # Uses "default" session for now - could be enhanced with actual session ID
        context_warning = check_context_health("default")
        if context_warning:
            logger.warning(f"Context exhaustion detected: {context_warning}")
            # Log warning but don't block - let operation continue
            # The warning will appear in logs for monitoring/debugging

        # Route to appropriate validator
        if tool_name.lower() == "bash":
            return _handle_bash(tool_name, parameters)
        elif tool_name.lower() == "task":
            return _handle_task(tool_name, parameters)
        else:
            # Other tools pass through
            return None

    except Exception as e:
        logger.error(f"Unexpected error in pre_tool_use_hook: {e}", exc_info=True)
        return f"Error during security validation: {str(e)}"


def _handle_bash(tool_name: str, parameters: dict) -> str | dict | None:
    """
    Handle Bash tool validation.

    Returns:
        None: allowed (no modification)
        str: blocked (error message)
        dict: allowed with modification (hookSpecificOutput JSON)
    """
    command = parameters.get("command", "")
    if not command:
        return "Error: Bash tool requires a command"

    # Validate command
    validator = BashValidator()
    result = validator.validate(command)

    if not result.allowed:
        logger.warning(f"BLOCKED: {command[:100]} - {result.reason}")
        return _format_blocked_message(result)

    # Save state for post-hook (ONLY for allowed commands)
    effective_command = result.modified_input.get("command", command) if result.modified_input else command
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=effective_command,
        tier=str(result.tier),
        allowed=True,
    )
    save_hook_state(state)

    # If validator modified the command (e.g., stripped footer), emit updatedInput
    if result.modified_input:
        logger.info(f"MODIFIED: {command[:80]} → stripped footer - tier={result.tier}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": result.reason,
                "updatedInput": result.modified_input
            }
        }

    logger.info(f"ALLOWED: {command[:100]} - tier={result.tier}")
    return None


def _handle_task(tool_name: str, parameters: dict) -> str | None:
    """
    Handle Task tool validation with resume support.

    Validates both new Task invocations and resume operations.
    Resume operations skip heavy validations since agent was already validated.

    NEW: Automatically injects project context for project agents.
    """
    # ========================================================================
    # PROJECT CONTEXT INJECTION (for new tasks only, not resume)
    # ========================================================================
    if not parameters.get("resume"):
        parameters = _inject_project_context(parameters)
        parameters = _inject_session_events(parameters)

    # ========================================================================
    # RESUME DETECTION AND VALIDATION
    # ========================================================================
    resume_id = parameters.get("resume")

    if resume_id:
        # This is a resume operation - validate differently

        # Step 24: Validate agentId format
        # Pattern: a###### where # is 0-9 or a-f (6-7 chars after 'a')
        if not re.match(r'^a[0-9a-f]{6,7}$', resume_id):
            logger.warning(f"BLOCKED Resume: Invalid agentId format '{resume_id}'")
            return (
                f"❌ Invalid resume ID format: '{resume_id}'\n\n"
                "Agent ID should match pattern: a######\n"
                "Example: a12345f or a123456\n\n"
                "The agent ID is returned at the end of agent responses.\n"
                "Look for: 'agentId: a12345' in the previous agent output."
            )

        # Step 25: Validate that prompt exists
        prompt = parameters.get("prompt", "")
        if not prompt or not prompt.strip():
            logger.warning(f"BLOCKED Resume: Missing prompt for agent {resume_id}")
            return (
                "❌ Resume requires a prompt\n\n"
                "When resuming an agent, you must provide instructions:\n\n"
                "Task(\n"
                "    resume=\"a12345\",\n"
                "    prompt=\"User approved. Execute the deployment plan.\"\n"
                ")\n\n"
                "The prompt tells the agent what to do next."
            )

        # Step 26: Skip heavy validations (agent already validated in phase 1)
        # Step 27: Log resume operation
        logger.info(f"✅ RESUME: Continuing agent {resume_id}")

        # Step 28: Save state for post-hook
        state = create_pre_hook_state(
            tool_name=tool_name,
            command=f"Task:resume:{resume_id}",
            tier="T0",  # Resume doesn't change tier
            allowed=True,
            is_t3=False,  # Approval already handled in phase 1
            has_approval=True,  # Implicit from phase 1
        )
        save_hook_state(state)

        logger.info(f"ALLOWED Resume: agent {resume_id} - prompt length: {len(prompt)}")
        return None  # Allow resume

    # ========================================================================
    # STANDARD TASK VALIDATION (new task, not resume)
    # ========================================================================
    validator = TaskValidator()
    result = validator.validate(parameters)

    if not result.allowed:
        logger.warning(f"BLOCKED Task: {result.agent_name} - {result.reason}")
        return result.reason

    # Save state for post-hook (ONLY for allowed tasks)
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=f"Task:{result.agent_name}",
        tier=str(result.tier),
        allowed=True,  # Always true here
        is_t3=result.is_t3_operation,
        has_approval=result.has_approval,
    )
    save_hook_state(state)

    logger.info(f"ALLOWED Task: {result.agent_name}")
    return None


def _format_blocked_message(result) -> str:
    """Format blocked command message."""
    msg = (
        f"Command blocked by security policy\n\n"
        f"Tier: {result.tier}\n"
        f"Reason: {result.reason}\n"
    )

    if result.suggestions:
        msg += "\nSuggestions:\n"
        for suggestion in result.suggestions:
            msg += f"  - {suggestion}\n"

    return msg


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """CLI interface for testing."""
    if len(sys.argv) < 2:
        print("Usage: python pre_tool_use_v2.py <command>")
        print("       python pre_tool_use_v2.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        _run_tests()
    else:
        command = " ".join(sys.argv[1:])
        result = pre_tool_use_hook("bash", {"command": command})
        if result:
            print(f"BLOCKED: {result}")
            sys.exit(1)
        else:
            print(f"ALLOWED: {command}")


def _run_tests():
    """Run validation tests."""
    print("Testing Pre-Tool Use Hook v2...\n")

    test_cases = [
        ("terraform validate", True, "T1"),
        ("terraform apply", False, "T3"),
        ("kubectl get pods", True, "T0"),
        ("kubectl apply -f manifest.yaml", False, "T3"),
        ("kubectl apply -f manifest.yaml --dry-run=client", True, "T2"),
        ("ls -la", True, "T0"),
        ("rm -rf /", False, "T3"),
    ]

    for command, expected_allowed, expected_tier in test_cases:
        result = pre_tool_use_hook("bash", {"command": command})
        actual_allowed = result is None
        status = "PASS" if actual_allowed == expected_allowed else "FAIL"
        print(f"{status}: {command}")
        if status == "FAIL":
            print(f"  Expected: allowed={expected_allowed}, Got: allowed={actual_allowed}")

    print("\nTest completed")


def has_stdin_data() -> bool:
    """Check if there's data available on stdin."""
    if sys.stdin.isatty():
        return False
    # Check if stdin has data available (non-blocking)
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except Exception:
        return not sys.stdin.isatty()


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    # Check if running from CLI with arguments
    if len(sys.argv) > 1:
        main()
    elif has_stdin_data():
        try:
            stdin_data = sys.stdin.read()
            if not stdin_data.strip():
                print("Error: Empty stdin data")
                sys.exit(1)

            hook_data = json.loads(stdin_data)

            logger.info(f"Hook event: {hook_data.get('hook_event_name')}")

            tool_name = hook_data.get("tool_name", "")
            tool_input = hook_data.get("tool_input", {})

            # Standard validation (auto-approval handled in BashValidator)
            result = pre_tool_use_hook(tool_name, tool_input)
            if isinstance(result, dict):
                # Dict = allowed with modification (updatedInput)
                # Output JSON so Claude Code applies the modified parameters
                print(json.dumps(result))
                sys.exit(0)
            elif isinstance(result, str):
                # String = BLOCKING error - Claude MUST stop execution
                # Exit code 2 = blocking, exit code 1 = non-blocking
                # See: https://docs.anthropic.com/en/docs/claude-code/hooks
                print(result)
                sys.exit(2)
            else:
                # None = allowed, no modification
                sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            print(f"Hook error: {str(e)}")
            sys.exit(1)
    else:
        # No args and no stdin - show usage
        print("Usage: python pre_tool_use_v2.py <command>")
        print("       python pre_tool_use_v2.py --test")
        print("       echo '{\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"ls\"}}' | python pre_tool_use_v2.py")
        sys.exit(1)
