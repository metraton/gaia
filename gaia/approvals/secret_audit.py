"""Find clear secrets stored by the approval flow (read-only) and write the SQL that scrubs them."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterator

from gaia.approvals.chain import canonical_payload
from gaia.redaction import REDACTED, TOKEN_SHAPE

# Narrower than the request-time refusal on purpose: each finding becomes an UPDATE of append-only history.
_BEARER = re.compile(r"(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{16,})", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"(?P<prefix>(?<![A-Za-z0-9_-])[A-Za-z0-9_]*"
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY)[A-Za-z0-9_]*=['\"]?)"
    r"(?P<value>[A-Za-z0-9][^\s'\"&\\]{7,})",
    re.IGNORECASE,
)
_SECRET_MANAGER_REFERENCE = re.compile(r"[\w.-]+:(?:latest|\d+)(?:,|$)")
_BARE_WORDS = re.compile(r"[A-Za-z]+(?:[-_][A-Za-z]+)*")
_PASSWORD_WORD_PLUS_DIGITS = re.compile(r"(?:password|secret|token|changeme|example|test)\d*", re.IGNORECASE)

#: (table, key column, text column) for every column the approval flow writes free text into.
SCANNED_COLUMNS = (
    ("approvals", "id", "payload_json"),
    ("approval_events", "id", "payload_json"),
    ("approval_events", "id", "metadata_json"),
    ("approval_grants", "approval_id", "command_set_json"),
    ("approval_grants", "approval_id", "failure_reason"),
)
_EVENTS_TRIGGER = "bu_approval_events_immutable"


@dataclass(frozen=True)
class Finding:
    table: str
    key: object
    column: str
    scrubbed: str


def _is_credential(value: str) -> bool:
    return not (
        _SECRET_MANAGER_REFERENCE.match(value)
        or _BARE_WORDS.fullmatch(value)
        or _PASSWORD_WORD_PLUS_DIGITS.fullmatch(value)
    )


def _mask_literal(match: re.Match[str]) -> str:
    return match.group("prefix") + REDACTED if _is_credential(match.group("value")) else match.group(0)


def _mask_string(text: str) -> str:
    text = _ASSIGNMENT.sub(_mask_literal, text)
    text = _BEARER.sub(_mask_literal, text)
    return TOKEN_SHAPE.sub(REDACTED, text)


def _mask(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_string(value)
    if isinstance(value, dict):
        return {key: _mask(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_mask(child) for child in value]
    return value


def scrub_text(text: str) -> str:
    """Return ``text`` with every real secret redacted and everything else byte-identical; JSON stays canonical JSON."""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, (dict, list)):
        masked = _mask(parsed)
        return text if masked == parsed else canonical_payload(masked)
    return _mask_string(text)


def open_read_only(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def scan(con: sqlite3.Connection) -> Iterator[Finding]:
    """Yield one finding per stored text value that carries a clear secret."""
    for table, key, column in SCANNED_COLUMNS:
        for row_key, text in con.execute(f"SELECT {key}, {column} FROM {table} WHERE {column} IS NOT NULL"):
            scrubbed = scrub_text(text)
            if scrubbed != text:
                yield Finding(table, row_key, column, scrubbed)


def _literal(value: object) -> str:
    return str(value) if isinstance(value, int) else "'" + str(value).replace("'", "''") + "'"


def scrub_sql(con: sqlite3.Connection, findings: list[Finding]) -> str:
    """The one transaction that writes each scrubbed text back, recreating the append-only events trigger inside it."""
    keys = {table: key for table, key, _ in SCANNED_COLUMNS}
    updates = {"approval_events": [], "other": []}
    for finding in findings:
        statement = (
            f"UPDATE {finding.table} SET {finding.column} = {_literal(finding.scrubbed)} "
            f"WHERE {keys[finding.table]} = {_literal(finding.key)};"
        )
        updates["approval_events" if finding.table == "approval_events" else "other"].append(statement)
    lines = ["BEGIN IMMEDIATE;", *updates["other"]]
    if updates["approval_events"]:
        trigger = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (_EVENTS_TRIGGER,)).fetchone()
        lines += [f"DROP TRIGGER {_EVENTS_TRIGGER};", *updates["approval_events"], f"{trigger[0]};"]
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"
