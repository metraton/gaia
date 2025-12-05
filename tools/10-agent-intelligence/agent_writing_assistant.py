#!/usr/bin/env python3
"""
AgentWritingAssistant - Helps Gaia write better agents by analyzing patterns
and providing intelligent suggestions based on successful agent implementations.
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import hashlib


@dataclass
class AgentPattern:
    """Represents a pattern found in agent definitions"""
    pattern_type: str  # 'structure', 'capability', 'tool_usage', 'security'
    frequency: int
    examples: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class AgentValidationResult:
    """Results from validating an agent markdown file"""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    score: float = 0.0


class AgentWritingAssistant:
    """
    Assists in writing high-quality agent definitions by analyzing patterns
    from existing agents and providing intelligent suggestions.
    """

    # Essential sections every agent should have
    REQUIRED_SECTIONS = [
        "## Overview",
        "## Core Responsibilities",
        "## Available Tools",
        "## Security Tiers",
        "## Workflow",
        "## Error Handling"
    ]

    # Best practice patterns from successful agents
    BEST_PRACTICES = {
        "clear_purpose": "Agent must have a single, well-defined purpose",
        "tool_documentation": "Each tool usage must be documented with examples",
        "tier_definitions": "Security tiers must be explicitly defined",
        "error_scenarios": "Common error scenarios and recovery paths documented",
        "context_requirements": "Required context clearly specified",
        "limitations": "Known limitations explicitly stated",
        "examples": "At least 3 usage examples provided"
    }

    def __init__(self, agents_dir: str = ".claude/agents"):
        """Initialize the assistant with the agents directory"""
        self.agents_dir = Path(agents_dir)
        self.patterns_cache = {}
        self.agent_capabilities = self._load_capabilities()

    def _load_capabilities(self) -> Dict:
        """Load agent capabilities from JSON file"""
        capabilities_file = Path("tools/agent_capabilities.json")
        if capabilities_file.exists():
            with open(capabilities_file, 'r') as f:
                return json.load(f)
        return {}

    def analyze_agent_patterns(self) -> Dict[str, AgentPattern]:
        """
        Analyze all existing agents to extract successful patterns

        Returns:
            Dictionary of patterns categorized by type
        """
        patterns = {
            "structure": [],
            "capabilities": [],
            "tool_usage": [],
            "security": [],
            "documentation": []
        }

        # Analyze each existing agent
        for agent_file in self.agents_dir.glob("*.md"):
            if agent_file.stem == "README":
                continue

            with open(agent_file, 'r') as f:
                content = f.read()

            # Extract structural patterns
            patterns["structure"].extend(self._extract_structure_patterns(content))

            # Extract capability patterns
            patterns["capabilities"].extend(self._extract_capability_patterns(content))

            # Extract tool usage patterns
            patterns["tool_usage"].extend(self._extract_tool_patterns(content))

            # Extract security patterns
            patterns["security"].extend(self._extract_security_patterns(content))

            # Extract documentation patterns
            patterns["documentation"].extend(self._extract_doc_patterns(content))

        # Consolidate and rank patterns by frequency
        consolidated = {}
        for category, pattern_list in patterns.items():
            consolidated[category] = self._consolidate_patterns(pattern_list)

        return consolidated

    def _extract_structure_patterns(self, content: str) -> List[str]:
        """Extract structural patterns from agent markdown"""
        patterns = []

        # Find all headers
        headers = re.findall(r'^#+\s+(.+)$', content, re.MULTILINE)
        patterns.extend(headers)

        # Find command patterns
        commands = re.findall(r'```bash\n(.+?)\n```', content, re.DOTALL)
        if commands:
            patterns.append("bash_examples")

        # Find code blocks
        code_blocks = re.findall(r'```\w+\n(.+?)\n```', content, re.DOTALL)
        if code_blocks:
            patterns.append("code_examples")

        return patterns

    def _extract_capability_patterns(self, content: str) -> List[str]:
        """Extract capability patterns from agent content"""
        patterns = []

        # Find capability keywords
        capabilities = re.findall(r'(?:can|able to|capability|supports?)\s+(\w+(?:\s+\w+){0,3})',
                                 content, re.IGNORECASE)
        patterns.extend(capabilities)

        return patterns

    def _extract_tool_patterns(self, content: str) -> List[str]:
        """Extract tool usage patterns"""
        patterns = []

        # Find tool mentions
        tools = re.findall(r'(?:tool|command|utility):\s*`([^`]+)`', content, re.IGNORECASE)
        patterns.extend(tools)

        # Find tool examples
        tool_examples = re.findall(r'(?:Example|Usage):.+?```\w*\n(.+?)\n```',
                                  content, re.DOTALL | re.IGNORECASE)
        if tool_examples:
            patterns.append("tool_examples_provided")

        return patterns

    def _extract_security_patterns(self, content: str) -> List[str]:
        """Extract security-related patterns"""
        patterns = []

        # Find tier mentions
        tiers = re.findall(r'T[0-3](?:\s*[-:]\s*([^.]+))?', content)
        patterns.extend([f"tier_{t[0] if isinstance(t, tuple) else t}" for t in tiers])

        # Find security keywords
        security_keywords = re.findall(
            r'(?:security|permission|approval|validation|guard)\s+(\w+(?:\s+\w+){0,2})',
            content, re.IGNORECASE
        )
        patterns.extend(security_keywords)

        return patterns

    def _extract_doc_patterns(self, content: str) -> List[str]:
        """Extract documentation patterns"""
        patterns = []

        # Check for examples section
        if re.search(r'^#+\s*Examples?', content, re.MULTILINE | re.IGNORECASE):
            patterns.append("has_examples_section")

        # Check for error handling section
        if re.search(r'^#+\s*Error\s+Handling', content, re.MULTILINE | re.IGNORECASE):
            patterns.append("has_error_handling")

        # Check for limitations section
        if re.search(r'^#+\s*Limitations?', content, re.MULTILINE | re.IGNORECASE):
            patterns.append("has_limitations")

        # Count example blocks
        example_count = len(re.findall(r'(?:Example|Usage):', content, re.IGNORECASE))
        if example_count >= 3:
            patterns.append("sufficient_examples")

        return patterns

    def _consolidate_patterns(self, pattern_list: List[str]) -> List[AgentPattern]:
        """Consolidate and rank patterns by frequency"""
        pattern_freq = {}

        for pattern in pattern_list:
            if pattern not in pattern_freq:
                pattern_freq[pattern] = AgentPattern(
                    pattern_type="",
                    frequency=0,
                    examples=[],
                    description=""
                )
            pattern_freq[pattern].frequency += 1

        # Sort by frequency
        sorted_patterns = sorted(
            pattern_freq.items(),
            key=lambda x: x[1].frequency,
            reverse=True
        )

        return [p[1] for p in sorted_patterns[:10]]  # Top 10 patterns

    def suggest_agent_template(self, purpose: str, agent_type: str = "specialist") -> str:
        """
        Generate an optimized agent template based on purpose and type

        Args:
            purpose: The main purpose of the agent
            agent_type: Type of agent ('specialist', 'meta', 'troubleshooter')

        Returns:
            Markdown template for the new agent
        """
        # Analyze patterns if not cached
        if not self.patterns_cache:
            self.patterns_cache = self.analyze_agent_patterns()

        # Generate agent name from purpose
        agent_name = self._generate_agent_name(purpose)

        template = f"""# {agent_name}

