#!/usr/bin/env python3
"""The shell surface of the protected set, and the proof both surfaces share it.

Plan 63 order 13 (AC-10, AC-1). A protected set enforced on the file-write
surface only is not a protected set: the same effect through an in-place stream
editor, a tee, a copy, a move or a git working-tree writer is the withheld
effect obtained without the consent that was demanded, and it makes no
difference that the shell route was not previously blocked.

The two surfaces are kept in agreement STRUCTURALLY, not by prose -- a prose
claim that they mirror each other is exactly what failed here, so the last two
tests read the syntax tree instead of believing a docstring.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import protected_paths  # noqa: E402
from modules.security import protected_path_guard  # noqa: E402
from modules.security.protected_path_guard import check  # noqa: E402

SOURCE_TARGET = str(HOOKS_DIR / "modules" / "security" / "mutative_verbs.py")
HARNESS_TARGET = ".claude/hooks/pre_tool_use.py"


class TestSourceCheckoutWritersDenied:
    """Every shell writer, against the tree where an edit is actually durable."""

    @pytest.mark.parametrize(
        "command",
        [
            f"sed -i 's/T3/T0/' {SOURCE_TARGET}",
            f"tee {SOURCE_TARGET}",
            f"cp /tmp/payload.py {SOURCE_TARGET}",
            f"mv /tmp/payload.py {SOURCE_TARGET}",
            f"git checkout -- {SOURCE_TARGET}",
        ],
    )
    def test_writer_into_source_hook_tree_is_denied(self, command):
        allowed, reason = check(command)
        assert allowed is False, (
            f"{command!r} writes Gaia hook source and must be denied"
        )
        assert "PROTECTED_PATH" in (reason or "")


class TestHarnessDenialsUnchanged:
    """The union must not have traded one tree for the other."""

    @pytest.mark.parametrize(
        "command",
        [
            f"sed -i 's/x/y/' {HARNESS_TARGET}",
            f"cp /tmp/payload.py {HARNESS_TARGET}",
            f"git mv /tmp/payload.py {HARNESS_TARGET}",
            "tee .claude/settings.json",
        ],
    )
    def test_writer_into_harness_tree_still_denied(self, command):
        allowed, _ = check(command)
        assert allowed is False


class TestReadsStillPass:
    """A fix that denies reads has broken the surface rather than widened it."""

    @pytest.mark.parametrize(
        "command",
        [
            f"cat {SOURCE_TARGET}",
            f"git diff {SOURCE_TARGET}",
            f"grep -n T3 {SOURCE_TARGET}",
            f"cat {HARNESS_TARGET}",
            f"git diff {HARNESS_TARGET}",
            "grep -r pattern .claude/hooks/",
        ],
    )
    def test_read_of_protected_path_is_allowed(self, command):
        allowed, reason = check(command)
        assert allowed is True, f"{command!r} is a read and must pass, got {reason!r}"

    def test_doc_under_the_source_hook_tree_is_writable(self):
        allowed, _ = check(f"tee {HOOKS_DIR / 'README.md'}")
        assert allowed is True


class TestOneSharedPredicate:
    """How the two surfaces are kept in agreement, shown rather than claimed."""

    def test_guard_consumes_the_shared_predicate_object(self):
        assert (
            protected_path_guard.is_protected_hook_path
            is protected_paths.is_protected_hook_path
        )

    def test_write_edit_gate_consumes_the_same_predicate(self):
        adapter = REPO_ROOT / "hooks" / "adapters" / "claude_code.py"
        tree = ast.parse(adapter.read_text(encoding="utf-8"))

        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "modules.security.protected_paths"
            and any(alias.name == "is_protected_hook_path" for alias in node.names)
        ]
        assert imports, (
            "the Write/Edit gate does not import the shared predicate -- the "
            "scope is duplicated again"
        )

        survivors = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_is_protected"
        ]
        assert not survivors, (
            "the adapter still defines its own protected-path predicate; two "
            "definitions is the drift generator this task removed"
        )
