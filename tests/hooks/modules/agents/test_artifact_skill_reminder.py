"""Unit tests for modules.agents.artifact_skill_reminder.

Isolates REMINDER_CACHE_DIR to a pytest tmp_path per test (mirroring the
RESUME_MAP_CACHE_DIR / CONTEXT_CACHE_DIR monkeypatch convention in
tests/contract/test_resume_persists_draft.py) so no test ever touches the
real /tmp/gaia-artifact-skill-reminders directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents import artifact_skill_reminder as reminder  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path, monkeypatch):
    """Point REMINDER_CACHE_DIR at a fresh tmp_path for every test."""
    monkeypatch.setattr(reminder, "REMINDER_CACHE_DIR", tmp_path / "reminders")


# ---------------------------------------------------------------------------
# should_remind: once-per-turn, once-per-artifact-class dedup
# ---------------------------------------------------------------------------


def test_first_call_reminds():
    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is True


def test_second_call_same_turn_same_skill_does_not_remind():
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is False


def test_third_call_same_turn_same_skill_still_does_not_remind():
    """Guards against a marker that only suppresses the SECOND call."""
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is False


def test_different_skill_same_turn_reminds_independently():
    """Dedup is keyed per artifact CLASS (skill), not globally per turn --
    a second, DIFFERENT governed class in the same turn still reminds once."""
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    assert reminder.should_remind("sess-1", "aabc123", "terraform-standards") is True


def test_different_agent_same_session_is_a_fresh_turn():
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    assert reminder.should_remind("sess-1", "adef456", "code-standards") is True


def test_different_session_same_agent_is_a_fresh_turn():
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    assert reminder.should_remind("sess-2", "aabc123", "code-standards") is True


def test_repeat_after_marking_a_different_skill_first_still_dedups():
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    reminder.should_remind("sess-1", "aabc123", "terraform-standards")
    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is False
    assert reminder.should_remind("sess-1", "aabc123", "terraform-standards") is False


# ---------------------------------------------------------------------------
# should_remind: missing ids never remind, and never persist
# ---------------------------------------------------------------------------


def test_missing_session_id_never_reminds():
    assert reminder.should_remind("", "aabc123", "code-standards") is False


def test_missing_agent_id_never_reminds():
    assert reminder.should_remind("sess-1", "", "code-standards") is False


def test_missing_skill_never_reminds():
    assert reminder.should_remind("sess-1", "aabc123", "") is False


def test_missing_ids_never_create_a_marker_file(tmp_path):
    reminder.should_remind("", "aabc123", "code-standards")
    reminder.should_remind("sess-1", "", "code-standards")
    reminder.should_remind("sess-1", "aabc123", "")
    assert not reminder.REMINDER_CACHE_DIR.exists() or not any(
        reminder.REMINDER_CACHE_DIR.glob("*.json")
    )


# ---------------------------------------------------------------------------
# should_remind: a persistence failure degrades to "always remind", never a
# crash and never a block on the caller.
# ---------------------------------------------------------------------------


def test_unwritable_cache_dir_still_reminds_without_raising(monkeypatch):
    def _boom(self, *args, **kwargs):
        raise OSError("simulated unwritable /tmp")

    monkeypatch.setattr(Path, "mkdir", _boom)

    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is True
    # Persistence failed, so nothing was recorded -- the NEXT call reminds
    # again rather than silently going quiet forever.
    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is True


def test_corrupt_marker_file_is_treated_as_empty(tmp_path):
    path = reminder._marker_path("sess-1", "aabc123")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is True


def test_marker_file_with_wrong_shape_is_treated_as_empty(tmp_path):
    """A marker whose 'skills' key is not a list (or is absent) must not
    crash -- it degrades to an empty reminded set."""
    path = reminder._marker_path("sess-1", "aabc123")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"skills": "code-standards"}', encoding="utf-8")

    assert reminder.should_remind("sess-1", "aabc123", "code-standards") is True


# ---------------------------------------------------------------------------
# build_reminder_context
# ---------------------------------------------------------------------------


def test_build_reminder_context_names_file_and_skill():
    text = reminder.build_reminder_context("/repo/hooks/foo.py", "code-standards")
    assert "/repo/hooks/foo.py" in text
    assert "code-standards" in text


def test_build_reminder_context_is_factual_not_imperative():
    """The wording states a fact about which skill governs the artifact class
    -- it must not read as a command ("load the skill", "you must") or any
    other system-directive phrasing that prompt-injection defenses could
    flag and surface to the user instead of folding into context."""
    text = reminder.build_reminder_context("/repo/hooks/foo.py", "code-standards")
    assert "governed by" in text
    lowered = text.lower()
    for imperative in ("load it", "you must", "before finishing"):
        assert imperative not in lowered, (
            f"reminder text reads as an instruction ({imperative!r}), not a "
            "factual observation"
        )


def test_build_reminder_context_names_the_loadable_skill():
    """The skill must be named in its loadable form so the agent can act on
    the observation without further lookup."""
    text = reminder.build_reminder_context("/repo/hooks/foo.py", "code-standards")
    assert "Skill('code-standards')" in text


def test_build_reminder_context_tagged_for_easy_grepping():
    text = reminder.build_reminder_context("/repo/hooks/foo.py", "code-standards")
    assert text.startswith("[SKILL_REMINDER]")


# ---------------------------------------------------------------------------
# cleanup_stale_markers
# ---------------------------------------------------------------------------


def test_cleanup_noop_when_cache_dir_absent():
    # REMINDER_CACHE_DIR points at a tmp_path subdir that was never created.
    reminder.cleanup_stale_markers()  # must not raise


def test_cleanup_removes_marker_older_than_ttl():
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    path = reminder._marker_path("sess-1", "aabc123")
    assert path.is_file()

    import json as _json
    written_at = _json.loads(path.read_text(encoding="utf-8"))["updated_at"]
    far_future = written_at + reminder.REMINDER_TTL_SECONDS + 1
    reminder.cleanup_stale_markers(now=far_future)

    assert not path.is_file()


def test_cleanup_keeps_marker_within_ttl():
    reminder.should_remind("sess-1", "aabc123", "code-standards")
    path = reminder._marker_path("sess-1", "aabc123")

    import json as _json
    data = _json.loads(path.read_text(encoding="utf-8"))
    just_inside_ttl = data["updated_at"] + reminder.REMINDER_TTL_SECONDS - 1
    reminder.cleanup_stale_markers(now=just_inside_ttl)

    assert path.is_file()


def test_cleanup_removes_unparseable_marker_file():
    path = reminder._marker_path("sess-1", "aabc123")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    reminder.cleanup_stale_markers()

    assert not path.is_file()


def test_cleanup_only_touches_json_files(tmp_path):
    reminder.REMINDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stray = reminder.REMINDER_CACHE_DIR / "not-a-marker.txt"
    stray.write_text("irrelevant", encoding="utf-8")

    reminder.cleanup_stale_markers(now=1_000_000.0 + reminder.REMINDER_TTL_SECONDS + 1)

    assert stray.is_file()
