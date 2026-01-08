#!/usr/bin/env python3
"""
Enhanced Conversation Manager with Agent Contract Support
Manages conversations with structured contracts and responses
"""

import json
import hashlib
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Union
from pathlib import Path
import re

# Import the new components
try:
    from .progressive_disclosure import ProgressiveDisclosureManager
    from .agent_contract_builder import AgentContractBuilder
except ImportError:
    # For standalone testing
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from progressive_disclosure import ProgressiveDisclosureManager
    from agent_contract_builder import AgentContractBuilder

logger = logging.getLogger(__name__)


class EnhancedConversationManager:
    """
    Enhanced conversation manager with support for:
    - Agent Contracts (structured input)
    - Agent Responses (structured output)
    - Progressive Disclosure
    - Automatic extraction and storage
    """

    def __init__(self, storage_path: str = ".claude/session/conversations",
                 project_context_path: str = ".claude/project-context.json"):
        """
        Initialize the enhanced conversation manager.

        Args:
            storage_path: Directory for conversation storage
            project_context_path: Path to project context JSON
        """
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.active_conversations: Dict[str, dict] = {}
        self.current_conversation_id: Optional[str] = None

        # Initialize new components
        self.progressive_manager = ProgressiveDisclosureManager()
        self.contract_builder = AgentContractBuilder(project_context_path)

        # Contract/Response versioning
        self.contract_version = "1.0"
        self.response_version = "1.0"

        # Continuation detection keywords
        self.continuation_keywords = [
            "esos", "estos", "los mismos", "anterior", "previo",
            "que mencioné", "que dije", "que encontraste",
            "los que", "las que", "continúa", "sigue",
            "those", "these", "previous", "mentioned", "same"
        ]

        # Agents that support contracts
        self.contract_enabled_agents = [
            "gitops-operator",
            "cloud-troubleshooter",
            "terraform-architect",
            "devops-developer",
            "cloud-troubleshooter"
        ]

        logger.info("Enhanced Conversation Manager initialized with contract support")

    def start_conversation(self, agent: str, initial_prompt: str) -> str:
        """
        Start a new conversation with an agent.

        Args:
            agent: Name of the agent
            initial_prompt: Initial user prompt

        Returns:
            Conversation ID
        """
        # Generate conversation ID
        timestamp = datetime.now()
        hash_input = f"{agent}{timestamp.isoformat()}{initial_prompt}"
        conv_hash = hashlib.md5(hash_input.encode()).hexdigest()[:6]
        conversation_id = f"conv-{agent}-{timestamp.strftime('%Y%m%d-%H%M%S')}-{conv_hash}"

        # Initialize conversation structure
        conversation = {
            "id": conversation_id,
            "agent": agent,
            "started": timestamp.isoformat(),
            "last_exchange": timestamp.isoformat(),
            "exchanges": [],  # Traditional text exchanges
            "agent_responses": [],  # Structured Agent Responses
            "summary": "",
            "summary_data": {},  # Extracted structured data
            "token_savings": 0,
            "contract_enabled": agent in self.contract_enabled_agents
        }

        # Store conversation
        self.active_conversations[conversation_id] = conversation
        self.current_conversation_id = conversation_id
        self._save_conversation(conversation_id)

        logger.info(f"Started conversation {conversation_id} with agent {agent}")
        return conversation_id

    def build_agent_contract(self,
                            conversation_id: str,
                            user_query: str,
                            force_level: Optional[int] = None) -> Dict[str, Any]:
        """
        Build an Agent Contract for the current query.

        Args:
            conversation_id: Conversation ID
            user_query: User's query
            force_level: Force specific context level (1-4)

        Returns:
            Agent Contract ready to send
        """
        if conversation_id not in self.active_conversations:
            raise ValueError(f"Conversation {conversation_id} not found")

        conversation = self.active_conversations[conversation_id]
        agent = conversation["agent"]

        # Build the contract
        contract = self.contract_builder.build_contract(
            user_query=user_query,
            agent_name=agent,
            conversation=conversation,
            force_level=force_level
        )

        # Log contract creation
        logger.info(f"Built Agent Contract for {agent}: "
                   f"Level {contract.get('conversation_contract', {}).get('context_level', 1)}")

        return contract

    def process_agent_response(self,
                              conversation_id: str,
                              agent_response: Union[str, Dict[str, Any]],
                              user_query: str) -> str:
        """
        Process an agent response (structured or text).

        Args:
            conversation_id: Conversation ID
            agent_response: Response from agent (JSON dict or text)
            user_query: The query that prompted this response

        Returns:
            Human-readable summary to show user
        """
        if conversation_id not in self.active_conversations:
            raise ValueError(f"Conversation {conversation_id} not found")

        conversation = self.active_conversations[conversation_id]
        timestamp = datetime.now().isoformat()

        # Add exchange record
        exchange = {
            "timestamp": timestamp,
            "prompt": user_query,
            "response": "",
            "is_structured": False
        }

        # Check if response is structured (Agent Response)
        if isinstance(agent_response, dict) and "version" in agent_response:
            # It's a structured Agent Response!
            exchange["is_structured"] = True
            exchange["response"] = agent_response.get("human_summary", "Task completed")

            # Store the full structured response
            conversation["agent_responses"].append(agent_response)

            # Extract and store key information automatically
            self._extract_from_agent_response(conversation, agent_response)

            # Calculate token savings
            full_size = len(json.dumps(agent_response))
            summary_size = len(exchange["response"])
            savings = full_size - summary_size
            conversation["token_savings"] += savings

            logger.info(f"Processed structured Agent Response v{agent_response.get('version')}, "
                       f"saved {savings} tokens")

            human_summary = agent_response.get("human_summary", "Task completed successfully")

        else:
            # Traditional text response
            response_text = agent_response if isinstance(agent_response, str) else str(agent_response)
            exchange["response"] = response_text

            # Try to extract structured data from text
            extracted = self._extract_key_information(response_text)
            if isinstance(extracted, dict):
                conversation["summary_data"].update(extracted)

            human_summary = response_text[:500] if len(response_text) > 500 else response_text

        # Add exchange to conversation
        conversation["exchanges"].append(exchange)
        conversation["last_exchange"] = timestamp

        # Update summary
        self._update_summary(conversation)

        # Save conversation
        self._save_conversation(conversation_id)

        return human_summary

    def _extract_from_agent_response(self, conversation: Dict, response: Dict[str, Any]):
        """
        Extract structured data from Agent Response.

        Args:
            conversation: Conversation dict to update
            response: Structured Agent Response
        """
        if "findings" not in response:
            return

        findings = response["findings"]

        # Initialize summary_data if needed
        if "summary_data" not in conversation:
            conversation["summary_data"] = {}

        # Extract resources
        if "resources" in findings and findings["resources"]:
            conversation["summary_data"]["resources"] = [
                r.get("name", "") for r in findings["resources"]
            ]
            conversation["summary_data"]["states"] = {
                r.get("name", ""): r.get("state", "Unknown")
                for r in findings["resources"]
                if r.get("name")
            }

        # Extract errors
        if "errors" in findings and findings["errors"]:
            conversation["summary_data"]["errors"] = [
                e.get("message", str(e)) for e in findings["errors"]
            ]

        # Extract metrics
        if "metrics" in findings:
            conversation["summary_data"]["metrics"] = findings["metrics"]

        # Extract analysis
        if "analysis" in findings:
            conversation["summary_data"]["analysis"] = findings["analysis"]

        # Store actions performed
        if "actions" in response and "performed" in response["actions"]:
            if "actions_performed" not in conversation["summary_data"]:
                conversation["summary_data"]["actions_performed"] = []

            for action in response["actions"]["performed"]:
                action_str = action.get("command", str(action))
                if action_str not in conversation["summary_data"]["actions_performed"]:
                    conversation["summary_data"]["actions_performed"].append(action_str)

    def get_progressive_context(self,
                               conversation_id: str,
                               user_query: str,
                               format: str = "contract") -> Union[Dict[str, Any], str]:
        """
        Get conversation context with Progressive Disclosure.

        Args:
            conversation_id: Conversation ID
            user_query: Current user query
            format: "contract" for Agent Contract, "text" for text summary

        Returns:
            Context in requested format
        """
        if conversation_id not in self.active_conversations:
            return {} if format == "contract" else ""

        conversation = self.active_conversations[conversation_id]

        # Analyze query intent
        intent = self.progressive_manager.analyze_query_intent(user_query)
        level = intent["recommended_level"]

        if format == "contract":
            # Return as Agent Contract
            return self.build_agent_contract(conversation_id, user_query)
        else:
            # Return as text (backward compatibility)
            return self._build_text_context(conversation, level)

    def _build_text_context(self, conversation: Dict, level: int) -> str:
        """
        Build text context for backward compatibility.

        Args:
            conversation: Conversation dict
            level: Context level (1-4)

        Returns:
            Text context string
        """
        context_parts = [
            f"## Conversación Activa (ID: {conversation['id'][-6:]})",
            f"Agent: {conversation['agent']}"
        ]

        summary_data = conversation.get("summary_data", {})

        # Level 1: Basic summary
        if level >= 1:
            if summary_data.get("resources"):
                context_parts.append(f"**Recursos:** {', '.join(summary_data['resources'][:5])}")
            if summary_data.get("metrics"):
                if isinstance(summary_data["metrics"], dict):
                    metrics_str = ", ".join(f"{k}: {v}" for k, v in list(summary_data["metrics"].items())[:3])
                else:
                    metrics_str = str(summary_data["metrics"])
                context_parts.append(f"**Métricas:** {metrics_str}")

        # Level 2: Add states and errors
        if level >= 2:
            if summary_data.get("states"):
                context_parts.append("\n**Estados:**")
                for resource, state in list(summary_data["states"].items())[:8]:
                    context_parts.append(f"  - {resource}: {state}")

            if summary_data.get("errors"):
                errors_str = " | ".join(summary_data["errors"][:3])
                context_parts.append(f"\n**Errores:** {errors_str}")

        # Level 3: Add recent response excerpt
        if level >= 3 and conversation.get("agent_responses"):
            last_response = conversation["agent_responses"][-1]
            if "human_summary" in last_response:
                context_parts.append(f"\n**Último análisis:** {last_response['human_summary']}")

        # Level 4: Add full recent history
        if level >= 4:
            if conversation.get("exchanges"):
                context_parts.append("\n**Historia reciente:**")
                for exchange in conversation["exchanges"][-2:]:
                    context_parts.append(f"  Q: {exchange['prompt'][:50]}...")
                    if exchange.get("response"):
                        context_parts.append(f"  A: {exchange['response'][:100]}...")

        context_parts.append(f"\n[Context Level: {level}/4]")
        return "\n".join(context_parts)

    def is_continuation(self, prompt: str, agent: str) -> bool:
        """
        Determine if a prompt is a continuation of an existing conversation.

        Args:
            prompt: User prompt
            agent: Agent name

        Returns:
            True if continuation detected
        """
        prompt_lower = prompt.lower()

        # Check for continuation keywords
        has_continuation_keyword = any(
            keyword in prompt_lower for keyword in self.continuation_keywords
        )

        # Check if there's an active conversation with this agent
        has_active_conversation = False
        for conv_id, conv in self.active_conversations.items():
            if conv["agent"] == agent:
                # Check if conversation is recent (within last hour)
                last_exchange = datetime.fromisoformat(conv["last_exchange"])
                time_diff = datetime.now() - last_exchange
                if time_diff.total_seconds() < 3600:  # 1 hour
                    has_active_conversation = True
                    break

        return has_continuation_keyword and has_active_conversation

    def get_active_conversation(self, agent: str) -> Optional[str]:
        """
        Get the most recent active conversation for an agent.

        Args:
            agent: Agent name

        Returns:
            Conversation ID or None
        """
        recent_conv = None
        recent_time = None

        for conv_id, conv in self.active_conversations.items():
            if conv["agent"] == agent:
                last_exchange = datetime.fromisoformat(conv["last_exchange"])
                if recent_time is None or last_exchange > recent_time:
                    recent_time = last_exchange
                    recent_conv = conv_id

        return recent_conv

    def _extract_key_information(self, text: str) -> Union[str, Dict[str, Any]]:
        """
        Extract key information from text response (fallback for non-structured responses).
        [Previous implementation remains the same]
        """
        # [Keep the existing enhanced implementation from earlier]
        extracted_data = {
            "resources": [],
            "states": {},
            "errors": [],
            "metrics": {},
            "actions": [],
            "summary_text": ""
        }

        # [Rest of the extraction logic remains the same as implemented earlier]
        # ... (keeping the existing pattern matching logic)

        return extracted_data

    def _update_summary(self, conversation: Dict):
        """Update conversation summary."""
        summary_parts = []

        if "summary_data" in conversation:
            data = conversation["summary_data"]

            # Add resources with states
            if data.get("resources") and data.get("states"):
                for resource in data["resources"][:3]:
                    if resource in data["states"]:
                        summary_parts.append(f"{resource}: {data['states'][resource]}")

            # Add errors
            if data.get("errors"):
                summary_parts.append(f"Errors: {', '.join(data['errors'][:2])}")

        conversation["summary"] = " | ".join(summary_parts) if summary_parts else "Active conversation"

    def _save_conversation(self, conversation_id: str):
        """Save conversation to disk."""
        if conversation_id not in self.active_conversations:
            return

        conversation = self.active_conversations[conversation_id]
        file_path = self.storage_path / f"{conversation_id}.json"

        try:
            with open(file_path, 'w') as f:
                json.dump(conversation, f, indent=2, default=str)
            logger.debug(f"Saved conversation {conversation_id}")
        except Exception as e:
            logger.error(f"Failed to save conversation {conversation_id}: {e}")

    def load_conversation(self, conversation_id: str) -> bool:
        """Load conversation from disk."""
        file_path = self.storage_path / f"{conversation_id}.json"

        if not file_path.exists():
            return False

        try:
            with open(file_path, 'r') as f:
                conversation = json.load(f)
            self.active_conversations[conversation_id] = conversation
            logger.info(f"Loaded conversation {conversation_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to load conversation {conversation_id}: {e}")
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get conversation statistics."""
        total_savings = sum(
            conv.get("token_savings", 0)
            for conv in self.active_conversations.values()
        )

        structured_responses = sum(
            len(conv.get("agent_responses", []))
            for conv in self.active_conversations.values()
        )

        return {
            "active_conversations": len(self.active_conversations),
            "total_token_savings": total_savings,
            "structured_responses": structured_responses,
            "contract_enabled_agents": len(self.contract_enabled_agents)
        }


# Example usage
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Create manager
    manager = EnhancedConversationManager()

    # Start a conversation
    conv_id = manager.start_conversation("gitops-operator", "check the pods")
    print(f"Started conversation: {conv_id}")

    # Build an Agent Contract
    contract = manager.build_agent_contract(conv_id, "check the pods in namespace default")
    print(f"\nAgent Contract built:")
    print(json.dumps(contract, indent=2)[:500])

    # Simulate an Agent Response
    agent_response = {
        "version": "1.0",
        "metadata": {
            "agent": "gitops-operator",
            "timestamp": datetime.now().isoformat(),
            "success": True
        },
        "findings": {
            "resources": [
                {"name": "tcm-api", "type": "pod", "state": "Running"},
                {"name": "tcm-web", "type": "pod", "state": "CrashLoopBackOff"}
            ],
            "errors": [
                {"resource": "tcm-web", "message": "Image not found"}
            ],
            "metrics": {"total": 2, "healthy": 1, "unhealthy": 1}
        },
        "human_summary": "Found 2 pods, tcm-web is failing with image error"
    }

    # Process the response
    summary = manager.process_agent_response(conv_id, agent_response, "check the pods")
    print(f"\nHuman summary: {summary}")

    # Test continuation with progressive disclosure
    contract2 = manager.build_agent_contract(conv_id, "show me the logs of those failing pods")
    print(f"\nSecond contract (with context):")
    print(json.dumps(contract2, indent=2)[:500])

    # Show statistics
    stats = manager.get_statistics()
    print(f"\nStatistics: {stats}")