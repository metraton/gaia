#!/usr/bin/env python3
"""
Context Section Reader for Claude Agent System

Reads specific sections from project-context.json for selective loading by agents.
Called by Claude orchestrator BEFORE invoking agents to reduce token usage.

Architecture:
- Claude orchestrator executes this script (NOT agents)
- Agents receive pre-filtered context in their prompts
- Reduces token usage by ~70% per agent invocation

Usage:
    from .claude.tools.context_section_reader import ContextSectionReader

    reader = ContextSectionReader()
    context = reader.get_for_agent('gitops-operator')

    # Pass context to agent in Task tool prompt
"""

from pathlib import Path
from typing import List, Dict, Optional, Any
import json


def find_claude_dir() -> Path:
    """Find the .claude directory by searching upward from current location"""
    current = Path.cwd()

    # If we're already in a .claude directory, return it
    if current.name == ".claude":
        return current

    # Look for .claude in current directory
    claude_dir = current / ".claude"
    if claude_dir.exists():
        return claude_dir

    # Search upward through parent directories
    for parent in current.parents:
        claude_dir = parent / ".claude"
        if claude_dir.exists():
            return claude_dir

    # Fallback - raise error if not found
    raise FileNotFoundError(
        "No .claude directory found. Please run from a project directory "
        "or specify context_file explicitly."
    )


