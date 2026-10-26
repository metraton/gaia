"""Finalize-time checks that a contract's cited commands, excerpts and evidence ids are backed by the turn's tool record and the evidence store."""

from __future__ import annotations

import re
import shlex
import shutil
import unicodedata
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia.redaction import redact_text

TRUNCATION_SUFFIX = "...[truncated]"
PASS_WORDS = frozenset({
    "pass", "passed", "passes", "passing", "ok", "okay", "success", "successful", "succeeded", "succeeds",
    "done", "true", "green", "yes",
})
FAIL_WORDS = frozenset({"fail", "failed", "fails", "failing", "failure", "failures", "false", "error", "errors", "red"})
NEGATIONS = frozenset({"not", "never", "no", "didnt", "doesnt", "dont", "isnt", "wasnt", "cannot", "cant"})
ZERO_COUNTS = frozenset({"0", "zero", "no", "none"})
COUNTED_NOUNS = frozenset({"all", "test", "tests", "check", "checks", "case", "cases", "of"})
VERDICT_VOCABULARY = PASS_WORDS | FAIL_WORDS | NEGATIONS | ZERO_COUNTS | COUNTED_NOUNS
QUOTE_KEYS = ("excerpt", "output")
COMMAND_RUNNING_BUILTINS = frozenset({"cd", "source", ".", "export", "command", "time"})
_ELISION = re.compile(r"\.\.\.|…")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=.*")
_APOSTROPHE = re.compile(r"['’ʼ]")
_TOKEN = re.compile(r"[^\W\d_]+|\d+")
_NON_ALNUM = re.compile(r"[\W_]")
SEGMENT_LIMIT = 160

Run = Tuple[str, bool, Optional[int]]
Binding = Mapping[str, frozenset]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _mark_word(char: str) -> str:
    if not unicodedata.category(char).startswith("S"):
        return char
    name = unicodedata.name(char, "").split()
    if "CHECK" in name or name[:2] == ["THUMBS", "UP"]:
        return " pass "
    if {"CROSS", "MARK"} <= set(name) or name[-2:] == ["BALLOT", "X"] or name[:2] == ["THUMBS", "DOWN"]:
        return " fail "
    return " "


def _segment(run: str) -> Optional[List[str]]:
    """Split ``run`` wholly into numbers and verdict vocabulary words, or None when it holds anything else."""
    if len(run) > SEGMENT_LIMIT:
        return None
    splits: List[Optional[List[str]]] = [[]] + [None] * len(run)
    for end in range(1, len(run) + 1):
        for start in range(end):
            piece = run[start:end]
            if splits[start] is not None and (piece in VERDICT_VOCABULARY or piece.isdigit()):
                splits[end] = splits[start] + [piece]
                break
    return splits[-1]


def _readings(text: str) -> List[List[str]]:
    """The NFKC, case-folded words of ``text``, marks read as pass/fail: split at non-alphanumerics, rejoined per blank-separated word, and rejoined whole when that splits into verdict words."""
    folded = _APOSTROPHE.sub("", "".join(_mark_word(char) for char in unicodedata.normalize("NFKC", text)).lower())
    per_word = []
    for word in folded.split():
        run = _NON_ALNUM.sub("", word)
        per_word += _segment(run) or _TOKEN.findall(run)
    readings = [_TOKEN.findall(folded), per_word]
    whole = _segment(_NON_ALNUM.sub("", folded))
    return readings + [whole] if whole else readings


def _is_bare_verdict(text: str) -> bool:
    return any(all(token.isdigit() or token in VERDICT_VOCABULARY for token in tokens) for tokens in _readings(text))


def _counts_zero(tokens: List[str], index: int) -> bool:
    before = index - 1
    while before >= 0 and tokens[before] in COUNTED_NOUNS:
        before -= 1
    after = index + 1
    while after < len(tokens) and tokens[after] in COUNTED_NOUNS:
        after += 1
    return (before >= 0 and tokens[before] in ZERO_COUNTS) or (after < len(tokens) and tokens[after] in ZERO_COUNTS)


