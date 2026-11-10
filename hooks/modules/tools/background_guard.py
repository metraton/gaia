"""A background shell command keeps its output off the host's task spool, and a turn does not close while one it started still runs."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, Iterable, List, Optional

from .shell_parser import drop_redirections, shell_split
from .stage_decomposer import StageDecomposer

logger = logging.getLogger(__name__)

BACKGROUND_OUTPUT_TAG = "[BACKGROUND_OUTPUT]"
BACKGROUND_TASK_LIVE_TAG = "[BACKGROUND_TASK_LIVE]"
SHELL_TASK_TYPE = "shell"
ENDED_STATUSES = frozenset({"completed", "failed", "killed", "stopped"})
SHELL_TOOL_NAMES = frozenset({"Bash", "PowerShell", "Monitor"})

_OUTPUT_REDIRECT = re.compile(r"^(\d+|&|\*)?(>>|>\||>&|>)(.*)$")
_FOLLOW_FLAGS = frozenset({"-f", "-F", "--follow", "--retry"})
_SAFE_STREAMS = frozenset({"pipe", "file", "sink"})


def _destination(target: str) -> str:
    if target == "/dev/null":
        return "sink"
    home = os.path.expanduser("~")
    for prefix in ("~", "${HOME}", "$HOME"):
        if target == prefix or target.startswith(prefix + "/"):
            target = home + target[len(prefix):]
            break
    if any(ch in target for ch in "$`*?[") or not os.path.isabs(target):
        return "unknown"
    from gaia.paths import scratch_dir, tmp_dir

    resolved = os.path.normpath(target)
    for root in (scratch_dir(), tmp_dir()):
        root_text = os.path.normpath(str(root))
        if resolved.startswith(root_text + os.sep):
            return "file"
    return "elsewhere"


def _streams(words: List[str], piped: bool) -> Dict[str, str]:
    streams = {"1": "pipe" if piped else "spool", "2": "spool"}
    index = 0
    while index < len(words):
        match = _OUTPUT_REDIRECT.match(words[index])
        index += 1
        if not match:
            continue
        fd, operator, target = match.group(1) or "1", match.group(2), match.group(3)
        if not target and index < len(words):
            target, index = words[index], index + 1
        if operator == ">&" and (target.isdigit() or target == "-"):
            if target == "-":
                streams[fd] = "sink"
            elif target in streams:
                streams[fd] = streams[target]
            continue
        destination = _destination(target)
        if fd in ("&", "*") or (operator == ">&" and fd == "1"):
            streams["1"] = streams["2"] = destination
        elif fd in streams:
            streams[fd] = destination
    return streams


def _bounded_filter(words: List[str]) -> bool:
    program = drop_redirections(words)
    if not program:
        return False
    name = os.path.basename(program[0])
    if name == "sleep":
        return True
    if name not in ("head", "tail") or (name == "tail" and _FOLLOW_FLAGS & set(program)):
        return False
    values: List[str] = []
    for position, word in enumerate(program[1:], start=1):
        if word in ("-n", "-c") and position + 1 < len(program):
            values.append(program[position + 1])
        elif re.match(r"^-[nc].", word):
            values.append(word[2:])
        elif word.startswith(("--lines=", "--bytes=")):
            values.append(word.split("=", 1)[1])
    return not any(value.startswith("-") for value in values)


def background_output_refusal(command: str) -> Optional[str]:
    """The refusal for a background command whose stdout or stderr can reach the host's spool, or None when every stream is bounded."""
    for stage in StageDecomposer().decompose(command).stages:
        try:
            words = shell_split(stage.command)
        except ValueError:
            words = stage.command.split()
        streams = _streams(words, piped=stage.operator == "|")
        if _bounded_filter(words):
            continue
        for fd, name in (("1", "stdout"), ("2", "stderr")):
            if streams[fd] not in _SAFE_STREAMS:
                return _output_refusal(stage.command, name, streams[fd])
    return None


