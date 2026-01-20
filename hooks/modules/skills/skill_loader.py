#!/usr/bin/env python3
"""
Skill Loader - Loads skills on-demand based on triggers

Skills are on-demand knowledge modules that reduce token duplication.
This loader determines which skills to load based on:
- Workflow phase (investigation, approval, execution)
- Keywords in the task prompt
- Agent type (for auto_load skills)
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class SkillLoader:
    """Loads skills based on triggers and workflow phase"""

    # Project agents that receive universal-protocol
    PROJECT_AGENTS = [
        "terraform-architect",
        "gitops-operator",
        "cloud-troubleshooter",
        "devops-developer"
    ]

    def __init__(self, skills_dir: Path, triggers_config: Path):
        """
        Initialize skill loader

        Args:
            skills_dir: Path to .claude/skills/ directory
            triggers_config: Path to skill-triggers.json
        """
        self.skills_dir = skills_dir
        self.triggers_config = triggers_config
        self.triggers = self._load_triggers()

    def _load_triggers(self) -> Dict:
        """Load skill triggers configuration"""
        try:
            with open(self.triggers_config, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load skill triggers: {e}")
            return {"workflow": {}, "domain": {}}

    def detect_phase(self, prompt: str) -> str:
        """
        Detect workflow phase from prompt

        Args:
            prompt: Task prompt from orchestrator

        Returns:
            Phase: 'start', 'approval', or 'execution'
        """
        prompt_lower = prompt.lower()

        # Check execution phase first (most specific)
        execution_triggers = self.triggers.get("workflow", {}).get("execution", {}).get("triggers", [])
        if any(trigger in prompt_lower for trigger in execution_triggers):
            return "execution"

        # Check approval phase
        approval_triggers = self.triggers.get("workflow", {}).get("approval", {}).get("triggers", [])
        if any(trigger in prompt_lower for trigger in approval_triggers):
            return "approval"

        # Default to start (investigation)
        return "start"

    def load_skills(self, prompt: str, subagent_type: str) -> Dict[str, str]:
        """
        Load skills based on prompt and subagent type

        Args:
            prompt: Task prompt
            subagent_type: Type of agent (terraform-architect, gitops-operator, etc.)

        Returns:
            Dict mapping skill name to skill content
        """
        skills_to_load = {}
        prompt_lower = prompt.lower()

        # 1. Load workflow skill based on phase
        phase = self.detect_phase(prompt)
        workflow_skill = self._load_workflow_skill(phase)
        if workflow_skill:
            skills_to_load.update(workflow_skill)

        # 2. Load domain skills based on triggers and auto_load
        domain_skills = self._load_domain_skills(prompt_lower, subagent_type)
        skills_to_load.update(domain_skills)

        # 3. Load standards skills based on triggers and auto_load
        standards_skills = self._load_standards_skills(prompt_lower, subagent_type)
        skills_to_load.update(standards_skills)

        logger.info(f"Loaded {len(skills_to_load)} skills for phase '{phase}': {list(skills_to_load.keys())}")
        return skills_to_load

    def _load_workflow_skill(self, phase: str) -> Dict[str, str]:
        """Load workflow skill for given phase"""
        workflow_skills = self.triggers.get("workflow", {})

        for skill_name, config in workflow_skills.items():
            # Match exact phase, OR auto_load only for start phase
            skill_phase = config.get("phase")
            is_auto_load = config.get("auto_load", False)

            if skill_phase == phase or (is_auto_load and phase == "start"):
                skill_path = self.skills_dir / "workflow" / skill_name / "SKILL.md"
                if skill_path.exists():
                    content = skill_path.read_text()
                    return {skill_name: content}

        return {}

    def _load_domain_skills(self, prompt_lower: str, subagent_type: str) -> Dict[str, str]:
        """Load domain skills based on keyword triggers and agent type"""
        domain_skills = {}
        domain_config = self.triggers.get("domain", {})

        for skill_name, config in domain_config.items():
            triggers = config.get("triggers", [])
            is_auto_load = config.get("auto_load", False)
            should_load = False

            # Check if should auto-load based on agent type
            if is_auto_load and subagent_type in self.PROJECT_AGENTS:
                should_load = True
                logger.debug(f"Auto-loading domain skill '{skill_name}' for agent {subagent_type}")
            # Check if any trigger matches in prompt
            elif any(trigger in prompt_lower for trigger in triggers):
                should_load = True
                logger.debug(f"Loading domain skill '{skill_name}' (matched triggers)")

            # Load the skill if conditions met
            if should_load:
                skill_path = self.skills_dir / "domain" / skill_name / "SKILL.md"
                if skill_path.exists():
                    content = skill_path.read_text()
                    domain_skills[skill_name] = content

        return domain_skills

    def _load_standards_skills(self, prompt_lower: str, subagent_type: str) -> Dict[str, str]:
        """Load standards skills based on keyword triggers and auto_load"""
        standards_skills = {}
        standards_config = self.triggers.get("standards", {})

        for skill_name, config in standards_config.items():
            triggers = config.get("triggers", [])
            is_auto_load = config.get("auto_load", False)
            should_load = False

            # Check if should auto-load (standards auto-load for ALL agents, not just PROJECT_AGENTS)
            if is_auto_load:
                should_load = True
                logger.debug(f"Auto-loading standards skill '{skill_name}' for all agents")
            # Check if any trigger matches in prompt
            elif any(trigger in prompt_lower for trigger in triggers):
                should_load = True
                logger.debug(f"Loading standards skill '{skill_name}' (matched triggers)")

            # Load the skill if conditions met
            if should_load:
                skill_path = self.skills_dir / "standards" / skill_name / "SKILL.md"
                if skill_path.exists():
                    content = skill_path.read_text()
                    standards_skills[skill_name] = content

        return standards_skills

    def format_skills_for_injection(self, skills: Dict[str, str]) -> str:
        """
        Format loaded skills for injection into prompt

        Args:
            skills: Dict mapping skill name to content

        Returns:
            Formatted string ready to inject
        """
        if not skills:
            return ""

        sections = ["# Active Skills\n"]

        for skill_name, content in skills.items():
            sections.append(f"## {skill_name}\n")
            sections.append(content)
            sections.append("\n---\n")

        return "\n".join(sections)


def get_skills_directory() -> Optional[Path]:
    """
    Get skills directory using dynamic detection.
    
    Tries three strategies in order:
    1. Git root (preferred for git repositories)
    2. Walk up to find .claude/ directory
    3. Fallback to relative path from this file
    
    Returns:
        Path to skills directory, or None if not found
    """
    # Strategy A: Git root
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )
        git_root = Path(result.stdout.strip())
        skills_dir = git_root / ".claude" / "skills"
        if skills_dir.exists():
            logger.info(f"Skills directory found via git root: {skills_dir}")
            return skills_dir
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug(f"Git root detection failed: {e}")

    # Strategy B: Walk up directories to find .claude/
    current = Path(__file__).resolve()
    for parent in current.parents:
        claude_dir = parent / ".claude"
        if claude_dir.exists():
            skills_dir = claude_dir / "skills"
            if skills_dir.exists():
                logger.info(f"Skills directory found via directory walk: {skills_dir}")
                return skills_dir

    # Strategy C: Fallback to relative path (old method)
    base_dir = Path(__file__).parent.parent.parent.parent
    skills_dir = base_dir / "skills"
    if skills_dir.exists():
        logger.warning(f"Skills directory found via fallback relative path: {skills_dir}")
        return skills_dir

    logger.error("Skills directory not found using any strategy")
    return None


def load_skills_for_task(prompt: str, subagent_type: str) -> str:
    """
    Convenience function to load skills for a task

    Args:
        prompt: Task prompt
        subagent_type: Agent type

    Returns:
        Formatted skills content ready to inject
    """
    # Get skills directory dynamically
    skills_dir = get_skills_directory()
    if not skills_dir:
        logger.error("Cannot load skills - skills directory not found")
        return ""

    # Determine config path
    triggers_config = skills_dir.parent / "config" / "skill-triggers.json"
    
    if not triggers_config.exists():
        logger.error(f"Skill triggers config not found: {triggers_config}")
        return ""

    # Create loader and load skills
    loader = SkillLoader(skills_dir, triggers_config)
    skills = loader.load_skills(prompt, subagent_type)

    # Format for injection
    return loader.format_skills_for_injection(skills)


# CLI for testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test skill loader")
    parser.add_argument("--test", action="store_true", help="Run test mode")
    parser.add_argument("--prompt", required=True, help="Task prompt")
    parser.add_argument("--agent", default="terraform-architect", help="Agent type")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    # Load skills
    skills_content = load_skills_for_task(args.prompt, args.agent)

    # Output results
    print("=" * 80)
    print(f"Prompt: {args.prompt}")
    print(f"Agent: {args.agent}")
    print("=" * 80)

    if skills_content:
        print("\nLoaded Skills:\n")
        print(skills_content)
    else:
        print("\nNo skills loaded.")

    print("=" * 80)
