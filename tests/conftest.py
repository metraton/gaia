"""
Root conftest.py - Shared test infrastructure for gaia.

Provides:
- Custom markers: llm, e2e (auto-skipped in default test runs)
- Session fixtures: package_root, agents_dir, skills_dir, config_dir, hooks_dir
- Frontmatter parser (manual, no PyYAML dependency)
- DB fixture helpers: temp_gaia_db, seed_workspace, seed_workspace_contracts,
  seed_agent_perms (shared across integration/unit/performance tests)
"""

import os
import shutil
import site
import tempfile
from collections.abc import MutableMapping
import pytest
from pathlib import Path


class IsolatedRuntimeEnv(MutableMapping):
    """Expose only runtime PATH and controlled test paths; never render values."""

    def __init__(self, root):
        root = Path(root)
        for directory in (root, root / "home", root / "tmp"):
            directory.mkdir(parents=True, exist_ok=True)
        self._values = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(root / "home"),
            "TMPDIR": str(root / "tmp"),
            "GAIA_DATA_DIR": str(root),
            "GAIA_DB": str(root / "gaia.db"),
            "GAIA_OPENCODE_ATTESTATION_DIR": str(root / "ledger"),
            "WORKSPACE": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def __getitem__(self, key):
        return self._values[key]

    def prepare_hook_workspace(self):
        """Opt bridge-backed tests into a real private cwd, not a resolver mock."""
        workspace = Path(self["WORKSPACE"])
        (workspace / ".claude").mkdir(exist_ok=True)
        return workspace

    def __setitem__(self, key, value):
        self._values[key] = value

    def __delitem__(self, key):
        del self._values[key]

    def copy(self):
        """Copy explicit fixture values without inheriting ambient environment."""
        copied = object.__new__(type(self))
        copied._values = self._values.copy()
        return copied

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return "<IsolatedRuntimeEnv: values redacted>"


def bridge_runtime_env():
    """Join an in-process test's scratch substrate without copying ambient secrets."""
    env = IsolatedRuntimeEnv(os.environ["WORKSPACE"])
    for key in ("GAIA_DATA_DIR", "GAIA_OPENCODE_ATTESTATION_DIR"):
        env[key] = os.environ[key]
    env["GAIA_DB"] = os.environ.get("GAIA_DB", str(Path(env["GAIA_DATA_DIR"]) / "gaia.db"))
    return env


# The only programs the suite may take for granted (D107): what CI installs and
# Gaia itself needs. Anything else a test runs is a fixture it writes in its tmp.
OWN_TOOLCHAIN = ("python", "git", "sh", "node", "npm", "bun", "gaia")


def require_tool(name):
    """Return the path of an own-toolchain program, failing the test loudly when it is absent.

    A skip would let the verdict change from one machine to the next, so a
    missing link of the chain is reported as a broken environment instead.
    """
    if name not in OWN_TOOLCHAIN:
        raise ValueError(f"{name!r} is not in the own toolchain {OWN_TOOLCHAIN}; write a fixture instead")
    path = shutil.which(name)
    if path is None:
        pytest.fail(f"{name} is not on PATH: the suite requires the own toolchain {OWN_TOOLCHAIN}",
                    pytrace=False)
    return path


@pytest.fixture
def bun():
    """The bun on PATH that drives the real OpenCode plugin."""
    return require_tool("bun")


# ============================================================================
# LAYER-1 SELECTION
#
# The layer-1 suite is everything a bare `pytest` collects from the repo root
# (testpaths in pyproject.toml) minus the entries below. npm test, CI and
# `gaia release` must reach it by invoking pytest with no selection of their
# own, so this tuple is the one place that decides what the suite contains.
# An entry still runs whenever a command-line argument names it or a path
# inside it. Why each entry is out:
#   - layer2_llm_evaluation spends LLM tokens.
#   - layer3_e2e drives a live Claude Code session.
#   - the exhaustive opencode alias matrix outruns its bun driver's subprocess
#     timeout; nightly.yml runs it by node id.
#   - NIGHTLY_ONLY: the *_mutants.py files pin the branch direction of concrete
#     cosmic-ray mutants rather than an observable behavior, and the tests/evals
#     files test the LLM eval harness, not Gaia. nightly.yml imports this tuple
#     and names every entry, so the mutation score is still measured.
# ============================================================================