class ContextSectionReader:
    """
    Read and filter sections from project-context.json for agent-specific loading.

    Token Optimization:
    - Without filtering: ~328 lines (1,312 tokens)
    - With filtering: ~80-100 lines (320-400 tokens)
    - Savings: ~70% per agent invocation
    """

    # Define which sections each agent needs (JSON keys in snake_case)
    AGENT_SECTIONS = {
        'gitops-operator': [
            'infrastructure_topology',
            'gitops_configuration',
            'gitops_repositories',
            'application_deployments',
            'operational_guidelines',
        ],
        'cloud-troubleshooter': [
            'infrastructure_topology',
            'operational_guidelines',
            'monitoring_observability',
        ],
        'terraform-architect': [
            'infrastructure_topology',
            'terraform_infrastructure',
            'terraform_configurations',
            'vpc_mapping',
            'operational_guidelines',
        ],
        'devops-developer': [
            'application_architecture',
            'application_deployments',
            'development_standards',
            'operational_guidelines',
        ],
        'cloud-troubleshooter': [
            'infrastructure_topology',
            'vpc_mapping',
            'dynamic_queries',
            'operational_guidelines',
        ]
    }

    def __init__(self, context_file: Optional[str] = None):
        """
        Initialize reader with project context file.

        Args:
            context_file: Path to project-context.json (default: searches for .claude/project-context/project-context.json)
        """
        if context_file is None:
            # Find the .claude directory by searching upward
            claude_dir = find_claude_dir()
            # Try project-context/ subdirectory first (new location)
            context_file = claude_dir / "project-context" / "project-context.json"
            if not Path(context_file).exists():
                # Fallback to root .claude/ (old location)
                context_file = claude_dir / "project-context.json"

        self.path = Path(context_file)

        if not self.path.exists():
            raise FileNotFoundError(f"Context file not found: {self.path}")

        with open(self.path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

        self._parse_sections()

    def _parse_sections(self) -> None:
        """Extract sections from JSON data."""
        self.sections: Dict[str, Any] = {}

        # Extract sections from JSON
        if 'sections' in self.data:
            self.sections = self.data['sections']
        else:
            raise ValueError("Invalid JSON structure: 'sections' key not found")

    def get_sections(self, section_names: List[str]) -> str:
        """
        Get specific sections as formatted JSON string.

        Args:
            section_names: List of section names to retrieve

        Returns:
            Formatted JSON string with requested sections
        """
        result = {}
        missing = []

        for name in section_names:
            if name in self.sections:
                result[name] = self.sections[name]
            else:
                missing.append(name)

        if missing:
            print(f"Warning: Sections not found: {missing}")

        if not result:
            return json.dumps({
                "error": "No sections found",
                "message": "Requested sections were not available."
            }, indent=2)

        # Format as JSON for agent consumption
        return json.dumps(result, indent=2, ensure_ascii=False)

    def get_for_agent(self, agent_name: str) -> str:
        """
        Get sections needed by specific agent.

        Args:
            agent_name: Name of the agent (e.g., 'gitops-operator')

        Returns:
            Markdown string with agent-specific context

        Raises:
            ValueError: If agent_name is not recognized
        """
        if agent_name not in self.AGENT_SECTIONS:
            available = ', '.join(self.AGENT_SECTIONS.keys())
            raise ValueError(
                f"Unknown agent: {agent_name}. "
                f"Available agents: {available}"
            )

        sections = self.AGENT_SECTIONS[agent_name]
        return self.get_sections(sections)

    def list_sections(self) -> List[str]:
        """Get list of all available sections."""
        return list(self.sections.keys())

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the context file.

        Returns:
            Dictionary with size and token estimates
        """
        # Calculate total JSON size
        total_json = json.dumps(self.data, ensure_ascii=False)
        total_chars = len(total_json)
        total_tokens = total_chars // 4  # Rough estimate: 4 chars per token

        return {
            'total_chars': total_chars,
            'total_tokens_estimated': total_tokens,
            'total_sections': len(self.sections),
            'sections': {
                name: {
                    'chars': len(json.dumps(content, ensure_ascii=False)),
                    'tokens_estimated': len(json.dumps(content, ensure_ascii=False)) // 4
                }
                for name, content in self.sections.items()
            }
        }

    def get_agent_stats(self, agent_name: str) -> Dict[str, Any]:
        """
        Get statistics for a specific agent's context.

        Args:
            agent_name: Name of the agent

        Returns:
            Dictionary with character and token counts for agent
        """
        context = self.get_for_agent(agent_name)
        chars = len(context)
        tokens = chars // 4

        full_stats = self.get_stats()
        savings = {
            'chars': full_stats['total_chars'] - chars,
            'tokens': full_stats['total_tokens_estimated'] - tokens,
            'percentage': round((1 - chars / full_stats['total_chars']) * 100, 1)
        }

        return {
            'agent': agent_name,
            'chars_loaded': chars,
            'tokens_estimated': tokens,
            'savings': savings
        }


def main():
    """CLI interface for testing and debugging."""
    import sys
    import json

    reader = ContextSectionReader()

    if len(sys.argv) < 2:
        print("Context Section Reader")
        print("\nUsage:")
        print("  python context_section_reader.py <command> [args]")
        print("\nCommands:")
        print("  list                    - List all available sections")
        print("  stats                   - Show statistics for context file")
        print("  agent <name>            - Get context for specific agent")
        print("  agent-stats <name>      - Show stats for agent's context")
        print("  sections <name1> <name2> - Get specific sections")
        print("\nAvailable agents:")
        for agent in reader.AGENT_SECTIONS.keys():
            print(f"  - {agent}")
        sys.exit(0)

    command = sys.argv[1]

    if command == 'list':
        print("Available sections:")
        for section in reader.list_sections():
            print(f"  - {section}")

    elif command == 'stats':
        stats = reader.get_stats()
        print(json.dumps(stats, indent=2))

    elif command == 'agent':
        if len(sys.argv) < 3:
            print("Error: Agent name required")
            sys.exit(1)

        agent_name = sys.argv[2]
        context = reader.get_for_agent(agent_name)
        print(context)

    elif command == 'agent-stats':
        if len(sys.argv) < 3:
            print("Error: Agent name required")
            sys.exit(1)

        agent_name = sys.argv[2]
        stats = reader.get_agent_stats(agent_name)
        print(json.dumps(stats, indent=2))

    elif command == 'sections':
        if len(sys.argv) < 3:
            print("Error: Section names required")
            sys.exit(1)

        section_names = sys.argv[2:]
        context = reader.get_sections(section_names)
        print(context)

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == '__main__':
    main()
