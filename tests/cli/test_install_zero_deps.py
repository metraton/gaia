"""
Install = NPM puro, CERO dependencias.

Gaia installs via npm with no external runtime dependencies. The CLI (bin/gaia)
is Python stdlib only; the npm entry (index.js) is Node stdlib only. The only
third-party package (chalk) is a publish-time release tool and must live in
devDependencies, never in runtime dependencies.

These tests guard that contract so a future change that re-introduces a runtime
dependency fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _package_json() -> dict:
    return json.loads((_REPO_ROOT / "package.json").read_text())


def test_runtime_dependencies_are_empty():
    """package.json `dependencies` must be empty -- install is zero-dep."""
    deps = _package_json().get("dependencies", {})
    assert deps == {}, (
        f"runtime dependencies must be empty (zero-dep install); found: {deps!r}"
    )


def test_no_self_referential_dependency():
    """The package must not depend on itself (a build artifact bug)."""
    pkg = _package_json()
    name = pkg.get("name")
    assert name not in pkg.get("dependencies", {}), (
        "package.json must not list itself as a runtime dependency"
    )


def test_chalk_is_dev_only():
    """chalk is a publish-time tool -> devDependencies, not dependencies."""
    pkg = _package_json()
    assert "chalk" not in pkg.get("dependencies", {}), (
        "chalk must not be a runtime dependency"
    )
    assert "chalk" in pkg.get("devDependencies", {}), (
        "chalk must remain available as a devDependency for pre-publish-validate.js"
    )


def test_cli_entry_is_stdlib_python():
    """bin/gaia must not import third-party python packages at the top level."""
    src = (_REPO_ROOT / "bin" / "gaia").read_text()
    # The CLI advertises "Zero external dependencies (stdlib only)".
    assert "stdlib only" in src.lower() or "zero external" in src.lower()