NIGHTLY_ONLY = (
    "tests/hooks/modules/security/test_approval_grants_mutants.py",
    "tests/hooks/modules/security/test_blocked_commands_mutants.py",
    "tests/hooks/modules/security/test_inline_ast_analyzer_mutants.py",
    "tests/hooks/modules/security/test_mutative_verbs_mutants.py",
    "tests/hooks/modules/security/test_tiers_mutants.py",
    "tests/evals/test_backend_routing.py",
    "tests/evals/test_baseline.py",
    "tests/evals/test_catalog.py",
    "tests/evals/test_evals.py",
    "tests/evals/test_graders_code.py",
    "tests/evals/test_graders_decision.py",
    "tests/evals/test_graders_trace.py",
    "tests/evals/test_reporter.py",
    "tests/evals/test_runner.py",
    "tests/evals/test_skill_injection_consumer.py",
    "tests/evals/test_skill_injection_dispatch_reality.py",
)

LAYER1_EXCLUDED = (
    "tests/layer2_llm_evaluation",
    "tests/layer3_e2e",
    "tests/integration/test_opencode_protected_edit_bootstrap.py"
    "::test_exhaustive_file_alias_payload_and_path_matrix_reaches_real_bridge",
    *NIGHTLY_ONLY,
)


def _rootdir_relative(config, path) -> str | None:
    try:
        return Path(path).resolve().relative_to(config.rootpath.resolve()).as_posix()
    except ValueError:
        return None


def _named_on_command_line(config, entry: str) -> bool:
    """Whether a command-line argument is ``entry`` or lies inside it."""
    for arg in config.args:
        path, sep, node = arg.partition("::")
        spelled = _rootdir_relative(config, Path(config.invocation_params.dir, path))
        if spelled is None:
            continue
        spelled += sep + node
        if spelled == entry or spelled.startswith((entry + "/", entry + "::")):
            return True
    return False


def pytest_ignore_collect(collection_path, config):
    """Skip excluded directories; None defers to --ignore and every other plugin."""
    relative = _rootdir_relative(config, collection_path)
    if relative in LAYER1_EXCLUDED and not _named_on_command_line(config, relative):
        return True
    return None


def _deselect_outside_layer1(config, items):
    """Deselect excluded node ids, reported as deselected like --deselect."""
    excluded = tuple(
        entry for entry in LAYER1_EXCLUDED
        if "::" in entry and not _named_on_command_line(config, entry)
    )
    if not excluded:
        return
    dropped = [item for item in items if item.nodeid.startswith(excluded)]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if item not in dropped]


# ============================================================================
# SESSION ISOLATION
# ============================================================================

SESSION_ROOT_ENV = "GAIA_TEST_SESSION_ROOT"
_created_session_root = None


def _isolate_session_home_and_tmpdir():
    """Give the session, its xdist workers and every subprocess a private HOME and TMPDIR.

    The first process of a session creates the root under the invoker's temp
    directory and exports it; xdist workers and nested pytest runs inherit it
    and reuse it. PYTHONUSERBASE stays on the invoker's user site, where pytest
    and xdist may be installed, because Python derives it from HOME.
    """
    global _created_session_root
    root = os.environ.get(SESSION_ROOT_ENV)
    if not root:
        os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())
        root = tempfile.mkdtemp(prefix="gaia-pytest-")
        os.environ[SESSION_ROOT_ENV] = root
        _created_session_root = root
    home, tmp = Path(root, "home"), Path(root, "tmp")
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["TMPDIR"] = str(tmp)
    tempfile.tempdir = str(tmp)


def pytest_sessionfinish(session, exitstatus):
    """Drop the root this process created after a passing run; a failing one keeps its tmp_path dirs."""
    if _created_session_root and exitstatus == 0:
        shutil.rmtree(_created_session_root, ignore_errors=True)


# ============================================================================
# MARKERS
# ============================================================================

