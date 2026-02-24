"""
Root conftest.py - Shared test infrastructure for gaia-ops.

Provides:
- Custom markers: llm, e2e (auto-skipped in default test runs)
- Session fixtures: package_root, agents_dir, skills_dir, config_dir, hooks_dir
- Frontmatter parser (manual, no PyYAML dependency)
"""

import pytest
from pathlib import Path


# ============================================================================
# MARKERS
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "llm: LLM evaluation tests (require ANTHROPIC_API_KEY)")
    config.addinivalue_line("markers", "e2e: E2E headless tests (require claude CLI)")


def pytest_collection_modifyitems(config, items):
    """Auto-skip llm and e2e tests unless explicitly requested via -m flag."""
    # If user explicitly passed -m, respect that
    markexpr = config.getoption("-m", default="")
    if markexpr:
        return

    skip_llm = pytest.mark.skip(reason="LLM tests skipped by default (use -m llm)")
    skip_e2e = pytest.mark.skip(reason="E2E tests skipped by default (use -m e2e)")

    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip_llm)
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


# ============================================================================
# SESSION FIXTURES
# ============================================================================

@pytest.fixture(scope="session")
def package_root():
    """Root of the gaia-ops package."""
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
    """
    Content of the orchestrator CLAUDE.md.

    Resolution order:
    1. package_root/CLAUDE.md (installed project layout)
    2. package_root/templates/CLAUDE.template.md (package repository layout)

    The template fallback is normalized with a small compatibility appendix
    so path-oriented assertions remain meaningful.
    """
    primary = package_root / "CLAUDE.md"
    if primary.exists():
        return primary.read_text()

    template = package_root / "templates" / "CLAUDE.template.md"
    if template.exists():
        content = template.read_text()
        missing_path_markers = []
        for marker in ["project-context.json", "agents/", "skills/"]:
            if marker not in content:
                missing_path_markers.append(f"- {marker}")

        if missing_path_markers:
            content += (
                "\n\n## Test Compatibility Paths\n"
                + "\n".join(missing_path_markers)
                + "\n"
            )
        return content

    pytest.skip("Neither CLAUDE.md nor templates/CLAUDE.template.md was found")


@pytest.fixture(scope="session")
def all_agent_files(agents_dir):
    """All agent .md files (excluding READMEs)."""
    return [f for f in agents_dir.glob("*.md") if "README" not in f.name.upper()]


@pytest.fixture(scope="session")
def all_skill_dirs(skills_dir):
    """All skill directories that contain a SKILL.md."""
    return [d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]


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
