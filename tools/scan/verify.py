"""
Health Check / Verification Functions for `gaia scan`

Provides post-install verification checks to confirm that the
gaia-ops installation is healthy. Used after fresh install (Mode 1)
and after rescan+sync (Mode 2).

Functions:
- run_verification: run all checks, return summary
- check_symlinks: verify symlinks exist and are valid
- check_claude_md: legacy check (CLAUDE.md no longer generated)
- check_settings_json: verify valid JSON
- check_project_context: verify exists and has sections
- check_python: verify python3 available
- check_hooks: verify pre_tool_use.py exists
"""

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single health check.

    Attributes:
        name: Check name for display.
        ok: Whether the check passed.
        detail: Human-readable detail string.
        fix: Optional fix suggestion if check failed.
    """
    name: str
    ok: bool
    detail: str = ""
    fix: Optional[str] = None


def check_symlinks(project_root: Path) -> CheckResult:
    """Verify that all expected symlinks exist in .claude/.

    Checks for: agents, tools, hooks, commands, config,
    skills, CHANGELOG.md (7 total).

    Args:
        project_root: Project root directory.

    Returns:
        CheckResult with count of valid symlinks.
    """
    names = [
        "agents", "tools", "hooks", "commands",
        "config", "skills",
        "CHANGELOG.md",
    ]
    valid = 0
    for name in names:
        link_path = project_root / ".claude" / name
        if link_path.exists() or link_path.is_symlink():
            valid += 1

    return CheckResult(
        name="Symlinks",
        ok=valid == len(names),
        detail=f"{valid}/{len(names)} valid",
        fix="Run `gaia scan` to recreate symlinks" if valid < len(names) else None,
    )


def check_claude_md(project_root: Path) -> CheckResult:
    """Check for CLAUDE.md presence. No longer required -- identity injected by hook.

    Kept for backward compatibility with callers that expect this check.
    """
    path = project_root / "CLAUDE.md"
    if path.is_file():
        return CheckResult(
            name="CLAUDE.md",
            ok=True,
            detail="Present (legacy -- identity now injected by hook)",
        )
    return CheckResult(
        name="CLAUDE.md",
        ok=True,
        detail="Not present (identity injected by UserPromptSubmit hook)",
    )


def check_settings_json(project_root: Path) -> CheckResult:
    """Verify that .claude/settings.json exists and is valid JSON.

    Args:
        project_root: Project root directory.

    Returns:
        CheckResult.
    """
    path = project_root / ".claude" / "settings.json"
    if not path.is_file():
        return CheckResult(
            name="settings.json",
            ok=False,
            detail="Missing",
            fix="Run `gaia scan`",
        )

    try:
        json.loads(path.read_text())
        return CheckResult(name="settings.json", ok=True, detail="Valid JSON")
    except (json.JSONDecodeError, OSError):
        return CheckResult(
            name="settings.json",
            ok=False,
            detail="Invalid JSON",
            fix="Delete and run `gaia scan`",
        )


def check_project_context(project_root: Path) -> CheckResult:
    """Verify that project context contracts exist in the DB (T1.3: DB-backed read).

    Reads from project_context_contracts table in gaia.db rather than from
    the legacy project-context.json file.

    Args:
        project_root: Project root directory.

    Returns:
        CheckResult with contract count.
    """
    try:
        import sys as _sys
        _repo_root = Path(__file__).resolve().parents[2]
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from gaia.project import current as _project_current
        from gaia.store.writer import _connect as _store_connect
        ws = _project_current(cwd=project_root)
        con = _store_connect()
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM project_context_contracts WHERE workspace = ?",
                (ws,),
            ).fetchone()
            count = row[0] if row else 0
        finally:
            con.close()
        if count == 0:
            return CheckResult(
                name="project-context",
                ok=False,
                detail="No contracts in DB",
                fix="Run `gaia scan`",
            )
        return CheckResult(
            name="project-context",
            ok=count >= 3,
            detail=f"{count} contracts",
            fix="Run `gaia scan` to enrich" if count < 3 else None,
        )
    except Exception as exc:
        return CheckResult(
            name="project-context",
            ok=False,
            detail=f"DB read error: {exc}",
            fix="Run `gaia scan`",
        )


def check_python() -> CheckResult:
    """Verify that python3 (or python on Windows) is available.

    Tries ``python3`` first, then ``python`` for Windows compatibility.

    Returns:
        CheckResult with Python version.
    """
    for cmd in ("python3", "python"):
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                if version.startswith("Python 3."):
                    return CheckResult(name="Python", ok=True, detail=version)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    return CheckResult(
        name="Python",
        ok=False,
        detail="Not found",
        fix="Install Python 3.9+",
    )


def check_hooks(project_root: Path) -> CheckResult:
    """Verify that pre_tool_use.py hook exists.

    Args:
        project_root: Project root directory.

    Returns:
        CheckResult.
    """
    hook_path = project_root / ".claude" / "hooks" / "pre_tool_use.py"
    if hook_path.exists():
        return CheckResult(name="Hooks", ok=True, detail="pre_tool_use.py found")

    return CheckResult(
        name="Hooks",
        ok=False,
        detail="pre_tool_use.py missing",
        fix="Run `gaia scan` to recreate symlinks",
    )


def run_verification(project_root: Path) -> List[CheckResult]:
    """Run all post-install verification checks.

    Args:
        project_root: Project root directory.

    Returns:
        List of CheckResult objects (all checks always run).
    """
    checks = [
        check_symlinks(project_root),
        check_claude_md(project_root),
        check_settings_json(project_root),
        check_project_context(project_root),
        check_python(),
        check_hooks(project_root),
    ]
    return checks


def print_verification(results: List[CheckResult]) -> bool:
    """Print verification results in a human-readable format.

    Args:
        results: List of CheckResult objects.

    Returns:
        True if all checks passed.
    """
    supports_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()

    def _green(t: str) -> str:
        return f"\033[32m{t}\033[0m" if supports_color else t

    def _yellow(t: str) -> str:
        return f"\033[33m{t}\033[0m" if supports_color else t

    def _red(t: str) -> str:
        return f"\033[31m{t}\033[0m" if supports_color else t

    def _gray(t: str) -> str:
        return f"\033[90m{t}\033[0m" if supports_color else t

    print("\n  Verifying installation...\n", file=sys.stderr)

    all_passed = True
    for r in results:
        padded = r.name.ljust(18)
        if r.ok:
            print(_green(f"    ✓ {padded} {r.detail}"), file=sys.stderr)
        else:
            print(_yellow(f"    ⚠ {padded} {r.detail}"), file=sys.stderr)
            if r.fix:
                print(_gray(f"      Fix: {r.fix}"), file=sys.stderr)
            all_passed = False

    print("", file=sys.stderr)
    return all_passed