def pytest_configure(config):
    """Isolate the session's HOME and TMPDIR, then register custom markers."""
    _isolate_session_home_and_tmpdir()
    config.addinivalue_line("markers", "llm: LLM evaluation tests (require ANTHROPIC_API_KEY)")
    config.addinivalue_line("markers", "e2e: E2E headless tests (require claude CLI)")
    config.addinivalue_line(
        "markers",
        "ci_subset: small, budget-bounded subset of L2/L3 (LLM) tests that runs in "
        "CI under a controlled token budget (brief #89 AC-6)",
    )
    config.addinivalue_line(
        "markers",
        "table(argnames, argvalues, ids=None): parametrize's signature, collected as "
        "ONE test that runs every row and fails naming each failing row. Rows share "
        "the test's fixtures, so only rows that need no fresh per-row state qualify.",
    )


# ============================================================================
# TABLE TESTS
#
# A corpus of hundreds of rows that exercise one code path is one behavior, so
# it is reported as one test: every row still runs and every row still asserts.
# ============================================================================

def _table_argnames(mark) -> list[str]:
    names = mark.args[0]
    if isinstance(names, str):
        names = names.split(",")
    return [name.strip() for name in names]


def _table_rows(item) -> list[tuple[str, dict, bool]]:
    """(row id, arguments, skipped) for the product of the item's table marks."""
    rows = [("", {}, False)]
    for mark in item.iter_markers("table"):
        if mark.kwargs.get("indirect"):
            raise pytest.UsageError(f"{item.nodeid}: table does not support indirect")
        names = _table_argnames(mark)
        ids = mark.kwargs.get("ids")
        expanded = []
        for index, raw in enumerate(mark.args[1]):
            param_id, marks = None, ()
            if isinstance(raw, type(pytest.param(None))):
                param_id, marks, raw = raw.id, raw.marks, raw.values
                raw = raw[0] if len(names) == 1 else raw
            values = (raw,) if len(names) == 1 else tuple(raw)
            if param_id is None and isinstance(ids, (list, tuple)):
                param_id = ids[index]
            elif param_id is None and callable(ids):
                param_id = "-".join(str(ids(v)) for v in values)
            param_id = param_id or "-".join(str(v)[:60] for v in values)
            skipped = any(
                m.name in ("skip", "xfail") or (m.name == "skipif" and m.args and m.args[0])
                for m in marks
            )
            expanded.append((param_id, dict(zip(names, values)), skipped))
        rows = [
            ("-".join(filter(None, (left_id, right_id))), {**left, **right}, l_skip or r_skip)
            for left_id, left, l_skip in rows
            for right_id, right, r_skip in expanded
        ]
    return rows


