"""Plan-first COMMAND_SET validation and immutable command fingerprints.

Requests contain Bash invocations that have already been shown to the user.
The runtime accepts an ordered sequence of steps, each a single program or a
pipeline whose highest stage decides its tier, holding at least one exact T3
command; a step that does not classify T3 is kept as an unsigned step of the
same sequence. Chains, protected paths, interactive programs, unquoted
parentheses, and permanently blocked commands are rejected before an approval
row can be minted: a request the runtime could only refuse after the user
signed is refused here instead.

A signature covers what a signed step executes, not only its bytes: each file
the step runs or reads is sealed by content or by stat identity
(:func:`sealed_files`) and must be unchanged when the item is matched
(:func:`files_unchanged`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable


class CommandSetValidationError(ValueError):
    """Raised when a proposed request-set is not eligible for COMMAND_SET."""


# Separators are read on the unquoted projection, since `python -c "a; b"` is
# one program; substitution runs even inside double quotes, so it is read raw.
_CHAIN = re.compile(r"(?:&&|\|\||;|\n)")
_SUBSTITUTION = re.compile(r"(?:`|\$\()")
# The trailing lookahead rejects a hyphen where `\b` accepted one: `ssh\b`
# matched `ssh-keygen`, a batch tool that never prompts, and refused it as
# interactive. Every alternative here names a program, and a hyphen starts a
# different program's name, never the same one's arguments.
_INTERACTIVE = re.compile(
    r"^(?:sudo\s+)?(?:vim?|nano|emacs|less|more|top|htop|watch|ssh|mysql|psql)(?![-\w])"
)
# An interpreter is interactive only when it is left to open its REPL: no
# program argument (script, -c/-e/-p code, -m module) or an explicit -i.
_INTERPRETER = re.compile(r"^(?:python(?:\d+(?:\.\d+)*)?|node)$")
_PROGRAM_FLAGS = frozenset({"-c", "-e", "--eval", "-p", "--print", "-m"})
_REPL_FLAGS = frozenset({"-i", "--interactive"})


def _blank_quoted(command: str) -> str:
    """Return ``command`` with quoted and escaped characters blanked, same length.

    What survives is what the shell itself reads as syntax. Raises
    CommandSetValidationError for a quote left open.
    """
    out: list[str] = []
    quote = None
    escaped = False
    for char in command:
        if escaped:
            out.append(" ")
            escaped = False
        elif char == "\\" and quote != "'":
            out.append(" ")
            escaped = True
        elif quote:
            out.append(char if char == quote else " ")
            if char == quote:
                quote = None
        else:
            if char in "'\"":
                quote = char
            out.append(char)
    if quote or escaped:
        raise CommandSetValidationError("has an unterminated quote or escape")
    return "".join(out)


def pipe_stages(command: str) -> list[str]:
    """Split ``command`` at its unquoted pipes; a command with none is one stage."""
    stages, start = [], 0
    for position, char in enumerate(_blank_quoted(command)):
        if char == "|":
            stages.append(command[start:position].strip())
            start = position + 1
    stages.append(command[start:].strip())
    return stages


def _unquoted_parenthesis_argument(command: str) -> str | None:
    """Name the argument holding an unquoted parenthesis, or None when there is none.

    A value token is named by the flag before it (``--title``), so the reason
    points at the argument the requester wrote rather than a fragment of it.
    """
    projection = _blank_quoted(command)
    spans = [match.span() for match in re.finditer(r"\S+", projection)]
    for index, (start, end) in enumerate(spans):
        if "(" in projection[start:end] or ")" in projection[start:end]:
            token = command[start:end]
            previous = command[slice(*spans[index - 1])] if index else ""
            if previous.startswith("-") and not token.startswith("-"):
                return previous.split("=", 1)[0]
            return token.split("=", 1)[0]
    return None


def _opens_repl(stage: str) -> bool:
    """True when an interpreter stage would start an interactive session."""
    tokens = shlex.split(stage)
    if tokens and tokens[0] == "sudo":
        tokens = tokens[1:]
    if not tokens or not _INTERPRETER.match(Path(tokens[0]).name):
        return False
    for token in tokens[1:]:
        if token in _REPL_FLAGS:
            return True
        if token in _PROGRAM_FLAGS or not token.startswith("-"):
            return False
    return True


#: The largest file sealed by its content. Hashing streams, so the bound is not
#: memory: it keeps the hook's match of a signed item fast, and a larger file
#: is sealed by its stat identity instead.
SEALED_FILE_MAX_BYTES = 64 * 1024 * 1024
_SCRIPT_RUNNER = re.compile(
    r"^(?:(?:ba|z|da|k)?sh|python(?:\d+(?:\.\d+)*)?|node|ruby|perl|php)$"
)
_INLINE_PROGRAM_FLAGS = _PROGRAM_FLAGS | {"-"}
#: Flags whose value may be a file or not (``git push -f origin``): a value that
#: is not a file yet is sealed as absent, so creating it later breaks the match.
_MAYBE_FILE_FLAGS = frozenset({"-f", "--file", "--filename", "--values"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")


def _file_operands(stage: str) -> list[tuple[str, bool]]:
    """Name each file ``stage`` runs or reads, with whether it must already exist.

    A file the stage surely runs or reads must exist: an interpreter's script,
    a program given as a path, and the value of a ``--<name>-file`` flag. A
    ``-f``-style value may be a file or a word, so it need not.
    """
    tokens = shlex.split(stage)
    while tokens and (tokens[0] == "sudo" or _ASSIGNMENT.match(tokens[0])):
        tokens = tokens[1:]
    if not tokens:
        return []
    operands: list[tuple[str, bool]] = []
    if "/" in tokens[0]:
        operands.append((tokens[0], True))
    elif _SCRIPT_RUNNER.match(tokens[0]):
        for token in tokens[1:]:
            if token in _INLINE_PROGRAM_FLAGS:
                break
            if not token.startswith("-"):
                operands.append((token, True))
                break
    for position, token in enumerate(tokens[1:], start=1):
        name, has_value, value = token.partition("=")
        if not has_value:
            value = tokens[position + 1] if position + 1 < len(tokens) else ""
        if not name.startswith("-") or not value or value == "-":
            continue
        if name.startswith("--") and name.endswith("-file"):
            operands.append((value, True))
        elif name in _MAYBE_FILE_FLAGS and (has_value or not value.startswith("-")):
            operands.append((value, False))
    return operands


def _content_digest(path: str) -> str:
    """The sha256 of the file at ``path``."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_compiled(path: str) -> bool:
    """True for an ELF binary."""
    with open(path, "rb") as handle:
        return handle.read(4) == b"\x7fELF"


