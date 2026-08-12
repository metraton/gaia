"""
Skill body reference integrity: an artifact a skill names must exist by that name.

Skill bodies cite files and symbols as evidence for what they claim, and nothing
checked those citations. A phantom reference therefore survives indefinitely and
is found only by hand, one file at a time.

SCOPE, deliberately narrow. A check that produces false positives gets disabled
and then protects nothing, so a citation is examined only when it is
unambiguously asserting something about THIS repository:

  IN   a backticked token rooted at a real top-level directory of this repo and
       carrying a file extension -- `hooks/modules/agents/foo.py`.
  IN   an explicit pytest-style anchor -- `path/to/file.py::Symbol::method`.
  OUT  placeholders and globs: any token holding '<', '*' or '...'.
  OUT  paths not rooted at a top-level directory of this repo. A skill that
       illustrates another project's `installer/README.md` or `docs/setup.md`
       is showing a shape, not asserting this repo's contents.
  OUT  bare directory mentions with no file extension.
  OUT  paths in prose that are not inside backticks.
  OUT  vendored third-party markdown. Anything under a `node_modules/` segment
       documents someone else's package and is not a Gaia skill.
  OUT  any path holding a `.claude/` segment. That tree is the installed copy,
       never a source-tree artifact, so such a citation names an install target
       or an illustration and can never be checked against this repo.

A citation resolves against the repo root OR anywhere inside the citing skill's
own directory. Both bases are needed because `scripts/` and `tools/` name a
top-level directory of this repo AND a conventional skill-local subdirectory:
`scripts/screenshot.cjs` in visual-verify means the skill's own copy, and reading
it as repo-rooted would report a phantom that is not one. A genuine phantom
resolves against neither base, so allowing the second costs no detection.

Fenced code blocks are deliberately NOT skipped. The defect this exists to catch
lived inside a fenced example that was presented as a gold standard, so skipping
fences would have skipped the reason for writing it.

WHAT THIS CANNOT DECIDE, stated so a green run is not read as more than it is: a
path that EXISTS while being credited with behavior it does not have. A real file
said to perform a check it does not perform passes RULE 1 untouched. Only the
``::symbol`` anchor makes any part of that class mechanically decidable, which is
why RULE 2 exists and why skill-creation asks for symbols rather than bare paths.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"

# A citation counts as a claim about this repo only when it is rooted at one of
# this repo's own top-level directories. This single anchor is what separates an
# assertion from an illustration drawn from some other project.
TOP_LEVEL_DIRS = frozenset(
    {
        "agents",
        "bin",
        "build",
        "config",
        "hooks",
        "scripts",
        "skills",
        "store",
        "tests",
        "tools",
    }
)

PLACEHOLDER_MARKERS = ("<", ">", "*", "...", "$", "{")

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_FILE_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_SYMBOL_DEF = "{kind} {name}"


def _is_repo_rooted(token: str) -> bool:
    head, _, rest = token.partition("/")
    return bool(rest) and head in TOP_LEVEL_DIRS


def _candidate_citations(text: str):
    """Yield (path, symbols) for every backticked token that asserts a repo artifact."""
    for raw in _BACKTICKED.findall(text):
        token = raw.strip()
        if not token or any(marker in token for marker in PLACEHOLDER_MARKERS):
            continue
        if any(ch.isspace() for ch in token) or "://" in token:
            continue

        path_part, *symbols = token.split("::")
        if not _is_repo_rooted(path_part) or ".claude/" in path_part:
            continue
        if not symbols and not _FILE_SUFFIX.search(path_part):
            continue
        yield path_part, symbols


def _symbol_is_defined(source: str, symbol: str) -> bool:
    return any(
        _SYMBOL_DEF.format(kind=kind, name=symbol) in source
        for kind in ("def", "class")
    )


def _resolve(path_part: str, repo_root: Path, skill_dir):
    candidate = repo_root / path_part
    if candidate.exists():
        return candidate
    if skill_dir is not None:
        for match in skill_dir.rglob(path_part):
            return match
    return None


def check_text(text: str, repo_root: Path = REPO_ROOT, skill_dir=None):
    """Return a list of problem descriptions for one markdown body.

    Exposed as a function rather than inlined in the test so the same rules can be
    replayed against arbitrary content -- a fixture, or a revision from git.
    """
    problems = []
    for path_part, symbols in _candidate_citations(text):
        target = _resolve(path_part, repo_root, skill_dir)
        if target is None:
            problems.append(f"{path_part} -- named but does not exist")
            continue
        if not symbols:
            continue
        try:
            source = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for symbol in symbols:
            if not _symbol_is_defined(source, symbol):
                problems.append(
                    f"{path_part}::{symbol} -- file exists but defines no such symbol"
                )
    return problems


# Pre-existing drift this check surfaced on the corpus it was written against.
# Quarantined rather than silently tolerated: xfail is strict, so repairing a
# file here turns the run red until its entry is deleted, which is what keeps the
# list from outliving the debt it records.
KNOWN_STALE = {
    "gaia-patterns/reference.md": (
        "component inventory cites tools/persist_transcript_analysis.py and "
        "config/context-contracts.json, neither of which exists, and calls the "
        "CLI entry point bin/gaia.js when the shipped binary is bin/gaia"
    ),
}


def _skill_markdown_files():
    return sorted(
        p
        for p in SKILLS_ROOT.rglob("*.md")
        if p.is_file() and "node_modules" not in p.parts
    )


# The five false claims that motivated this module, verbatim in the shapes they
# were written in. They are kept together because they do NOT share a fate: only
# the second names an artifact that is absent, and a reader who assumes the other
# four are covered would be trusting a guarantee this check does not give.
DEFECT_CORPUS = {
    "capability_claim_about_a_real_file": (
        "Validation: `tests/system/test_directory_structure.py` verifies all "
        "skill folders have a `SKILL.md`."
    ),
    "absent_module_backticked": (
        "- `hooks/modules/agents/skill_injection.py` -- runtime that reads and "
        "injects skill content"
    ),
    "capability_claim_in_see_also": (
        "- `tests/system/test_directory_structure.py` -- verifies README and "
        "SKILL.md existence"
    ),
    "absent_module_unbackticked_shorthand": (
        "adapters/claude_code.py -> modules/agents/skill_injection.py"
    ),
    "frontmatter_field_that_does_not_exist": (
        "`SKILL.md` must have valid frontmatter: `name:`, `description:`, "
        "`metadata.type:`"
    ),
}


class TestSkillReferenceIntegrity:
    """Every artifact a skill body names must exist by that name."""

    def test_skill_corpus_has_markdown(self):
        """Guard against a path change silently emptying the corpus."""
        assert _skill_markdown_files(), f"no skill markdown found under {SKILLS_ROOT}"

    @pytest.mark.parametrize(
        "skill_md", _skill_markdown_files(), ids=lambda p: str(p.relative_to(SKILLS_ROOT))
    )
    def test_cited_artifacts_exist(self, skill_md, request):
        """A repo-rooted path or ::symbol anchor cited in a skill must resolve."""
        relative = str(skill_md.relative_to(SKILLS_ROOT))
        if relative in KNOWN_STALE:
            request.node.add_marker(
                pytest.mark.xfail(strict=True, reason=KNOWN_STALE[relative])
            )
        skill_dir = skill_md.parent
        while skill_dir.parent != SKILLS_ROOT and skill_dir != SKILLS_ROOT:
            skill_dir = skill_dir.parent
        problems = check_text(
            skill_md.read_text(encoding="utf-8"), skill_dir=skill_dir
        )
        assert not problems, "{}:\n  {}".format(
            skill_md.relative_to(REPO_ROOT), "\n  ".join(problems)
        )


class TestDetectionBoundary:
    """Pin what this check catches and, just as deliberately, what it does not."""

    def test_flags_an_absent_module(self):
        problems = check_text(DEFECT_CORPUS["absent_module_backticked"])
        assert problems == [
            "hooks/modules/agents/skill_injection.py -- named but does not exist"
        ]

    def test_flags_an_absent_symbol_in_a_real_file(self):
        problems = check_text(
            "see `tests/layer1_prompt_regression/test_skill_reference_integrity.py"
            "::check_text` and `tests/layer1_prompt_regression/"
            "test_skill_reference_integrity.py::no_such_helper`"
        )
        assert problems == [
            "tests/layer1_prompt_regression/test_skill_reference_integrity.py"
            "::no_such_helper -- file exists but defines no such symbol"
        ]

    @pytest.mark.parametrize(
        "case",
        [
            "capability_claim_about_a_real_file",
            "capability_claim_in_see_also",
            "absent_module_unbackticked_shorthand",
            "frontmatter_field_that_does_not_exist",
        ],
    )
    def test_known_blind_spots_stay_undetected(self, case):
        """These four are out of reach, and the reason differs per case.

        The two capability claims name a file that genuinely exists while
        crediting it with a check it does not perform -- deciding that needs the
        cited file's semantics, not its presence. The shorthand is neither
        backticked nor rooted at a top-level directory, so it never becomes a
        candidate. The frontmatter claim is not a path at all.

        Asserting the blind spots keeps them honest: if a later rule starts
        catching one, this fails and the docstring above has to be rewritten
        rather than quietly becoming wrong.
        """
        assert check_text(DEFECT_CORPUS[case]) == []

    def test_illustrative_paths_from_other_projects_are_ignored(self):
        """The false-positive guard: a foreign path must never be reported."""
        assert check_text("`installer/README.md` and `docs/onboarding.md`") == []

    def test_placeholders_are_ignored(self):
        assert check_text("`skills/<name>/SKILL.md` and `agents/*.md`") == []
