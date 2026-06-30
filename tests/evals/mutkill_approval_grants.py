#!/usr/bin/env python3
"""Fast, cosmic-ray-faithful mutation-kill harness (generic, any module).

PURPOSE
-------
Eval harness for an agentic hardening loop. The loop writes tests that kill
surviving mutants and re-runs this harness to measure progress. The harness
must be FAITHFUL to cosmic-ray: same mutants, same "kill" semantics. It is NOT
a reimplementation -- it reuses cosmic-ray's own machinery:

  * Mutation specs are read from the cosmic-ray session DB
    (table `mutation_specs`), which is the authoritative inventory cosmic-ray
    itself produced via `cosmic-ray init`.
  * Each mutant is applied + tested via cosmic_ray.mutating.mutate_and_test --
    the exact function at the heart of `cosmic-ray exec`. This guarantees
    byte-identical mutants and identical KILLED/SURVIVED/INCOMPETENT outcomes.
  * The test-command is read from the per-module toml (--toml), so it tracks
    whatever the loop configures there.
  * The module to mutate is derived from `module-path` in the toml (under
    [cosmic-ray]) -- no hardcoded module. Override with --module if needed.

It does NOT run `cosmic-ray exec` (T3). All work is local: a process pool of
workers, each operating in its own isolated clone of hooks/ + tests/ so that
concurrent on-disk mutation of the target module never collides. Cloning and
cleanup are done in-process via shutil (not shell verbs), so the harness itself
triggers no T3 approval.

OUTPUT
------
Prints a final block ending with EXACTLY one line:

    METRIC kill_rate=XX.XX

plus killed/survived/incompetent/total counts, for an agentic loop to parse.

The kill_rate matches cosmic-ray's `cr-rate`: killed / (total - incompetent).

ANTI-STALENESS RULE (AC-10)
---------------------------
A cosmic-ray session (.sqlite) stores mutation inventory + test results from
when `cosmic-ray init`/`exec` last ran. If you change test files or the source
module WITHOUT re-running `cosmic-ray init`, `cr-rate` will report a STALE
kill_rate (computed from old test results). This harness re-runs `mutate_and_test`
against the current tests on every invocation, so IT IS NEVER STALE -- but the
session's mutant inventory still comes from the last `cosmic-ray init`. If the
source module changed shape (lines shifted, functions renamed), the mutant specs
may no longer map correctly either.

Rule: **re-run `cosmic-ray init` after any change to test files, the source
module, or the test-command in the toml.** Use `--check-stale` to detect this.

See also: tests/evals/mutation-*.toml headers for the full re-init obligation.

USAGE
-----
    # approval_grants (original use-case -- defaults still work):
    uv run python tests/evals/mutkill_approval_grants.py --session approval-grants.sqlite --toml tests/evals/mutation-approval-grants.toml

    # blocked_commands (new -- module derived from toml):
    uv run python tests/evals/mutkill_approval_grants.py --session blocked-commands.sqlite --toml tests/evals/mutation-blocked-commands.toml

    # quick smoke:
    uv run python tests/evals/mutkill_approval_grants.py --session blocked-commands.sqlite --toml tests/evals/mutation-blocked-commands.toml --limit 3 --quiet

    # explicit module override:
    uv run python tests/evals/mutkill_approval_grants.py --session foo.sqlite --toml tests/evals/mutation-foo.toml --module hooks/modules/security/foo.py

    # staleness check only (no mutation run):
    uv run python tests/evals/mutkill_approval_grants.py --session approval-grants.sqlite --toml tests/evals/mutation-approval-grants.toml --check-stale
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# --------------------------------------------------------------------------
# Paths (resolved relative to this file -> gaia repo root)
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent              # tests/evals
REPO_ROOT = HERE.parent.parent                      # gaia/
# cosmic-ray session lives at the repo root (where `cosmic-ray init/exec` runs),
# per tests/evals/mutation-approval-grants.toml header. Fall back to tests/evals
# if a copy is placed alongside the toml.
_SESSION_ROOT = REPO_ROOT / "approval-grants.sqlite"
_SESSION_LOCAL = HERE / "approval-grants.sqlite"
DEFAULT_SESSION = _SESSION_ROOT if _SESSION_ROOT.exists() else _SESSION_LOCAL
DEFAULT_TOML = HERE / "mutation-approval-grants.toml"

# A worker clone must reproduce cosmic-ray's environment: it runs pytest with
# cwd = repo root, where `import gaia` (the ./gaia source package) and the
# tests/ fixtures resolve via cwd on sys.path. So the clone mirrors the whole
# working tree EXCEPT heavy/irrelevant dirs and the .claude security boundary.
# The mutated file path stays at module_rel (derived from toml) inside the clone.
CLONE_IGNORE_DIRS = frozenset({
    "node_modules", ".venv", ".git", "dist", "mutants",
    "logs", ".pytest_cache", "__pycache__", ".claude",
    "gaia.egg-info", ".mypy_cache", ".ruff_cache",
})


def _clone_ignore(dirpath, names):
    """shutil.copytree ignore callback: prune heavy/irrelevant dirs, pyc, and
    cosmic-ray session sqlites (large, not needed inside the clone)."""
    skip = set()
    for n in names:
        if n in CLONE_IGNORE_DIRS:
            skip.add(n)
        elif n.endswith((".pyc", ".sqlite")):
            skip.add(n)
    return skip


# --------------------------------------------------------------------------
# Faithful config readers
# --------------------------------------------------------------------------
def read_test_command(toml_path: Path) -> str:
    """Read test-command from the cosmic-ray per-module toml.

    Uses tomllib (3.11+) when available, else a minimal line scan. We only need
    the single `test-command` key under [cosmic-ray]; full TOML parsing is not
    required, but tomllib is preferred for fidelity.
    """
    text = toml_path.read_text(encoding="utf-8")
    try:
        import tomllib  # py311+
        data = tomllib.loads(text)
        cmd = data.get("cosmic-ray", {}).get("test-command")
        if cmd:
            return cmd
    except Exception:
        pass
    # Fallback: scan for `test-command = "..."`
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("test-command"):
            _, _, rhs = s.partition("=")
            return rhs.strip().strip('"').strip("'")
    raise SystemExit(f"Could not find test-command in {toml_path}")


def read_timeout(toml_path: Path, default: float = 30.0) -> float:
    text = toml_path.read_text(encoding="utf-8")
    try:
        import tomllib
        data = tomllib.loads(text)
        t = data.get("cosmic-ray", {}).get("timeout")
        if t is not None:
            return float(t)
    except Exception:
        pass
    return default


def read_module_path(toml_path: Path) -> str | None:
    """Read module-path from the cosmic-ray per-module toml.

    Returns the value of `[cosmic-ray] module-path` (a relative path such as
    ``hooks/modules/security/approval_grants.py`` or a package directory such
    as ``hooks/modules/security``), or None if the key is absent.
    """
    text = toml_path.read_text(encoding="utf-8")
    try:
        import tomllib  # py311+
        data = tomllib.loads(text)
        mp = data.get("cosmic-ray", {}).get("module-path")
        if mp:
            return str(mp)
    except Exception:
        pass
    # Fallback: scan for `module-path = "..."`
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("module-path"):
            _, _, rhs = s.partition("=")
            return rhs.strip().strip('"').strip("'")
    return None


def _extract_test_paths_from_command(test_command: str, repo_root: Path) -> list[Path]:
    """Extract test file paths from a pytest test-command string.

    Scans the command tokens for tokens that look like test paths (start with
    'tests/' or end with '.py', and exist on disk relative to repo_root).
    Returns a list of resolved Paths for files that actually exist.
    """
    paths = []
    for token in test_command.split():
        # Skip flags and the pytest invocation itself.
        if token.startswith("-") or token in ("python3", "-m", "pytest", "uv", "run"):
            continue
        candidate = repo_root / token
        if candidate.is_file():
            paths.append(candidate)
    return paths


def check_session_staleness(
    session_path: Path,
    toml_path: Path,
    module_rel: str | None,
    repo_root: Path,
) -> list[str]:
    """Compare session mtime against test files, source module, and toml.

    Returns a list of human-readable warning strings (empty = session is fresh).
    Each entry names the file that is newer than the session and explains why
    this matters (the session's recorded outcomes may not reflect current tests).

    Does NOT abort or raise; callers decide how to act on the warnings.
    """
    try:
        session_mtime = session_path.stat().st_mtime
    except OSError:
        return [f"cannot stat session file: {session_path}"]

    warnings: list[str] = []

    def _check(path: Path, label: str) -> None:
        try:
            m = path.stat().st_mtime
        except OSError:
            return
        if m > session_mtime:
            import datetime
            sess_ts = datetime.datetime.fromtimestamp(session_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            file_ts = datetime.datetime.fromtimestamp(m).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            warnings.append(
                f"STALE SESSION: {label} is newer than the session\n"
                f"  session mtime : {sess_ts}  ({session_path.name})\n"
                f"  file mtime    : {file_ts}  ({path})\n"
                f"  -> cr-rate results from `cosmic-ray exec` may not reflect\n"
                f"     the current tests. Re-run: cosmic-ray init <toml> <session>"
            )

    # 1. The toml itself (test-command or module-path may have changed).
    _check(toml_path, "toml config")

    # 2. The source module under mutation.
    if module_rel:
        module_path = repo_root / module_rel
        if module_path.is_file():
            _check(module_path, f"source module ({module_rel})")
        elif module_path.is_dir():
            # Package path: check all .py files in the package.
            for py in module_path.rglob("*.py"):
                _check(py, f"source module ({py.relative_to(repo_root)})")

    # 3. Test files referenced by the test-command in the toml.
    try:
        test_command = read_test_command(toml_path)
        for tp in _extract_test_paths_from_command(test_command, repo_root):
            _check(tp, f"test file ({tp.relative_to(repo_root)})")
    except SystemExit:
        pass  # toml missing test-command; skip test-file checks

    return warnings


def load_specs(session_path: Path):
    """Load mutation specs from the cosmic-ray session DB.

    Returns a list of plain dicts (picklable for the process pool). Each row is
    the authoritative spec cosmic-ray itself generated; we reconstruct the
    MutationSpec inside the worker so we don't depend on cosmic_ray being
    importable in the parent before fork semantics matter.
    """
    con = sqlite3.connect(f"file:{session_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT job_id, module_path, operator_name, operator_args, occurrence,
                   start_pos_row, start_pos_col, end_pos_row, end_pos_col,
                   definition_name
            FROM mutation_specs
            ORDER BY start_pos_row, start_pos_col, occurrence
            """
        ).fetchall()
    finally:
        con.close()
    specs = []
    for r in rows:
        # operator_args is stored double-encoded: SQLAlchemy's Column(JSON)
        # serializes the value, and cosmic-ray's work_db json.dumps()'d it
        # before that. cosmic-ray reads it back with two json.loads layers.
        # We read raw TEXT via sqlite3, so decode repeatedly until we land on
        # a dict (the operator-args mapping mutate_and_test expects).
        args = r["operator_args"]
        for _ in range(3):
            if isinstance(args, dict):
                break
            if args is None or args == "":
                args = {}
                break
            try:
                args = json.loads(args)
            except Exception:
                args = {}
                break
        if not isinstance(args, dict):
            args = {}
        specs.append(
            {
                "job_id": r["job_id"],
                "operator_name": r["operator_name"],
                "operator_args": args,
                "occurrence": r["occurrence"],
                "start_pos": (r["start_pos_row"], r["start_pos_col"]),
                "end_pos": (r["end_pos_row"], r["end_pos_col"]),
                "definition_name": r["definition_name"],
            }
        )
    return specs


