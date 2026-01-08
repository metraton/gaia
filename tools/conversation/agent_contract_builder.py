#!/usr/bin/env python3
"""
Agent Contract Builder
Constructs structured contracts for agents by combining multiple data sources
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from datetime import datetime

try:
    from .progressive_disclosure import ProgressiveDisclosureManager
except ImportError:
    from progressive_disclosure import ProgressiveDisclosureManager

logger = logging.getLogger(__name__)


class AgentContractBuilder:
    """
    Builds Agent Contracts by combining:
    1. Project Context (static information)
    2. Agent Responses (previous interactions)
    3. Task specification (current request)
    """

    def __init__(self, project_context_path: Optional[str] = None):
        """
        Initialize the contract builder.

        Args:
            project_context_path: Path to project-context.json file
        """
        self.project_context_path = project_context_path or ".claude/project-context.json"
        self.project_context = self._load_project_context()
        self.progressive_manager = ProgressiveDisclosureManager()

        # Schema version for contracts
        self.contract_version = "1.0"

        logger.info(f"Agent Contract Builder initialized with context from {self.project_context_path}")

    def _load_project_context(self) -> Dict[str, Any]:
        """Load project context from JSON file."""
        try:
            context_file = Path(self.project_context_path)
            if context_file.exists():
                with open(context_file, 'r') as f:
                    return json.load(f)
            else:
                logger.warning(f"Project context file not found: {self.project_context_path}")
                return {}
        except Exception as e:
            logger.error(f"Error loading project context: {e}")
            return {}

    def build_contract(self,
                      user_query: str,
                      agent_name: str,
                      conversation: Optional[Dict[str, Any]] = None,
                      force_level: Optional[int] = None) -> Dict[str, Any]:
        """
        Build a complete Agent Contract for the specified agent.

        Args:
            user_query: The user's query/request
            agent_name: Name of the target agent
            conversation: Current conversation state (includes agent_responses)
            force_level: Force a specific context level (1-4)

        Returns:
            Complete Agent Contract ready to send to the agent
        """
        # Analyze the user query
        intent = self.progressive_manager.analyze_query_intent(user_query)

        # Determine context level
        context_level = force_level or intent["recommended_level"]

        # Start building the contract
        contract = {
            "version": self.contract_version,
            "timestamp": datetime.now().isoformat(),
            "agent": agent_name
        }

        # Add conversation ID if available
        if conversation and "id" in conversation:
            contract["conversation_id"] = conversation["id"]

        # Build project contract (static context)
        project_contract = self._build_project_contract(intent, context_level)
        if project_contract:
            contract["project_contract"] = project_contract

        # Build conversation contract (dynamic context from previous interactions)
        if conversation and intent["references_previous"]:
            conversation_contract = self._build_conversation_contract(
                conversation,
                intent,
                context_level
            )
            if conversation_contract:
                contract["conversation_contract"] = conversation_contract

        # Build infrastructure contract if needed
        if intent["needs_infrastructure"] and context_level >= 3:
            infrastructure_contract = self._build_infrastructure_contract(intent)
            if infrastructure_contract:
                contract["infrastructure_contract"] = infrastructure_contract

        # Build task contract (always required)
        contract["task_contract"] = self._build_task_contract(user_query, intent, conversation)

        # Add expected response format
        contract["expected_response"] = self._build_expected_response(agent_name, intent)

        # Validate the contract
        if not self._validate_contract(contract):
            logger.warning("Contract validation failed, but proceeding anyway")

        # Log contract statistics
        self._log_contract_stats(contract, context_level)

        return contract

    def _build_project_contract(self,
                               intent: Dict[str, Any],
                               context_level: int) -> Optional[Dict[str, Any]]:
        """Build project contract from static project context."""
        if not self.project_context:
            return None

        project_contract = {}

        # Level 1: Basic information
        if context_level >= 1:
            if "project_details" in self.project_context:
                details = self.project_context["project_details"]
                project_contract.update({
                    "project_id": details.get("project_id"),
                    "environment": details.get("environment", "development")
                })

            if "cluster" in self.project_context:
                cluster = self.project_context["cluster"]
                project_contract.update({
                    "cluster": cluster.get("name"),
                    "region": cluster.get("region")
                })

        # Level 2+: Add namespace if mentioned or needed
        if context_level >= 2:
            # Extract namespace from entities or use default
            namespaces = intent.get("entities", {}).get("namespaces", [])
            if namespaces:
                project_contract["namespace"] = namespaces[0]
            elif "default_namespace" in self.project_context:
                project_contract["namespace"] = self.project_context["default_namespace"]

        return project_contract if project_contract else None

    def _build_conversation_contract(self,
                                    conversation: Dict[str, Any],
                                    intent: Dict[str, Any],
                                    context_level: int) -> Optional[Dict[str, Any]]:
        """Build conversation contract from previous agent responses."""
        if not conversation.get("agent_responses"):
            return None

        conversation_contract = {
            "context_level": context_level
        }

        # Get the most recent agent response
        recent_responses = conversation["agent_responses"]
        last_response = recent_responses[-1] if recent_responses else {}

        # Level 2+: Include referenced resources
        if context_level >= 2 and "findings" in last_response:
            findings = last_response["findings"]

            # Extract resources that were found
            if "resources" in findings and findings["resources"]:
                conversation_contract["referenced_resources"] = findings["resources"][:10]

        # Level 3+: Include errors and previous actions
        if context_level >= 3:
            # Include errors from previous response
            if "findings" in last_response and "errors" in last_response["findings"]:
                conversation_contract["previous_errors"] = last_response["findings"]["errors"][:5]

            # Include actions that were performed
            if "actions" in last_response and "performed" in last_response["actions"]:
                conversation_contract["actions_performed"] = [
                    action.get("command", str(action))
                    for action in last_response["actions"]["performed"][-5:]
                ]

        # Level 4: Include full recent history
        if context_level >= 4:
            # Include last 2 full responses for complete context
            conversation_contract["response_history"] = recent_responses[-2:]

        return conversation_contract if len(conversation_contract) > 1 else None

    def _build_infrastructure_contract(self,
                                      intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build infrastructure contract if infrastructure details are needed."""
        if not self.project_context:
            return None

        infrastructure_contract = {}

        # Add database information if mentioned or needed
        if "database" in str(intent.get("query", "")).lower():
            if "databases" in self.project_context:
                infrastructure_contract["databases"] = self.project_context["databases"]

        # Add networking information if needed
        if "network" in str(intent.get("query", "")).lower() or "vpc" in str(intent.get("query", "")).lower():
            if "networking" in self.project_context:
                infrastructure_contract["networking"] = self.project_context["networking"]

        # Add storage information if mentioned
        if "storage" in str(intent.get("query", "")).lower() or "bucket" in str(intent.get("query", "")).lower():
            if "storage" in self.project_context:
                infrastructure_contract["storage"] = self.project_context["storage"]

        return infrastructure_contract if infrastructure_contract else None

    def _build_task_contract(self,
                            user_query: str,
                            intent: Dict[str, Any],
                            conversation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build the task contract (always required)."""
        task_contract = {
            "description": user_query,
            "action": intent.get("action", "custom"),
            "scope": self._determine_scope(intent)
        }

        # Add targets if there are referenced resources
        targets = []

        # From entities in the query
        if intent.get("entities", {}).get("resources"):
            targets.extend(intent["entities"]["resources"])

        # From referenced resources in conversation
        if (intent.get("references_previous") and
            conversation and
            conversation.get("agent_responses")):

            last_response = conversation["agent_responses"][-1]
            if "findings" in last_response and "resources" in last_response["findings"]:
                for resource in last_response["findings"]["resources"][:5]:
                    resource_id = resource.get("name", "")
                    if resource_id and resource_id not in targets:
                        targets.append(resource_id)

        if targets:
            task_contract["targets"] = targets

        # Add options based on intent
        options = {}

        if intent.get("needs_detail"):
            options["verbose"] = True

        if intent.get("needs_debugging"):
            options["include_stack_traces"] = True

        if "recent" in user_query.lower() or "últimos" in user_query.lower():
            options["time_range"] = "10m"
        elif "today" in user_query.lower() or "hoy" in user_query.lower():
            options["time_range"] = "24h"

        if intent.get("is_simple"):
            options["max_results"] = 10

        if options:
            task_contract["options"] = options

        return task_contract

    def _determine_scope(self, intent: Dict[str, Any]) -> str:
        """Determine the scope of the task."""
        action = intent.get("action", "custom")

        # Map actions to scopes
        scope_mapping = {
            "check_status": "diagnostic",
            "get_logs": "diagnostic",
            "diagnose_error": "diagnostic",
            "validate": "diagnostic",
            "fix_issue": "remediation",
            "deploy": "write",
            "rollback": "write",
            "apply": "write",
            "plan": "read"
        }

        return scope_mapping.get(action, "read")

    def _build_expected_response(self,
                                agent_name: str,
                                intent: Dict[str, Any]) -> Dict[str, Any]:
        """Build expected response format specification."""
        expected_response = {
            "format": "structured",  # Always prefer structured responses
            "required_fields": ["findings", "human_summary"],
            "optional_fields": ["actions", "next_steps", "artifacts"]
        }

        # Agent-specific required fields
        agent_requirements = {
            "gitops-operator": ["resources", "errors"],
            "terraform-architect": ["validation_results", "plan_summary"],
            "cloud-troubleshooter": ["diagnostics", "recommendations"],
            "devops-developer": ["build_status", "test_results"]
        }

        if agent_name in agent_requirements:
            expected_response["required_fields"].extend(agent_requirements[agent_name])

        # Add specific fields based on intent
        if intent.get("needs_debugging"):
            expected_response["required_fields"].append("analysis")

        if intent.get("action") == "get_logs":
            expected_response["required_fields"].append("logs")

        # Set max response size based on context level
        context_level = intent.get("recommended_level", 2)
        max_tokens_by_level = {1: 500, 2: 1000, 3: 2000, 4: 4000}
        expected_response["max_tokens"] = max_tokens_by_level.get(context_level, 1000)

        return expected_response

    def _validate_contract(self, contract: Dict[str, Any]) -> bool:
        """Validate that the contract meets minimum requirements."""
        # Check required fields
        if "version" not in contract:
            logger.error("Contract missing version field")
            return False

        if "task_contract" not in contract:
            logger.error("Contract missing task_contract field")
            return False

        # Validate task contract
        task = contract["task_contract"]
        if "description" not in task or "action" not in task:
            logger.error("Task contract missing required fields")
            return False

        return True

    def _log_contract_stats(self, contract: Dict[str, Any], context_level: int):
        """Log statistics about the built contract."""
        # Estimate token count
        contract_str = json.dumps(contract)
        estimated_tokens = len(contract_str) // 4

        # Count components
        components = []
        if "project_contract" in contract:
            components.append("project")
        if "conversation_contract" in contract:
            components.append("conversation")
        if "infrastructure_contract" in contract:
            components.append("infrastructure")
        components.append("task")  # Always present

        logger.info(f"Contract built: Level {context_level}, "
                   f"Components: {components}, "
                   f"Estimated tokens: {estimated_tokens}")

    def extract_from_project_context(self, field_path: str) -> Any:
        """
        Extract specific field from project context using dot notation.

        Args:
            field_path: Dot-separated path (e.g., "cluster.name")

        Returns:
            Value at the specified path or None
        """
        if not self.project_context:
            return None

        parts = field_path.split(".")
        value = self.project_context

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None

        return value

    def merge_contracts(self, *contracts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge multiple partial contracts into one.

        Args:
            *contracts: Variable number of contract dictionaries

        Returns:
            Merged contract
        """
        merged = {
            "version": self.contract_version,
            "timestamp": datetime.now().isoformat()
        }

        for contract in contracts:
            if contract:
                for key, value in contract.items():
                    if key not in merged:
                        merged[key] = value
                    elif isinstance(value, dict) and isinstance(merged[key], dict):
                        # Merge dictionaries
                        merged[key].update(value)
                    elif isinstance(value, list) and isinstance(merged[key], list):
                        # Extend lists (avoid duplicates)
                        for item in value:
                            if item not in merged[key]:
                                merged[key].append(item)

        return merged


# Example usage and testing
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Create builder
    builder = AgentContractBuilder()

    # Simulate conversation with previous response
    mock_conversation = {
        "id": "conv-12345",
        "agent_responses": [
            {
                "version": "1.0",
                "metadata": {"agent": "gitops-operator", "success": True},
                "findings": {
                    "resources": [
                        {"name": "tcm-api", "type": "pod", "state": "CrashLoopBackOff"},
                        {"name": "tcm-web", "type": "pod", "state": "Running"}
                    ],
                    "errors": [
                        {"resource": "tcm-api", "message": "Cannot connect to database"}
                    ]
                },
                "human_summary": "Found 2 pods, 1 is failing"
            }
        ]
    }

    # Test queries
    test_cases = [
        ("check the pods in namespace default", "gitops-operator", None),  # Simple
        ("show me the logs of those pods", "gitops-operator", mock_conversation),  # Reference
        ("debug the database connection error", "cloud-troubleshooter", mock_conversation)  # Complex
    ]

    print("Agent Contract Builder Test")
    print("=" * 60)

    for query, agent, conversation in test_cases:
        print(f"\nQuery: '{query}'")
        print(f"Agent: {agent}")

        contract = builder.build_contract(query, agent, conversation)

        print("Contract components:")
        for key in contract:
            if key not in ["version", "timestamp", "agent", "conversation_id"]:
                if isinstance(contract[key], dict):
                    print(f"  - {key}: {list(contract[key].keys())}")
                else:
                    print(f"  - {key}: {contract[key]}")

        # Pretty print the contract
        print("\nFull contract:")
        print(json.dumps(contract, indent=2)[:500] + "...")

    print("\n" + "=" * 60)
    print("Testing complete!")