## Overview

**Purpose**: {purpose}

**Type**: {agent_type.capitalize()} Agent

**Version**: 1.0.0

## Core Responsibilities

1. **Primary**: [Define primary responsibility]
2. **Secondary**: [Define secondary responsibilities]
3. **Tertiary**: [Define supporting responsibilities]

## Available Tools

### Required Tools
- `tool1` - [Description and usage]
- `tool2` - [Description and usage]

### Optional Tools
- `tool3` - [Description when needed]

## Security Tiers

| Tier | Operations | Approval Required | Examples |
|------|------------|-------------------|----------|
| T0 | Read-only operations | No | `kubectl get`, `terraform show` |
| T1 | Local modifications | No | File edits, local validations |
| T2 | Reversible remote operations | No | `git push` to feature branch |
| T3 | Production changes | **Yes** | `terraform apply`, `kubectl apply` |

## Context Requirements

### Required Context
```json
{{
  "project_id": "string",
  "environment": "string",
  "credentials_path": "string"
}}
```

### Optional Context
```json
{{
  "additional_config": "object"
}}
```

## Workflow

### Phase 1: Initialization
1. Load required context
2. Validate credentials
3. Check prerequisites

### Phase 2: Execution
1. [Main execution steps]
2. [Validation steps]
3. [Output generation]

