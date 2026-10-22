"""`gaia map` HABITS and skill names stay worded as the orchestrator identity that owns them."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IDENTITY = (REPO / "agents" / "gaia-orchestrator.md").read_text(encoding="utf-8")

_spec = importlib.util.spec_from_file_location("gaia_cli_map", REPO / "bin" / "cli" / "map.py")
gaia_map = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gaia_map)

HABIT_ANCHORS = (
    ("a doubt, an idea or a pending", "Where it goes", "The user voices a doubt, an idea or a pending"),
    ("beside something to do", "offer a reminder in one line", "A time or a date is mentioned beside something to do"),
    ("ids arrive in a return", "project's thread row", "Ids arrive in a return"),
    ("(N) count", "gaia context project <name>", "a `(N)` count sits beside a name"),
    ("did not name", "only on their yes", "I offer it in one line after the answer"),
)


def test_each_habit_carries_the_wording_of_its_identity_row():
    habits = [f"{event} -> {act}" for event, act in gaia_map.HABITS]
    assert len(habits) == len(HABIT_ANCHORS)
    for line, (event_anchor, act_anchor, identity_anchor) in zip(habits, HABIT_ANCHORS):
        assert event_anchor.lower() in line.lower() and act_anchor in line, line
        assert identity_anchor in IDENTITY, identity_anchor


def test_map_names_skills_by_their_current_names():
    text = gaia_map.render_map([], "ws") + "\n" + "\n".join(gaia_map.render_intent(i) for i in gaia_map.INTENTS)
    assert "technical-writing" in text and "deck-builder" in text
    assert "readme-writing" not in text and "diagram-builder" not in text
    for skill in ("technical-writing", "deck-builder"):
        assert (REPO / "skills" / skill / "SKILL.md").is_file()
