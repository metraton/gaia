"""Evidence entries rebuilt from the tool calls the host recorded for a turn."""

import re
from typing import Dict, Iterable, List

from gaia.redaction import redact_text

RECOVERED_MARK = "[recovered from transcript] "

# These calls are how the turn wrote its record, not evidence it gathered.
_CONTRACT_BOOKKEEPING = re.compile(r"^\s*gaia\s+contract\b")


def recovered_entries(tool_sequence: Iterable) -> Dict[str, List[str]]:
    """Marked ``commands_run`` and ``files_checked`` entries for the Bash
    commands and Read paths in ``tool_sequence``; never ``verbatim_outputs``."""
    commands: List[str] = []
    files: List[str] = []
    for call in tool_sequence:
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
