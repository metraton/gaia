"""Reject kubectl Secret reads whose output can expose secret values."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass


_READ_VERBS = frozenset({"get", "list"})
_SECRET_RESOURCES = frozenset({"secret", "secrets"})
_VALUE_OUTPUTS = frozenset(
    {"json", "yaml", "jsonpath", "jsonpath-as-json", "go-template", "template"}
)
_FLAGS_WITH_VALUES = frozenset(
    {
        "--as",
        "--as-group",
        "--cache-dir",
        "--certificate-authority",
        "--client-certificate",
        "--client-key",
        "--cluster",
        "--context",
        "--field-selector",
        "--kubeconfig",
        "--label-columns",
        "--namespace",
        "--request-timeout",
        "--selector",
        "--server",
        "--token",
        "--user",
        "-A",
        "-l",
        "-n",
        "-o",
    }
)
_SENSITIVE_COLUMN = re.compile(
    r"(?:^|[.])(?:data|stringdata|annotations)(?:[.\[]|$)|last-applied-configuration",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KubectlSecretOutputViolation:
    """Explain why a kubectl Secret read must be refused before execution."""

    reason: str
    suggestion: str


def check_kubectl_secret_output(command: str) -> KubectlSecretOutputViolation | None:
    """Return a violation when a kubectl Secret read selects value-bearing output."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or os.path.basename(tokens[0]).lower() != "kubectl":
        return None

    verb_index = _first_read_verb(tokens)
    if verb_index is None:
        return None
    resource = _first_positional(tokens, verb_index + 1)
    if resource is None or not _is_secret_resource(resource):
        return None

    output = _output_value(tokens)
    if output is None:
        return None
    output_kind, _, expression = output.partition("=")
    normalized = output_kind.strip().lower()
    if normalized in _VALUE_OUTPUTS:
        return _violation(normalized)
    if normalized == "custom-columns" and _SENSITIVE_COLUMN.search(expression):
        return _violation(normalized)
    return None


def _first_read_verb(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens[1:], start=1):
        if token.lower() in _READ_VERBS:
            return index
    return None


def _first_positional(tokens: list[str], start: int) -> str | None:
    skip_value = False
    for token in tokens[start:]:
        if skip_value:
            skip_value = False
            continue
        if token in _FLAGS_WITH_VALUES:
            skip_value = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def _is_secret_resource(token: str) -> bool:
    resource = token.split("/", 1)[0].split(".", 1)[0].lower()
    return resource in _SECRET_RESOURCES


def _output_value(tokens: list[str]) -> str | None:
    for index, token in enumerate(tokens):
        lowered = token.lower()
        if lowered in {"-o", "--output"}:
            return tokens[index + 1] if index + 1 < len(tokens) else ""
        if lowered.startswith("--output="):
            return token.split("=", 1)[1]
        if lowered.startswith("-o="):
            return token.split("=", 1)[1]
        if lowered.startswith("-o") and len(token) > 2:
            return token[2:]
    return None


def _violation(output: str) -> KubectlSecretOutputViolation:
    return KubectlSecretOutputViolation(
        reason=f"kubectl Secret output '{output}' can expose secret values",
        suggestion=(
            "Use the default/name output for Secret metadata, safe custom-columns limited "
            "to metadata fields, or 'kubectl describe secret NAME' for key names and sizes."
        ),
    )
