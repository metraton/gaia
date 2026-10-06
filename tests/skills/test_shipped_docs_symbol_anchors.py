"""The skill and agent docs the package ships give `gaia doctor`'s Symbol-anchors check a pass.

The sandbox install gate fails a release on any doctor warning, so an
un-anchored ``symbol()`` reference must fail CI before it reaches a release.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "bin"))

import cli.doctor as doctor  # noqa: E402


def _installed_layout(tmp_path: Path) -> Path:
    """A project root whose .claude/skills and .claude/agents are the repo's own trees, as an install links them."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    for tree in ("skills", "agents"):
        (claude / tree).symlink_to(REPO_ROOT / tree, target_is_directory=True)
    return tmp_path


def test_repo_skills_and_agents_pass_the_symbol_anchors_check(tmp_path):
    """Every codebase symbol a shipped doc cites is written as a resolvable `file.py::symbol` anchor."""
    result = doctor.check_symbol_anchors(_installed_layout(tmp_path))
    assert result["severity"] == "pass", result["detail"]
