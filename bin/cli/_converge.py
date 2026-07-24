"""Shared drift-free convergence primitives for `gaia dev` and `gaia release`.

Both `gaia dev` (origin = the local SOURCE tree) and `gaia release` (origin = a
shipped ARTIFACT) must leave a destination's install surfaces converged on the
command's origin. There is NO `--from` flag: the COMMAND is the origin, so this
module never guesses a source -- the caller passes an `origin` descriptor.

This module owns the READ half of the convergence routine: it inspects the 5
install surfaces of a destination workspace and classifies each into one of the
3 idempotency cases the design names --

    * ALIGNED -- the surface already matches the origin (a no-op reconcile);
    * STALE   -- the surface exists but diverges from the origin (reconcile
                 rewrites it WITHOUT destroying user state);
    * ABSENT  -- the surface is not installed yet (reconcile creates it);

plus a 4th UNKNOWN degrade when a surface cannot be inspected (never a false
ALIGNED). The DB surface additionally carries a schema-DIRECTION verdict
(forward / reverse / aligned) mirroring the bootstrap direction guard.

The 5 surfaces (design):
  1. PATH `gaia` (bare, unqualified invocation)
  2. hooks wired into `.claude/settings.local.json`
  3. workspace `node_modules/@jaguilar87/gaia`
  4. global npm install (`~/.npm-global` / npm prefix)
  5. DB schema (`~/.gaia/gaia.db` schema_version vs the code's EXPECTED)

The WRITE half (the actual reconcile) lives in the install actors, not here:
dev's pack/install/wire, `install.reconcile_global_via_npm_link` (surface 4),
and `scripts/bootstrap_database.py`'s forward migration + direction guard
(surface 5). Keeping inspection here -- pure, read-only, dependency-light --
lets `gaia dev`, `gaia release`, and `gaia doctor` share ONE classification of
"where does the destination stand relative to the origin?" without duplicating
it three ways.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

# Surface identifiers (stable keys for reports/tests).
SURFACE_PATH_GAIA = "path_gaia"
SURFACE_HOOKS_SETTINGS = "hooks_settings"
SURFACE_WORKSPACE_NODE_MODULES = "workspace_node_modules"
SURFACE_GLOBAL_NPM = "global_npm"
SURFACE_DB_SCHEMA = "db_schema"

# Idempotency states (the 3 cases + a degrade).
STATE_ALIGNED = "aligned"
STATE_STALE = "stale"
STATE_ABSENT = "absent"
STATE_UNKNOWN = "unknown"

# Schema-direction verdicts (surface 5).
DIR_ALIGNED = "aligned"
DIR_FORWARD = "forward"   # code ahead of DB -> forward migration pending
DIR_REVERSE = "reverse"   # code behind DB -> the finalize-breaking drift
DIR_ABSENT = "absent"

_NPM_PACKAGE = "@jaguilar87/gaia"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _surface(surface: str, state: str, detail: str, **extra) -> dict:
    r = {"surface": surface, "state": state, "detail": detail}
    r.update(extra)
    return r


# ---------------------------------------------------------------------------
# Surface 1 -- PATH gaia
# ---------------------------------------------------------------------------

def inspect_path_gaia(origin_version: "str | None") -> dict:
    """Classify the bare-`gaia`-on-PATH surface against the origin's version.

    ABSENT   -- no `gaia` resolvable on PATH.
    UNKNOWN  -- resolves but its version cannot be read (nothing to compare).
    ALIGNED  -- PATH gaia's version == origin_version (or origin unknown, so no
                divergence can be proven).
    STALE    -- PATH gaia resolves to a DIFFERENT version than the origin.
    """
    which = shutil.which("gaia")
    if not which:
        return _surface(SURFACE_PATH_GAIA, STATE_ABSENT, "no `gaia` on PATH")
    try:
        resolved = Path(which).resolve(strict=True)
    except OSError:
        return _surface(SURFACE_PATH_GAIA, STATE_UNKNOWN, f"`gaia` on PATH does not resolve ({which})")
    pkg = _read_json(resolved.parent.parent / "package.json")
    version = pkg.get("version") if pkg else None
    if version is None:
        return _surface(SURFACE_PATH_GAIA, STATE_UNKNOWN, f"PATH gaia at {resolved} (version unknown)")
    if origin_version is None or version == origin_version:
        return _surface(
            SURFACE_PATH_GAIA, STATE_ALIGNED,
            f"PATH gaia v{version}", path=str(resolved), version=version,
        )
    return _surface(
        SURFACE_PATH_GAIA, STATE_STALE,
        f"PATH gaia v{version} != origin v{origin_version}",
        path=str(resolved), version=version,
    )


# ---------------------------------------------------------------------------
# Surface 2 -- hooks in .claude/settings.local.json
# ---------------------------------------------------------------------------

def inspect_hooks_settings(workspace: Path) -> dict:
    """Classify the workspace's wired-hooks surface.

    ABSENT  -- no settings.local.json.
    STALE   -- settings.local.json exists but carries no `hooks` section
               (present-but-unwired -> reconcile merges hooks in).
    ALIGNED -- settings.local.json carries a non-empty `hooks` section.
    UNKNOWN -- settings.local.json is unreadable/invalid JSON.
    """
    settings = Path(workspace) / ".claude" / "settings.local.json"
    if not settings.is_file():
        return _surface(SURFACE_HOOKS_SETTINGS, STATE_ABSENT, "no .claude/settings.local.json")
    data = _read_json(settings)
    if data is None:
        return _surface(SURFACE_HOOKS_SETTINGS, STATE_UNKNOWN, "settings.local.json unreadable/invalid")
    hooks = data.get("hooks")
    if hooks:
        return _surface(
            SURFACE_HOOKS_SETTINGS, STATE_ALIGNED,
            f"{len(hooks)} hook event(s) wired",
        )
    return _surface(SURFACE_HOOKS_SETTINGS, STATE_STALE, "settings.local.json present but no hooks wired")


# ---------------------------------------------------------------------------
# Surface 3 -- workspace node_modules/@jaguilar87/gaia
# ---------------------------------------------------------------------------

def inspect_workspace_node_modules(workspace: Path, origin_version: "str | None") -> dict:
    """Classify the workspace-local node_modules install against the origin.

    ABSENT  -- no node_modules/@jaguilar87/gaia package.json.
    UNKNOWN -- installed but version unreadable.
    ALIGNED -- installed version == origin_version (or origin unknown).
    STALE   -- installed version differs from the origin.
    """
    nm = Path(workspace) / "node_modules" / "@jaguilar87" / "gaia"
    pkg = _read_json(nm / "package.json")
    if not pkg:
        return _surface(SURFACE_WORKSPACE_NODE_MODULES, STATE_ABSENT, "no node_modules/@jaguilar87/gaia")
    version = pkg.get("version")
    if version is None:
        return _surface(SURFACE_WORKSPACE_NODE_MODULES, STATE_UNKNOWN, "node_modules install version unknown")
    if origin_version is None or version == origin_version:
        return _surface(
            SURFACE_WORKSPACE_NODE_MODULES, STATE_ALIGNED,
            f"node_modules install v{version}", version=version,
        )
    return _surface(
        SURFACE_WORKSPACE_NODE_MODULES, STATE_STALE,
        f"node_modules install v{version} != origin v{origin_version}", version=version,
    )


# ---------------------------------------------------------------------------
# Surface 4 -- global npm install
# ---------------------------------------------------------------------------

def inspect_global_npm(npm_global_bin: "Path | None", origin_version: "str | None") -> dict:
    """Classify the global npm `gaia` surface against the origin.

    *npm_global_bin* is the directory where npm writes the global `gaia` shim
    (install._npm_global_prefix()); passing it in keeps this pure/testable.

    ABSENT  -- no global bin dir, or no `gaia` shim in it.
    UNKNOWN -- a global `gaia` exists but its version cannot be read.
    ALIGNED -- global version == origin_version (or origin unknown).
    STALE   -- global version differs from the origin (the stale-global-shadow
               case the npm-link reconcile of surface 4 closes).
    """
    if npm_global_bin is None:
        return _surface(SURFACE_GLOBAL_NPM, STATE_ABSENT, "npm global bin dir not resolved")
    shim = Path(npm_global_bin) / "gaia"
    if not shim.exists():
        return _surface(SURFACE_GLOBAL_NPM, STATE_ABSENT, "no global `gaia` shim in npm bin")
    try:
        resolved = shim.resolve(strict=True)
    except OSError:
        return _surface(SURFACE_GLOBAL_NPM, STATE_UNKNOWN, "global `gaia` shim does not resolve")
    pkg = _read_json(resolved.parent.parent / "package.json")
    version = pkg.get("version") if pkg else None
    if version is None:
        return _surface(SURFACE_GLOBAL_NPM, STATE_UNKNOWN, f"global gaia at {resolved} (version unknown)")
    if origin_version is None or version == origin_version:
        return _surface(
            SURFACE_GLOBAL_NPM, STATE_ALIGNED,
            f"global gaia v{version}", version=version,
        )
    return _surface(
        SURFACE_GLOBAL_NPM, STATE_STALE,
        f"global gaia v{version} != origin v{origin_version}", version=version,
    )


# ---------------------------------------------------------------------------
# Surface 5 -- DB schema (with direction)
# ---------------------------------------------------------------------------

def inspect_db_schema(expected_version: int, db_path: Path) -> dict:
    """Classify the DB schema surface AND the migration direction.

    ABSENT           -- no DB file yet (fresh machine).
    UNKNOWN          -- DB unreadable, or no schema_version table (legacy DB).
    ALIGNED  (DIR_ALIGNED)  -- live == expected.
    STALE    (DIR_FORWARD)  -- live < expected: code ahead, forward migration
                               pending (safe -- bootstrap migrates forward).
    STALE    (DIR_REVERSE)  -- live > expected: code BEHIND the DB, the
                               finalize-breaking drift the bootstrap direction
                               guard REFUSES (never install code older than DB).

    The `direction` key carries the DIR_* verdict; state is STALE for both
    forward and reverse (both diverge from the origin), so a caller that only
    reads `state` still sees the skew, while one that reads `direction`
    distinguishes the safe-forward case from the refuse-reverse case.
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return _surface(SURFACE_DB_SCHEMA, STATE_ABSENT, f"no DB at {db_path}", direction=DIR_ABSENT)
    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        return _surface(SURFACE_DB_SCHEMA, STATE_UNKNOWN, f"could not open DB: {exc}", direction=DIR_ABSENT)
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'")
        if cur.fetchone() is None:
            return _surface(
                SURFACE_DB_SCHEMA, STATE_UNKNOWN,
                "schema_version table missing (legacy DB)", direction=DIR_ABSENT,
            )
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
        live = cur.fetchone()[0]
    except sqlite3.Error as exc:
        return _surface(SURFACE_DB_SCHEMA, STATE_UNKNOWN, f"could not read schema_version: {exc}", direction=DIR_ABSENT)
    finally:
        con.close()

    if live == expected_version:
        return _surface(
            SURFACE_DB_SCHEMA, STATE_ALIGNED,
            f"schema v{live} == code expectation", direction=DIR_ALIGNED, live=live,
        )
    if live < expected_version:
        return _surface(
            SURFACE_DB_SCHEMA, STATE_STALE,
            f"schema v{live} < code expects v{expected_version} (forward migration pending)",
            direction=DIR_FORWARD, live=live,
        )
    return _surface(
        SURFACE_DB_SCHEMA, STATE_STALE,
        f"schema v{live} > code expects v{expected_version} "
        f"(code BEHIND DB -- reverse-direction drift; install would be refused)",
        direction=DIR_REVERSE, live=live,
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def converge_report(
    workspace: Path,
    *,
    origin_version: "str | None",
    expected_version: int,
    db_path: Path,
    npm_global_bin: "Path | None",
) -> dict:
    """Inspect all 5 surfaces of *workspace* against the origin and aggregate.

    Returns ``{"surfaces": [<per-surface dict>...], "converged": bool,
    "reverse_direction": bool}`` where:
      * ``converged`` is True iff every surface is ALIGNED (or ABSENT, which a
        reconcile creates cleanly -- the not-installed idempotency case);
      * ``reverse_direction`` is True iff the DB surface is the refuse-reverse
        case, so a caller can hard-stop before an install that would be refused.

    Pure/read-only: every external dependency (PATH, npm bin, DB path) is passed
    in or resolved read-only, so the aggregate is deterministic and testable.
    """
    surfaces = [
        inspect_path_gaia(origin_version),
        inspect_hooks_settings(workspace),
        inspect_workspace_node_modules(workspace, origin_version),
        inspect_global_npm(npm_global_bin, origin_version),
        inspect_db_schema(expected_version, db_path),
    ]
    reverse = any(
        s["surface"] == SURFACE_DB_SCHEMA and s.get("direction") == DIR_REVERSE
        for s in surfaces
    )
    # STALE or UNKNOWN means not-converged; ALIGNED and ABSENT are both clean
    # reconcile inputs (ABSENT = the not-installed case a reconcile creates).
    converged = all(s["state"] in (STATE_ALIGNED, STATE_ABSENT) for s in surfaces)
    return {"surfaces": surfaces, "converged": converged, "reverse_direction": reverse}


# ---------------------------------------------------------------------------
# Shared driver -- ONE inspect + format + degrade path for dev AND release
# ---------------------------------------------------------------------------
#
# `converge_report` above is the PURE aggregate (every external dependency
# injected). The three helpers below are the shared DRIVER both callers use so
# the "resolve the DB path, inspect, format the report, degrade on failure"
# sequence is written ONCE, not once in `gaia dev` and again in `gaia release`.
# They stay import-light: the caller still resolves `expected_version` (from
# `cli.doctor`) and `npm_global_bin` (from `cli.install`) and injects them, so
# this module never imports doctor/install and stays a leaf.

def default_db_path() -> Path:
    """The gaia.db path both callers inspect: ``$GAIA_DB`` or ``~/.gaia/gaia.db``.

    Written once here so `gaia dev` and `gaia release` resolve the SAME DB path
    for the schema-direction surface rather than each spelling out the env
    lookup (and risking a drift between them).
    """
    return Path(
        os.environ.get("GAIA_DB", str(Path("~/.gaia/gaia.db").expanduser()))
    ).expanduser()


def format_convergence_report(report: dict, origin_version: "str | None") -> list[str]:
    """Render a `converge_report` result into display lines (pure -- no I/O).

    The shared human-facing rendering of the 5-surface convergence: a header
    naming the verdict + origin, one line per surface, and -- when the DB is the
    refuse-reverse case -- the reverse-direction warning. Returned as lines so a
    caller can print them (dev), fold them into a gate `detail` (release), or
    assert on them (tests) without this module owning the sink.
    """
    verdict = "CONVERGED" if report.get("converged") else "SKEW"
    lines = [f"\n  convergence ({verdict}) -- 5 surfaces vs origin v{origin_version or '?'}:"]
    for s in report.get("surfaces", []):
        lines.append(f"    [{s['state']:<7}] {s['surface']:<22} {s['detail']}")
    if report.get("reverse_direction"):
        lines.append(
            "  [!] DB is NEWER than this code (reverse-direction drift): an install "
            "would be REFUSED. Use a newer origin; do NOT downgrade the DB."
        )
    return lines


def run_convergence_report(
    workspace: Path,
    *,
    origin_version: "str | None",
    expected_version: int,
    db_path: Path,
    npm_global_bin: "Path | None",
    quiet: bool = False,
    emit=print,
) -> dict:
    """Inspect *workspace*'s 5 surfaces vs the origin, report, and never raise.

    The shared driver `gaia dev` calls after its reconcile (origin = the local
    SOURCE) and `gaia release` calls as a pre-release gate (origin = the release
    ARTIFACT). Wraps `converge_report` in a best-effort guard: on any inspection
    failure it returns a degraded ``{"surfaces": [], "converged": False,
    "reverse_direction": False, "error": <str>}`` instead of propagating, so an
    inspection error never breaks the dev loop or aborts a release run. When not
    *quiet*, prints the `format_convergence_report` lines (or the failure note)
    via *emit*. Returns the raw report dict for callers/tests.
    """
    try:
        report = converge_report(
            workspace,
            origin_version=origin_version,
            expected_version=expected_version,
            db_path=db_path,
            npm_global_bin=npm_global_bin,
        )
    except Exception as exc:  # never let inspection break the caller
        if not quiet:
            emit(f"  [?] convergence: inspection failed ({exc})")
        return {"surfaces": [], "converged": False, "reverse_direction": False, "error": str(exc)}

    if not quiet:
        for line in format_convergence_report(report, origin_version):
            emit(line)
    return report
