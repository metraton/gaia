#!/usr/bin/env python3
"""
Agent Invoker Helper - Context Pre-Loading Pattern

This helper demonstrates the CORRECT pattern for invoking specialized agents
with pre-loaded, filtered context from project-context.json.

Philosophy:
- Claude orchestrator (main conversation) pre-loads context BEFORE invoking agents
- Agents receive everything in the prompt - NO file reads needed
- 50-76% token savings per agent invocation

Usage:
    from .claude.tools.agent_invoker_helper import AgentInvokerHelper

    helper = AgentInvokerHelper()

    # Get pre-loaded context for terraform-architect
    context = helper.get_agent_context('terraform-architect')

    # Build prompt with pre-loaded context
    prompt = helper.build_prompt(
        agent='terraform-architect',
        task_description='Clean up legacy TCM resources',
        instructions=[
            'Identify all Terraform modules...',
            'Create a plan showing...',
            'Generate instructions...'
        ]
    )

    # Invoke agent with pre-loaded context
    Task(
        subagent_type='terraform-architect',
        description='Clean up legacy TCM resources',
        prompt=prompt
    )
"""

from pathlib import Path
from typing import List, Optional, Dict
import json


class AgentInvokerHelper:
    """Helper for invoking agents with pre-loaded, filtered context."""

    def __init__(self):
        """Initialize the helper with context reader."""
        from context_section_reader import ContextSectionReader
        self.reader = ContextSectionReader()

    def get_agent_context(self, agent_name: str) -> str:
        """
        Get pre-filtered context for a specific agent.

        Args:
            agent_name: Name of the agent (e.g., 'terraform-architect')

        Returns:
            Markdown string with pre-filtered context sections

        Example:
            context = helper.get_agent_context('terraform-architect')
            # Returns ~145 lines, ~580 tokens (vs 1,312 tokens full)
        """
        return self.reader.get_for_agent(agent_name)

    def get_token_savings(self, agent_name: str) -> Dict:
        """
        Get token savings estimate for context pre-loading.

        Args:
            agent_name: Name of the agent

        Returns:
            Dictionary with token counts and savings percentage
        """
        return self.reader.get_agent_stats(agent_name)

    def build_prompt(
        self,
        agent: str,
        task_description: str,
        instructions: List[str],
        additional_context: Optional[str] = None
    ) -> str:
        """
        Build a prompt with pre-loaded context for an agent.

        This is the CORRECT pattern - context comes in the prompt, not from files.

        Args:
            agent: Agent name (e.g., 'terraform-architect')
            task_description: High-level description of the task
            instructions: List of specific instructions
            additional_context: Optional additional context

        Returns:
            Complete prompt with pre-loaded context

        Example:
            prompt = helper.build_prompt(
                agent='terraform-architect',
                task_description='Clean up TCM resources',
                instructions=[
                    'List modules in /terraform/tf_live/rnd/',
                    'Create plan for tcm-vpc removal',
                    'Do NOT execute destroy'
                ]
            )
        """
        # Get pre-filtered context for this agent
        context = self.get_agent_context(agent)

        # Build instruction section
        instruction_text = '\n'.join([f'{i+1}. {instr}' for i, instr in enumerate(instructions)])

        # Construct prompt
        prompt = f"""# Project Context (Pre-Loaded for {agent})

{context}

---

# Task Description

{task_description}

# Task Instructions

{instruction_text}
"""

        if additional_context:
            prompt += f"""
# Additional Context

{additional_context}
"""

        return prompt

    def invoke_agent_with_context(
        self,
        agent_name: str,
        task_description: str,
        instructions: List[str],
        additional_context: Optional[str] = None
    ) -> str:
        """
        Generate a complete Task tool invocation with pre-loaded context.

        This shows how to invoke agents the CORRECT way.

        Args:
            agent_name: Agent to invoke
            task_description: Task description
            instructions: Task instructions
            additional_context: Optional additional context

        Returns:
            Python code snippet ready for Task() invocation
        """
        prompt = self.build_prompt(
            agent=agent_name,
            task_description=task_description,
            instructions=instructions,
            additional_context=additional_context
        )

        code_snippet = f'''# CORRECT INVOCATION PATTERN
Task(
    subagent_type='{agent_name}',
    description='{task_description}',
    prompt=f"""
{prompt}
"""
)
'''
        return code_snippet

    def print_agent_stats(self) -> None:
        """Print token savings statistics for all agents."""
        print("\n📊 Token Savings by Agent (Context Pre-Loading)\n")
        print("Agent                      | Lines | Tokens | Savings")
        print("-" * 60)

        for agent in self.reader.AGENT_SECTIONS.keys():
            stats = self.reader.get_agent_stats(agent)
            print(
                f"{agent:26} | {stats['lines_loaded']:5} | "
                f"{stats['tokens_estimated']:6} | {stats['savings']['percentage']:.1f}%"
            )

        full_stats = self.reader.get_stats()
        print("\n" + "=" * 60)
        print(f"Full project-context.json: {full_stats['total_lines']} lines, "
              f"{full_stats['total_tokens_estimated']} tokens")
        print("=" * 60)


# Example usage showing the CORRECT pattern
if __name__ == '__main__':
    helper = AgentInvokerHelper()

    # Show stats
    helper.print_agent_stats()

    print("\n\n" + "="*70)
    print("EXAMPLE: Invoking terraform-architect with pre-loaded context")
    print("="*70 + "\n")

    # Build example prompt
    example_prompt = helper.build_prompt(
        agent='terraform-architect',
        task_description='Clean up legacy TCM resources',
        instructions=[
            'Identify all Terraform modules in /terraform/tf_live/rnd/',
            'Create a plan showing what will be destroyed',
            'Do NOT execute destroy - only plan and document'
        ]
    )

    print(example_prompt[:500] + "...\n")

    print("\n" + "="*70)
    print("CORRECT INVOCATION CODE:")
    print("="*70 + "\n")

    print(helper.invoke_agent_with_context(
        agent_name='terraform-architect',
        task_description='Clean up legacy TCM resources',
        instructions=[
            'Identify all Terraform modules in /terraform/tf_live/rnd/',
            'Create a plan showing what will be destroyed',
            'Do NOT execute destroy - only plan and document'
        ]
    ))
