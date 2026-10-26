"""Finalize-time checks that a contract's cited commands, excerpts and evidence ids are backed by the turn's tool record and the evidence store."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Tuple

from gaia.contract.validator import _canonical_verification_type
from gaia.redaction import redact_text

TRUNCATION_SUFFIX = "...[truncated]"
BARE_VERDICTS = frozenset({
    "pass", "passed", "ok", "fail", "failed", "true", "false", "done", "green", "yes", "success",
})
_ELISION = re.compile(r"\.\.\.|…")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _recorded_outputs(records: List[Mapping[str, Any]]) -> Dict[str, List[Tuple[str, bool]]]:
    outputs: Dict[str, List[Tuple[str, bool]]] = {}
    for record in records:
        command = record.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        held = outputs.setdefault(_norm(command), [])
        excerpt = record.get("excerpt")
        if isinstance(excerpt, str) and excerpt.strip():
            truncated = excerpt.endswith(TRUNCATION_SUFFIX)
            held.append((_norm(excerpt[: -len(TRUNCATION_SUFFIX)] if truncated else excerpt), truncated))
    return outputs


def _found_in_order(excerpt: str, output: str) -> bool:
    position = 0
    for fragment in filter(None, (_norm(part) for part in _ELISION.split(redact_text(excerpt)))):
        position = output.find(fragment, position)
        if position < 0:
            return False
        position += len(fragment)
    return True


def _evidence_errors(path: str, cited: Any, evidence_exists: Callable[[int], bool]) -> List[str]:
    ids = cited if isinstance(cited, list) else [cited]
    errors = []
    for value in ids:
        if isinstance(value, bool) or not str(value).strip().isdigit():
            errors.append(f"{path}: {value!r} is not an evidence id")
        elif not evidence_exists(int(str(value).strip())):
            errors.append(f"{path}: evidence id {value} does not exist (gaia evidence show {value})")
    return errors


def check_content(
    envelope: Mapping[str, Any], records: List[Mapping[str, Any]], *, bound: bool,
    evidence_exists: Callable[[int], bool],
) -> Tuple[List[str], List[str]]:
    """Return (errors, skipped): what the tool record or evidence store refutes, and each check left unmade with its reason."""
    report = envelope.get("evidence_report") or {}
    outputs = _recorded_outputs(records) if bound else {}
    errors: List[str] = []
    skipped: List[str] = []
    unbound = "no tool record is bound to this turn"

    verification = report.get("verification")
    if isinstance(verification, dict) and _canonical_verification_type(verification.get("type")) in ("command", "code"):
        command = _norm(str(verification.get("command") or ""))
        cited = verification.get("evidence_ids")
        if cited not in (None, []):
            errors += _evidence_errors("evidence_report.verification.evidence_ids", cited, evidence_exists)
        elif command.lower() in BARE_VERDICTS:
            errors.append(
                f"evidence_report.verification.command {command!r} is a verdict, not a check: name the "
                "command a verifier re-runs, as this turn ran it, or cite verification.evidence_ids"
            )
        elif not bound:
            skipped.append(f"evidence_report.verification.command not compared: {unbound}")
        elif _norm(redact_text(command)) not in outputs:
            errors.append(
                f"evidence_report.verification.command {command!r} is not a command this turn ran: run it "
                "and name it exactly as run, or cite verification.evidence_ids"
            )

    for index, entry in enumerate(report.get("verbatim_outputs") or []):
        if not isinstance(entry, dict):
            continue
        path = f"evidence_report.verbatim_outputs[{index}]"
        if "evidence_id" in entry:
            errors += _evidence_errors(f"{path}.evidence_id", entry["evidence_id"], evidence_exists)
        command, excerpt = entry.get("command"), entry.get("excerpt")
        if not isinstance(command, str) or not isinstance(excerpt, str):
            continue
        held = outputs.get(_norm(redact_text(command)))
        if not bound:
            skipped.append(f"{path} not compared: {unbound}")
        elif held is None:
            skipped.append(f"{path} not compared: {command!r} is not in this turn's tool record")
        elif not held:
            skipped.append(f"{path} not compared: the record holds no output for {command!r}")
        elif any(_found_in_order(excerpt, text) for text, _ in held):
            continue
        elif any(truncated for _, truncated in held):
            skipped.append(f"{path} not compared: the recorded output of {command!r} is truncated and its head lacks the excerpt")
        else:
            errors.append(f"{path}: the excerpt is not in the recorded output of {command!r}")
    return errors, skipped