def load_baseline_survivor_ids(session_path: Path) -> set:
    """job_ids of mutants the cosmic-ray session recorded as SURVIVED.

    These are the mutants the hardening loop is trying to kill. Restricting to
    them gives a fast incremental eval (the kill_rate over the survivor subset =
    fraction of the original survivors that new tests now kill)."""
    con = sqlite3.connect(f"file:{session_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT job_id FROM work_results WHERE test_outcome = 'SURVIVED'"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


# --------------------------------------------------------------------------
# Stable mutant identity (re-init-proof skip matching)
# --------------------------------------------------------------------------
# WHY: `cosmic-ray init` regenerates a fresh random uuid4 `job_id` for every
# mutant on every run. The equivalents-*.skip files used to list bare job_ids,
# so after a re-init those ids matched NOTHING in the new session and silently
# excluded zero mutants -- producing a false "100% killable" (the skip
# appeared to apply but did not). The fix is to key exclusions on the mutant's
# STABLE identity, which `cosmic-ray init` preserves byte-for-byte because it
# is derived from the source AST, not from a uuid:
#
#     operator_name | start_row:start_col-end_row:end_col | occurrence
#
# A skip-file line may now be EITHER:
#   * a stable-id token (preferred, re-init-proof), or
#   * a legacy 32-hex job_id (still honored for backward compatibility, but
#     fragile across re-init -- emit a stable id instead going forward).

# A bare cosmic-ray job_id is a uuid4 hex: exactly 32 lowercase hex chars.
import re as _re  # local alias; module already imports re-free
_JOB_ID_RE = _re.compile(r"^[0-9a-f]{32}$")


def stable_id(spec: dict) -> str:
    """Canonical, re-init-proof identity for a mutation spec.

    Built from fields `cosmic-ray init` reproduces deterministically from the
    source AST: the operator, the exact source span, and the occurrence index.
    The same source + same operator always yields the same stable_id, even
    though the job_id (uuid4) differs on every init.
    """
    sr, sc = spec["start_pos"]
    er, ec = spec["end_pos"]
    op = spec["operator_name"]
    occ = spec["occurrence"]
    return f"{op}|{sr}:{sc}-{er}:{ec}|{occ}"


def parse_skip_file(skip_path: Path) -> tuple[set, set]:
    """Parse a skip-file into (stable_ids, legacy_job_ids).

    Non-comment, non-blank lines are classified: a line matching the 32-hex
    job_id shape goes to the legacy set; everything else is treated as a
    stable-id token. Both sets are matched against the current session in
    `compute_skip_jobids`.
    """
    stable: set[str] = set()
    legacy: set[str] = set()
    for raw_line in skip_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # A skip line may carry a trailing inline note after whitespace; the
        # first whitespace-delimited token is the id. (Stable ids contain no
        # spaces; job_ids are bare hex.)
        token = line.split()[0]
        if _JOB_ID_RE.match(token):
            legacy.add(token)
        else:
            stable.add(token)
    return stable, legacy


def compute_skip_jobids(specs: list, stable: set, legacy: set) -> tuple[set, list]:
    """Resolve skip-file tokens to the CURRENT session's job_ids.

    Returns (jobids_to_exclude, unmatched_tokens). A stable id is matched by
    recomputing stable_id() for each spec; a legacy job_id is matched directly.
    Unmatched tokens are returned so the caller can warn (a stale legacy id, or
    a stable id whose source span moved, is a signal the skip needs re-keying).
    """
    by_stable: dict = {}
    by_jobid: set = set()
    for s in specs:
        by_stable[stable_id(s)] = s["job_id"]
        by_jobid.add(s["job_id"])
    exclude: set = set()
    unmatched: list = []
    for sid in stable:
        jid = by_stable.get(sid)
        if jid is not None:
            exclude.add(jid)
        else:
            unmatched.append(sid)
    for jid in legacy:
        if jid in by_jobid:
            exclude.add(jid)
        else:
            unmatched.append(jid)
    return exclude, unmatched


# --------------------------------------------------------------------------
# Worker: runs a shard of specs inside its own clone
# --------------------------------------------------------------------------
def _clone_repo_subset(dst: Path) -> None:
    """Clone the repo working tree (minus heavy/irrelevant dirs and .claude)
    into dst, preserving the relative layout cosmic-ray's pytest run depends on
    (import gaia, tests/ fixtures, conftest path resolution)."""
    shutil.copytree(
        REPO_ROOT,
        dst,
        symlinks=True,
        ignore=_clone_ignore,
        dirs_exist_ok=True,
    )


def _run_shard(shard, test_command: str, timeout: float, keep_clone: bool,
               module_rel: str):
    """Run a shard of mutation specs in an isolated clone. Returns list of
    (job_id, operator_name, outcome_str).

    ``module_rel`` is the repo-relative path to the module being mutated,
    derived from ``[cosmic-ray] module-path`` in the toml (or --module).
    """
    # cosmic_ray must be importable inside the worker (uv-managed venv).
    from cosmic_ray.work_item import MutationSpec
    from cosmic_ray.mutating import mutate_and_test, TestOutcome

    clone = Path(tempfile.mkdtemp(prefix="mutkill_wk_"))
    results = []
    try:
        _clone_repo_subset(clone)
        module_path = str(clone / module_rel)
        # Run tests from the clone root so relative path resolution in tests +
        # conftest binds to the clone's hooks/, not the live repo.
        prev_cwd = os.getcwd()
        os.chdir(clone)
        # Ensure mutated source is re-read, never cached as .pyc.
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            for spec in shard:
                ms = MutationSpec(
                    module_path=module_path,
                    operator_name=spec["operator_name"],
                    occurrence=spec["occurrence"],
                    start_pos=spec["start_pos"],
                    end_pos=spec["end_pos"],
                    operator_args=spec["operator_args"],
                    definition_name=spec["definition_name"],
                )
                wr = mutate_and_test([ms], test_command, timeout)
                outcome = wr.test_outcome
                # WorkerOutcome NO_TEST -> mutation not applicable; mirror
                # cosmic-ray which records such as no-op (skipped). We map a
                # None test_outcome to "skipped".
                if outcome is None:
                    label = "skipped"
                else:
                    label = TestOutcome(outcome).value  # survived/killed/incompetent
                results.append((spec["job_id"], spec["operator_name"], label))
        finally:
            os.chdir(prev_cwd)
    finally:
        if not keep_clone:
            shutil.rmtree(clone, ignore_errors=True)
    return results


def _chunk(seq, n):
    """Split seq into n round-robin shards (balances long-running operators)."""
    shards = [[] for _ in range(n)]
    for i, item in enumerate(seq):
        shards[i % n].append(item)
    return [s for s in shards if s]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", type=Path, default=DEFAULT_SESSION,
                    help="cosmic-ray session sqlite (read-only)")
    ap.add_argument("--toml", type=Path, default=DEFAULT_TOML,
                    help="cosmic-ray per-module toml (for test-command/timeout/module-path)")
    ap.add_argument("--module", type=str, default=None,
                    help="repo-relative path to the module to mutate "
                         "(overrides module-path from toml; required when toml "
                         "lacks a module-path key)")
    ap.add_argument("-j", "--jobs", type=int, default=min(8, os.cpu_count() or 4),
                    help="parallel workers (each gets an isolated clone)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only run first N specs (smoke); 0 = all")
    ap.add_argument("--operators", nargs="*", default=None,
                    help="restrict to these operator_name values (incremental loop use)")
    ap.add_argument("--only-survivors", action="store_true",
                    help="restrict to the mutants that SURVIVED in the cosmic-ray "
                         "session DB. Fast incremental loop mode: only re-test the "
                         "476 baseline survivors, the population the loop is trying "
                         "to kill. kill_rate is then computed over that subset.")
    ap.add_argument("--timeout", type=float, default=None,
                    help="per-mutant test timeout (default: from toml)")
    ap.add_argument("--keep-clones", action="store_true",
                    help="do not delete worker clones (debug)")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress per-survivor listing")
    ap.add_argument("--skip-file", type=Path, default=None,
                    help="path to a file with job_ids to exclude from the "
                         "kill-rate denominator (one per line; lines starting "
                         "with '#' are ignored). Excluded mutants are not "
                         "counted in total, killed, or survived — the kill_rate "
                         "is measured over the remaining killable population.")
    ap.add_argument("--dump-json", type=Path, default=None,
                    help="write per-mutant {job_id: outcome} to this file "
                         "(fidelity cross-check against cosmic-ray session)")
    ap.add_argument("--check-stale", action="store_true",
                    help="compare session mtime against test files, source module, "
                         "and toml; warn on stderr if any are newer than the session "
                         "(= cr-rate results may be stale). When used alone (without "
                         "running mutants), exit 0 if fresh, exit 1 if stale.")
    args = ap.parse_args()

    if not args.session.exists():
        raise SystemExit(f"session not found: {args.session}")
    if not args.toml.exists():
        raise SystemExit(f"toml not found: {args.toml}")

    # AC-10 staleness guard: always run (warns on stderr); --check-stale exits
    # after the check without running any mutants (exit 1 = stale, 0 = fresh).
    _stale_module = args.module if args.module else read_module_path(args.toml)
    stale_warnings = check_session_staleness(
        args.session, args.toml, _stale_module, REPO_ROOT
    )
    if stale_warnings:
        print("", file=sys.stderr)
        print("WARNING: cosmic-ray session may be STALE (AC-10 anti-staleness rule):",
              file=sys.stderr)
        for w in stale_warnings:
            print(f"  {w}", file=sys.stderr)
        print("  -> Use `mutkill` (this harness) for fresh kill_rate; "
              "re-init session before using `cr-rate`.", file=sys.stderr)
        print("", file=sys.stderr)
        if args.check_stale:
            raise SystemExit(1)
    else:
        if args.check_stale:
            print("session is FRESH (no staleness detected)", file=sys.stderr)
            raise SystemExit(0)

    # Derive the module to mutate: --module wins; fall back to module-path in toml.
    module_rel: str
    if args.module:
        module_rel = args.module
    else:
        mp = read_module_path(args.toml)
        if not mp:
            raise SystemExit(
                f"Could not find module-path in {args.toml}. "
                "Pass --module <repo-relative-path> explicitly."
            )
        module_rel = mp

    # Parse equivalent-mutant exclusions (AC-5 skip list). Tokens are resolved
    # to the CURRENT session's job_ids by STABLE identity below -- not by the
    # raw job_id, which cosmic-ray regenerates on every init.
    skip_stable: set[str] = set()
    skip_legacy: set[str] = set()
    if args.skip_file is not None:
        if not args.skip_file.exists():
            raise SystemExit(f"skip-file not found: {args.skip_file}")
        skip_stable, skip_legacy = parse_skip_file(args.skip_file)

    test_command = read_test_command(args.toml)
    timeout = args.timeout if args.timeout is not None else read_timeout(args.toml)

    specs = load_specs(args.session)
    if args.only_survivors:
        survivor_ids = load_baseline_survivor_ids(args.session)
        specs = [s for s in specs if s["job_id"] in survivor_ids]
    if args.operators:
        wanted = set(args.operators)
        specs = [s for s in specs if s["operator_name"] in wanted]
    # Resolve skip tokens to current-session job_ids by stable identity, then
    # remove the matched mutants from the population entirely.
    skip_ids: set[str] = set()
    if skip_stable or skip_legacy:
        skip_ids, unmatched = compute_skip_jobids(specs, skip_stable, skip_legacy)
        if unmatched:
            print(
                f"# WARNING: {len(unmatched)} skip-file token(s) matched NO mutant "
                f"in this session (stale id, or source span moved -- re-key the "
                f"skip-file):",
                file=sys.stderr,
            )
            for tok in sorted(unmatched):
                print(f"#   unmatched: {tok}", file=sys.stderr)
        specs = [s for s in specs if s["job_id"] not in skip_ids]
    if args.limit:
        specs = specs[: args.limit]

    total = len(specs)
    if total == 0:
        raise SystemExit("no specs selected")

    jobs = max(1, min(args.jobs, total))
    shards = _chunk(specs, jobs)

    print(f"# mutkill harness", file=sys.stderr)
    print(f"# module        : {module_rel}", file=sys.stderr)
    print(f"# session       : {args.session}", file=sys.stderr)
    print(f"# test-command  : {test_command}", file=sys.stderr)
    if skip_ids:
        print(f"# skip-file     : {args.skip_file}  ({len(skip_ids)} equivalents excluded)",
              file=sys.stderr)
    print(f"# timeout       : {timeout}s   workers: {jobs}   mutants: {total}",
          file=sys.stderr)

    t0 = time.time()
    all_results = []
    if jobs == 1:
        all_results.extend(_run_shard(shards[0], test_command, timeout,
                                      args.keep_clones, module_rel))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = [ex.submit(_run_shard, sh, test_command, timeout,
                              args.keep_clones, module_rel)
                    for sh in shards]
            done = 0
            for fut in as_completed(futs):
                part = fut.result()
                all_results.extend(part)
                done += 1
                print(f"# shard {done}/{len(futs)} done "
                      f"({len(all_results)}/{total} mutants, "
                      f"{time.time()-t0:.1f}s)", file=sys.stderr)
    elapsed = time.time() - t0

    killed = sum(1 for _, _, o in all_results if o == "killed")
    survived = sum(1 for _, _, o in all_results if o == "survived")
    incompetent = sum(1 for _, _, o in all_results if o == "incompetent")
    skipped = sum(1 for _, _, o in all_results if o == "skipped")
    n = len(all_results)

    # cr-rate semantics: killed / (total - incompetent - skipped). Survivors
    # and killed are the testable population; incompetent/skipped are excluded.
    denom = n - incompetent - skipped
    kill_rate = (killed / denom * 100.0) if denom else 0.0

    print()
    print("=" * 64)
    module_label = Path(module_rel).name
    print(f"MUTATION KILL REPORT  ({module_label})")
    if skip_ids:
        print(f"  equivalents excl  : {len(skip_ids)}  (AC-5 skip list, not in denom)")
    print(f"  total mutants     : {n}  (killable population)")
    print(f"  killed            : {killed}")
    print(f"  survived          : {survived}")
    print(f"  incompetent       : {incompetent}")
    if skipped:
        print(f"  skipped (no-op)   : {skipped}")
    print(f"  testable (denom)  : {denom}")
    print(f"  elapsed           : {elapsed:.1f}s")
    print("=" * 64)

    if not args.quiet and survived:
        print(f"\n# {survived} SURVIVORS (job_id  operator):")
        for jid, op, o in sorted(all_results, key=lambda r: r[1]):
            if o == "survived":
                print(f"  {jid}  {op}")

    if args.dump_json:
        args.dump_json.write_text(
            json.dumps({jid: o for jid, _, o in all_results}, indent=0),
            encoding="utf-8",
        )

    # The single machine-parseable line the loop greps for.
    print(f"METRIC kill_rate={kill_rate:.2f}")
    print(f"METRIC killed={killed} survived={survived} "
          f"incompetent={incompetent} skipped={skipped} total={n}")


if __name__ == "__main__":
    main()
