"""Recognize reads whose output can carry secrets, and run them with that output redacted.

A Bash result reaches the host whatever the command's exit code, and a hook
cannot rewrite the output of a Bash call that exits non-zero. So the command
itself is rewritten before it runs: this file, executed as a script, runs the
original command, redacts what it printed and exits with the original code.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

PROFILE_TEXT = "text"
PROFILE_SECRET_STORE = "secret-store"
PROFILE_SECRET_VALUE = "secret-value"
#: Strongest first: a command is redacted by the strongest profile any of its stages needs.
_PROFILES_BY_STRENGTH = (PROFILE_SECRET_VALUE, PROFILE_SECRET_STORE, PROFILE_TEXT)

_OPERATORS = frozenset({"|", "||", "&&", ";", "&", "|&", "(", ")"})
_KUBECTL_VERBS = frozenset({"get", "describe"})
_KUBECTL_RESOURCES = frozenset(
    {"secret", "secrets", "deployment", "deployments", "deploy", "configmap", "configmaps", "cm"}
)
_KUBECTL_FLAGS_WITH_VALUES = frozenset(
    {
        "--as", "--as-group", "--cluster", "--context", "--field-selector", "--kubeconfig",
        "--namespace", "--output", "--selector", "--server", "--token", "--user",
        "-l", "-n", "-o",
    }
)
_SECRET_STORES = frozenset({"vault", "bao"})
_STORE_KV_READS = frozenset({"get", "read"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_GIT_FLAGS_WITH_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_GCLOUD_SECRET_READ = ("secrets", "versions", "access")
_AWS_SECRET_READ = ("secretsmanager", "get-secret-value")
_AWS_DECRYPTING_READS = frozenset({"get-parameter", "get-parameters", "get-parameters-by-path"})
_SOPS_DECRYPT_FLAGS = frozenset({"-d", "--decrypt"})


def redaction_profile(command: str) -> str | None:
    """Return how a command's output must be redacted, or None when it reads no secrets."""
    profiles = {_stage_profile(stage) for stage in _stages(command)}
    return next((profile for profile in _PROFILES_BY_STRENGTH if profile in profiles), None)


def redacted_command(command: str, profile: str) -> str:
    """Wrap a command so it runs unchanged and only its redacted output is printed."""
    return " ".join(
        shlex.quote(part) for part in (sys.executable, str(Path(__file__).resolve()), profile, command)
    )


def _stages(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token in _OPERATORS:
            stages.append([])
        else:
            stages[-1].append(token)
    return [_unwrapped(stage) for stage in stages if stage]


def _unwrapped(stage: list[str]) -> list[str]:
    """Drop the leading wrappers and assignments the T3 detector also peels, leaving the read itself."""
    from modules.security.mutative_verbs import _peel_leading_command_wrappers

    remainder, _ = _peel_leading_command_wrappers(shlex.join(stage))
    return shlex.split(remainder)


def _stage_profile(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    program = os.path.basename(tokens[0]).lower()
    args = tokens[1:]
    if program == "kubectl" and _kubectl_reads_values(args):
        return PROFILE_TEXT
    if program in _SECRET_STORES and _store_reads_secret(args):
        return PROFILE_SECRET_STORE
    if _prints_secret_value(program, args):
        return PROFILE_SECRET_VALUE
    if program == "rg" or (program in _GREPS and _grep_is_recursive(args)):
        return PROFILE_TEXT
    if program == "git" and _positionals(args, _GIT_FLAGS_WITH_VALUES)[:1] == ["grep"]:
        return PROFILE_TEXT
    return None


def _kubectl_reads_values(args: list[str]) -> bool:
    positionals = _positionals(args, _KUBECTL_FLAGS_WITH_VALUES)
    if len(positionals) < 2 or positionals[0].lower() not in _KUBECTL_VERBS:
        return False
    kinds = (part.split("/", 1)[0].split(".", 1)[0].lower() for part in positionals[1].split(","))
    return any(kind in _KUBECTL_RESOURCES for kind in kinds)


def _store_reads_secret(args: list[str]) -> bool:
    positionals = [token.lower() for token in _positionals(args)]
    if positionals[:1] == ["read"]:
        return True
    return positionals[:1] == ["kv"] and len(positionals) > 1 and positionals[1] in _STORE_KV_READS


def _prints_secret_value(program: str, args: list[str]) -> bool:
    """Whether a cloud secret manager or sops read prints a decrypted secret value.

    The subcommand words are searched as a run anywhere among the positionals,
    so a global flag's value before them (``--project prod``) does not hide them.
    """
    words = [token.lower() for token in _positionals(args)]
    if program == "gcloud":
        return _has_run(words, _GCLOUD_SECRET_READ)
    if program == "aws":
        decrypting = "--with-decryption" in args and any(
            _has_run(words, ("ssm", verb)) for verb in _AWS_DECRYPTING_READS
        )
        return decrypting or _has_run(words, _AWS_SECRET_READ)
    if program == "sops":
        return bool(_SOPS_DECRYPT_FLAGS.intersection(args)) or "decrypt" in words
    return False


def _has_run(words: list[str], run: tuple[str, ...]) -> bool:
    return any(tuple(words[start : start + len(run)]) == run for start in range(len(words)))


def _grep_is_recursive(args: list[str]) -> bool:
    for token in args:
        if token in {"--recursive", "--dereference-recursive"}:
            return True
        if token.startswith("-") and not token.startswith("--") and token[1:].isalpha():
            if "r" in token or "R" in token:
                return True
    return False


def _positionals(args: list[str], flags_with_values: frozenset[str] = frozenset()) -> list[str]:
    positionals: list[str] = []
    skip = False
    for token in args:
        if skip:
            skip = False
        elif token in flags_with_values:
            skip = True
        elif not token.startswith("-"):
            positionals.append(token)
    return positionals


def _run(profile: str, command: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gaia.redaction import redact_secret_store_output, redact_secret_value_output, redact_text

    redact = {
        PROFILE_SECRET_VALUE: redact_secret_value_output,
        PROFILE_SECRET_STORE: redact_secret_store_output,
    }.get(profile, redact_text)
    completed = subprocess.run(["bash", "-c", command], capture_output=True)
    for stream, data in ((sys.stdout, completed.stdout), (sys.stderr, completed.stderr)):
        stream.write(redact(data.decode("utf-8", errors="replace")))
        stream.flush()
    code = completed.returncode
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(_run(sys.argv[1], sys.argv[2]))