def _file_seal(path: str) -> dict:
    """How the file at ``path`` is sealed now: by content, by stat identity, or as absent.

    ``{"path", "sha256"}`` holds the content digest, None when nothing is
    there. An ELF binary and a file past SEALED_FILE_MAX_BYTES are sealed as
    ``{"path", "stat"}``: device, inode, size, mtime_ns and ctime_ns. An ELF
    is an installed program rather than something the requester wrote, so
    what must hold is that it is the same file; ctime cannot be set from user
    space, so a replacement or an in-place rewrite always changes the identity.
    Raises CommandSetValidationError for a directory or other non-regular file.
    """
    if not os.path.lexists(path):
        return {"path": path, "sha256": None}
    if not os.path.isfile(path):
        raise CommandSetValidationError(
            f"reads {path}, which is not a regular file; name the files it holds"
        )
    info = os.stat(path)
    if info.st_size > SEALED_FILE_MAX_BYTES or _is_compiled(path):
        identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        return {"path": path, "stat": identity}
    return {"path": path, "sha256": _content_digest(path)}


def sealed_files(command: str, cwd: str) -> list[dict]:
    """Seal every file ``command`` runs or reads, resolved from ``cwd`` (:func:`_file_seal`).

    Raises CommandSetValidationError for a required file that does not exist
    yet and for any file :func:`_file_seal` cannot seal.
    """
    seals: dict[str, dict] = {}
    for stage in pipe_stages(command):
        for operand, required in _file_operands(stage):
            path = os.path.normpath(os.path.join(cwd, os.path.expanduser(operand)))
            if path in seals:
                continue
            if required and not os.path.lexists(path):
                raise CommandSetValidationError(
                    f"runs or reads {operand}, which does not exist yet; write it before "
                    "requesting -- the signature seals its content"
                )
            try:
                seals[path] = _file_seal(path)
            except OSError as exc:
                raise CommandSetValidationError(f"cannot read {operand}: {exc.strerror}") from exc
    return list(seals.values())


def files_unchanged(item: dict) -> bool:
    """True when every file sealed in ``item`` still seals exactly as it did when signed."""
    for seal in item.get("files") or ():
        try:
            if _file_seal(seal["path"]) != seal:
                return False
        except (CommandSetValidationError, OSError):
            return False
    return True


