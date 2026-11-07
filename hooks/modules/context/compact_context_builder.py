"""The brief a host's summarizer gets before a compaction, the one text both hosts send."""
from __future__ import annotations

import json
import re
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[2] / "mods" / "compaction" / "register.ts"
_STEERING_DECLARATION = re.compile(r'^export const STEERING = ".*"$', re.MULTILINE)


def build_summary_instructions() -> str:
    """The brief a host that summarizes its own session is given before it writes the summary."""
    return (
        "# Compaction Instructions\n\n"
        "Write the summary so the next turn can resume without re-deriving anything:\n"
        "- Do not restate what the next turn's birth block already delivers: its resume "
        "point carries the active plan and task, open contracts, pending approvals and "
        "the resume note, or the command that lists them.\n"
        "- State the active objective and the exact next action.\n"
        "- Carry durable work as coordinates, never its body: task and plan ids, "
        "contract ids, approval ids, memory slugs, commit SHAs, brief names.\n"
        "- Keep facts that exist only in this conversation, labeled as not durable.\n"
        "- Compress tool output and intermediate reasoning to what the next decision needs.\n"
        "- End with this line verbatim: Live state, habits and which capability serves an "
        "intent: `gaia map`"
    )


def write_mod_steering(path: Path = MOD_PATH) -> bool:
    """Rewrites the Claude Code mod's STEERING declaration as build_summary_instructions; True when the file changed."""
    source = path.read_text()
    declaration = f"export const STEERING = {json.dumps(build_summary_instructions())}"
    updated, count = _STEERING_DECLARATION.subn(lambda _: declaration, source)
    if count != 1:
        raise ValueError(f"{path} must declare STEERING as exactly one one-line string literal; found {count}")
    if updated == source:
        return False
    path.write_text(updated)
    return True


if __name__ == "__main__":
    print(f"{MOD_PATH}: {'updated' if write_mod_steering() else 'unchanged'}")
