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
_QUOTED_VALUE = re.compile(
    rf"(?P<prefix>['\"]?{_SENSITIVE_KEY_PATTERN}['\"]?\s*:\s*)"
    rf"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
_PLAIN_VALUE = re.compile(
    rf"(?P<prefix>\b{_SENSITIVE_KEY_PATTERN}\b\s*[:=]\s*)"
    r"(?P<value>(?!Bearer\b)[^\s,}\]]+)",
    re.IGNORECASE,
)
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
    redacted = _BEARER_VALUE.sub(rf"\g<prefix>{REDACTED}", redacted)
    redacted = _QUOTED_VALUE.sub(_replace_quoted_value, redacted)
    redacted = _PLAIN_VALUE.sub(rf"\g<prefix>{REDACTED}", redacted)
    return _FLAG_VALUE.sub(rf"\g<prefix>{REDACTED}", redacted)


def redact_tree(value: Any) -> Any:
    """Return a recursively redacted copy of a JSON-like value."""
    if isinstance(value, Mapping):
        secret_object = str(value.get("kind", "")).lower() == "secret"
        redacted = {}
        for key, child in value.items():
            if _is_sensitive_key(key):
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


def _parse_json_container(text: str) -> dict[str, Any] | list[Any] | None:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SENSITIVE_KEYS


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