def pytest_generate_tests(metafunc):
    """Bind a table's argument names once, so the test is collected as one item."""
    names = [n for mark in metafunc.definition.iter_markers("table") for n in _table_argnames(mark)]
    if names:
        metafunc.parametrize(names, [tuple(None for _ in names)], ids=["table"])


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Run every row of a table test and fail once, listing the failing rows."""
    if pyfuncitem.get_closest_marker("table") is None:
        return None
    import inspect

    rows = _table_rows(pyfuncitem)
    table_names = set(rows[0][1])
    fixtures = {
        name: pyfuncitem.funcargs[name]
        for name in inspect.signature(pyfuncitem.obj).parameters
        if name not in table_names
    }
    failures, ran = [], 0
    for row_id, arguments, skipped in rows:
        if skipped:
            continue
        try:
            pyfuncitem.obj(**fixtures, **arguments)
            ran += 1
        except pytest.skip.Exception:
            continue
        except (Exception, pytest.fail.Exception) as error:
            ran += 1
            failures.append(f"[{row_id}] {type(error).__name__}: {error}")
    if failures:
        pytest.fail(
            f"{len(failures)} of {ran} rows failed:\n" + "\n".join(failures), pytrace=False
        )
    if not ran:
        pytest.skip("every row of the table was skipped")
    return True


@pytest.fixture(autouse=True)
def _clear_path_cache():
    """Clear path resolution cache before and after each test.

    find_claude_dir() and get_plugin_data_dir() are decorated with
    @lru_cache(maxsize=1) and resolve from Path.cwd(). Without clearing,
    the first test to call either function caches a .claude path that
    contaminates every subsequent test whose cwd differs.
    """
    try:
        import sys
        hooks_dir = str(Path(__file__).resolve().parent.parent / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        from modules.core.paths import clear_path_cache
        clear_path_cache()
    except (ImportError, Exception):
        pass
    yield
    try:
        from modules.core.paths import clear_path_cache
        clear_path_cache()
    except (ImportError, Exception):
        pass


@pytest.fixture(autouse=True)
def _isolate_gaia_data_dir(tmp_path, monkeypatch):
    """Architectural test-DB isolation -- the personal ~/.gaia is unreachable.

    Every DB path in Gaia resolves through gaia.paths.data_dir(), which honors
    GAIA_DATA_DIR and falls back to ~/.gaia. gaia.store.writer._connect() and
    db_path() both flow through it. Without isolation, any test that calls a
    writer/reader without an explicit db_path silently touches the developer's
    real ~/.gaia/gaia.db -- a locally-masked leak that fails on a clean CI
    runner (e.g. test_fix_noop_when_already_indexed).

    This autouse function-scoped fixture points GAIA_DATA_DIR at a per-test
    tmp directory and clears GAIA_DB / GAIA_DB_PATH so no ambient override can
    reach the real home. Because monkeypatch.setenv applies fixture-first and a
    test's own setenv runs after, individual tests that set their own
    GAIA_DATA_DIR (e.g. tests/cli/test_gaia_doctor.py::check_project_context)
    transparently override this default. temp_gaia_db and explicit db_path=
    callers are unaffected -- this only changes the *fallback* resolution.
    """
    data_dir = tmp_path / "_gaia_isolated_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.delenv("GAIA_DB_PATH", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_dispatch_identity(monkeypatch):
    """Test-env isolation from the (now-live) dispatch-identity injection.

    The PreToolUse hook rewrites a subagent's Bash command to
    ``export GAIA_DISPATCH_AGENT=<agent>; <command>`` so the DB-side guards
    (memory / evidence / brief+plan content / state transitions) fail CLOSED for
    a dispatched subagent. A consequence: when the test suite itself is run BY a
    Gaia subagent, the pytest process (and every subprocess it spawns) inherits
    that dispatch identity, so any test exercising a curator-only write would
    fail with ``StateTransitionForbidden`` / ``ContentWriteForbidden`` merely
    because of the ambient identity -- not because the write mechanic is wrong.

    Mirroring ``_isolate_gaia_data_dir``: this autouse fixture clears
    ``GAIA_DISPATCH_AGENT`` so every test defaults to the human-CLI (fail-open)
    path, and a test that specifically exercises the guards sets it explicitly
    (fixture-first ordering means the test's own ``monkeypatch.setenv`` runs
    after and wins). Subprocess-based fixtures that ``os.environ.copy()`` also
    inherit the cleared env, so the subprocess is hermetic too.
    """
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_claude_session_id(monkeypatch):
    """Start every test without a Claude session id and restore it afterwards.

    The hook adapter writes a generated session id straight into os.environ,
    outside monkeypatch, so without this a later test inherits it. A suite run
    from inside Claude Code also inherits CLAUDE_CODE_SESSION_ID, which the
    approvals CLI reads as the requesting session, as it reads the
    GAIA_HOST_SESSION_ID an OpenCode dispatch exports.
    """
    # delenv records nothing for an absent variable, so a write during the test
    # would survive teardown; the setenv first guarantees an undo entry.
    for name in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GAIA_HOST_SESSION_ID"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    yield


@pytest.fixture(autouse=True)
def _fresh_mutative_classification_cache():
    """Drop the classifier's verdict cache after each test.

    The cache is keyed on command and cwd, not on file content, and
    tmp_path_retention_policy deletes a passed test's directory so the next
    test with the same name prefix reuses the path: a script rewritten there
    would otherwise get the previous test's verdict.
    """
    yield
    try:
        from modules.security.mutative_verbs import _detect_mutative_command
        _detect_mutative_command.cache_clear()
    except ImportError:
        pass


def pytest_collection_modifyitems(config, items):
    """Deselect what layer 1 excludes; auto-skip llm and e2e tests unless -m selects them."""
    _deselect_outside_layer1(config, items)
    # If user explicitly passed -m, respect that
    markexpr = config.getoption("-m", default="")
    if markexpr:
        return

    skip_llm = pytest.mark.skip(reason="LLM tests skipped by default (use -m llm)")
    skip_e2e = pytest.mark.skip(reason="E2E tests skipped by default (use -m e2e)")

    for item in items:
        if item.get_closest_marker("llm"):
            item.add_marker(skip_llm)
        if item.get_closest_marker("e2e"):
            item.add_marker(skip_e2e)


# ============================================================================
# SESSION FIXTURES
# ============================================================================

@pytest.fixture(scope="session")
def package_root():
    """Root of the gaia package."""
    root = Path(__file__).resolve().parents[1]
    return root.resolve() if root.is_symlink() else root


@pytest.fixture(scope="session")
def agents_dir(package_root):
    """Directory containing agent definition .md files."""
    d = package_root / "agents"
    return d.resolve() if d.is_symlink() else d


@pytest.fixture(scope="session")
def skills_dir(package_root):
    """Directory containing skill directories with SKILL.md files."""
    d = package_root / "skills"
    return d.resolve() if d.is_symlink() else d


@pytest.fixture(scope="session")
def config_dir(package_root):
    """Directory containing config files (context-contracts, etc)."""
    d = package_root / "config"
    return d.resolve() if d.is_symlink() else d


@pytest.fixture(scope="session")
def hooks_dir(package_root):
    """Directory containing hook scripts."""
    d = package_root / "hooks"
    return d.resolve() if d.is_symlink() else d


@pytest.fixture(scope="session")
def claude_md_content(package_root):
    """Content of the orchestrator identity.

    Orchestrator identity lives in agents/gaia-orchestrator.md, activated
    via settings.local.json agent field. This fixture returns the content
    of that file for tests that need to verify orchestrator content.
    """
    identity_path = package_root / "agents" / "gaia-orchestrator.md"
    if identity_path.exists():
        return identity_path.read_text()

    pytest.skip("agents/gaia-orchestrator.md not found")


@pytest.fixture(scope="session")
def all_agent_files(agents_dir):
    """All agent .md files (excluding READMEs)."""
    return [f for f in agents_dir.glob("*.md") if "README" not in f.name.upper()]


@pytest.fixture(scope="session")
def all_skill_dirs(skills_dir):
    """All skill directories that contain a SKILL.md."""
    return [d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]


# ============================================================================
# DB FIXTURE HELPERS
#
# Canonical implementation lives in tests/fixtures/db_helpers.py. This conftest
# imports those helpers and wraps the schema bootstrap in a pytest fixture for
# tests that prefer the fixture-injection style.
#
# Test modules in any subdirectory should import the helper functions from
# tests.fixtures.db_helpers directly. The temp_gaia_db fixture is consumed via
# the normal pytest fixture mechanism.
# ============================================================================

from tests.fixtures.db_helpers import (  # noqa: E402,F401
    bootstrap_gaia_schema,
    seed_agent_perms,
    seed_workspace,
    seed_workspace_contracts,
)


@pytest.fixture()
def temp_gaia_db(tmp_path):
    """Isolated SQLite DB with the v3 schema (workspaces + context tables).

    Scope: function (each test gets a fresh DB).
    Returns the Path to the created DB file.
    The file is cleaned up automatically when tmp_path is torn down.
    """
    db_path = tmp_path / "gaia_test.db"
    bootstrap_gaia_schema(db_path)
    return db_path


# ============================================================================
# FULL-BOOTSTRAP DB TEMPLATE (perf: build once, copy per test)
#
# scripts/bootstrap_database.py materializes the full production schema and
# seeds it (agent_permissions, schema_version floor, FTS5 backfill). Re-running
# it per test in a fixture serialized the tests/test_writer_*.py tail onto a
# single xdist worker while the others sat idle.
#
# This session-scoped fixture runs that bootstrap EXACTLY ONCE per run (shared
# by the xdist workers) into an immutable template .db file. Per-test fixtures then
# `copy_bootstrapped_db(...)` it -- a filesystem copy is milliseconds vs a
# multi-second subprocess storm. Isolation is preserved exactly: every test
# still gets its OWN independent .db file that it alone mutates; the template
# is never opened for writing after it is built. Coverage is preserved exactly:
# the copied bytes ARE the real bootstrap output, not a hand-rolled substitute.
# ============================================================================

@pytest.fixture(scope="session")
def bootstrapped_db_template(tmp_path_factory):
    """Build the full bootstrapped Gaia DB once per session; return its Path.

    Built via the real ``scripts/bootstrap_database.py`` -- the engine `gaia
    install` and the lazy bootstrap in `bin/gaia` run -- so the template is
    what a live bootstrap produces (schema + agent_permissions +
    schema_version floor + FTS5 mirrors). Immutable after creation -- consumers
    copy it, never mutate it.
    """
    repo_root = Path(__file__).resolve().parents[1]
    bootstrap = repo_root / "scripts" / "bootstrap_database.py"
    # One build serves every xdist worker: the workers share the parent of
    # their base temp, and the lock makes the first one build while the rest
    # wait and reuse it. Without fcntl (Windows) each worker builds its own.
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base
    template_dir = shared / "gaia_db_template"
    try:
        import fcntl
    except ImportError:
        fcntl = None
        template_dir = tmp_path_factory.mktemp("gaia_db_template")
    template_dir.mkdir(exist_ok=True)
    env = IsolatedRuntimeEnv(template_dir)
    template = Path(env["GAIA_DB"])
    with open(shared / "gaia_db_template.lock", "w") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        if not (template_dir / "built").exists():
            _build_template(bootstrap, env, template)
            (template_dir / "built").touch()
    return template


def _build_template(bootstrap: Path, env, template: Path) -> None:
    """Run the real bootstrap into ``template`` and fail loudly if it did not produce one."""
    import subprocess
    import sys

    # WORKSPACE only sets the bootstrap's seeded workspaces.identity row; the
    # writer tests insert their own 'me' workspace and never rely on it.
    res = subprocess.run(
        [sys.executable, str(bootstrap)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert res.returncode == 0, (
        f"bootstrap template build failed:\nstdout:\n{res.stdout}\n"
        f"stderr:\n{res.stderr}"
    )
    assert template.exists(), "bootstrap did not produce a template DB"


def copy_bootstrapped_db(template: Path, dest: Path) -> Path:
    """Copy the session bootstrap template to ``dest`` (a fresh per-test DB).

    Returns ``dest``. Each caller gets an independent, fully-mutable DB file
    identical to a freshly-bootstrapped one, with none of the per-test
    subprocess cost.
    """
    import shutil

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(template), str(dest))
    return dest


# ============================================================================
# FRONTMATTER PARSER (manual, no PyYAML)
# ============================================================================

def parse_frontmatter(text):
    """
    Parse YAML frontmatter from markdown text (manual parser, no PyYAML).

    Supports simple key-value pairs and lists (- item).

    Args:
        text: Full markdown text starting with ---

    Returns:
        dict with parsed frontmatter fields, or empty dict if no frontmatter
    """
    if not text.startswith("---"):
        return {}

    try:
        end = text.index("---", 3)
    except ValueError:
        return {}

    fm_text = text[3:end]
    result = {}
    current_key = None
    current_list = None

    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item under current key
        if stripped.startswith("- ") and current_key and current_list is not None:
            current_list.append(stripped[2:].strip())
            continue

        # New key-value pair
        if ":" in stripped:
            # End previous list
            if current_key and current_list is not None:
                result[current_key] = current_list

            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if value:
                result[key] = value
                current_key = key
                current_list = None
            else:
                # Start of a list
                current_key = key
                current_list = []
        else:
            # Not a key-value, not a list item - end list
            if current_key and current_list is not None:
                result[current_key] = current_list
                current_key = None
                current_list = None

    # Finalize last list
    if current_key and current_list is not None:
        result[current_key] = current_list

    return result
