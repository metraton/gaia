#!/usr/bin/env python3
"""Windows-compatibility CI smoke -- the evidence source for the Windows
artifact acceptance criteria (brief gaia-windows-compatibility).

This script is invoked by the ``windows-compat`` job in
``.github/workflows/ci.yml`` on ``windows-latest``. It runs on the REAL
Windows runner, so it exercises the exact platform surfaces the fixes target:

  * ``hooks/modules/core/filelock.py`` -- the ``fcntl`` crash (#5). ``fcntl`` is
    POSIX-only; on Windows an unconditional ``import fcntl`` raises
    ModuleNotFoundError at hook-IMPORT time. The ``imports`` phase catches that
    at import, and the ``smoke`` phase catches it at RUNTIME (the lock is
    actually acquired/released through the PostToolUse critical-event path).
  * ``scripts/bootstrap_database.py`` -- DB bootstrap with NO ``sqlite3`` CLI
    and NO ``bash`` (the ``smoke`` phase bootstraps a throwaway DB here; the
    workflow's install step re-proves it end to end).
  * FTS5 availability + trigger sync in the stdlib ``sqlite3`` module.
  * The T3 grant cycle, activated at the DB plane so the check does NOT depend
    on which activation route the host happens to pick (risk R3).

Phases (each maps to an acceptance criterion; see the AC map in ci.yml):

  imports  -> AC-2  import EVERY hooks/ module; fail hard if any fails to import
  doctor   -> AC-6  `gaia doctor` reports no RED (error) checks on Windows
  smoke    -> AC-8  post_tool_use runs without crashing + T3 grant activation
                    + FTS5 sync, all verified against the DB

Each phase prints ``[AC-N] PASS``/``[AC-N] FAIL`` and returns a shell exit code
(0 = pass, non-zero = fail) so a CI step fails loudly on regression. The phases
are self-contained: ``imports`` needs no DB, and ``smoke`` bootstraps its own
isolated ``GAIA_DATA_DIR`` so it does not touch the machine's real ~/.gaia.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
SCRIPTS_DIR = REPO_ROOT / "scripts"
GAIA_BIN = REPO_ROOT / "bin" / "gaia"

# Make the package + hooks importable exactly as the production entry points do
# (pre_tool_use.py / post_tool_use.py put hooks/ on sys.path so `modules.xxx`
# and `adapters.xxx` resolve; REPO_ROOT lets `gaia.*` and `tests.fixtures.*`
# resolve).
for _p in (str(REPO_ROOT), str(HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# AC-2 -- every hooks/ module imports on this platform
# ---------------------------------------------------------------------------

def _hook_module_names() -> list[str]:
    """Every importable module name under hooks/ (mirrors doctor's discovery in
    bin/cli/doctor.py::check_hooks_importable)."""
    names: list[str] = []
    for py_file in sorted(HOOKS_DIR.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel = py_file.relative_to(HOOKS_DIR)
        if rel.name == "__init__.py":
            parts = rel.parts[:-1]
            if not parts:
                continue
        else:
            parts = rel.with_suffix("").parts
        names.append(".".join(parts))
    return names


def phase_imports() -> int:
    """AC-2: import every hooks/ module; a single failure fails the phase.

    This is the direct, instant catcher for the fcntl regression (#5): on
    Windows the offending ``import fcntl`` raises at import of the module that
    (transitively) pulls in filelock, and this phase surfaces it by name.
    """
    module_names = _hook_module_names()
    failures: list[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 - report ANY import-time failure
            failures.append(f"{mod_name}: {type(exc).__name__}: {exc}")

    total = len(module_names)
    if failures:
        print(
            f"[AC-2] FAIL: {len(failures)}/{total} hook module(s) fail to "
            f"import on {sys.platform}:"
        )
        for line in failures:
            print(f"   - {line}")
        return 1
    print(f"[AC-2] PASS: {total}/{total} hook modules import on {sys.platform}")
    return 0


# ---------------------------------------------------------------------------
# AC-6 -- `gaia doctor` has no RED (error) checks
# ---------------------------------------------------------------------------

def phase_doctor(workspace: str) -> int:
    """AC-6: run `gaia doctor --json` and fail if any check is severity=error.

    Warnings are tolerated (doctor exits 1 on warnings, 2 on errors); the AC is
    "no checks in red", i.e. no *errors*. Parsing the JSON lets us hold that
    exact line instead of keying on the coarser process exit code.
    """
    cmd = [
        sys.executable, str(GAIA_BIN), "doctor", "--json",
        "--workspace", workspace,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("[AC-6] FAIL: `gaia doctor --json` did not emit parseable JSON.")
        print("  stdout:\n" + (proc.stdout or "<empty>"))
        print("  stderr:\n" + (proc.stderr or "<empty>"))
        return 1

    checks = data.get("checks", [])
    errors = [c for c in checks if c.get("severity") == "error"]
    warnings = [c for c in checks if c.get("severity") == "warning"]

    for c in checks:
        print(f"   [{c.get('severity','?'):7}] {c.get('name','?')}: {c.get('detail','')}")

    if errors:
        print(f"[AC-6] FAIL: {len(errors)} check(s) in RED on {sys.platform}:")
        for c in errors:
            print(f"   - {c.get('name')}: {c.get('detail')}  (fix: {c.get('fix','-')})")
        return 1
    print(
        f"[AC-6] PASS: 0 red checks on {sys.platform} "
        f"({len(warnings)} warning(s) tolerated, status={data.get('status')})"
    )
    return 0


# ---------------------------------------------------------------------------
# AC-8 -- runtime smoke: post_tool_use no-crash + T3 grant activation + FTS5
# ---------------------------------------------------------------------------

def _run_post_tool_use_critical_event(workspace: Path) -> tuple[bool, str]:
    """Drive hooks/post_tool_use.py with a synthetic critical-event payload.

    A successful ``git commit`` Bash result is a critical event, so the adapter
    routes it into SessionContextWriter.update_context() -> exclusive_file_lock
    -> the platform lock backend (fcntl on POSIX, msvcrt on Windows). If the
    Windows lock backend were broken the hook process would crash; we assert a
    clean exit and no traceback.
    """
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "winsmoke-posttool",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m \"winsmoke\""},
        "tool_response": {"output": "1 file changed", "exit_code": 0},
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(HOOKS_DIR), str(REPO_ROOT), env.get("PYTHONPATH", "")) if p
    )
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "post_tool_use.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(workspace),
        env=env,
        timeout=60,
    )
    ok = proc.returncode == 0 and "Traceback" not in proc.stderr
    detail = f"exit={proc.returncode}; stderr={proc.stderr.strip()[:400]}"
    return ok, detail


def _run_grant_cycle(workspace: Path) -> tuple[bool, str]:
    """Block -> activate -> retry a T3 command, verifying DB state at each step.

    Uses the production pre_tool_use entry point (via the committed harness) for
    block and retry, and activates the pending approval at the DB plane
    (``activate_db_pending_by_prefix``) -- deliberately NOT via any host-chosen
    activation route (risk R3). The retry runs under a DIFFERENT session id than
    the block to confirm the grant is session-agnostic, exactly as the shipped
    tests/integration/test_grant_cycle_no_session_env.py invariant requires.
    """
    from tests.fixtures.grant_cycle_harness import run_pre_tool_use_event
    from gaia.approvals.store import get_pending
    from modules.security.approval_grants import activate_db_pending_by_prefix

    command = "git push origin winsmoke"
    block_session = "winsmoke-block"
    retry_session = "winsmoke-retry"  # different on purpose

    # Phase 1: block (subagent context => structured deny + DB pending row).
    block = run_pre_tool_use_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": block_session,
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "agent_id": "a1234567",
        },
        cwd=workspace,
    )
    if block.permission_decision != "deny":
        return False, f"block: expected deny, got {block.permission_decision!r} (exit {block.exit_code}); stderr={block.stderr[:300]}"

    pending = get_pending(all_sessions=True)
    if len(pending) != 1:
        return False, f"block: expected exactly 1 pending approval in DB, got {len(pending)}"

    approval_id = pending[0]["id"]
    if not approval_id.startswith("P-"):
        return False, f"unexpected approval_id format: {approval_id!r}"

    # Phase 2: activate at the DB plane, under a DIFFERENT session (R3-safe).
    activation = activate_db_pending_by_prefix(
        approval_id[2:10], current_session_id=retry_session
    )
    if not getattr(activation, "success", False):
        return False, f"activate: failed status={getattr(activation,'status',None)!r} reason={getattr(activation,'reason',None)!r}"
    if get_pending(all_sessions=True):
        return False, "activate: pending row should be consumed after activation"

    # Phase 3: retry under the different session -> must be allowed through.
    retry = run_pre_tool_use_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": retry_session,
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "agent_id": "a1234567",
        },
        cwd=workspace,
    )
    if not retry.is_allowed:
        return False, f"retry: expected allow, got decision={retry.permission_decision!r} exit={retry.exit_code}; stderr={retry.stderr[:300]}"
    return True, "block -> DB pending -> DB activate -> cross-session retry allowed"


def _run_fts5_sync() -> tuple[bool, str]:
    """Write an episode and confirm the episodes_fts mirror is trigger-synced.

    Exercises FTS5 on the Windows stdlib sqlite3 build: insert_episode writes
    into ``episodes`` and the ``episodes_ai`` AFTER INSERT trigger must mirror
    the row into the ``episodes_fts`` virtual table. A MATCH query proves both
    that FTS5 is compiled in AND that the trigger fired.
    """
    import sqlite3

    from gaia.store.writer import insert_episode
    from gaia.paths import db_path

    marker = "winsmokefts5token"
    res = insert_episode(
        workspace="winsmoke",
        episode_id="winsmoke-ep-1",
        fields={
            "title": "windows smoke episode",
            "prompt": f"prompt {marker} body",
            "tags": "winsmoke",
        },
    )
    if res.get("status") != "applied":
        return False, f"insert_episode failed: {res}"

    con = sqlite3.connect(str(db_path()))
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM episodes_fts WHERE episodes_fts MATCH ?",
            (marker,),
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] < 1:
        return False, f"episodes_fts did not mirror the inserted row (MATCH count={row})"
    return True, "episode insert mirrored into episodes_fts via AFTER INSERT trigger"


def phase_smoke() -> int:
    """AC-8: the runtime smoke, in a fully isolated throwaway data dir."""
    tmp = Path(tempfile.mkdtemp(prefix="gaia_winsmoke_"))
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    workspace = tmp / "ws"
    # A workspace root for the hook to resolve. An empty .claude/ mirrors the
    # cwd the shipped grant-cycle integration test builds (its `_make_cwd`); it
    # is a hook cwd, not a fabricated install fixture.
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    db = data_dir / "gaia.db"

    # Point every DB consumer (writer, approvals store, hooks) at the isolated
    # data dir, and the bootstrapper at the same file.
    os.environ["GAIA_DATA_DIR"] = str(data_dir)
    os.environ["GAIA_DB"] = str(db)

    # Bootstrap the DB with NO sqlite3 CLI and NO bash (Windows path).
    boot = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "bootstrap_database.py")],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=180,
    )
    if boot.returncode != 0 or not db.is_file():
        print(f"[AC-8] FAIL: bootstrap_database.py exit={boot.returncode}, db_exists={db.is_file()}")
        print("  stdout:\n" + (boot.stdout or "<empty>"))
        print("  stderr:\n" + (boot.stderr or "<empty>"))
        return 1

    checks = [
        ("post_tool_use no-crash (filelock runtime)", lambda: _run_post_tool_use_critical_event(workspace)),
        ("T3 grant activation (DB plane, session-agnostic)", lambda: _run_grant_cycle(workspace)),
        ("FTS5 sync (episodes -> episodes_fts trigger)", _run_fts5_sync),
    ]

    failed = False
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        status = "PASS" if ok else "FAIL"
        print(f"   [{status}] {name}: {detail}")
        failed = failed or not ok

    if failed:
        print(f"[AC-8] FAIL: one or more runtime smoke checks failed on {sys.platform}")
        return 1
    print(f"[AC-8] PASS: post_tool_use + T3 grant activation + FTS5 sync all OK on {sys.platform}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gaia Windows-compatibility CI smoke.")
    parser.add_argument("phase", choices=["imports", "doctor", "smoke"])
    parser.add_argument("--workspace", default=None, help="workspace for the doctor phase")
    args = parser.parse_args(argv)

    if args.phase == "imports":
        return phase_imports()
    if args.phase == "doctor":
        if not args.workspace:
            print("doctor phase requires --workspace")
            return 2
        return phase_doctor(args.workspace)
    return phase_smoke()


if __name__ == "__main__":
    sys.exit(main())
