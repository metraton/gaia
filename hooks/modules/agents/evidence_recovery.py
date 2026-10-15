"""Evidence entries rebuilt from the tool calls the host recorded for a turn."""

import re
from typing import AbstractSet, Dict, Iterable, List

from gaia.redaction import redact_text

RECOVERED_MARK = "[recovered from transcript] "

# These calls are how the turn wrote its record, not evidence it gathered.
_CONTRACT_BOOKKEEPING = re.compile(r"^\s*gaia\s+contract\b")
_CONTRACT_FINALIZE = re.compile(r"^\s*gaia\s+contract\s+finalize\b")


def _row_segments(calls: List, failed_tool_use_ids: AbstractSet[str]) -> range:
    """The segments that belong to the row being closed.

    A finalized row is never modified, so a segment whose finalize succeeded
    closed its row and the work after it went to a new one. The row being
    closed ends at the last such segment and starts after the one before it;
    a refused finalize closed nothing.
    """
    closing = sorted({
        call.segment for call in calls
        if call.tool_name == "Bash"
        and isinstance(call.arguments.get("command"), str)
        and _CONTRACT_FINALIZE.match(call.arguments["command"])
        and call.tool_use_id not in failed_tool_use_ids
    })
    last = closing[-1] if closing else max((call.segment for call in calls), default=0)
    first = closing[-2] + 1 if len(closing) > 1 else 0
    return range(first, last + 1)


def recovered_entries(
    tool_sequence: Iterable, failed_tool_use_ids: AbstractSet[str] = frozenset(),
) -> Dict[str, List[str]]:
    """Marked ``commands_run`` and ``files_checked`` entries for the Bash
    commands and Read paths of the segments that belong to the row being
    closed; never ``verbatim_outputs``."""
    calls = list(tool_sequence)
    segments = _row_segments(calls, failed_tool_use_ids)
    commands: List[str] = []
    files: List[str] = []
    for call in calls:
        if call.segment not in segments:
            continue
        if call.tool_name == "Bash":
            command = call.arguments.get("command")
            if isinstance(command, str) and command.strip() and not _CONTRACT_BOOKKEEPING.match(command):
                commands.append(RECOVERED_MARK + redact_text(command))
        elif call.tool_name == "Read":
            path = call.arguments.get("file_path")
            entry = RECOVERED_MARK + path if isinstance(path, str) and path else None
            if entry and entry not in files:
                files.append(entry)
    return {"commands_run": commands, "files_checked": files}
