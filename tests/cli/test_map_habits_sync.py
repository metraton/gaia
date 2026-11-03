"""`gaia map` HABITS and skill names stay worded as the skill or identity section that owns each habit."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
IDENTITY = REPO / "agents" / "gaia-orchestrator.md"
MEMORY = REPO / "skills" / "memory" / "SKILL.md"
AGENT_RESPONSE = REPO / "skills" / "agent-response" / "SKILL.md"

_spec = importlib.util.spec_from_file_location("gaia_cli_map", REPO / "bin" / "cli" / "map.py")
gaia_map = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gaia_map)

HABIT_ANCHORS = (
    ("a doubt, an idea or a pending", "Where it goes", MEMORY, "voices a doubt, an idea or a pending"),
    ("beside something to do", "one-line proposal of a reminder, saved on their yes", IDENTITY,
     "one-line proposal of a reminder, saved on their yes"),
    ("ids arrive in a return", "project's thread row", AGENT_RESPONSE, "Ids that arrive in a return"),
    ("names a thing", "gaia map <word>", IDENTITY, "`gaia map <word>` for a word it does not place"),
    ("did not name", "only on their yes", IDENTITY, "act on it only on their yes"),
)


def test_each_habit_carries_the_wording_of_its_owner():
    habits = [f"{event} -> {act}" for event, act in gaia_map.HABITS]
    assert len(habits) == len(HABIT_ANCHORS)
    for line, (event_anchor, act_anchor, owner, owner_anchor) in zip(habits, HABIT_ANCHORS):
        assert event_anchor.lower() in line.lower() and act_anchor in line, line
        assert owner_anchor in owner.read_text(encoding="utf-8"), f"{owner.name}: {owner_anchor}"


def test_map_names_skills_by_their_current_names():
    text = gaia_map.render_map([], "ws") + "\n" + "\n".join(gaia_map.render_intent(i) for i in gaia_map.INTENTS)
    assert "technical-writing" in text and "deck-builder" in text
    assert "readme-writing" not in text and "diagram-builder" not in text
    for skill in ("technical-writing", "deck-builder"):
        assert (REPO / "skills" / skill / "SKILL.md").is_file()