def command_fingerprint(command: str) -> str:
    """Return the hard byte fingerprint used at request and execution time."""
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def request_fingerprint(commands: Iterable[str]) -> str:
    """Return an order-sensitive fingerprint for an exact command sequence."""
    payload = json.dumps(list(commands), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_request_set(
    commands: list[str], *, cwd: str | None = None, cwds: list[str] | None = None,
) -> list[dict]:
    """Validate and normalize a plan-first set without minting or consuming grants.

    ``cwds`` gives each command the directory it will run in, so a relative
    script is read where it will execute; ``cwd`` applies one to all.
    """
    if not isinstance(commands, list) or len(commands) < 1:
        raise CommandSetValidationError("COMMAND_SET requires at least one command")

    hooks_dir = str(Path(__file__).resolve().parents[2] / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)

    from modules.security.blocked_commands import is_blocked_command
    from modules.security.flag_classifiers import (
        OUTCOME_BLOCKED,
        OUTCOME_MUTATIVE,
        classify_by_flags,
    )
    from modules.security.mutative_verbs import detect_mutative_command
    from modules.security.protected_path_guard import check as check_protected

    normalized: list[dict] = []
    for index, raw in enumerate(commands):
        if not isinstance(raw, str) or not raw or raw != raw.strip():
            raise CommandSetValidationError(f"command[{index}] must be a non-empty exact string")
        try:
            if _SUBSTITUTION.search(raw) or _CHAIN.search(_blank_quoted(raw)):
                raise CommandSetValidationError("must be one atomic program or pipeline, not a chain")
            parenthesized = _unquoted_parenthesis_argument(raw)
            stages = pipe_stages(raw)
            if any(not stage for stage in stages):
                raise CommandSetValidationError("has an empty pipeline stage")
            interactive = any(_INTERACTIVE.search(s) or _opens_repl(s) for s in stages)
        except (CommandSetValidationError, ValueError) as exc:
            raise CommandSetValidationError(f"command[{index}] {exc}") from exc
        if parenthesized:
            raise CommandSetValidationError(
                f"command[{index}] argument {parenthesized} has unquoted parentheses; "
                "quote its value -- the signed bytes are the bytes that run, "
                "so Gaia never re-quotes them"
            )
        if interactive:
            raise CommandSetValidationError(f"command[{index}] is interactive")
        protected_allowed, _protected_reason = check_protected(raw)
        if not protected_allowed:
            raise CommandSetValidationError(f"command[{index}] targets a protected path")
        if any(is_blocked_command(part).is_blocked for part in [raw, *stages]):
            raise CommandSetValidationError(f"command[{index}] is permanently blocked")
        if len(stages) > 1:
            _require_executable_pipeline(index, raw)

        stage_cwd = cwds[index] if cwds else cwd
        is_t3 = False
        for stage in stages:
            flag = classify_by_flags(stage)
            if flag is not None and flag.outcome == OUTCOME_BLOCKED:
                raise CommandSetValidationError(f"command[{index}] is permanently blocked")
            is_t3 = is_t3 or detect_mutative_command(stage, cwd=stage_cwd).is_mutative or (
                flag is not None
                and flag.outcome == OUTCOME_MUTATIVE
                and not flag.command_family.startswith("git_")
            )
        item = {"command": raw, "fingerprint": command_fingerprint(raw), "rationale": "",
                "signed": bool(is_t3)}
        if is_t3:
            try:
                files = sealed_files(raw, stage_cwd or os.getcwd())
            except (CommandSetValidationError, ValueError) as exc:
                raise CommandSetValidationError(f"command[{index}] {exc}") from exc
            if files:
                item["files"] = files
        normalized.append(item)
    if not any(item["signed"] for item in normalized):
        raise CommandSetValidationError("COMMAND_SET requires at least one command classified T3")
    return normalized


def _require_executable_pipeline(index: int, command: str) -> None:
    """Refuse a pipeline the Bash policy refuses before any grant is consulted.

    The Bash policy denies a piped cloud CLI output and a dangerous stage
    composition ahead of its COMMAND_SET match, so signing either could only
    end in a denial.
    """
    from modules.security.composition_rules import (
        CompositionDecision,
        build_composition_stages,
        check_composition,
    )
    from modules.tools.cloud_pipe_validator import validate_cloud_pipe
    from modules.tools.stage_decomposer import StageDecomposer

    if validate_cloud_pipe(command) is not None:
        raise CommandSetValidationError(
            f"command[{index}] pipes a cloud CLI, which the Bash policy refuses"
        )
    stages = build_composition_stages(StageDecomposer().decompose(command).stages)
    composition = check_composition(stages)
    if composition.decision != CompositionDecision.ALLOW:
        raise CommandSetValidationError(
            f"command[{index}] is a {composition.pattern} pipe composition, "
            "which the Bash policy refuses"
        )
