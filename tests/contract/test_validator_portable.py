"""
AC-2 -- portability boundary of the form-layer validator.

Proves that ``gaia.contract.validator`` imports and validates a correct
envelope inside a subprocess whose ``sys.path`` exposes ONLY the Python
standard library plus the gaia package root (needed so ``gaia`` /
``gaia.state`` -- the whitelisted SSOT import documented in validator.py's
"PORTABILITY CONTRACT" -- resolve). Per T1's carry-forward: AC-2's "no
gaia.store, no hooks, no third party" does NOT forbid ``gaia`` /
``gaia.state`` -- those two are explicitly whitelisted below; only
``hooks*``, ``gaia.store*``, and any non-stdlib/non-gaia top-level import
are treated as violations.

The child subprocess is launched with ``-I -S`` (isolated + no site
import), so:
  - no PYTHONPATH / user site / cwd leak in -- ``sys.path`` starts as
    stdlib-only, and the test inserts ONLY the repo root (so ``import gaia``
    resolves).
  - no site-packages / venv on the path at all -- an accidental import of a
    third-party package (anything installed only in .venv) raises
    ImportError and the subprocess exits non-zero. This is the concrete
    enforcement of "no third-party package", not merely an assertion.
  - ``hooks/`` and ``gaia/store`` are technically reachable from the same
    repo root (they are sibling/nested packages on disk), so their
    exclusion is NOT enforced by path restriction -- it is verified by
    diffing ``sys.modules`` before/after the import and asserting neither
    name appears among the newly-imported modules.

Two complementary checks satisfy AC-2's "falla si sus imports incluyen
hooks o terceros (cubre importlib/transitivos)":
  1. ``test_subprocess_import_and_validate_is_portable`` -- the REAL
     transitive import graph of gaia.contract.validator, executed in the
     sandboxed subprocess, is inspected. This is the integration proof.
  2. ``test_classifier_flags_forbidden_and_allows_whitelisted`` -- a unit
     test of the same classification rule (duplicated here, self-contained,
     since the child runs in an isolated interpreter and cannot import this
     test module) against synthetic module names, proving the rule itself
     actually distinguishes a violation (hooks.foo, gaia.store.bar, a
     third-party name) from an allowed import (gaia, gaia.state, a stdlib
     name) -- not just that nothing bad happened to import today.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The import whitelist enforced by this test (per T1 carry-forward):
#   - any stdlib top-level module (sys.stdlib_module_names)
#   - "gaia", "gaia.contract", "gaia.contract.validator" (the module under
#     test and its own package chain)
#   - "gaia.state" (the SSOT for VALID_PLAN_STATUSES; itself stdlib-pure)
# Explicitly forbidden regardless of the above:
#   - "hooks" or anything under "hooks."
#   - "gaia.store" or anything under "gaia.store."
# Anything else (a non-stdlib, non-gaia top-level) is "any third-party
# package" and is also a violation.
# ---------------------------------------------------------------------------
ALLOWED_GAIA_MODULES = frozenset(
    {"gaia", "gaia.contract", "gaia.contract.validator", "gaia.state"}
)


def _classify_violation(name: str, stdlib_names: frozenset) -> bool:
    """Return True if ``name`` is a portability violation under the whitelist.

    Mirrors the identical logic embedded in the child subprocess script
    (``_CHILD_SCRIPT`` below) -- duplicated, not imported, because the child
    runs in an isolated interpreter with no path back to this test module.
    """
    if name == "hooks" or name.startswith("hooks."):
        return True
    if name == "gaia.store" or name.startswith("gaia.store."):
        return True
    top = name.split(".")[0]
    if top in stdlib_names or name in ALLOWED_GAIA_MODULES:
        return False
    return True  # non-stdlib, non-gaia top-level == third-party


# ---------------------------------------------------------------------------
# Child subprocess script.
#
# Run with `python3 -I -S <script> <repo_root>`:
#   -I  isolated mode: ignores PYTHONPATH/PYTHONHOME, no user site dir.
#   -S  do not import `site` at all: no site-packages/.venv on sys.path.
# The only path this script adds itself is `repo_root`, so `import gaia`
# resolves and nothing else not already stdlib-shipped is reachable.
# ---------------------------------------------------------------------------
_CHILD_SCRIPT = textwrap.dedent(
    """
    import json
    import sys

    repo_root = sys.argv[1]
    sys.path.insert(0, repo_root)

    baseline = set(sys.modules.keys())

    try:
        from gaia.contract.validator import validate_form
    except Exception as exc:
        print(json.dumps({"import_error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)

    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": "a1b2c3",
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    result = validate_form(envelope)

    new_modules = sorted(set(sys.modules.keys()) - baseline)

    stdlib_names = set(sys.stdlib_module_names)
    allowed_gaia = {"gaia", "gaia.contract", "gaia.contract.validator", "gaia.state"}

    violations = []
    for name in new_modules:
        if name == "hooks" or name.startswith("hooks."):
            violations.append(name)
            continue
        if name == "gaia.store" or name.startswith("gaia.store."):
            violations.append(name)
            continue
        top = name.split(".")[0]
        if top in stdlib_names or name in allowed_gaia:
            continue
        violations.append(name)

    print(json.dumps({
        "validate_ok": result.ok,
        "new_module_count": len(new_modules),
        "new_modules": new_modules,
        "violations": violations,
    }))
    sys.exit(0 if (result.ok and not violations) else 1)
    """
)


@pytest.fixture()
def repo_root(package_root: Path) -> Path:
    return package_root


def test_subprocess_import_and_validate_is_portable(repo_root: Path):
    """Integration proof (AC-2): import + validate a correct envelope in a
    stdlib-only (+gaia) subprocess; assert pass AND assert no hooks/,
    gaia.store, or third-party module was transitively pulled in."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as script_file:
        script_file.write(_CHILD_SCRIPT)
        script_path = script_file.name

    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", script_path, str(repo_root)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)

    assert proc.returncode == 0, (
        "portable subprocess import+validate failed "
        f"(exit={proc.returncode}); stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )

    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "import_error" not in payload, payload.get("import_error")
    assert payload["validate_ok"] is True, payload
    assert payload["violations"] == [], (
        f"portability boundary breached -- forbidden transitive imports: "
        f"{payload['violations']}"
    )
    # Sanity: the module under test really did run inside the sandbox (a
    # non-empty new_modules set proves the import machinery executed, not
    # that everything was skipped/cached).
    assert payload["new_module_count"] > 0, payload


def test_classifier_flags_forbidden_and_allows_whitelisted():
    """Unit proof that the whitelist rule itself distinguishes a violation
    from an allowed import -- so a future hooks/gaia.store/third-party pull
    -in would actually fail this suite, not silently pass because nothing
    bad happened to import today."""
    stdlib_names = frozenset(sys.stdlib_module_names)

    forbidden = [
        "hooks",
        "hooks.modules.agents.contract_validator",
        "gaia.store",
        "gaia.store.db",
        "requests",  # a real third-party package name, not on stdlib
        "yaml",
    ]
    for name in forbidden:
        assert _classify_violation(name, stdlib_names) is True, (
            f"expected {name!r} to be flagged as a portability violation"
        )

    allowed = [
        "gaia",
        "gaia.contract",
        "gaia.contract.validator",
        "gaia.state",
        "json",
        "re",
        "enum",
        "dataclasses",
        "typing",
    ]
    for name in allowed:
        assert _classify_violation(name, stdlib_names) is False, (
            f"expected {name!r} to be whitelisted, not flagged"
        )
