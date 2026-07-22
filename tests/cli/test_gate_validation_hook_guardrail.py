"""R1-B-1 guardrail (AC-4): validate_gate must live ONLY in verify_brief.

The hard guardrail of R1-B-1: the pure gate validator (gaia.state.gate_validation
.validate_gate) is wired into the plan verification flow via verify_brief ONLY.
It must NOT be imported or called by ANY per-turn hook (PreToolUse / PostToolUse
/ SubagentStop -- anything under hooks/), because that would give it a per-turn
footprint the design explicitly forbids. This test asserts that ABSENCE, via
both a textual scan and a real AST parse of every Python source under hooks/.

Filename intentionally does not contain the AC-1/2/3 -k selector strings.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HOOKS_DIR = _REPO_ROOT / "hooks"

_FORBIDDEN_NAMES = {"validate_gate", "gate_validation"}


def _hook_py_files() -> list[Path]:
    return sorted(p for p in _HOOKS_DIR.rglob("*.py") if p.is_file())


def test_no_hook_source_text_references_validate_gate():
    """Textual scan: no hook .py file mentions validate_gate / gate_validation."""
    assert _HOOKS_DIR.is_dir(), f"hooks dir not found at {_HOOKS_DIR}"
    offenders: list[str] = []
    for path in _hook_py_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in _FORBIDDEN_NAMES:
            if needle in text:
                offenders.append(f"{path.relative_to(_REPO_ROOT)} references {needle!r}")
    assert not offenders, (
        "R1-B-1 guardrail violated -- validate_gate must NOT be wired into any "
        "per-turn hook:\n  " + "\n  ".join(offenders)
    )


def test_no_hook_imports_or_calls_validate_gate_via_ast():
    """AST scan: no hook imports gate_validation / validate_gate, nor calls
    a bare `validate_gate(...)`. Complements the textual scan (catches the
    real import/call graph, ignores comments/strings that merely mention it)."""
    assert _HOOKS_DIR.is_dir(), f"hooks dir not found at {_HOOKS_DIR}"
    offenders: list[str] = []
    for path in _hook_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = path.relative_to(_REPO_ROOT)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "gate_validation" in alias.name:
                        offenders.append(f"{rel}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "gate_validation" in mod:
                    offenders.append(f"{rel}: from {mod} import ...")
                for alias in node.names:
                    if alias.name in _FORBIDDEN_NAMES:
                        offenders.append(f"{rel}: from {mod} import {alias.name}")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "validate_gate":
                    offenders.append(f"{rel}: calls validate_gate(...)")
                elif isinstance(func, ast.Attribute) and func.attr == "validate_gate":
                    offenders.append(f"{rel}: calls .validate_gate(...)")
    assert not offenders, (
        "R1-B-1 guardrail violated -- validate_gate must NOT be imported or "
        "called by any per-turn hook:\n  " + "\n  ".join(offenders)
    )
