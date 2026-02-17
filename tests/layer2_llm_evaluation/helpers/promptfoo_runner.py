"""
Promptfoo runner helper.

Wraps `npx promptfoo eval` execution and parses JSON results
for use in pytest assertions.
"""

import json
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class PromptfooResult:
    """Result from a promptfoo evaluation run."""
    success: bool
    total: int
    passed: int
    failed: int
    errors: int
    results: list
    raw_output: str
    error_message: Optional[str] = None


def run_promptfoo(
    config_path: Optional[Path] = None,
    filter_description: Optional[str] = None,
    timeout: int = 120,
) -> PromptfooResult:
    """
    Run promptfoo eval and parse results.

    Args:
        config_path: Path to promptfoo.yaml. Defaults to tests/promptfoo.yaml.
        filter_description: Optional filter to run specific tests.
        timeout: Timeout in seconds.

    Returns:
        PromptfooResult with parsed evaluation data.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "promptfoo.yaml"

    cmd = [
        "npx", "promptfoo", "eval",
        "--config", str(config_path),
        "--output", "json",
        "--no-progress-bar",
    ]

    if filter_description:
        cmd.extend(["--filter-description", filter_description])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(config_path.parent.parent),
        )

        if result.returncode != 0 and not result.stdout:
            return PromptfooResult(
                success=False,
                total=0,
                passed=0,
                failed=0,
                errors=1,
                results=[],
                raw_output=result.stderr,
                error_message=f"promptfoo failed with exit code {result.returncode}: {result.stderr[:500]}",
            )

        # Parse JSON output
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return PromptfooResult(
                success=False,
                total=0,
                passed=0,
                failed=0,
                errors=1,
                results=[],
                raw_output=result.stdout[:1000],
                error_message="Failed to parse promptfoo JSON output",
            )

        results = data.get("results", [])
        stats = data.get("stats", {})

        passed = stats.get("successes", 0)
        failed = stats.get("failures", 0)
        errors = stats.get("errors", 0) if "errors" in stats else 0
        total = passed + failed + errors

        return PromptfooResult(
            success=failed == 0 and errors == 0,
            total=total,
            passed=passed,
            failed=failed,
            errors=errors,
            results=results,
            raw_output=result.stdout[:2000],
        )

    except subprocess.TimeoutExpired:
        return PromptfooResult(
            success=False,
            total=0,
            passed=0,
            failed=0,
            errors=1,
            results=[],
            raw_output="",
            error_message=f"promptfoo eval timed out after {timeout}s",
        )
    except FileNotFoundError:
        return PromptfooResult(
            success=False,
            total=0,
            passed=0,
            failed=0,
            errors=1,
            results=[],
            raw_output="",
            error_message="npx not found. Install Node.js and run: npm install -g promptfoo",
        )
