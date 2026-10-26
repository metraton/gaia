"""Finalize-time checks that a contract's cited commands, excerpts and evidence ids are backed by the turn's tool record and the evidence store."""

from __future__ import annotations

import re
import shlex
import shutil
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia.redaction import redact_text

TRUNCATION_SUFFIX = "...[truncated]"
BARE_VERDICTS = frozenset({
    "pass", "passed", "ok", "fail", "failed", "true", "false", "done", "green", "yes", "success",
})
COMMAND_RUNNING_BUILTINS = frozenset({"cd", "source", ".", "export", "command", "time"})
_ELISION = re.compile(r"\.\.\.|…")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=.*")

Run = Tuple[str, bool, Optional[int]]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _recorded_runs(records: List[Mapping[str, Any]]) -> Dict[str, List[Run]]:
    runs: Dict[str, List[Run]] = {}
    for record in records:
        command = record.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        excerpt = record.get("excerpt") if isinstance(record.get("excerpt"), str) else ""
        truncated = excerpt.endswith(TRUNCATION_SUFFIX)
        output = _norm(excerpt[: -len(TRUNCATION_SUFFIX)] if truncated else excerpt)
        runs.setdefault(_norm(command), []).append((output, truncated, record.get("exit_code")))
    return runs


def _fragments(excerpt: str) -> List[str]:
    return [fragment for fragment in (_norm(part) for part in _ELISION.split(redact_text(excerpt))) if fragment]


def _token_pattern(fragment: str) -> "re.Pattern[str]":
    head = r"(?<!\w)" if re.match(r"\w", fragment) else ""
    tail = r"(?!\w)" if re.search(r"\w\Z", fragment) else ""
    return re.compile(head + re.escape(fragment) + tail)


def _found_in_order(fragments: List[str], output: str) -> bool:
    position = 0
    for fragment in fragments:
        match = _token_pattern(fragment).search(output, position)
        if match is None:
            return False
        position = match.end()
    return True


def runs_a_program(command: str) -> bool:
    """True when a shell could run ``command``: past leading assignments its first word is a path, a program on PATH, or a builtin that runs one, and not a verdict word."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    program = next((word for word in words if not _ASSIGNMENT.fullmatch(word)), "")
    if not program or re.sub(r"\W+\Z", "", program.lower()) in BARE_VERDICTS:
        return False
    return "/" in program or program in COMMAND_RUNNING_BUILTINS or shutil.which(program) is not None


def _evidence_errors(path: str, cited: Any, evidence_exists: Callable[[int], bool]) -> List[str]:
    ids = cited if isinstance(cited, list) else [cited]
    errors = []
    for value in ids:
        if isinstance(value, bool) or not str(value).strip().isdigit():
            errors.append(f"{path}: {value!r} is not an evidence id")
        elif not evidence_exists(int(str(value).strip())):
            errors.append(f"{path}: evidence id {value} does not exist (gaia evidence show {value})")
    return errors


def _exit_errors(verification: Mapping[str, Any], command: str, runs: List[Run]) -> Tuple[List[str], List[str]]:
    expected = verification.get("expect_exit", 0)
    if isinstance(expected, bool) or not isinstance(expected, int):
        return [f"evidence_report.verification.expect_exit {expected!r} is not an exit code"], []
    if str(verification.get("result") or "").strip().lower() != "pass":
        return [], []
    last_exit = runs[-1][2]
    if last_exit is None:
        return [], [f"evidence_report.verification exit not compared: the record holds no exit code for {command!r}"]
    if last_exit != expected:
        return [
            f"evidence_report.verification.command {command!r} last exited {last_exit}, so it cannot back result "
            f"pass: run it until it exits {expected}, or declare verification.expect_exit when that exit is the "
            "expected outcome"
        ], []
    return [], []


def _verification_errors(
    verification: Mapping[str, Any], recorded: Dict[str, List[Run]], *, bound: bool,
    evidence_exists: Callable[[int], bool],
) -> Tuple[List[str], List[str]]:
    command = _norm(verification["command"]) if isinstance(verification.get("command"), str) else ""
    cited = verification.get("evidence_ids")
    if cited not in (None, []):
        return _evidence_errors("evidence_report.verification.evidence_ids", cited, evidence_exists), []
    if not command:
        return [], []
    if command.lower() in BARE_VERDICTS:
        return [
            f"evidence_report.verification.command {command!r} is a verdict, not a check: name the "
            "command a verifier re-runs, as this turn ran it, or cite verification.evidence_ids"
        ], []
    if not bound and not runs_a_program(command):
        return [
            f"evidence_report.verification.command {command!r} names nothing a shell could run (a path, a "
            "program on PATH, or a builtin that runs one): name the command a verifier re-runs, or cite "
            "verification.evidence_ids"
        ], []
    if not bound:
        return [], ["evidence_report.verification.command not compared: no tool record is bound to this turn"]
    runs = recorded.get(_norm(redact_text(command)))
    if runs is None:
        return [
            f"evidence_report.verification.command {command!r} is not a command this turn ran: run it "
            "and name it exactly as run, or cite verification.evidence_ids"
        ], []
    return _exit_errors(verification, command, runs)


def check_content(
    envelope: Mapping[str, Any], records: List[Mapping[str, Any]], *, bound: bool,
    evidence_exists: Callable[[int], bool],
) -> Tuple[List[str], List[str]]:
    """Return (errors, skipped): what the tool record or evidence store refutes, and each check left unmade with its reason."""
    report = envelope.get("evidence_report") or {}
    recorded = _recorded_runs(records) if bound else {}
    errors: List[str] = []
    skipped: List[str] = []

    verification = report.get("verification")
    if isinstance(verification, dict):
        errors, skipped = _verification_errors(verification, recorded, bound=bound, evidence_exists=evidence_exists)

    for index, entry in enumerate(report.get("verbatim_outputs") or []):
        if not isinstance(entry, dict):
            continue
        path = f"evidence_report.verbatim_outputs[{index}]"
        if "evidence_id" in entry:
            errors += _evidence_errors(f"{path}.evidence_id", entry["evidence_id"], evidence_exists)
        command, excerpt = entry.get("command"), entry.get("excerpt")
        if not isinstance(command, str) or not isinstance(excerpt, str):
            continue
        fragments = _fragments(excerpt)
        runs = recorded.get(_norm(redact_text(command)))
        held = [(output, truncated) for output, truncated, _ in runs or [] if output]
        if not fragments:
            errors.append(f"{path}: the excerpt quotes nothing: quote the output lines it attests")
        elif not bound:
            skipped.append(f"{path} not compared: no tool record is bound to this turn")
        elif runs is None and "evidence_id" not in entry:
            errors.append(
                f"{path}: {command!r} is not a command this turn ran: attribute the excerpt to the command "
                "exactly as run, or cite the evidence_id that holds it"
            )
        elif runs is None:
            continue
        elif not held:
            skipped.append(f"{path} not compared: the record holds no output for {command!r}")
        elif any(_found_in_order(fragments, output) for output, _ in held):
            continue
        elif any(truncated for _, truncated in held):
            skipped.append(f"{path} not compared: the recorded output of {command!r} is truncated and its head lacks the excerpt")
        else:
            errors.append(f"{path}: the excerpt is not in the recorded output of {command!r}")
    return errors, skipped