def _claims_pass(result: str) -> bool:
    """A result claims a pass when it holds an unnegated pass word and no failure word other than a zero count of one."""
    for tokens in _readings(result):
        passes = any(token in PASS_WORDS and (i == 0 or tokens[i - 1] not in NEGATIONS) for i, token in enumerate(tokens))
        fails = any(token in FAIL_WORDS and not _counts_zero(tokens, i) for i, token in enumerate(tokens))
        if passes and not fails:
            return True
    return False


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
    if not program or _is_bare_verdict(program):
        return False
    return "/" in program or program in COMMAND_RUNNING_BUILTINS or shutil.which(program) is not None


def _tied(row: Mapping[str, Any], binding: Binding) -> bool:
    """A turn bound to a plan task is proven only by evidence of that task; a turn bound to a brief alone, only by the brief's task-less evidence."""
    task = row.get("task_id")
    if binding["task_ids"]:
        return task is not None and str(task) in binding["task_ids"]
    return task is None and row.get("brief_id") in binding["brief_ids"]


def _evidence_errors(
    path: str, cited: Any, evidence: Callable[[int], Optional[Mapping[str, Any]]], binding: Binding, *, proves: bool,
) -> List[str]:
    """An evidence id holds only when its row exists and is tied to this turn's work; one cited as proof must also be positive."""
    ids = cited if isinstance(cited, list) else [cited]
    errors = []
    for value in ids:
        if isinstance(value, bool) or not str(value).strip().isdigit():
            errors.append(f"{path}: {value!r} is not an evidence id")
            continue
        row = evidence(int(str(value).strip()))
        if row is None:
            errors.append(f"{path}: evidence id {value} does not exist (gaia evidence show {value})")
        elif proves and row.get("polarity", "positive") != "positive":
            errors.append(
                f"{path}: evidence id {value} has polarity {row.get('polarity')!r}: it records a refutation, and "
                "only positive evidence proves a claim"
            )
        elif not _tied(row, binding):
            scope = (
                f"plan task {sorted(binding['task_ids'])}" if binding["task_ids"]
                else f"brief {sorted(binding['brief_ids'])} with no plan task, so only that brief's task-less evidence counts"
            )
            errors.append(
                f"{path}: evidence id {value} is not tied to this turn's work: it was recorded under brief "
                f"{row.get('brief_id')} and task {row.get('task_id')!r}, and this turn is bound to {scope}"
            )
    return errors


def _exit_errors(verification: Mapping[str, Any], command: str, runs: List[Run]) -> Tuple[List[str], List[str]]:
    expected = verification.get("expect_exit", 0)
    if isinstance(expected, bool) or not isinstance(expected, int):
        return [f"evidence_report.verification.expect_exit {expected!r} is not an exit code"], []
    result = str(verification.get("result") or "").strip()
    if not _claims_pass(result):
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


def _command_errors(
    verification: Mapping[str, Any], command: str, recorded: Dict[str, List[Run]], *, bound: bool, proven: bool,
) -> Tuple[List[str], List[str]]:
    if _is_bare_verdict(command):
        return [
            f"evidence_report.verification.command {command!r} is a verdict, not a check: name the "
            "command a verifier re-runs, as this turn ran it"
        ], []
    runs = recorded.get(_norm(redact_text(command)))
    if runs is not None:
        return _exit_errors(verification, command, runs)
    if proven:
        return [], []
    if not bound and not runs_a_program(command):
        return [
            f"evidence_report.verification.command {command!r} names nothing a shell could run (a path, a "
            "program on PATH, or a builtin that runs one): name the command a verifier re-runs, or cite "
            "verification.evidence_ids"
        ], []
    if not bound:
        return [], ["evidence_report.verification.command not compared: no tool record is bound to this turn"]
    return [
        f"evidence_report.verification.command {command!r} is not a command this turn ran: run it "
        "and name it exactly as run, or cite verification.evidence_ids"
    ], []


