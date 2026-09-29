"""Plan-first COMMAND_SET validation and immutable command fingerprints.

Requests contain Bash invocations that have already been shown to the user.
The runtime accepts one or more exact T3 commands, each a single program or a
pipeline whose highest stage is T3. Chains, protected paths, interactive
programs, unquoted parentheses, and commands that are safe or permanently
blocked are rejected before an approval row can be minted: a request the
runtime could only refuse after the user signed is refused here instead.
"""

from __future__ import annotations

import hashlib
import json
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
        if not is_t3:
            raise CommandSetValidationError(f"command[{index}] is not classified T3")
        normalized.append(
            {"command": raw, "fingerprint": command_fingerprint(raw), "rationale": ""}
        )
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
