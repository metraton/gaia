"""The OpenCode roster after an install of the real packaged tree lists every shipped skill, memory included.

The orchestrator's identity and the memory CLI both tell it to load Skill('memory');
an install that leaves that skill out makes the instruction impossible to follow.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(_PACKAGE_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT / "bin"))

from cli import _install_helpers  # noqa: E402

_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)
_OPENCODE_SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _shipped_skills() -> list[str]:
    return sorted(p.parent.name for p in (_PACKAGE_ROOT / "skills").glob("*/SKILL.md"))


def test_full_install_lists_every_shipped_skill_including_memory(tmp_path):
    result = _install_helpers.configure_opencode_plugin(tmp_path, _PACKAGE_ROOT)

    assert result["action"] == "updated", result
    installed = tmp_path / ".opencode" / "skills"
    listed = sorted(p.name for p in installed.iterdir() if (p / "SKILL.md").is_file())
    assert listed == _shipped_skills()
    assert "memory" in listed


def test_every_shipped_skill_is_a_valid_opencode_skill(tmp_path):
    """OpenCode skips a skill whose name differs from its directory or is not kebab-case, with no error."""
    for name in _shipped_skills():
        head = (_PACKAGE_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        declared = _NAME.search(head)
        assert declared and declared.group(1) == name, name
        assert _OPENCODE_SKILL_NAME.match(name), name
