"""Every file and anchor the diagram-builder skill's prose cites must exist.

A citation is a markdown link ``[text](target)`` or a backticked ``*.md`` name.
The backticked form is checked too because the skill grows by references that
later tasks add: naming one before it exists must fail here, not ship as a
dangling pointer an agent then tries to open.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "diagram-builder"

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_BACKTICKED_MD = re.compile(r"`([^`\s]+\.md)`")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.MULTILINE)
_EXTERNAL = ("http://", "https://", "mailto:")


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    text = heading.strip().lower().replace("`", "")
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {_slug(h) for h in _HEADING.findall(path.read_text(encoding="utf-8"))}


def _prose_files(skill_dir: Path) -> list[Path]:
    return [p for p in sorted(skill_dir.rglob("*.md")) if "node_modules" not in p.parts]


def broken_references(skill_dir: Path) -> list[str]:
    """One line per citation in the skill's markdown that does not resolve."""
    broken = []
    for md in _prose_files(skill_dir):
        text = md.read_text(encoding="utf-8")
        for target in _LINK.findall(text):
            if target.startswith(_EXTERNAL):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (md.parent / file_part).resolve() if file_part else md
            if not dest.exists():
                broken.append(f"{md.relative_to(skill_dir)}: link to missing {target}")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                broken.append(f"{md.relative_to(skill_dir)}: missing anchor {target}")
        for name in _BACKTICKED_MD.findall(text):
            if not ((md.parent / name).exists() or (skill_dir / name).exists()):
                broken.append(f"{md.relative_to(skill_dir)}: names missing file {name}")
    return broken


def test_every_citation_in_the_skill_resolves():
    assert broken_references(SKILL_DIR) == []


def test_the_checker_reports_a_broken_link_and_anchor(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "# Title\n[ok](SKILL.md#title) [gone](gone.md) [bad](SKILL.md#nope) `later.md`\n",
        encoding="utf-8",
    )
    assert broken_references(tmp_path) == [
        "SKILL.md: link to missing gone.md",
        "SKILL.md: missing anchor SKILL.md#nope",
        "SKILL.md: names missing file later.md",
    ]
