"""
Critical event detection.

Detects events that warrant context updates:
- Git commits
- Git pushes
- File modification batches
- Spec-kit milestones
"""

import os
import re
import logging
from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime

from ..core.paths import get_session_dir

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Types of critical events."""
    GIT_COMMIT = "git_commit"
    GIT_PUSH = "git_push"
    FILE_MODIFICATIONS = "file_modifications"
    SPECKIT_MILESTONE = "speckit_milestone"


@dataclass
class CriticalEvent:
    """A detected critical event."""
    event_type: EventType
    data: Dict[str, Any]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            **self.data,
        }


class CriticalEventDetector:
    """Detect critical events that warrant context updates."""

    # Track file modifications within session
    _file_modification_count: int = 0
    _file_modification_threshold: int = int(os.environ.get("FILE_MOD_THRESHOLD", "3"))

    SPECKIT_COMMANDS = [
        "/speckit.specify",
        "/speckit.plan",
        "/speckit.tasks",
        "/speckit.implement",
        "/speckit.constitution",
    ]

    def detect_git_commit(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        success: bool
    ) -> Optional[CriticalEvent]:
        """Detect successful git commit."""
        if not success or tool_name.lower() != "bash":
            return None

        command = parameters.get("command", "")
        if "git commit" not in command or not result:
            return None

        result_str = str(result)

        # Extract commit hash
        commit_hash = ""
        match = re.search(r'\[[\w\-/]+ ([a-f0-9]{7,})\]', result_str)
        if match:
            commit_hash = match.group(1)

        # Extract commit message
        commit_message = ""
        if commit_hash:
            msg_match = re.search(
                r'\[[\w\-/]+ [a-f0-9]{7,}\]\s*(.+)',
                result_str,
                re.MULTILINE
            )
            if msg_match:
                commit_message = msg_match.group(1).strip().split('\n')[0]

        return CriticalEvent(
            event_type=EventType.GIT_COMMIT,
            data={
                "commit_hash": commit_hash,
                "commit_message": commit_message,
                "command": command,
            }
        )

    def detect_git_push(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        success: bool
    ) -> Optional[CriticalEvent]:
        """Detect successful git push."""
        if not success or tool_name.lower() != "bash":
            return None

        command = parameters.get("command", "")
        if "git push" not in command or not result:
            return None

        result_str = str(result)

        # Extract branch info
        branch = ""
        match = re.search(
            r'To .+\n\s+[a-f0-9]+\.\.[a-f0-9]+\s+([\w\-/]+)\s+->',
            result_str
        )
        if match:
            branch = match.group(1)

        return CriticalEvent(
            event_type=EventType.GIT_PUSH,
            data={
                "branch": branch,
                "command": command,
            }
        )

    def detect_file_modifications(self, tool_name: str) -> Optional[CriticalEvent]:
        """Check if file modification count crosses threshold."""
        if tool_name.lower() in ["edit", "write", "notebookedit"]:
            CriticalEventDetector._file_modification_count += 1

            if CriticalEventDetector._file_modification_count >= self._file_modification_threshold:
                count = CriticalEventDetector._file_modification_count
                CriticalEventDetector._file_modification_count = 0

                return CriticalEvent(
                    event_type=EventType.FILE_MODIFICATIONS,
                    data={"modification_count": count}
                )
        return None

    def detect_speckit_milestone(
        self,
        tool_name: str,
        parameters: Dict[str, Any]
    ) -> Optional[CriticalEvent]:
        """Detect spec-kit milestone commands."""
        if tool_name.lower() != "slashcommand":
            return None

        command = parameters.get("command", "")
        for speckit_cmd in self.SPECKIT_COMMANDS:
            if speckit_cmd in command:
                return CriticalEvent(
                    event_type=EventType.SPECKIT_MILESTONE,
                    data={"command": speckit_cmd}
                )
        return None

    def detect_all(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any = None,
        success: bool = True
    ) -> List[CriticalEvent]:
        """Run all detectors and return found events."""
        events = []

        # Git commit
        event = self.detect_git_commit(tool_name, parameters, result, success)
        if event:
            events.append(event)

        # Git push
        event = self.detect_git_push(tool_name, parameters, result, success)
        if event:
            events.append(event)

        # File modifications
        event = self.detect_file_modifications(tool_name)
        if event:
            events.append(event)

        # Speckit milestone
        event = self.detect_speckit_milestone(tool_name, parameters)
        if event:
            events.append(event)

        return events


# Singleton detector
_detector: Optional[CriticalEventDetector] = None


def get_detector() -> CriticalEventDetector:
    """Get singleton event detector."""
    global _detector
    if _detector is None:
        _detector = CriticalEventDetector()
    return _detector


def detect_critical_event(
    tool_name: str,
    parameters: Dict[str, Any],
    result: Any = None,
    success: bool = True
) -> List[CriticalEvent]:
    """Detect critical events (convenience function)."""
    return get_detector().detect_all(tool_name, parameters, result, success)
