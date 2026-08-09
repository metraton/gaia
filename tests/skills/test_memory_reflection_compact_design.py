"""Prompt-contract regressions for reflection, curation, and compaction."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_main_skills_fit_progressive_disclosure_budgets() -> None:
    limits = {
        "skills/memory/SKILL.md": (100, 150),
        "skills/session-reflection/SKILL.md": (80, 120),
        "skills/gaia-compact/SKILL.md": (50, 70),
    }
    for path, (minimum, maximum) in limits.items():
        count = len(_read(path).splitlines())
        assert minimum <= count <= maximum, (path, count)


def test_integrated_user_prompt_has_one_coherent_flow() -> None:
    """The worked example is ONE flow: a single request triggers reflect+save,
    and that same example continues into a follow-up compact instead of
    starting over from a fresh prompt. The literal Spanish sentence pinned
    here previously ("hagamos una reflexión...") was retired prose from
    before examples.md was rewritten around a single scenario; the
    invariant that survives is the flow being integrated, not that exact
    wording.
    """
    reflection = _read("skills/session-reflection/SKILL.md")
    examples = _read("skills/session-reflection/examples.md")
    memory = _read("skills/memory/SKILL.md")
    compact = _read("skills/gaia-compact/SKILL.md")

    assert "reflexionemos y guardemos" in examples
    assert "after this review" in _flat(examples)
    assert "then asks to compact" in _flat(examples)
    assert "already-canonical work is" in reflection and "never copied" in reflection
    assert "gaia_system" in reflection
    assert "Meaningful milestone" in memory or "meaningful milestone" in memory
    assert "never their bodies" in compact
    assert "unsaved transient" in compact.lower()


def test_reflection_covers_candidate_skip_improvement_and_consent() -> None:
    """The consent phrasing pinned here previously ("pre-authorizes
    persistence after this display", "user may still") was retired when
    consent mechanics were consolidated into `memory` -- reflection now
    delegates rather than restating them, and `carry_forward` is the actual
    status literal (not the hyphenated "carry-forward" adjective the old
    prose used). Re-pinned against the current delegation and the real
    token spelling.
    """
    reflection = _read("skills/session-reflection/SKILL.md")
    assert "SKIP" in reflection
    assert "briefs, plans, tasks" in reflection
    assert "feedback" in reflection and "carry_forward" in reflection
    assert "Consent mechanics belong to" in _flat(reflection)
    assert "`memory`" in reflection


def test_operator_is_exact_best_effort_materializer() -> None:
    """"best-effort by default" was retired from gaia-operator.md: the
    operator's OWN identity is now exclusively the "exact" half (apply the
    orchestrator's instructions with no interpretation); batch semantics
    belong to the technique-owning skill it materializes through (`memory`).

    The adjective "best-effort" is deliberately NOT asserted. It has never
    appeared in skills/memory/SKILL.md in any revision -- it lives in
    skills/memory/examples.md -- so pinning it here asserted a word that was
    never in the file under test, and pinned a word rather than a property:
    the prose could not be reworded without breaking a test whose subject was
    untouched. What must hold is the two-mode contract the operator
    materializes -- an independent batch degrades per operation, a checkpoint
    does not degrade at all -- so that is what is asserted.
    """
    operator = _flat(_read("agents/gaia-operator.md"))
    memory = _flat(_read("skills/memory/SKILL.md"))
    assert "exact verbs, scopes, values, ordering, and verification criteria" in operator
    assert "one observed result per operation" in operator
    assert "apply those instructions with no interpretation" in operator
    assert "Report partial batch failures per operation" in memory
    assert "is one atomic operation and remains all-or-nothing" in memory
    assert "NEEDS_INPUT" in operator
    assert "infer the domain" not in operator
    assert "not\na batch" not in operator


def test_three_memory_floors_are_explicit_and_distinct() -> None:
    memory = _read("skills/memory/SKILL.md")
    for floor in ("Events", "Episodes", "Curated memory"):
        assert floor in memory
    assert "Events and episodes are evidence, not durable truth" in memory


def test_compact_has_no_legacy_file_memory_or_durable_body_copy() -> None:
    compact = _read("skills/gaia-compact/SKILL.md")
    assert "MEMORY.md" not in compact
    assert ".claude/projects" not in compact
    assert "never their bodies" in compact
    assert "ACTIVE OBJECTIVE" in compact
    assert "RESUME POINT" in compact
