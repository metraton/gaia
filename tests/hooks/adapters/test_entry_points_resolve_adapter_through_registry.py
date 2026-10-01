"""Every host entry point obtains its adapter from the registry and never names a concrete host adapter class."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hooks"))

from adapters import registry  # noqa: E402,F401
from adapters.base import HookAdapter  # noqa: E402

ENTRY_POINTS = sorted((ROOT / "hooks").glob("*.py")) + [ROOT / "opencode" / "bridge.py"]


def _concrete_adapter_names() -> frozenset:
    pending, names = list(HookAdapter.__subclasses__()), set()
    while pending:
        cls = pending.pop()
        names.add(cls.__name__)
        pending.extend(cls.__subclasses__())
    return frozenset(names)


def _references(path: Path, names: frozenset) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found += [alias.name for alias in node.names if alias.name in names]
        elif isinstance(node, ast.Name) and node.id in names:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in names:
            found.append(node.attr)
    return [f"{path.relative_to(ROOT)}: {name}" for name in found]


def test_no_entry_point_names_a_concrete_host_adapter_class():
    names = _concrete_adapter_names()
    assert {"ClaudeCodeAdapter", "OpenCodeAdapter"} <= names

    offenders = [ref for path in ENTRY_POINTS for ref in _references(path, names)]

    assert offenders == []
