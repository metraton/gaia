"""Finalize-time checks that a contract's cited commands, excerpts and evidence ids are backed by the turn's tool record and the evidence store."""

from __future__ import annotations

import re
import shlex
import shutil
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia.redaction import redact_text

TRUNCATION_SUFFIX = "...[truncated]"
PASS_VERDICTS = frozenset({"pass", "passed", "ok", "true", "done", "green", "yes", "success"})
BARE_VERDICTS = PASS_VERDICTS | {"fail", "failed", "false"}
QUOTE_KEYS = ("excerpt", "output")
COMMAND_RUNNING_BUILTINS = frozenset({"cd", "source", ".", "export", "command", "time"})
_ELISION = re.compile(r"\.\.\.|…")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=.*")

Run = Tuple[str, bool, Optional[int]]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _verdict_word(text: str) -> str:
    return re.sub(r"\W+\Z", "", text.strip().lower())


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
    if not program or _verdict_word(program) in BARE_VERDICTS:
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
    result = str(verification.get("result") or "").strip()
    if _verdict_word(result) not in PASS_VERDICTS:
        return [], []
    last_exit = runs[-1][2]
    if last_exit is None:
        return [], [f"evidence_report.verification exit not compared: the record holds no exit code for {command!r}"]
    if last_exit != expected:
        return [
            f"evidence_report.verification.command {command!r} last exited {last_exit}, so it cannot back result "
            f"{result!r}: run it until it exits {expected}, or declare verification.expect_exit when that exit is "
            "the expected outcome"
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
        path = f"evidence_report.verbatim_outputs[{index}]"
        fields = entry if isinstance(entry, dict) else {"excerpt": entry}
        cited = "evidence_id" in fields
        if cited:
            errors += _evidence_errors(f"{path}.evidence_id", fields["evidence_id"], evidence_exists)
        quotes = [fields[key] for key in QUOTE_KEYS if isinstance(fields.get(key), str)]
        command = fields.get("command")
        if quotes and not isinstance(command, str) and not cited:
            skipped.append(f"{path} not compared: it names no command and cites no evidence_id")
            continue
        if not quotes or not isinstance(command, str):
            continue
        for excerpt in quotes:
            error, skip = _excerpt_finding(path, command, excerpt, recorded, bound=bound, cited=cited)
            if error and error not in errors:
                errors.append(error)
            if skip and skip not in skipped:
                skipped.append(skip)
    return errors, skipped


def _excerpt_finding(
    path: str, command: str, excerpt: str, recorded: Dict[str, List[Run]], *, bound: bool, cited: bool,
) -> Tuple[Optional[str], Optional[str]]:
    fragments = _fragments(excerpt)
    runs = recorded.get(_norm(redact_text(command)))
    held = [(output, truncated) for output, truncated, _ in runs or [] if output]
    if not fragments:
        return f"{path}: the excerpt quotes nothing: quote the output lines it attests", None
    if not bound:
        return None, f"{path} not compared: no tool record is bound to this turn"
    if runs is None and not cited:
        return (
            f"{path}: {command!r} is not a command this turn ran: attribute the excerpt to the command "
            "exactly as run, or cite the evidence_id that holds it"
        ), None
    if runs is None or any(_found_in_order(fragments, output) for output, _ in held):
        return None, None
    if not held:
        return None, f"{path} not compared: the record holds no output for {command!r}"
    if any(truncated for _, truncated in held):
        return None, f"{path} not compared: the recorded output of {command!r} is truncated and its head lacks the excerpt"
    return f"{path}: the excerpt is not in the recorded output of {command!r}", None