def _output_refusal(stage: str, stream: str, destination: str) -> str:
    where = {
        "spool": "goes to the host's task spool",
        "unknown": "goes to a path this hook cannot resolve",
        "elsewhere": "goes to a file outside Gaia's scratch",
    }[destination]
    return (
        f"{BACKGROUND_OUTPUT_TAG} Refusing a background command whose {stream} {where}: `{stage}`. "
        "The host spools a background command's output into the system temp directory with no size cap, "
        "so an unbounded listing fills that volume and stops every session on the machine. "
        "Run it in the background with both streams in a file under Gaia's scratch -- "
        "`<command> &> ~/.gaia/scratch/<contract_id>.log` -- and Read that file for progress, "
        "or end the pipeline in a bounded filter (`<command> 2>&1 | head -n 200`). "
        "A command whose output you need in the reply runs in the foreground. Not approvable."
    )


def _shell_result_texts(transcript_path: str) -> Iterable[str]:
    shell_calls: set = set()
    with open(transcript_path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            tool_use_result = record.get("toolUseResult")
            if isinstance(tool_use_result, dict) and tool_use_result.get("backgroundTaskId"):
                yield str(tool_use_result["backgroundTaskId"])
            content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") in SHELL_TOOL_NAMES:
                    shell_calls.add(block.get("id"))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in shell_calls:
                    yield json.dumps(block.get("content"))


def owned_live_shell_tasks(payload: dict, transcript_path: str) -> List[dict]:
    """The in-flight shell tasks of the stop payload's ``background_tasks`` that this turn's own shell calls launched."""
    live = [
        task for task in payload.get("background_tasks") or []
        if isinstance(task, dict) and task.get("type") == SHELL_TASK_TYPE
        and str(task.get("status") or "").lower() not in ENDED_STATUSES and task.get("id")
    ]
    if not live or not transcript_path:
        return []
    try:
        texts = list(_shell_result_texts(transcript_path))
    except OSError as exc:
        logger.warning("background task ownership unreadable from %s: %s", transcript_path, exc)
        return []
    return [
        task for task in live
        if any(re.search(rf"(?<![\w-]){re.escape(str(task['id']))}(?![\w-])", text) for text in texts)
    ]


def close_refusal(payload: dict, transcript_path: str) -> Optional[str]:
    """The reason a turn may not close yet, naming each background command it started that is still running; None when there is none."""
    tasks = owned_live_shell_tasks(payload, transcript_path)
    if not tasks:
        return None
    listed = "; ".join(f"{task['id']} (`{str(task.get('command') or task.get('description') or '')[:200]}`)" for task in tasks)
    first = tasks[0]["id"]
    return (
        f"{BACKGROUND_TASK_LIVE_TAG} This turn started background commands that are still running: {listed}. "
        "A command left running after its turn keeps writing to the host's spool with no one left to stop it. "
        f"Stop each one with the TaskStop tool (task_id \"{first}\"), or keep working until the host's "
        "completion notification for it arrives in this turn, then close the turn again."
    )


def auto_background_notice(tool_response: object) -> str:
    """A note for the model when the host moved a foreground command to the background at its timeout; "" otherwise."""
    if not isinstance(tool_response, dict):
        return ""
    task_id = tool_response.get("backgroundTaskId")
    if not task_id or not tool_response.get("timedOutAfterMs") or tool_response.get("backgroundEndsWithFinalResponse"):
        return ""
    return (
        f"{BACKGROUND_OUTPUT_TAG} The host moved this command to the background at its timeout as task {task_id}, "
        "and its output now grows in the host's spool with no size cap. If its output is unbounded, stop it with "
        f"TaskStop (task_id \"{task_id}\") and rerun it with run_in_background as "
        "`<command> &> ~/.gaia/scratch/<contract_id>.log`; this turn cannot close while it runs."
    )
