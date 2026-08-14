"""S7 smoke -> measurement: assert skill/artifact correlation on real transcripts.

``skill_injection_consumer`` (in ``graders.py``) only ever reads a JSONL audit
slice that the TEST ITSELF hand-writes as a fixture (``skill_injection_pipe_
detected.jsonl`` / ``skill_injection_clean.jsonl``). It proves the consumer's
own matching logic, never that the underlying detector
(``hooks/modules/agents/skill_injection_verifier.verify_skill_injection``,
composed with ``artifact_skill_map.expected_skills_for_paths``) catches a gap
that actually happened on a real transcript. S7 was green while a real
dispatch -- ``gaia-system`` writing ``.js``/``.html`` under
``skills/diagram-builder/assets/`` -- wrote code with ``coding-standards``
never loaded, and no existing eval could have failed on it.

Design tension and resolution: a real transcript on disk under
``~/.claude/projects/`` rotates and is not reproducible -- an eval pinned to
a live path breaks the moment that file is gone, and CI never has
``~/.claude/projects`` at all. But an eval that only ever exercises a
hand-written JSONL fixture is exactly the smoke this module replaces. Both
needs are met by NOT choosing one over the other:

- A committed, trimmed-but-real fixture pair under ``fixtures/transcripts/``
  gives the deterministic case: real ``tool_use`` blocks and real
  ``<command-name>`` tags, lifted verbatim from the two measured incident
  transcripts (``agent-ac5109df0a5ac8022.jsonl`` /
  ``agent-a06342709ff59f350.jsonl``), trimmed only by dropping the
  surrounding noise (hook context blobs, unrelated Read calls) that has no
  bearing on the skill/artifact correlation being measured. This is what
  runs in CI, every time, with no dependency on any machine's live history.
- An opt-in sweep over the two SPECIFIC live transcripts named in the
  incident report additionally re-measures the real files on disk when
  present, so the fixture's claim ("this really happened") stays checked
  against the artifact it was lifted from. When those files are not present
  on the running machine (a fresh checkout, CI, a different developer), the
  sweep test declares an explicit ``pytest.skip`` -- visible in the run
  report as SKIPPED, never silently folded into PASSED.

Both paths call the SAME production code the hook itself runs
(``transcript_analyzer.analyze`` for the written-file list,
``skill_injection_verifier.verify_skill_injection`` composed with
``artifact_skill_map`` for the expected-skill judgment) -- nothing here
re-implements the detection; the point is only to point it at real bytes
instead of a synthetic audit line.

A second gap remained even with the above: every case, real or live-swept,
was still anchored to the same two measured incidents (``.js``/``.mjs``).
None proved the detector catches a NEW hole in a class of artifact it was
never shown before -- it only proved it still recognizes the two it was
built from, a museum piece rather than a working alarm. The synthetic
fixture pair (``dispatch_gap_python_hook_module.jsonl`` /
``dispatch_closed_python_hook_module.jsonl``), hand-written rather than
lifted from any incident, writes a ``.py`` module instead -- still governed
by ``code-standards`` per ``artifact_skill_map``, but an extension neither
historical fixture touches -- to measure that capability directly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Same sys.path convention as tests/hooks/modules/agents/test_skill_injection_verifier.py
# and tests/evals/graders.py's lazy tools_dir import: this file lives at
# tests/evals/, so parents[2] is the gaia package root.
_GAIA_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _GAIA_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents.artifact_skill_map import expected_skills_for_paths  # noqa: E402
from modules.agents.skill_injection_verifier import verify_skill_injection  # noqa: E402
from modules.agents.transcript_analyzer import analyze  # noqa: E402


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "transcripts"

# Real, trimmed excerpt of agent-ac5109df0a5ac8022.jsonl (gaia-system dispatch,
# 2026-07-24): two real Edit tool_use blocks writing to
# skills/diagram-builder/assets/{engine.js,index.html}, plus one real
# <command-name> tag showing another skill WAS loaded that turn --
# coding-standards never appears anywhere in the file.
GAP_FIXTURE = FIXTURES_DIR / "dispatch_gap_diagram_builder.jsonl"

# Real, trimmed excerpt of agent-a06342709ff59f350.jsonl (developer dispatch,
# 2026-07-25): one real Write tool_use writing a .mjs scratch script, plus the
# real <command-name>coding-standards</command-name> tag with its full
# SKILL.md body (the fingerprints verify_skill_injection searches for).
CLOSED_FIXTURE = FIXTURES_DIR / "dispatch_closed_playwright_check.jsonl"

# Synthetic, hand-written transcripts -- NOT lifted from any real incident.
# Both GAP_FIXTURE and CLOSED_FIXTURE above pin the detector to the two
# extensions actually measured (.js, .mjs); a detector that only ever passes
# on those two files could still be re-implementing "does this specific
# filename appear," not "does the artifact class trigger the skill." These
# two fixtures write a .py file instead -- an extension neither historical
# incident touched, still governed by code-standards per
# artifact_skill_map.ARTIFACT_SKILL_RULES -- to prove the detector
# generalizes to a class of artifact/skill pairing it was never measured
# against, not merely memorizes the two known holes.
SYNTHETIC_GAP_FIXTURE = FIXTURES_DIR / "dispatch_gap_python_hook_module.jsonl"
SYNTHETIC_CLOSED_FIXTURE = FIXTURES_DIR / "dispatch_closed_python_hook_module.jsonl"
# CLOSED_FIXTURE is verbatim evidence of a turn that loaded the skill under its
# former name (coding-standards) with its former body, so it carries none of the
# current fingerprints and its "no gap" assertion cannot hold. Rewriting the
# embedded body to match would forge the evidence the fixture exists to be, so
# the test is marked xfail(strict=True) instead: replacing this fixture with a
# real incident that loads the current skill turns the xfail into a failure,
# forcing the marker off rather than letting it rot into a silent skip.
# SYNTHETIC_CLOSED_FIXTURE carries the true-positive case in the meantime.

# The two transcripts named in the incident report -- if present on THIS
# machine's ~/.claude/projects, the live-sweep test re-measures them directly
# instead of only trusting the committed fixture copy.
_NAMED_INCIDENT_TRANSCRIPTS = frozenset({
    "agent-ac5109df0a5ac8022.jsonl",
    "agent-a7846e255454bda0a.jsonl",
})

_LIVE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def _written_code_paths(transcript_path: Path) -> List[str]:
    """Return every Write/Edit ``file_path`` recorded in the transcript.

    Delegates entirely to ``transcript_analyzer.analyze`` -- the same
    single-pass parser the hook uses -- rather than re-parsing the JSONL
    here.
    """
    analysis = analyze(str(transcript_path))
    return [
        tc.arguments.get("file_path")
        for tc in analysis.tool_sequence
        if tc.tool_name in ("Write", "Edit")
        and isinstance(tc.arguments.get("file_path"), str)
    ]


def _measure_dispatch_gap(transcript_path: Path, agent_type: str) -> Optional[Dict[str, Any]]:
    """Reproduce the real skill/artifact check against one transcript file.

    Composes the exact production path: written files -> governing skills
    (``artifact_skill_map``) -> fingerprint search over the FULL transcript
    text (``skill_injection_verifier.verify_skill_injection``).
    ``declared_skills`` is deliberately empty -- this measurement cares only
    about what the ARTIFACT required, not what frontmatter happened to
    declare, mirroring the incident: the gap existed regardless of
    declaration.
    """
    written_paths = _written_code_paths(transcript_path)
    transcript_text = transcript_path.read_text(encoding="utf-8", errors="replace")
    return verify_skill_injection(
        agent_type=agent_type,
        transcript_text=transcript_text,
        declared_skills=[],
        written_paths=written_paths,
    )


# ---------------------------------------------------------------------------
# Fixture presence -- a missing fixture is a hard failure, never a skip. The
# committed fixture is expected to exist on every checkout; its absence means
# the fixture was deleted or mis-pathed, not that there is nothing to measure.
# ---------------------------------------------------------------------------


def test_fixtures_exist():
    assert GAP_FIXTURE.exists(), f"missing committed fixture: {GAP_FIXTURE}"
    assert CLOSED_FIXTURE.exists(), f"missing committed fixture: {CLOSED_FIXTURE}"
    assert SYNTHETIC_GAP_FIXTURE.exists(), (
        f"missing committed fixture: {SYNTHETIC_GAP_FIXTURE}"
    )
    assert SYNTHETIC_CLOSED_FIXTURE.exists(), (
        f"missing committed fixture: {SYNTHETIC_CLOSED_FIXTURE}"
    )


# ---------------------------------------------------------------------------
# Deterministic case -- the committed fixture, every run, no live dependency.
# ---------------------------------------------------------------------------


def test_gap_fixture_reproduces_the_measured_hole():
    """The real, measured incident: gaia-system wrote engine.js without ever
    loading code-standards (named coding-standards at the time). This must
    FAIL loud (assert on the anomaly's presence) whenever the gap is real --
    a green run here would mean the fixture stopped reflecting the incident,
    not that the incident is fixed.
    """
    written = _written_code_paths(GAP_FIXTURE)
    assert written, "fixture carries no Write/Edit calls -- fixture rotted"
    assert any(p.endswith("engine.js") for p in written), (
        f"expected engine.js among written paths, got: {written}"
    )

    expected = expected_skills_for_paths(written)
    assert expected, "no written path resolved to a governing skill -- fixture rotted"
    assert "code-standards" in expected.values()

    result = _measure_dispatch_gap(GAP_FIXTURE, agent_type="gaia-system")

    assert result is not None, (
        "expected a skill_injection_gap anomaly for the engine.js write "
        "with no code-standards fingerprint in transcript -- got none"
    )
    assert result["type"] == "skill_injection_gap"
    assert "code-standards" in result["missing_skills"]
    triggering = result["triggering_artifacts"]["code-standards"]
    assert any(p.endswith("engine.js") for p in triggering)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "CLOSED_FIXTURE is verbatim historical evidence: it records a turn that "
        "loaded the skill under its former name and body, so it matches none of "
        "the current code-standards fingerprints. Editing the embedded body to "
        "make this pass would forge the evidence. Replace the fixture with a real "
        "incident that loaded the current skill, then drop this marker."
    ),
)
def test_closed_fixture_shows_no_gap_when_skill_was_loaded():
    """Same shape (a written source file), but the skill's own fingerprint
    IS present in the transcript this time -- the check must pass silently,
    proving the eval does not just always fail on any written code file.
    """
    written = _written_code_paths(CLOSED_FIXTURE)
    assert written, "fixture carries no Write/Edit calls -- fixture rotted"
    assert any(p.endswith(".mjs") for p in written), (
        f"expected a .mjs write among written paths, got: {written}"
    )

    expected = expected_skills_for_paths(written)
    assert "code-standards" in expected.values()

    result = _measure_dispatch_gap(CLOSED_FIXTURE, agent_type="developer")

    assert result is None, (
        f"expected no anomaly once code-standards was loaded, got: {result}"
    )


# ---------------------------------------------------------------------------
# Generalization case -- a synthetic artifact/skill pairing NEITHER historical
# incident measured. GAP_FIXTURE and CLOSED_FIXTURE above only prove the
# detector reproduces the two already-known holes (.js, .mjs); these two
# tests prove it also catches a THIRD, never-measured artifact class (.py) --
# the capability this eval exists to check, not a third pinned incident.
# ---------------------------------------------------------------------------


def test_generalizes_to_new_artifact_class_never_measured_before():
    """A never-before-seen hole: a .py hook module written with no
    code-standards fingerprint anywhere in the transcript. Neither
    GAP_FIXTURE nor CLOSED_FIXTURE ever exercises a .py write, so a detector
    that merely special-cased ``engine.js`` / ``pw-check.mjs`` would pass
    this fixture through silently. This must FAIL loud (assert on the
    anomaly's presence) -- a green run here means the detector generalizes,
    not that this particular file is exempt.
    """
    written = _written_code_paths(SYNTHETIC_GAP_FIXTURE)
    assert written, "fixture carries no Write/Edit calls -- fixture rotted"
    assert any(p.endswith(".py") for p in written), (
        f"expected a .py write among written paths, got: {written}"
    )
    assert not any(p.endswith((".js", ".mjs")) for p in written), (
        "synthetic fixture must not reuse the extensions already covered "
        "by the historical fixtures, or it proves nothing new"
    )

    expected = expected_skills_for_paths(written)
    assert expected, "no written path resolved to a governing skill -- fixture rotted"
    assert "code-standards" in expected.values()

    result = _measure_dispatch_gap(SYNTHETIC_GAP_FIXTURE, agent_type="gaia-system")

    assert result is not None, (
        "expected a skill_injection_gap anomaly for the .py write with no "
        "code-standards fingerprint in transcript -- got none, meaning the "
        "detector did not generalize past the two known incidents"
    )
    assert result["type"] == "skill_injection_gap"
    assert "code-standards" in result["missing_skills"]
    triggering = result["triggering_artifacts"]["code-standards"]
    assert any(p.endswith(".py") for p in triggering)


def test_synthetic_closed_fixture_shows_no_gap_when_skill_was_loaded():
    """Symmetric negative case, same never-measured artifact class: the SAME
    .py write, but code-standards' own fingerprint IS present in the
    transcript this time. Without this case, test_generalizes_to_new_
    artifact_class_never_measured_before could be satisfied by a detector
    that always reports a gap for any .py write regardless of what was
    loaded -- this proves the detector distinguishes the two, not just
    alarms unconditionally.
    """
    written = _written_code_paths(SYNTHETIC_CLOSED_FIXTURE)
    assert written, "fixture carries no Write/Edit calls -- fixture rotted"
    assert any(p.endswith(".py") for p in written), (
        f"expected a .py write among written paths, got: {written}"
    )

    expected = expected_skills_for_paths(written)
    assert "code-standards" in expected.values()

    result = _measure_dispatch_gap(SYNTHETIC_CLOSED_FIXTURE, agent_type="gaia-system")

    assert result is None, (
        f"expected no anomaly once code-standards was loaded, got: {result}"
    )


# ---------------------------------------------------------------------------
# Opt-in live sweep -- re-measures the ACTUAL files on disk that the incident
# report names, when this machine happens to still carry them. Declares an
# explicit skip (visible as SKIPPED, never folded into a false PASS) when
# they are absent, per the no-silent-pass requirement: this test either
# genuinely measures real bytes, or it visibly says it measured nothing.
# ---------------------------------------------------------------------------


def _iter_live_incident_transcripts() -> List[Path]:
    if not _LIVE_PROJECTS_ROOT.exists():
        return []
    # rglob, not a fixed-depth glob: a transcript lives at
    # projects/<project-slug>/<session-uuid>/subagents/agent-*.jsonl, and the
    # nesting depth above "subagents/" is an implementation detail of the
    # harness, not a contract this test should assume.
    return [
        p
        for p in _LIVE_PROJECTS_ROOT.rglob("subagents/agent-*.jsonl")
        if p.name in _NAMED_INCIDENT_TRANSCRIPTS
    ]


def test_live_sweep_named_incident_transcripts():
    live_matches = _iter_live_incident_transcripts()
    if not live_matches:
        pytest.skip(
            "no live transcript under ~/.claude/projects matched "
            f"{sorted(_NAMED_INCIDENT_TRANSCRIPTS)} -- sweep is opt-in "
            "evidence on top of the committed fixture, not a hard "
            "requirement on machines without this history"
        )

    for transcript_path in live_matches:
        result = _measure_dispatch_gap(transcript_path, agent_type="gaia-system")
        assert result is not None, (
            f"{transcript_path} no longer reproduces the measured gap -- "
            "if code-standards is now loaded in this transcript, update "
            "the committed fixture and this test's expectation together"
        )
        assert "code-standards" in result["missing_skills"]
