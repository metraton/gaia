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
import pytest
from pathlib import Path


# ============================================================================
# MARKERS
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "llm: LLM evaluation tests (require ANTHROPIC_API_KEY)")
    config.addinivalue_line("markers", "e2e: E2E headless tests (require claude CLI)")
    config.addinivalue_line(
        "markers",
        "ci_subset: small, budget-bounded subset of L2/L3 (LLM) tests that runs in "
        "CI under a controlled token budget (brief #89 AC-6)",
    )


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


def pytest_collection_modifyitems(config, items):
    """Auto-skip llm and e2e tests unless explicitly requested via -m flag."""
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
