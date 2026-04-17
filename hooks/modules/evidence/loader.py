"""
Brief frontmatter loader.

Contract:
    load_acs(brief_path: Path) -> list[Evidence]

Evidence fields:
    id: str              # "AC-1", "AC-2", ...
    description: str
    type: str            # command | url | playwright | artifact | metric
    shape: dict          # type-specific
    artifact: str        # relative artifact path

Exceptions:
    InvalidBriefFrontmatter -- YAML parse error or missing '---' markers.
    UnknownEvidenceType     -- evidence.type outside the allowed set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_ALLOWED_EVIDENCE_TYPES = frozenset({
    "command",
    "url",
    "playwright",
    "artifact",
    "metric",
})


class InvalidBriefFrontmatter(Exception):
    """Raised when the brief has no frontmatter or the YAML block is malformed."""


class UnknownEvidenceType(Exception):
    """Raised when evidence.type is not in the allowed set."""


@dataclass
class Evidence:
    """One acceptance criterion with its evidence spec."""

    id: str
    description: str
    type: str
    shape: dict
    artifact: str


def _extract_frontmatter(text: str) -> dict:
    """Parse the YAML block between two '---' markers at the top of `text`.

    Raises InvalidBriefFrontmatter when markers are absent or YAML is malformed.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        raise InvalidBriefFrontmatter("Brief is missing opening '---' marker")

    # Find the closing '---' on its own line.
    closing_index = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            closing_index = i
            break
    if closing_index is None:
        raise InvalidBriefFrontmatter("Brief is missing closing '---' marker")

    yaml_block = "".join(lines[1:closing_index])
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        raise InvalidBriefFrontmatter(f"Invalid YAML in frontmatter: {exc}") from exc

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise InvalidBriefFrontmatter(
            f"Frontmatter must be a mapping, got {type(parsed).__name__}"
        )
    return parsed


def load_acs(brief_path: Path) -> list[Evidence]:
    """Parse `brief_path` and return its acceptance_criteria as Evidence objects."""
    text = Path(brief_path).read_text(encoding="utf-8")
    frontmatter = _extract_frontmatter(text)

    raw_acs: Any = frontmatter.get("acceptance_criteria")
    if raw_acs is None:
        return []
    if not isinstance(raw_acs, list):
        raise InvalidBriefFrontmatter(
            "acceptance_criteria must be a list"
        )

    evidences: list[Evidence] = []
    for entry in raw_acs:
        if not isinstance(entry, dict):
            raise InvalidBriefFrontmatter(
                "Each acceptance_criteria entry must be a mapping"
            )

        ev_spec = entry.get("evidence") or {}
        ev_type = ev_spec.get("type", "")
        if ev_type not in _ALLOWED_EVIDENCE_TYPES:
            raise UnknownEvidenceType(
                f"Unknown evidence type: {ev_type!r} "
                f"(allowed: {sorted(_ALLOWED_EVIDENCE_TYPES)})"
            )

        shape = ev_spec.get("shape") or {}
        evidences.append(
            Evidence(
                id=str(entry.get("id", "")),
                description=str(entry.get("description", "")),
                type=ev_type,
                shape=dict(shape) if isinstance(shape, dict) else {},
                artifact=str(entry.get("artifact", "")),
            )
        )
    return evidences