### Phase 3: Cleanup
1. Clean temporary resources
2. Log results
3. Update metrics

## Error Handling

### Common Errors

| Error | Cause | Resolution |
|-------|-------|------------|
| `CredentialsError` | Missing or invalid credentials | Check authentication |
| `ValidationError` | Input validation failed | Verify input format |
| `TimeoutError` | Operation exceeded timeout | Retry with extended timeout |

### Recovery Strategies
1. **Automatic Retry**: For transient errors (network, timeouts)
2. **Manual Intervention**: For configuration errors
3. **Escalation**: For critical failures

## Examples

### Example 1: Basic Usage
```bash
# Basic {agent_name} invocation
Task(subagent_type="{agent_name}",
     prompt="[basic task description]")
```

### Example 2: Advanced Usage
```bash
# Advanced configuration
Task(subagent_type="{agent_name}",
     prompt="[complex task with context]",
     context={{"key": "value"}})
```

### Example 3: Error Recovery
```bash
# With error handling
try:
    Task(subagent_type="{agent_name}",
         prompt="[risky operation]")
except Exception as e:
    # Recovery logic
    pass
```

## Limitations

1. **Scope**: [Define what the agent cannot do]
2. **Dependencies**: [External dependencies required]
3. **Performance**: [Known performance constraints]
4. **Compatibility**: [Version or platform limitations]

## Related Agents

- `related-agent-1`: [How they interact]
- `related-agent-2`: [When to use instead]

## Metrics

- **Success Rate Target**: 95%
- **Average Execution Time**: < 30 seconds
- **Token Usage**: Optimized for < 1000 tokens per invocation

## Maintenance Notes

- **Last Updated**: [Date]
- **Maintainer**: [Team/Person]
- **Review Cycle**: Quarterly

---

