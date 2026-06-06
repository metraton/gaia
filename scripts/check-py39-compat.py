#!/usr/bin/env python3
"""
Static Python 3.9 compatibility check -- runs on any Python 3.x, no 3.9 needed.

Why this exists: Gaia declares ``requires-python = ">=3.9"`` and CI runs a 3.9
matrix leg, but day-to-day development happens on 3.11/3.12. PEP 604 union
syntax in annotations (``X | None``, ``int | str``) parses fine on 3.10+ and
raises ``TypeError`` at runtime on 3.9 when the annotation is evaluated --
unless the module opts into deferred evaluation with
``from __future__ import annotations``. That exact class of bug shipped in
5.0.4. This check catches it locally, before the tag, instead of in the 3.9
matrix leg after publish.

What it flags: a module under a runtime tree (bin/cli, hooks, gaia, tools)
that uses PEP 604 union syntax in an *annotation position* (function arg,
return, variable annotation) AND does NOT have ``from __future__ import
annotations`` as a module-level statement. With the future import present the
annotation is never evaluated at runtime on 3.9, so the union is safe; without
it, the union is evaluated and breaks.

It uses the AST, not a regex: a regex on ``|`` cannot tell a bitwise OR
(``flags | MASK``) or a string literal from a type union, and would either
miss real bugs or drown the signal in false positives. The AST inspects only
annotation nodes, so ``flags | MASK`` in a function body is correctly ignored.

Exit 0 when clean; exit 1 with the offending file:line:symbol list otherwise.

Usage:
  python3 scripts/check-py39-compat.py            # scan the default runtime trees
  python3 scripts/check-py39-compat.py path ...   # scan explicit paths
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Runtime trees: code that actually executes under the consumer's interpreter.
# Build/release tooling (scripts/) and tests/ are excluded -- they run under the
# developer/CI interpreter, not the shipped 3.9 floor.
DEFAULT_TREES = ["bin/cli", "hooks", "gaia", "tools"]


def _has_future_annotations(tree: ast.Module) -> bool:
    """True if the module starts with ``from __future__ import annotations``."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
        # __future__ imports must precede other code; once we hit a non-docstring,
        # non-future statement we can stop -- but keep scanning imports to be safe.
    return False


class _AnnotationUnionFinder(ast.NodeVisitor):
    """Collects PEP 604 unions (``a | b``) that appear in annotation positions."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def _scan_annotation(self, annotation: ast.expr | None, where: str) -> None:
        if annotation is None:
            return
        for node in ast.walk(annotation):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                self.hits.append((node.lineno, where))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node) -> None:
        a = node.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
            if arg is not None:
                self._scan_annotation(arg.annotation, f"{node.name}() arg '{arg.arg}'")
        self._scan_annotation(node.returns, f"{node.name}() return")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = ast.unparse(node.target) if hasattr(ast, "unparse") else "<var>"
        self._scan_annotation(node.annotation, f"variable '{target}'")
        self.generic_visit(node)


def check_file(path: Path) -> list[str]:
    """Return a list of violation strings for one file (empty if clean)."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{path}: could not read ({exc})"]

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # A syntax error is itself a hard failure worth surfacing -- e.g. a
        # mis-placed ``from __future__`` import (must be first statement).
        return [f"{path}:{exc.lineno}: SyntaxError: {exc.msg}"]

    if _has_future_annotations(tree):
        return []  # deferred evaluation -- unions are safe on 3.9

    finder = _AnnotationUnionFinder()
    finder.visit(tree)
    rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    return [
        f"{rel}:{lineno}: PEP 604 union in annotation ({where}) without "
        f"`from __future__ import annotations`"
        for lineno, where in sorted(finder.hits)
    ]


def iter_python_files(targets: list[Path]):
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            yield target
        elif target.is_dir():
            for p in sorted(target.rglob("*.py")):
                if "__pycache__" in p.parts:
                    continue
                yield p


def main(argv: list[str]) -> int:
    if argv:
        targets = [Path(a) if Path(a).is_absolute() else REPO_ROOT / a for a in argv]
    else:
        targets = [REPO_ROOT / tree for tree in DEFAULT_TREES]

    targets = [t for t in targets if t.exists()]
    if not targets:
        print("check-py39-compat: no target paths exist; nothing to scan.")
        return 0

    violations: list[str] = []
    scanned = 0
    for py_file in iter_python_files(targets):
        scanned += 1
        violations.extend(check_file(py_file))

    if violations:
        print(f"check-py39-compat: FAIL -- {len(violations)} violation(s) in {scanned} file(s):\n")
        for v in violations:
            print(f"  {v}")
        print(
            "\nFix: add `from __future__ import annotations` as the first statement "
            "after the module docstring, OR rewrite the annotation as "
            "`Optional[X]` / `Union[X, Y]` from typing."
        )
        return 1

    print(f"check-py39-compat: OK -- {scanned} file(s) scanned, no Python 3.9 union violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
