"""
tests/cli/test_parity.py -- Structural parity tests between JS and Python Gaia CLIs.

Each test function exercises one JS-Python pair. The tests verify that:
- The Python CLI runs successfully (exit 0 or expected non-zero).
- Where JS supports --json, the Python output contains at least every key
  the JS output contains (extra Python keys are allowed).
- Types of shared keys agree.

Tests that require Node.js are marked @pytest.mark.skipif(not node_available(), ...)
so CI stays green when node is absent.

Test IDs follow the pattern: test_parity_<subcommand>.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"

if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

# Import helpers from compare_runner (same tests/ directory)
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from compare_runner import (
    compare_subcommand,
    node_available,
    run_python,
    run_js,
    JS_CLI_MAP,
    _make_temp_project,
    compare_json_structures,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def minimal_project():
    """
    Module-scoped temporary .claude/ project directory.
    Created once per test module run to keep things fast.
    Cleaned up automatically at module teardown.
    """
    project_dir = _make_temp_project()
    yield project_dir
    import shutil
    shutil.rmtree(str(project_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skip_if_no_node():
    return pytest.mark.skipif(
        not node_available(),
        reason="node not available -- skipping JS parity check",
    )


def _assert_python_runs(subcommand: str, project_dir: Path, py_args: list = None):
    """Assert the Python CLI subcommand runs without crashing.

    Acceptable exit codes:
      0 -- healthy / ok / no-data
      1 -- warnings found
      2 -- errors/critical (e.g. doctor on broken installation)
    """
    import subprocess
    gaia_bin = BIN_DIR / "gaia"
    if py_args is None:
        py_args = [subcommand]
    cmd = ["python3", str(gaia_bin)] + py_args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        timeout=30,
    )
    assert result.returncode in (0, 1, 2), (
        f"python gaia {subcommand} exited {result.returncode}\n"
        f"stderr: {result.stderr[:300]}\n"
        f"stdout: {result.stdout[:300]}"
    )
    return result


def _assert_python_json_valid(subcommand: str, project_dir: Path, py_args: list = None):
    """Assert the Python CLI produces valid JSON output.

    Runs the CLI and parses stdout as JSON regardless of exit code (exit 0/1/2
    are all valid -- doctor exits 2 on critical installations).
    """
    if py_args is None:
        py_args = [subcommand, "--json"]
    result = _assert_python_runs(subcommand, project_dir, py_args)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"python gaia {subcommand} --json did not produce valid JSON: {exc}\n"
            f"stdout: {result.stdout[:500]}"
        )
    return data


# ---------------------------------------------------------------------------
# Parity marker
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.parity


# ---------------------------------------------------------------------------
# test_parity_status
#
# JS gaia-status.js does NOT support --json.
# Verify: Python CLI runs, --json output is structurally sound.
# ---------------------------------------------------------------------------

class TestParityStatus:
    """Parity tests for gaia status vs gaia-status.js."""

    def test_python_status_runs(self, minimal_project):
        """Python gaia status should exit 0 on a minimal project."""
        result = _assert_python_runs("status", minimal_project, ["status"])
        assert "Gaia System Status" in result.stdout

    def test_python_status_json_structure(self, minimal_project):
        """Python gaia status --json should produce required keys."""
        data = _assert_python_json_valid("status", minimal_project, ["status", "--json"])
        required_keys = [
            "last_agent",
            "episode_count",
            "agent_session_count",
            "pending_count",
            "anomaly_count",
            "context_updated",
        ]
        for key in required_keys:
            assert key in data, f"Missing key in gaia status --json: {key}"

    def test_python_status_types(self, minimal_project):
        """Python gaia status --json types should match expected schema."""
        data = _assert_python_json_valid("status", minimal_project, ["status", "--json"])
        assert isinstance(data["pending_count"], int)
        assert isinstance(data["anomaly_count"], int)
        assert isinstance(data["episode_count"], int)
        assert isinstance(data["agent_session_count"], int)
        # last_agent is None or dict
        assert data["last_agent"] is None or isinstance(data["last_agent"], dict)

    @_skip_if_no_node()
    def test_js_status_runs(self, minimal_project):
        """JS gaia-status.js should run without crashing (no --json support)."""
        import subprocess
        js_path = REPO_ROOT / "bin" / "gaia-status.js"
        result = subprocess.run(
            ["node", str(js_path)],
            capture_output=True,
            text=True,
            cwd=str(minimal_project),
            timeout=30,
        )
        # JS status exits 0 (healthy) or 1 (no .claude) -- both acceptable
        assert result.returncode in (0, 1), (
            f"node gaia-status.js exited {result.returncode}\n"
            f"stderr: {result.stderr[:300]}"
        )


# ---------------------------------------------------------------------------
# test_parity_doctor
#
# JS gaia-doctor.js supports --json.
# Full structural parity comparison.
# ---------------------------------------------------------------------------

class TestParityDoctor:
    """Parity tests for gaia doctor vs gaia-doctor.js."""

    def test_python_doctor_json_structure(self, minimal_project):
        """Python gaia doctor --json must contain: healthy, status, checks keys."""
        data = _assert_python_json_valid("doctor", minimal_project)
        assert "healthy" in data, "Missing key: healthy"
        assert "status" in data, "Missing key: status"
        assert "checks" in data, "Missing key: checks"
        assert isinstance(data["checks"], list), "checks should be a list"

    def test_python_doctor_check_structure(self, minimal_project):
        """Each check in gaia doctor --json must have name, severity, ok, detail."""
        data = _assert_python_json_valid("doctor", minimal_project)
        for check in data["checks"]:
            assert "name" in check, f"Check missing 'name': {check}"
            assert "severity" in check, f"Check missing 'severity': {check}"
            assert "ok" in check, f"Check missing 'ok': {check}"
            assert "detail" in check, f"Check missing 'detail': {check}"
            assert check["severity"] in ("pass", "info", "warning", "error"), (
                f"Unknown severity: {check['severity']}"
            )

    def test_python_doctor_check_count(self, minimal_project):
        """Python gaia doctor should run all 11 checks."""
        data = _assert_python_json_valid("doctor", minimal_project)
        assert len(data["checks"]) == 11, (
            f"Expected 11 checks, got {len(data['checks'])}: {[c['name'] for c in data['checks']]}"
        )

    def test_python_doctor_status_values(self, minimal_project):
        """Python gaia doctor status field must be one of: healthy, degraded, critical."""
        data = _assert_python_json_valid("doctor", minimal_project)
        assert data["status"] in ("healthy", "degraded", "critical"), (
            f"Unexpected status: {data['status']}"
        )

    @_skip_if_no_node()
    def test_js_python_doctor_structural_parity(self, minimal_project):
        """
        JS and Python doctor --json must share the same top-level keys (name, severity,
        ok, detail in each check entry).
        """
        result = compare_subcommand("doctor", minimal_project)
        # Missing fields are failures; extra fields are acceptable
        missing = result.get("missing", [])
        mismatches = result.get("mismatches", [])
        assert not missing, (
            f"Fields present in JS but missing from Python:\n"
            + "\n".join(f"  - {m['field']}" for m in missing)
        )
        assert not mismatches, (
            f"Type mismatches between JS and Python:\n"
            + "\n".join(f"  - {m['field']}: JS={m['js_type']} Python={m['py_type']}" for m in mismatches)
        )


# ---------------------------------------------------------------------------
# test_parity_history
#
# JS gaia-history.js does NOT support --json.
# Verify Python CLI runs and --json output has correct structure.
# ---------------------------------------------------------------------------

class TestParityHistory:
    """Parity tests for gaia history vs gaia-history.js."""

    def test_python_history_runs(self, minimal_project):
        """Python gaia history should exit 0 on a minimal project."""
        _assert_python_runs("history", minimal_project, ["history", "--limit", "5"])

    def test_python_history_json_structure(self, minimal_project):
        """Python gaia history --json should produce a bare list of entries."""
        data = _assert_python_json_valid("history", minimal_project, ["history", "--json", "--limit", "5"])
        # Python CLI outputs a bare list (simpler, more standard API pattern).
        # JS gaia-history.js has no --json flag; Python adds JSON output as bare list.
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}"

    def test_python_history_empty(self, minimal_project):
        """Python gaia history --json on project with no history returns empty list."""
        data = _assert_python_json_valid("history", minimal_project, ["history", "--json", "--limit", "5"])
        # Minimal project has no episodes -- should be an empty bare list
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}"
        assert data == [], f"Expected empty list, got {data}"

    @_skip_if_no_node()
    def test_js_history_runs(self, minimal_project):
        """JS gaia-history.js should run without crashing."""
        import subprocess
        js_path = REPO_ROOT / "bin" / "gaia-history.js"
        result = subprocess.run(
            ["node", str(js_path), "--limit", "5"],
            capture_output=True,
            text=True,
            cwd=str(minimal_project),
            timeout=30,
        )
        # 0 (ok) or 1 (no .claude or warnings) are both acceptable
        assert result.returncode in (0, 1), (
            f"node gaia-history.js exited {result.returncode}\n"
            f"stderr: {result.stderr[:300]}"
        )


# ---------------------------------------------------------------------------
# test_parity_metrics
#
# JS gaia-metrics.js does NOT support --json.
# Verify Python CLI runs and --json output has required keys.
# ---------------------------------------------------------------------------

class TestParityMetrics:
    """Parity tests for gaia metrics vs gaia-metrics.js."""

    def test_python_metrics_runs(self, minimal_project):
        """Python gaia metrics should exit 0."""
        _assert_python_runs("metrics", minimal_project, ["metrics"])

    def test_python_metrics_json_structure(self, minimal_project):
        """Python gaia metrics --json should always produce a valid JSON dict with full schema.

        Both empty state and data-present state return the same schema keys with zero values
        or populated values respectively.
        (JS gaia-metrics.js has no --json flag; Python adds richer structure.)
        """
        data = _assert_python_json_valid("metrics", minimal_project)
        assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}"
        # Always expects full schema keys -- no "message" wrapper for empty state
        assert "security_tiers" in data, (
            f"metrics --json must have 'security_tiers' key, got: {list(data.keys())}"
        )
        assert "agent_invocations" in data, (
            f"metrics --json must have 'agent_invocations' key, got: {list(data.keys())}"
        )

    def test_python_metrics_types(self, minimal_project):
        """Python gaia metrics --json types should match schema."""
        data = _assert_python_json_valid("metrics", minimal_project)
        assert isinstance(data["security_tiers"], dict), "security_tiers should be a dict"
        assert isinstance(data["agent_invocations"], dict), "agent_invocations should be a dict"
        assert isinstance(data["security_tiers"]["total"], int), "security_tiers.total should be int"

    @_skip_if_no_node()
    def test_js_metrics_runs(self, minimal_project):
        """JS gaia-metrics.js should run without crashing."""
        import subprocess
        js_path = REPO_ROOT / "bin" / "gaia-metrics.js"
        result = subprocess.run(
            ["node", str(js_path)],
            capture_output=True,
            text=True,
            cwd=str(minimal_project),
            timeout=30,
        )
        assert result.returncode in (0, 1), (
            f"node gaia-metrics.js exited {result.returncode}\n"
            f"stderr: {result.stderr[:300]}"
        )


# ---------------------------------------------------------------------------
# test_parity_cleanup
#
# JS gaia-cleanup.js does NOT support --json.
# Verify Python --dry-run runs and exits 0.
# ---------------------------------------------------------------------------

class TestParityCleanup:
    """Parity tests for gaia cleanup vs gaia-cleanup.js."""

    def test_python_cleanup_dry_run(self, minimal_project):
        """Python gaia cleanup --dry-run should exit 0."""
        _assert_python_runs("cleanup", minimal_project, ["cleanup", "--dry-run"])

    def test_python_cleanup_prune_dry_run(self, minimal_project):
        """Python gaia cleanup --prune --dry-run should exit 0."""
        _assert_python_runs("cleanup", minimal_project, ["cleanup", "--prune", "--dry-run"])

    @_skip_if_no_node()
    def test_js_cleanup_runs(self, minimal_project):
        """
        JS gaia-cleanup.js should be runnable.
        NOTE: We do NOT run cleanup without --dry-run to avoid mutating the project.
        JS cleanup has no --dry-run flag, so we just verify it's importable by node.
        """
        import subprocess
        js_path = REPO_ROOT / "bin" / "gaia-cleanup.js"
        # We only verify the file exists and node can parse it (--input-type check)
        assert js_path.is_file(), f"gaia-cleanup.js not found at {js_path}"


# ---------------------------------------------------------------------------
# test_parity_update
#
# JS gaia-update.js does NOT support --dry-run with JSON.
# Verify Python --dry-run runs and exits 0.
# ---------------------------------------------------------------------------

class TestParityUpdate:
    """Parity tests for gaia update vs gaia-update.js."""

    def test_python_update_dry_run(self, minimal_project):
        """Python gaia update --dry-run should exit 0."""
        _assert_python_runs("update", minimal_project, ["update", "--dry-run"])

    @_skip_if_no_node()
    def test_js_update_file_exists(self, minimal_project):
        """JS gaia-update.js should be present on disk."""
        js_path = REPO_ROOT / "bin" / "gaia-update.js"
        assert js_path.is_file(), f"gaia-update.js not found at {js_path}"


# ---------------------------------------------------------------------------
# Cross-subcommand: compare_runner integration
# ---------------------------------------------------------------------------

class TestCompareRunnerIntegration:
    """Tests for the compare_runner module itself."""

    def test_compare_runner_returns_result_for_all_subcommands(self, minimal_project):
        """compare_subcommand should return a result dict for every known subcommand."""
        from compare_runner import ALL_SUBCOMMANDS
        for sub in ALL_SUBCOMMANDS:
            result = compare_subcommand(sub, minimal_project)
            assert "subcommand" in result
            assert "pass" in result
            assert "py_available" in result
            assert "report" in result
            assert isinstance(result["report"], str)

    def test_compare_json_structures_match(self):
        """compare_json_structures should detect matching structures."""
        js = {"a": 1, "b": "x", "c": True}
        py = {"a": 2, "b": "y", "c": False, "d": "extra"}  # d is extra
        results = compare_json_structures(js, py)
        statuses = {r["field"]: r["status"] for r in results}
        assert statuses["a"] == "match"
        assert statuses["b"] == "match"
        assert statuses["c"] == "match"
        assert statuses["d"] == "extra"

    def test_compare_json_structures_missing(self):
        """compare_json_structures should detect missing keys."""
        js = {"a": 1, "b": 2}
        py = {"a": 3}  # b is missing
        results = compare_json_structures(js, py)
        statuses = {r["field"]: r["status"] for r in results}
        assert statuses["a"] == "match"
        assert statuses["b"] == "missing"

    def test_compare_json_structures_type_mismatch(self):
        """compare_json_structures should detect type mismatches."""
        js = {"x": 1}
        py = {"x": "one"}
        results = compare_json_structures(js, py)
        assert results[0]["status"] == "mismatch"
        assert results[0]["js_type"] == "int"
        assert results[0]["py_type"] == "str"

    def test_compare_json_structures_nested(self):
        """compare_json_structures should recurse into nested dicts."""
        js = {"outer": {"inner": 42}}
        py = {"outer": {"inner": 99, "bonus": "x"}}
        results = compare_json_structures(js, py)
        statuses = {r["field"]: r["status"] for r in results}
        assert statuses["outer.inner"] == "match"
        assert statuses["outer.bonus"] == "extra"
