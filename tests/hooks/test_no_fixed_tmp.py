"""No hook-runtime module builds a path under a fixed ``/tmp``.

A hook-owned temporary file belongs under ``gaia.paths.tmp_dir()``: it
relocates with ``GAIA_DATA_DIR``, is swept by Gaia's retention, and exists on
Windows, where ``/tmp`` resolves to the root of whatever drive the hook runs
from. Security modules still reason about ``/tmp`` as a path a command names,
so the check targets path construction, not every mention of the string.
"""

from __future__ import annotations

import ast
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"

_PATH_BUILDERS = frozenset({"Path", "PurePath", "PosixPath", "join", "makedirs", "open"})


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _fixed_tmp_paths(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _callee_name(node) not in _PATH_BUILDERS:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and (
                arg.value == "/tmp" or arg.value.startswith("/tmp/")
            ):
                lines.append(node.lineno)
    return lines


def test_detector_flags_a_fixed_tmp_path():
    assert _fixed_tmp_paths('from pathlib import Path\nD = Path("/tmp/gaia-x")\n') == [2]
    assert _fixed_tmp_paths('SCRATCH = ("/tmp/", "/run/")\n') == []


def test_no_hook_module_builds_a_fixed_tmp_path():
    offenders = [
        f"{path.relative_to(HOOKS_DIR.parent).as_posix()}:{line}"
        for path in sorted(HOOKS_DIR.rglob("*.py"))
        for line in _fixed_tmp_paths(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"use gaia.paths.tmp_dir() instead of /tmp: {offenders}"
