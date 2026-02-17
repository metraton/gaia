"""
Claude headless helper for E2E tests.

Provides utilities to:
- Set up temporary test projects with .claude/ structure
- Run claude CLI in headless mode (-p --output-format json)
- Parse and validate JSON responses
"""

import json
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClaudeResponse:
    """Parsed response from claude headless execution."""
    success: bool
    output: str
    parsed_json: Optional[dict]
    exit_code: int
    error: Optional[str] = None


def setup_test_project(tmp_dir: Path, package_root: Path) -> Path:
    """
    Create a temporary project directory with full .claude/ structure.

    Copies agents, skills, hooks, config, and settings from the gaia-ops
    package into a temp directory to simulate an installed project.

    Args:
        tmp_dir: Temporary directory for the test project.
        package_root: Root of the gaia-ops package.

    Returns:
        Path to the created .claude/ directory.
    """
    claude_dir = tmp_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Copy agents
    src_agents = package_root / "agents"
    if src_agents.is_dir():
        shutil.copytree(src_agents, claude_dir / "agents", dirs_exist_ok=True)

    # Copy skills
    src_skills = package_root / "skills"
    if src_skills.is_dir():
        shutil.copytree(src_skills, claude_dir / "skills", dirs_exist_ok=True)

    # Copy hooks
    src_hooks = package_root / "hooks"
    if src_hooks.is_dir():
        shutil.copytree(src_hooks, claude_dir / "hooks", dirs_exist_ok=True)

    # Copy config
    src_config = package_root / "config"
    if src_config.is_dir():
        shutil.copytree(src_config, claude_dir / "config", dirs_exist_ok=True)

    # Create project-context directory with minimal context
    pc_dir = claude_dir / "project-context"
    pc_dir.mkdir(exist_ok=True)
    pc_file = pc_dir / "project-context.json"
    pc_file.write_text(json.dumps({
        "metadata": {
            "project_name": "test-project",
            "cloud_provider": "gcp",
            "primary_region": "us-east4",
        },
        "sections": {
            "project_details": {
                "cluster_name": "test-cluster"
            }
        }
    }, indent=2))

    # Create settings.json with hooks
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "type": "command",
                    "command": f"python3 {claude_dir / 'hooks' / 'pre_tool_use.py'}"
                }
            ]
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

    # Create CLAUDE.md
    src_claude_md = package_root / "CLAUDE.md"
    if src_claude_md.exists():
        shutil.copy2(src_claude_md, tmp_dir / "CLAUDE.md")

    return claude_dir


def run_claude_headless(
    project_dir: Path,
    prompt: str,
    allowed_tools: Optional[list] = None,
    timeout: int = 60,
) -> ClaudeResponse:
    """
    Execute claude CLI in headless mode.

    Args:
        project_dir: Project directory (with .claude/ inside).
        prompt: Prompt to send to claude.
        allowed_tools: List of allowed tools (e.g., ["Bash", "Read"]).
        timeout: Timeout in seconds.

    Returns:
        ClaudeResponse with parsed output.
    """
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
    ]

    if allowed_tools:
        for tool in allowed_tools:
            cmd.extend(["--allowedTools", tool])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(project_dir),
        )

        parsed = None
        try:
            parsed = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            pass

        return ClaudeResponse(
            success=result.returncode == 0,
            output=result.stdout,
            parsed_json=parsed,
            exit_code=result.returncode,
            error=result.stderr if result.returncode != 0 else None,
        )

    except subprocess.TimeoutExpired:
        return ClaudeResponse(
            success=False,
            output="",
            parsed_json=None,
            exit_code=-1,
            error=f"claude timed out after {timeout}s",
        )
    except FileNotFoundError:
        return ClaudeResponse(
            success=False,
            output="",
            parsed_json=None,
            exit_code=-1,
            error="claude CLI not found. Install from https://docs.anthropic.com/claude-code",
        )