def _verification_errors(
    verification: Mapping[str, Any], recorded: Dict[str, List[Run]], *, bound: bool,
    evidence: Callable[[int], Optional[Mapping[str, Any]]], binding: Binding,
) -> Tuple[List[str], List[str]]:
    command = _norm(verification["command"]) if isinstance(verification.get("command"), str) else ""
    cited = verification.get("evidence_ids")
    errors: List[str] = []
    if cited not in (None, []):
        errors = _evidence_errors("evidence_report.verification.evidence_ids", cited, evidence, binding, proves=True)
    if not command:
        return errors, []
    command_errors, skipped = _command_errors(
        verification, command, recorded, bound=bound, proven=cited not in (None, []) and not errors,
    )
    return errors + command_errors, skipped


def _kind(value: Any) -> str:
    name = type(value).__name__
    return f"{'an' if name[0] in 'aeiou' else 'a'} {name}"


def _entry_reason(entry: Any, cited: bool) -> Optional[str]:
    """Why no quote of a verbatim entry can be compared, or None when each quote key is judged on its own."""
    if not isinstance(entry, (str, dict)):
        return f"it is {_kind(entry)}, neither a quote nor an object"
    fields = entry if isinstance(entry, dict) else {"excerpt": entry}
    if not any(key not in ("command", "evidence_id") for key in fields):
        return "it quotes nothing"
    command = fields.get("command")
    if command is not None and not isinstance(command, str):
        return f"its command is {_kind(command)}, not a string"
    if command is None:
        return (
            "it names no command; the excerpt is not compared to the text of the evidence it cites" if cited
            else "it names no command and cites no evidence_id"
        )
    return None


def check_content(
    envelope: Mapping[str, Any], records: List[Mapping[str, Any]], *, bound: bool,
    evidence: Callable[[int], Optional[Mapping[str, Any]]], binding: Binding,
) -> Tuple[List[str], List[str]]:
    """Return (errors, skipped): what the tool record or evidence store refutes, and each check left unmade with its reason.

    ``evidence`` returns an evidence row by id, or None; ``binding`` holds the ``brief_ids`` and ``task_ids``
    (as strings) of this turn's work, which a cited evidence row must belong to.
    """
    report = envelope.get("evidence_report") or {}
    recorded = _recorded_runs(records) if bound else {}
    errors: List[str] = []
    skipped: List[str] = []

    verification = report.get("verification")
    if isinstance(verification, dict):
        errors, skipped = _verification_errors(verification, recorded, bound=bound, evidence=evidence, binding=binding)

    for index, entry in enumerate(report.get("verbatim_outputs") or []):
        path = f"evidence_report.verbatim_outputs[{index}]"
        cited = isinstance(entry, dict) and "evidence_id" in entry
        if cited:
            errors += _evidence_errors(f"{path}.evidence_id", entry["evidence_id"], evidence, binding, proves=False)
        reason = _entry_reason(entry, cited)
        if reason is not None:
            skipped.append(f"{path} not compared: {reason}")
            continue
        fields = entry if isinstance(entry, dict) else {"excerpt": entry}
        for key, quote in fields.items():
            if key in ("command", "evidence_id"):
                continue
            if key not in QUOTE_KEYS:
                skipped.append(f"{path}.{key} not compared: only excerpt and output are compared to the record")
                continue
            if not isinstance(quote, str):
                skipped.append(f"{path}.{key} not compared: it is {_kind(quote)}, not a string")
                continue
            error, skip = _excerpt_finding(path, fields["command"], quote, recorded, bound=bound, cited=cited)
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
    if runs is None:
        return None, (
            f"{path} not compared: {command!r} is not a command this turn ran; the excerpt is not compared "
            "to the text of the evidence it cites"
        )
    if any(_found_in_order(fragments, output) for output, _ in held):
        return None, None
    if not held:
        return None, f"{path} not compared: the record holds no output for {command!r}"
    if any(truncated for _, truncated in held):
        return None, f"{path} not compared: the recorded output of {command!r} is truncated and its head lacks the excerpt"
    return f"{path}: the excerpt is not in the recorded output of {command!r}", None