**Navigation**: [Back to Agents Catalog](../config/AGENTS.md) | [Meta Agent: Gaia](./gaia.md)
"""

        return template

    def _generate_agent_name(self, purpose: str) -> str:
        """Generate an appropriate agent name from purpose"""
        # Extract key words
        words = purpose.lower().split()

        # Remove common words
        stopwords = ['the', 'a', 'an', 'for', 'to', 'of', 'and', 'or', 'but']
        keywords = [w for w in words if w not in stopwords]

        # Common agent name patterns
        if 'troubleshoot' in purpose.lower() or 'diagnose' in purpose.lower():
            suffix = 'troubleshooter'
        elif 'develop' in purpose.lower() or 'build' in purpose.lower():
            suffix = 'developer'
        elif 'deploy' in purpose.lower() or 'operate' in purpose.lower():
            suffix = 'operator'
        elif 'optimize' in purpose.lower() or 'architect' in purpose.lower():
            suffix = 'architect'
        else:
            suffix = 'agent'

        # Generate name
        if keywords:
            prefix = keywords[0]
            return f"{prefix}-{suffix}"
        else:
            return f"custom-{suffix}"

    def validate_agent_markdown(self, content: str) -> AgentValidationResult:
        """
        Validate an agent markdown file for completeness and best practices

        Args:
            content: The markdown content to validate

        Returns:
            ValidationResult with errors, warnings, and suggestions
        """
        result = AgentValidationResult(is_valid=True)

        # Check required sections
        for section in self.REQUIRED_SECTIONS:
            if section not in content:
                result.errors.append(f"Missing required section: {section}")
                result.is_valid = False

        # Check for examples
        example_count = len(re.findall(r'(?:Example|Usage):', content, re.IGNORECASE))
        if example_count < 3:
            result.warnings.append(f"Only {example_count} examples found (minimum 3 recommended)")

        # Check for security tier definitions
        if not re.search(r'T[0-3]', content):
            result.errors.append("No security tier definitions found")
            result.is_valid = False

        # Check for tool documentation
        tools = re.findall(r'`([^`]+)`', content)
        if len(tools) < 2:
            result.warnings.append("Few tool references found - document all tools used")

        # Check for error handling
        if not re.search(r'(?:error|exception|failure)', content, re.IGNORECASE):
            result.warnings.append("No error handling documentation found")

        # Check for context requirements
        if not re.search(r'(?:context|requirement|prerequisite)', content, re.IGNORECASE):
            result.warnings.append("Context requirements not documented")

        # Provide suggestions based on analysis
        if not result.is_valid:
            result.suggestions.append("Use suggest_agent_template() to generate a complete template")

        if len(result.warnings) > 2:
            result.suggestions.append("Review best practices in existing agents")

        # Calculate quality score
        result.score = self._calculate_quality_score(content, result)

        return result

    def _calculate_quality_score(self, content: str, validation_result: AgentValidationResult) -> float:
        """Calculate a quality score for the agent definition"""
        score = 100.0

        # Deduct for errors
        score -= len(validation_result.errors) * 10

        # Deduct for warnings
        score -= len(validation_result.warnings) * 5

        # Bonus for best practices
        if re.search(r'```\w+', content):  # Has code examples
            score += 5
        if re.search(r'\|.*\|.*\|', content):  # Has tables
            score += 5
        if len(content) > 2000:  # Comprehensive documentation
            score += 5

        return max(0, min(100, score))

    def generate_agent_tests(self, agent_name: str, capabilities: List[str]) -> str:
        """
        Generate test cases for a new agent

        Args:
            agent_name: Name of the agent
            capabilities: List of agent capabilities

        Returns:
            Python test file content
        """
        test_template = f'''#!/usr/bin/env python3
"""
Test suite for {agent_name} agent
Auto-generated by AgentWritingAssistant
"""

import pytest
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tools.agent_router import AgentRouter
from tools.context_provider import ContextProvider


class Test{agent_name.replace("-", "").title()}:
    """Test cases for {agent_name} agent"""

    @pytest.fixture
    def agent_router(self):
        """Initialize agent router"""
        return AgentRouter()

    @pytest.fixture
    def context_provider(self):
        """Initialize context provider"""
        return ContextProvider()

    def test_agent_exists(self):
        """Test that agent markdown file exists"""
        agent_file = Path(".claude/agents/{agent_name}.md")
        assert agent_file.exists(), f"Agent file {{agent_file}} not found"

    def test_agent_routing(self, agent_router):
        """Test that agent is properly routed"""
        # Test routing for agent's primary domain
        test_queries = [
            # Add specific test queries based on agent purpose
        ]

        for query in test_queries:
            result = agent_router.route(query)
            assert result["agent"] == "{agent_name}", f"Failed to route: {{query}}"

    def test_agent_capabilities(self):
        """Test that agent capabilities are defined"""
        capabilities_file = Path("tools/agent_capabilities.json")
        assert capabilities_file.exists()

        with open(capabilities_file) as f:
            capabilities = json.load(f)

        assert "{agent_name}" in capabilities
        agent_caps = capabilities["{agent_name}"]

        # Verify required fields
        required_fields = ["domains", "tiers", "tools", "capabilities", "limitations"]
        for field in required_fields:
            assert field in agent_caps, f"Missing field: {{field}}"

    def test_context_requirements(self, context_provider):
        """Test that context can be provided for agent"""
        context = context_provider.get_context("{agent_name}", {{}})
        assert context is not None
        assert "contract" in context

    def test_security_tiers(self):
        """Test security tier definitions"""
        agent_file = Path(".claude/agents/{agent_name}.md")
        with open(agent_file) as f:
            content = f.read()

        # Check for tier definitions
        for tier in ["T0", "T1", "T2", "T3"]:
            assert tier in content, f"Security tier {{tier}} not documented"

    def test_error_handling(self):
        """Test that error handling is documented"""
        agent_file = Path(".claude/agents/{agent_name}.md")
        with open(agent_file) as f:
            content = f.read()

        assert "Error Handling" in content or "error" in content.lower()

    def test_examples_provided(self):
        """Test that usage examples are provided"""
        agent_file = Path(".claude/agents/{agent_name}.md")
        with open(agent_file) as f:
            content = f.read()

        import re
        examples = re.findall(r'Example \\d+:', content, re.IGNORECASE)
        assert len(examples) >= 3, f"Only {{len(examples)}} examples found (minimum 3)"


# Capability-specific tests
'''

        # Add capability-specific tests
        for capability in capabilities[:5]:  # Top 5 capabilities
            test_template += f'''
    def test_capability_{capability.replace(" ", "_").replace("-", "_")}(self):
        """Test {capability} capability"""
        # TODO: Implement specific test for {capability}
        pass
'''

        test_template += '''

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
'''

        return test_template

    def suggest_improvements(self, agent_content: str) -> List[str]:
        """
        Suggest improvements for an existing agent definition

        Args:
            agent_content: Current agent markdown content

        Returns:
            List of improvement suggestions
        """
        suggestions = []

        # Validate first
        validation = self.validate_agent_markdown(agent_content)

        # Add suggestions from validation
        suggestions.extend(validation.suggestions)

        # Analyze patterns
        patterns = self.analyze_agent_patterns()

        # Compare with best practices
        for practice, description in self.BEST_PRACTICES.items():
            if not self._check_practice(agent_content, practice):
                suggestions.append(f"Best Practice: {description}")

        # Check for modern patterns
        if "conversation_manager" not in agent_content.lower():
            suggestions.append("Consider integrating with ConversationManager for context continuity")

        if "episodic_memory" not in agent_content.lower():
            suggestions.append("Consider using EpisodicMemory for learning from past interactions")

        if not re.search(r'```mermaid', agent_content):
            suggestions.append("Add workflow diagram using Mermaid for visual clarity")

        # Check for optimization opportunities
        if "token" not in agent_content.lower():
            suggestions.append("Document token usage optimization strategies")

        if "cache" not in agent_content.lower() and "caching" not in agent_content.lower():
            suggestions.append("Consider implementing caching for frequently accessed data")

        return suggestions

    def _check_practice(self, content: str, practice: str) -> bool:
        """Check if a best practice is followed in the content"""
        practice_checks = {
            "clear_purpose": lambda c: "Purpose" in c and len(re.findall(r'Purpose:.*', c)[0]) > 20,
            "tool_documentation": lambda c: len(re.findall(r'`\w+`\s*-', c)) >= 3,
            "tier_definitions": lambda c: all(f"T{i}" in c for i in range(4)),
            "error_scenarios": lambda c: "Error" in c and len(re.findall(r'Error.*:', c)) >= 2,
            "context_requirements": lambda c: "Context" in c and "Required" in c,
            "limitations": lambda c: "Limitation" in c or "Cannot" in c or "constraint" in c.lower(),
            "examples": lambda c: len(re.findall(r'Example \d+:', c, re.IGNORECASE)) >= 3
        }

        check_func = practice_checks.get(practice)
        if check_func:
            try:
                return check_func(content)
            except:
                return False
        return False


def main():
    """Main function for testing the assistant"""
    import sys

    assistant = AgentWritingAssistant()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "analyze":
            patterns = assistant.analyze_agent_patterns()
            print(json.dumps(patterns, indent=2, default=str))

        elif command == "template":
            purpose = input("Enter agent purpose: ")
            template = assistant.suggest_agent_template(purpose)
            print(template)

        elif command == "validate":
            if len(sys.argv) > 2:
                agent_file = sys.argv[2]
                with open(agent_file) as f:
                    content = f.read()
                result = assistant.validate_agent_markdown(content)
                print(f"Valid: {result.is_valid}")
                print(f"Score: {result.score}/100")
                if result.errors:
                    print("Errors:", result.errors)
                if result.warnings:
                    print("Warnings:", result.warnings)
                if result.suggestions:
                    print("Suggestions:", result.suggestions)
            else:
                print("Usage: python agent_writing_assistant.py validate <agent_file>")

        elif command == "test":
            agent_name = input("Enter agent name: ")
            capabilities = input("Enter capabilities (comma-separated): ").split(",")
            test_code = assistant.generate_agent_tests(agent_name, capabilities)
            print(test_code)

        else:
            print(f"Unknown command: {command}")

    else:
        print("""
AgentWritingAssistant - Help write better agents

Usage:
  python agent_writing_assistant.py analyze     - Analyze existing agent patterns
  python agent_writing_assistant.py template    - Generate new agent template
  python agent_writing_assistant.py validate <file> - Validate agent markdown
  python agent_writing_assistant.py test        - Generate test cases
        """)


if __name__ == "__main__":
    main()