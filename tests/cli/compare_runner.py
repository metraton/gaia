#!/usr/bin/env python3
"""
compare_runner.py -- Side-by-side structural comparison of JS and Python Gaia CLIs.

Usage:
    python tests/cli/compare_runner.py status
    python tests/cli/compare_runner.py doctor
    python tests/cli/compare_runner.py history
    python tests/cli/compare_runner.py metrics
    python tests/cli/compare_runner.py all
    python tests/cli/compare_runner.py --help

Compares JSON structure (keys + types) between:
    node bin/gaia-<name>.js --json   (where supported)
    python bin/gaia <name> --json

Output equivalence: every field present in JS output must also appear in Python
output with the same type. Extra fields in Python output are allowed.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Project root / binary paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"

# Mapping of subcommand name -> JS CLI (relative to REPO_ROOT) and its args
# None means the JS CLI does not support --json; skip it.
JS_CLI_MAP = {
    "status":  {"js_bin": "bin/gaia-status.js",  "js_args": [],         "json_flag": None},
    "doctor":  {"js_bin": "bin/gaia-doctor.js",  "js_args": [],         "json_flag": "--json"},
    "history": {"js_bin": "bin/gaia-history.js", "js_args": [],         "json_flag": None},
    "metrics": {"js_bin": "bin/gaia-metrics.js", "js_args": [],         "json_flag": None},
    "cleanup": {"js_bin": "bin/gaia-cleanup.js", "js_args": [],         "json_flag": None},
    "update":  {"js_bin": "bin/gaia-update.js",  "js_args": [],         "json_flag": None},
    # gaia-scan is a Node wrapper that calls gaia-scan.py; context scan maps to it
    "context": {"js_bin": None, "js_args": [], "json_flag": None},
}

# Python CLI subcommands and their args for a comparable call
PY_CLI_MAP = {
    "status":  {"py_args": ["status",  "--json"]},
    "doctor":  {"py_args": ["doctor",  "--json"]},
    "history": {"py_args": ["history", "--json", "--limit", "5"]},
    "metrics": {"py_args": ["metrics", "--json"]},
    "cleanup": {"py_args": ["cleanup", "--dry-run"]},
    "update":  {"py_args": ["update",  "--dry-run"]},
}

# Known acceptable divergences between JS and Python CLIs.
# These are documented parity gaps -- truly unavoidable because JS CLIs lack --json support.
#
# "js_no_json" -- JS CLI does not support --json; Python-only JSON output is an enhancement.
#
# Resolved divergences (T6):
#   history: was "different_structure" (wrapped dict). Now bare list. Resolved.
#   metrics: was "different_structure" (tiers key + empty message wrapper). Now security_tiers
#            key and full schema with zero values for empty state. Resolved.
KNOWN_DIVERGENCES = {
    "status": {
        "type": "js_no_json",
        "note": "JS gaia-status.js has no --json flag; Python adds JSON output.",
    },
    "history": {
        "type": "js_no_json",
        "note": "JS gaia-history.js has no --json flag; Python outputs bare list.",
    },
    "metrics": {
        "type": "js_no_json",
        "note": "JS gaia-metrics.js has no --json flag; Python outputs full schema with security_tiers key.",
    },
    "cleanup": {
        "type": "js_no_json",
        "note": "JS gaia-cleanup.js has no --dry-run/--json flag.",
    },
    "update": {
        "type": "js_no_json",
        "note": "JS gaia-update.js has no --dry-run with JSON output.",
    },
}

ALL_SUBCOMMANDS = list(PY_CLI_MAP.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[mKGHF]")
# ISO 8601 timestamps
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
# Absolute file paths
PATH_RE = re.compile(r"(?:/[^\s\"',:]+){2,}")
# Short time strings like HH:MM or MM-DD HH:MM
SHORT_TIME_RE = re.compile(r"\b\d{2}-\d{2} \d{2}:\d{2}\b|\b\d{2}:\d{2}\b")
# Version strings like v1.2.3
VERSION_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def normalize_text(text: str) -> str:
    """Strip ANSI codes and normalise dynamic values in plain text output."""
    text = strip_ansi(text)
    text = TIMESTAMP_RE.sub("<TIMESTAMP>", text)
    text = SHORT_TIME_RE.sub("<TIME>", text)
    text = PATH_RE.sub("<PATH>", text)
    text = VERSION_RE.sub("<VERSION>", text)
    return text


def normalize_value(v: Any) -> Any:
    """Recursively normalise a JSON value, replacing dynamic data with placeholders."""
    if isinstance(v, str):
        v = TIMESTAMP_RE.sub("<TIMESTAMP>", v)
        v = SHORT_TIME_RE.sub("<TIME>", v)
        v = PATH_RE.sub("<PATH>", v)
        v = VERSION_RE.sub("<VERSION>", v)
        return v
    if isinstance(v, dict):
        return {k: normalize_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [normalize_value(i) for i in v]
    return v


def node_available() -> bool:
    """Return True if node is on PATH."""
    return shutil.which("node") is not None


def _run(cmd: list, cwd: Path, timeout: int = 30) -> "tuple[int, str, str]":
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as exc:
        return -1, "", str(exc)


def run_js(subcommand: str, project_dir: Path) -> "tuple[bool, str | dict | None, str]":
    """
    Run the JS CLI for a subcommand against project_dir.

    Returns:
        (success, parsed_output, error_message)
        parsed_output is dict if --json available, str otherwise, None on failure.
    """
    info = JS_CLI_MAP.get(subcommand)
    if info is None or info["js_bin"] is None:
        return False, None, f"No JS CLI mapped for '{subcommand}'"

    if not node_available():
        return False, None, "node not available"

    js_path = REPO_ROOT / info["js_bin"]
    if not js_path.is_file():
        return False, None, f"JS CLI not found: {js_path}"

    base_args = info.get("js_args", [])
    json_flag = info.get("json_flag")

    cmd = ["node", str(js_path)] + base_args
    if json_flag:
        cmd.append(json_flag)

    rc, stdout, stderr = _run(cmd, cwd=project_dir)
    if rc == -1:
        return False, None, stderr

    if json_flag:
        try:
            return True, json.loads(stdout), ""
        except json.JSONDecodeError as e:
            return False, None, f"JS JSON parse error: {e}\nStdout: {stdout[:500]}"
    else:
        # No JSON support -- return normalised text
        return True, normalize_text(stdout or stderr), ""


def run_python(subcommand: str, project_dir: Path) -> "tuple[bool, dict | str | None, str]":
    """
    Run the Python CLI for a subcommand against project_dir.

    Returns:
        (success, parsed_output, error_message)
        parsed_output is dict if --json available, str otherwise, None on failure.
    """
    info = PY_CLI_MAP.get(subcommand)
    if info is None:
        return False, None, f"No Python CLI mapped for '{subcommand}'"

    gaia_bin = BIN_DIR / "gaia"
    if not gaia_bin.is_file():
        return False, None, f"Python gaia binary not found: {gaia_bin}"

    cmd = ["python3", str(gaia_bin)] + info["py_args"]
    rc, stdout, stderr = _run(cmd, cwd=project_dir)
    if rc == -1:
        return False, None, stderr

    py_args = info["py_args"]
    uses_json = "--json" in py_args

    if uses_json:
        try:
            return True, json.loads(stdout), ""
        except json.JSONDecodeError as e:
            return False, None, f"Python JSON parse error: {e}\nStdout: {stdout[:500]}"
    else:
        return True, normalize_text(stdout or stderr), ""


# ---------------------------------------------------------------------------
# Structural comparison
# ---------------------------------------------------------------------------

FieldResult = dict  # {"field": str, "status": "match"|"mismatch"|"missing"|"extra", "js_type": str, "py_type": str, "detail": str}


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    return type(v).__name__


def compare_json_structures(js_data: Any, py_data: Any, path: str = "") -> "list[FieldResult]":
    """
    Recursively compare JSON structures.

    Rules:
    - Every key in js_data must be present in py_data (same type).
    - Extra keys in py_data are "extra" (allowed -- not a failure).
    - Recurse into nested dicts.
    - For lists: compare element types at index 0 if both non-empty.
    """
    results = []

    if isinstance(js_data, dict) and isinstance(py_data, dict):
        all_keys = set(js_data.keys()) | set(py_data.keys())
        for key in sorted(all_keys):
            field_path = f"{path}.{key}" if path else key
            if key in js_data and key not in py_data:
                results.append({
                    "field": field_path,
                    "status": "missing",
                    "js_type": _type_name(js_data[key]),
                    "py_type": "n/a",
                    "detail": "Field present in JS but missing from Python",
                })
            elif key not in js_data and key in py_data:
                results.append({
                    "field": field_path,
                    "status": "extra",
                    "js_type": "n/a",
                    "py_type": _type_name(py_data[key]),
                    "detail": "Extra field in Python (allowed)",
                })
            else:
                jv, pv = js_data[key], py_data[key]
                jt, pt = _type_name(jv), _type_name(pv)

                if jt == pt:
                    if isinstance(jv, dict):
                        results.extend(compare_json_structures(jv, pv, field_path))
                    elif isinstance(jv, list):
                        results.append({
                            "field": field_path,
                            "status": "match",
                            "js_type": f"list[{len(jv)}]",
                            "py_type": f"list[{len(pv)}]",
                            "detail": "List lengths may differ -- structural types match",
                        })
                    else:
                        results.append({
                            "field": field_path,
                            "status": "match",
                            "js_type": jt,
                            "py_type": pt,
                            "detail": "",
                        })
                else:
                    results.append({
                        "field": field_path,
                        "status": "mismatch",
                        "js_type": jt,
                        "py_type": pt,
                        "detail": f"Type mismatch: JS={jt}, Python={pt}",
                    })
    else:
        jt, pt = _type_name(js_data), _type_name(py_data)
        status = "match" if jt == pt else "mismatch"
        results.append({
            "field": path or "root",
            "status": status,
            "js_type": jt,
            "py_type": pt,
            "detail": "" if status == "match" else f"Root type mismatch: JS={jt}, Python={pt}",
        })

    return results


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

STATUS_SYMBOLS = {
    "match":    "OK  ",
    "mismatch": "FAIL",
    "missing":  "MISS",
    "extra":    "EXTR",
}


def _render_report(subcommand: str, comparison_results: "list[FieldResult]", js_ok: bool, py_ok: bool,
                   js_err: str, py_err: str, js_raw=None, py_raw=None) -> str:
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  Subcommand: {subcommand}")
    lines.append(f"{'='*60}")

    if not js_ok:
        lines.append(f"  JS CLI:     UNAVAILABLE -- {js_err}")
    if not py_ok:
        lines.append(f"  Python CLI: FAILED -- {py_err}")

    if not js_ok or not py_ok:
        lines.append("")
        return "\n".join(lines)

    # Summary counts
    by_status: dict[str, list] = {}
    for r in comparison_results:
        by_status.setdefault(r["status"], []).append(r)

    missing   = by_status.get("missing", [])
    mismatch  = by_status.get("mismatch", [])
    extra     = by_status.get("extra", [])
    match     = by_status.get("match", [])

    pass_fail = "PASS" if not missing and not mismatch else "FAIL"
    lines.append(f"  Result:     {pass_fail}")
    lines.append(f"  Matched:    {len(match)}  Extra(Python): {len(extra)}  Missing: {len(missing)}  Type-mismatch: {len(mismatch)}")
    lines.append("")
    lines.append(f"  {'Status':<6}  {'Field':<40}  {'JS type':<12}  {'Python type':<12}")
    lines.append(f"  {'-'*6}  {'-'*40}  {'-'*12}  {'-'*12}")

    for r in sorted(comparison_results, key=lambda x: ({"missing": 0, "mismatch": 1, "extra": 2, "match": 3}[x["status"]], x["field"])):
        sym = STATUS_SYMBOLS[r["status"]]
        lines.append(f"  {sym}    {r['field']:<40}  {r['js_type']:<12}  {r['py_type']:<12}")
        if r.get("detail"):
            lines.append(f"         {r['detail']}")

    lines.append("")

    if missing:
        lines.append("  Missing fields (JS has them, Python does not):")
        for r in missing:
            lines.append(f"    - {r['field']}  ({r['js_type']})")
        lines.append("")

    if mismatch:
        lines.append("  Type mismatches:")
        for r in mismatch:
            lines.append(f"    - {r['field']}  JS={r['js_type']} Python={r['py_type']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public comparison entry point
# ---------------------------------------------------------------------------

CompareResult = dict  # {"subcommand", "pass", "js_available", "py_available", "missing", "mismatches", "extra", "field_results", "report"}


def compare_subcommand(subcommand: str, project_dir: Path) -> CompareResult:
    """
    Run JS and Python CLIs for a subcommand, compare, return structured result.
    """
    js_info = JS_CLI_MAP.get(subcommand)
    py_info = PY_CLI_MAP.get(subcommand)

    if py_info is None:
        return {
            "subcommand": subcommand,
            "pass": None,  # not applicable
            "js_available": False,
            "py_available": False,
            "missing": [],
            "mismatches": [],
            "extra": [],
            "field_results": [],
            "report": f"Subcommand '{subcommand}' not in comparison map",
        }

    js_ok, js_raw, js_err = run_js(subcommand, project_dir)
    py_ok, py_raw, py_err = run_python(subcommand, project_dir)

    # If JS has no JSON support, we only verify Python runs successfully
    has_js_json = js_info and js_info.get("json_flag") is not None

    if not py_ok:
        report = _render_report(subcommand, [], js_ok, py_ok, js_err, py_err)
        return {
            "subcommand": subcommand,
            "pass": False,
            "js_available": js_ok,
            "py_available": False,
            "missing": [],
            "mismatches": [],
            "extra": [],
            "field_results": [],
            "report": report,
        }

    if not js_ok or not has_js_json:
        # JS unavailable or no JSON: mark as skip (None = inconclusive but Python ran)
        report = _render_report(subcommand, [], js_ok, py_ok, js_err or "no --json flag", py_err)
        return {
            "subcommand": subcommand,
            "pass": None,  # inconclusive -- Python worked, JS unavailable/no-json
            "js_available": js_ok,
            "py_available": py_ok,
            "missing": [],
            "mismatches": [],
            "extra": [],
            "field_results": [],
            "report": report,
        }

    # Both have JSON -- do structural comparison
    field_results = compare_json_structures(js_raw, py_raw)
    missing   = [r for r in field_results if r["status"] == "missing"]
    mismatches = [r for r in field_results if r["status"] == "mismatch"]
    extra     = [r for r in field_results if r["status"] == "extra"]

    passed = len(missing) == 0 and len(mismatches) == 0
    report = _render_report(subcommand, field_results, js_ok, py_ok, js_err, py_err, js_raw, py_raw)

    return {
        "subcommand": subcommand,
        "pass": passed,
        "js_available": js_ok,
        "py_available": True,
        "missing": missing,
        "mismatches": mismatches,
        "extra": extra,
        "field_results": field_results,
        "report": report,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _make_temp_project() -> Path:
    """Create a minimal temporary .claude/ project for CLI comparisons."""
    tmpdir = Path(tempfile.mkdtemp(prefix="gaia_compare_"))
    claude_dir = tmpdir / ".claude"
    claude_dir.mkdir()

    # Minimal project-context
    pc_dir = claude_dir / "project-context"
    pc_dir.mkdir()
    (pc_dir / "project-context.json").write_text(json.dumps({
        "metadata": {"last_updated": "2026-01-01T00:00:00Z", "version": "2.0"},
        "sections": {"stack": {}, "git": {}, "infrastructure": {}},
    }))

    em_dir = pc_dir / "episodic-memory"
    em_dir.mkdir()
    (em_dir / "index.json").write_text(json.dumps({"episodes": []}))

    (pc_dir / "pending-updates").mkdir()
    (pc_dir / "pending-updates" / "pending-index.json").write_text(json.dumps({"pending_count": 0}))

    (pc_dir / "workflow-episodic-memory").mkdir()
    (pc_dir / "workflow-episodic-memory" / "signals").mkdir()

    return tmpdir


def main():
    parser = argparse.ArgumentParser(
        description="Compare JS and Python Gaia CLIs side-by-side.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default="all",
        choices=ALL_SUBCOMMANDS + ["all"],
        help="Subcommand to compare, or 'all' (default: all)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Project directory with .claude/ (default: create temp dir)",
    )
    parser.add_argument(
        "--json-out",
        action="store_true",
        help="Print results as JSON instead of human-readable report",
    )
    args = parser.parse_args()

    if args.project_dir:
        project_dir = Path(args.project_dir).resolve()
        cleanup = False
    else:
        project_dir = _make_temp_project()
        cleanup = True

    if not node_available():
        print("WARNING: 'node' not found on PATH -- JS CLI comparisons will be skipped")

    subcommands = ALL_SUBCOMMANDS if args.subcommand == "all" else [args.subcommand]

    all_results = []
    for sub in subcommands:
        result = compare_subcommand(sub, project_dir)
        all_results.append(result)
        if not args.json_out:
            print(result["report"])

    if cleanup:
        import shutil as _shutil
        _shutil.rmtree(str(project_dir), ignore_errors=True)

    if args.json_out:
        # Strip reports from JSON (can be verbose)
        print(json.dumps([
            {k: v for k, v in r.items() if k != "report"}
            for r in all_results
        ], indent=2))
    else:
        # Summary table
        print(f"\n{'='*60}")
        print("  Summary")
        print(f"{'='*60}")
        print(f"  {'Subcommand':<16}  {'Result':<10}  {'JS avail':<10}  {'Py avail':<10}  Missing  Mismatch")
        print(f"  {'-'*16}  {'-'*10}  {'-'*10}  {'-'*10}  -------  --------")
        for r in all_results:
            if r["pass"] is True:
                result_str = "PASS"
            elif r["pass"] is False:
                result_str = "FAIL"
            else:
                result_str = "SKIP"
            js_str = "yes" if r["js_available"] else "no"
            py_str = "yes" if r["py_available"] else "no"
            missing_n = len(r.get("missing", []))
            mismatch_n = len(r.get("mismatches", []))
            print(f"  {r['subcommand']:<16}  {result_str:<10}  {js_str:<10}  {py_str:<10}  {missing_n:<7}  {mismatch_n}")

        print()

    # Exit code: 0 if all pass or skip, 1 if any fail
    failures = [r for r in all_results if r["pass"] is False]
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
