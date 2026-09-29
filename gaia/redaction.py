"""Redact secret-bearing values while preserving diagnostic structure."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "accesskey",
        "apikey",
        "auth",
        "authorization",
        "clientkey",
        "clientsecret",
        "credential",
        "credentials",
        "key",
        "password",
        "passwd",
        "privatekey",
        "secret",
        "secretkey",
        "token",
    }
)
_SENSITIVE_KEY_PATTERN = (
    r"(?:access[-_]?key|api[-_]?key|auth(?:orization)?|client[-_]?(?:key|secret)|"
    r"credentials?|key|passw(?:or)?d|private[-_]?key|secret(?:[-_]?key)?|token)"
)
_NAME_QUALIFIERS = r"(?:[A-Za-z0-9]+[-_])*"
_QUOTED_VALUE = re.compile(
    rf"(?P<prefix>['\"]?{_SENSITIVE_KEY_PATTERN}['\"]?\s*:\s*)"
    rf"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_PLAIN_VALUE = re.compile(
    rf"(?P<prefix>\b{_NAME_QUALIFIERS}{_SENSITIVE_KEY_PATTERN}\b\s*[:=]\s*)"
    r"(?P<value>(?!Bearer\b)[^\s,}\]]+)",
    re.IGNORECASE,
)
_SENSITIVE_NAME = re.compile(_SENSITIVE_KEY_PATTERN, re.IGNORECASE)
_QUALIFIED_KEY = re.compile(rf"{_NAME_QUALIFIERS}{_SENSITIVE_KEY_PATTERN}", re.IGNORECASE)
_YAML_ENV_PAIR = re.compile(
    r"(?m)^(?P<prefix>(?P<indent>[ \t]*)-[ \t]+name:[ \t]*['\"]?(?P<name>[^'\"\s]+)['\"]?[ \t]*\r?\n"
    r"(?P=indent)[ \t]+value:[ \t]*)(?P<value>\S.*)$"
)
_TABLE_RULE = re.compile(r"^-{3,}\s+-{3,}\s*$")
_TABLE_ROW = re.compile(r"^(?P<key>\S+)(?P<gap>\s+)\S.*$")
_FLAG_VALUE = re.compile(
    rf"(?P<prefix>--{_SENSITIVE_KEY_PATTERN}(?:=|\s+))(?P<value>[^\s]+)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(
    r"(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._~+/=-]+)",
    re.IGNORECASE,
)
_SECRET_KIND = re.compile(r"(?m)^\s*kind\s*:\s*Secret\s*(?:#.*)?$")
_YAML_DOCUMENT = re.compile(r"(?m)(^---\s*$)")
_YAML_SECTION = re.compile(r"^(?P<indent>\s*)(?:data|stringData)\s*:\s*(?:#.*)?$")
_YAML_VALUE = re.compile(
    r"^(?P<indent>\s+)(?P<key>[^:#][^:]*):(?P<space>\s*)(?P<value>.*)$"
)


def redact_text(text: str) -> str:
    """Return text with recognizable secret values replaced by a stable marker."""
    if not isinstance(text, str) or not text:
        return text

    parsed = _parse_json_container(text)
    if parsed is not None:
        return json.dumps(redact_tree(parsed), ensure_ascii=False)

    redacted = _redact_secret_yaml(text)
    redacted = _YAML_ENV_PAIR.sub(_replace_env_pair, redacted)
    redacted = _BEARER_VALUE.sub(rf"\g<prefix>{REDACTED}", redacted)
    redacted = _QUOTED_VALUE.sub(_replace_quoted_value, redacted)
    redacted = _PLAIN_VALUE.sub(rf"\g<prefix>{REDACTED}", redacted)
    return _FLAG_VALUE.sub(rf"\g<prefix>{REDACTED}", redacted)


def redact_tree(value: Any) -> Any:
    """Return a recursively redacted copy of a JSON-like value."""
    if isinstance(value, Mapping):
        secret_object = str(value.get("kind", "")).lower() == "secret"
        sensitive_env = isinstance(value.get("name"), str) and bool(
            _SENSITIVE_NAME.search(value["name"])
        )
        redacted = {}
        for key, child in value.items():
            if _is_sensitive_key(key) or (sensitive_env and key == "value"):
                redacted[key] = REDACTED
            elif secret_object and str(key).lower() in {"data", "stringdata"}:
                redacted[key] = _redact_all_leaves(child)
            else:
                redacted[key] = redact_tree(child)
        return redacted
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_tree(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_secret_store_output(text: str) -> str:
    """Return a secret store's read output with every stored value withheld.

    Any key of a secret engine may hold a secret, so values are withheld by
    position rather than by key name: JSON leaves under ``data``, and the value
    column of a key/value table. Output of any other shape (a raw ``-field``
    value, YAML) cannot be told apart from a bare secret, so it is withheld whole.
    """
    if not isinstance(text, str) or not text.strip():
        return text
    parsed = _parse_json_container(text)
    if parsed is not None:
        return json.dumps(_redact_data_leaves(parsed), ensure_ascii=False, indent=2) + "\n"
    lines = text.splitlines(keepends=True)
    rule = next((i for i, line in enumerate(lines) if _TABLE_RULE.match(line)), None)
    if rule is None:
        return f"{REDACTED}\n"
    for index in range(rule + 1, len(lines)):
        row = _TABLE_ROW.match(lines[index].rstrip("\r\n"))
        if row:
            ending = lines[index][len(lines[index].rstrip("\r\n")) :]
            lines[index] = f"{row.group('key')}{row.group('gap')}{REDACTED}{ending}"
    return "".join(lines)


def has_clear_secret(text: str) -> bool:
    """Say whether text carries a recognizable secret value written in clear.

    A shell reference (``$TOKEN``, ``${TOKEN}``) names where the value lives
    rather than the value, so it is not a secret in clear. A bare ``key`` is
    redacted in output but is too common a data field name to refuse on.
    """
    if not isinstance(text, str) or not text:
        return False
    for pattern in (_BEARER_VALUE, _QUOTED_VALUE, _PLAIN_VALUE, _FLAG_VALUE):
        for match in pattern.finditer(text):
            value = match.group("value").strip("'\"")
            name = re.sub(r"[^a-z]", "", match.group("prefix").lower())
            if name == "key":
                continue
            if value and not value.startswith("$") and value != REDACTED:
                return True
    return False


def _redact_data_leaves(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _redact_all_leaves(child) if str(key).lower() == "data" else _redact_data_leaves(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_data_leaves(item) for item in value]
    return value


def _replace_env_pair(match: re.Match[str]) -> str:
    if not _SENSITIVE_NAME.search(match.group("name")):
        return match.group(0)
    return f"{match.group('prefix')}{REDACTED}"


def _parse_json_container(text: str) -> dict[str, Any] | list[Any] | None:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_KEYS or bool(_QUALIFIED_KEY.fullmatch(str(key)))


def _redact_all_leaves(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _redact_all_leaves(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_all_leaves(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_redact_all_leaves(child) for child in value)
    return REDACTED


def _replace_quoted_value(match: re.Match[str]) -> str:
    quote = match.group("quote")
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"


def _redact_secret_yaml(text: str) -> str:
    parts = _YAML_DOCUMENT.split(text)
    return "".join(
        _redact_yaml_document(part) if not _YAML_DOCUMENT.fullmatch(part) else part
        for part in parts
    )


def _redact_yaml_document(document: str) -> str:
    if not _SECRET_KIND.search(document):
        return document

    lines = document.splitlines(keepends=True)
    section_indent: int | None = None
    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        section = _YAML_SECTION.match(content)
        if section:
            section_indent = len(section.group("indent"))
            continue
        if section_indent is None or not content.strip():
            continue
        value = _YAML_VALUE.match(content)
        if value and len(value.group("indent")) > section_indent:
            ending = line[len(content) :]
            lines[index] = (
                f"{value.group('indent')}{value.group('key')}:{value.group('space')}"
                f"{REDACTED}{ending}"
            )
            continue
        if len(content) - len(content.lstrip()) <= section_indent:
            section_indent = None
    return "".join(lines)
