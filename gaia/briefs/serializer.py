"""
gaia.briefs.serializer -- Brief markdown <-> dict round-trip.

The on-disk brief format used by `/home/jorge/ws/me/briefs/*/brief.md` is:

    ---
    status: <str>
    surface_type: <str>
    acceptance_criteria:
      - id: AC-1
        description: "..."
        evidence:
          type: command
          shape:
            run: "..."
            expect: "..."
        artifact: evidence/AC-1.txt
      - id: AC-2
        ...
    ---

    # <title>

    ## Objective
    <body>

    ## Context
    <body>

    ## Approach
    <body>

    ## Acceptance Criteria
    <optional human summary>

    ## Milestones
    - **M1: name** -- description
    - **M2: name** -- description
    ...

    ## Out of Scope
    <body>

We support a strict round-trip: parse(serialize(x)) == x for all fields
the schema captures. We do NOT preserve verbatim YAML/markdown formatting
because the DB is the source of truth post-migration.

The parser is tolerant: it accepts known variants seen across the existing
briefs (lists with or without trailing colon, evidence blocks with nested
keys, ACs missing optional fields).

No external dependencies. We implement a minimal YAML subset (key: value,
list items, nested 2-space indentation) sufficient for the brief format.
"""

from __future__ import annotations

import json
import re
from typing import Any


# ---------------------------------------------------------------------------
# Frontmatter parser (minimal YAML subset)
# ---------------------------------------------------------------------------

def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Parse a frontmatter block (between leading and matching ``---`` fences).

    Supports:
      - Simple ``key: value`` pairs
      - Multi-line lists (``- item`` lines under a bare key)
      - Nested dicts via 2-space indentation under a bare key
      - List of dicts (the ``acceptance_criteria`` shape used by briefs)

    Returns a dict. Returns {} when no frontmatter or malformed.
    """
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}

    body = text[3:end].lstrip("\n")
    return _parse_yaml_block(body, 0)


def _indent_of(line: str) -> int:
    """Return number of leading spaces (treating tabs as 4 spaces, defensively)."""
    n = 0
    for ch in line:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4
        else:
            break
    return n


def _strip_quotes(s: str) -> str:
    """Strip matching surrounding double or single quotes, if present."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _coerce_scalar(s: str) -> Any:
    """Coerce a scalar string to int/float/bool/None where unambiguous; else strip quotes."""
    s = s.strip()
    if not s:
        return ""
    # Quoted -- always string
    if (s[0] == s[-1] and s[0] in ('"', "'")) and len(s) >= 2:
        return s[1:-1]
    low = s.lower()
    if low == "null" or low == "~":
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return s


def _split_lines(text: str) -> list[str]:
    return [line.rstrip("\r") for line in text.split("\n")]


def _parse_yaml_block(text: str, base_indent: int) -> dict[str, Any]:
    """Parse a contiguous YAML block into a dict, preserving structure.

    `base_indent` is the indent of the block's keys (0 for top-level frontmatter).
    """
    lines = _split_lines(text)
    result: dict[str, Any] = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue

        indent = _indent_of(line)
        if indent < base_indent:
            break
        if indent > base_indent:
            i += 1
            continue

        stripped = line.strip()
        # key: value  OR  key:
        if ":" not in stripped:
            i += 1
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if value:
            result[key] = _coerce_scalar(value)
            i += 1
            continue

        # Bare key -- inspect next non-empty line to decide list vs dict
        j = i + 1
        # Find the first non-empty/non-comment subordinate line
        while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
            j += 1
        if j >= len(lines):
            result[key] = None
            i = j
            continue
        sub_indent = _indent_of(lines[j])
        if sub_indent <= base_indent:
            result[key] = None
            i = j
            continue
        sub_line = lines[j].lstrip()
        if sub_line.startswith("- "):
            # List
            list_val, consumed = _parse_yaml_list(lines[j:], sub_indent)
            result[key] = list_val
            i = j + consumed
        else:
            # Nested dict
            sub_text = "\n".join(lines[j:])
            nested = _parse_yaml_block(sub_text, sub_indent)
            # Advance i past the nested block
            consumed = 0
            for k in range(j, len(lines)):
                if lines[k].strip() and _indent_of(lines[k]) < sub_indent and not lines[k].lstrip().startswith("#"):
                    consumed = k - j
                    break
                consumed = k - j + 1
            result[key] = nested
            i = j + consumed

    return result


def _parse_yaml_list(lines: list[str], list_indent: int) -> tuple[list[Any], int]:
    """Parse a YAML list starting at lines[0] (which begins with ``- ``).

    Each list item may be:
      - a scalar:  ``- value``
      - a dict:    ``- key: val`` followed by indented continuation lines.

    Returns (parsed_list, lines_consumed).
    """
    result: list[Any] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        indent = _indent_of(line)
        if indent < list_indent:
            break
        if indent > list_indent:
            # Belongs to previous item -- but that path is handled inside item parser
            i += 1
            continue

        stripped = line.lstrip()
        if not stripped.startswith("- "):
            break

        item_text = stripped[2:]  # strip the "- "
        # If item_text is "key: value" -> start a dict
        # If item_text contains ":" with no leading "-", treat as inline first key
        if ":" in item_text:
            # First key of a dict item
            key, _, value = item_text.partition(":")
            key = key.strip()
            value = value.strip()
            item_dict: dict[str, Any] = {}
            if value:
                item_dict[key] = _coerce_scalar(value)
            else:
                item_dict[key] = None
                # The bare-key inline value handled below alongside continuation
            # Collect continuation lines (indent > list_indent) for the same item
            j = i + 1
            block_lines: list[str] = []
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    block_lines.append(nxt)
                    j += 1
                    continue
                nxt_indent = _indent_of(nxt)
                # Same-level new item starts with "- " at list_indent -> stop
                if nxt_indent == list_indent and nxt.lstrip().startswith("- "):
                    break
                if nxt_indent <= list_indent:
                    break
                block_lines.append(nxt)
                j += 1

            # If there were continuation lines, parse them at the indent of the
            # first key continuation. Use the indent of the first non-blank
            # block line for the inner block.
            first_inner_indent = None
            for bl in block_lines:
                if bl.strip() and not bl.lstrip().startswith("#"):
                    first_inner_indent = _indent_of(bl)
                    break

            if value == "" and first_inner_indent is not None:
                # The first key was bare; its value lives in the indented block.
                # Need to also accept that subsequent keys at first_inner_indent
                # belong to the item dict.
                # Strategy: rebuild the item by treating "key:" as bare key and
                # parsing the block starting from there.
                # Simpler: treat the whole item as a YAML block parsed at
                # first_inner_indent, but we need to inject a synthetic key.
                # Instead, parse block_lines as a YAML block and merge.
                inner = _parse_yaml_block("\n".join(block_lines), first_inner_indent)
                # Decide how to merge: the bare key in item_dict needs the value
                # from the inner block keyed under this same name? No -- the
                # convention used in our briefs is that subsequent indented
                # keys belong to the dict, e.g.:
                #   - id: AC-1
                #     description: "..."
                #     evidence:
                #       type: command
                # The first `id` is always inline; the rest at the inner indent
                # are siblings. So if `value == ""` is rare; treat it as nested.
                if item_dict[key] is None:
                    # Bare first key with nested value
                    item_dict[key] = inner
                else:
                    item_dict.update(inner)
            elif first_inner_indent is not None:
                # Subsequent keys belong to the item dict (siblings of the first)
                inner = _parse_yaml_block("\n".join(block_lines), first_inner_indent)
                # Merge as siblings
                for k, v in inner.items():
                    item_dict[k] = v

            result.append(item_dict)
            i = j
        else:
            # Scalar list item
            result.append(_coerce_scalar(item_text))
            i += 1

    return result, i


# ---------------------------------------------------------------------------
# Frontmatter serializer (minimal YAML subset, matching brief.md style)
# ---------------------------------------------------------------------------

def _yaml_quote(value: Any) -> str:
    """Render a scalar as a YAML-compatible string.

    - Booleans -> true/false
    - None     -> null (skipped at higher level when desired)
    - int      -> as-is
    - str      -> double-quoted iff it contains special chars; else bare
    """
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    # Quote if contains : # or starts/ends with whitespace, or is empty
    needs_quote = (not s) or any(c in s for c in (":", "#", "\n")) or s != s.strip()
    if needs_quote:
        # Double-quote with backslash-escapes
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _serialize_yaml_value(value: Any, indent: int) -> str:
    """Serialize an arbitrary value in the briefs frontmatter style."""
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v != {} and v != []:
                lines.append(f"{pad}{k}:")
                lines.append(_serialize_yaml_value(v, indent + 2))
            else:
                lines.append(f"{pad}{k}: {_yaml_quote(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                # First key inline with "- "; remaining keys at indent+2
                items = list(item.items())
                if not items:
                    lines.append(f"{pad}- {{}}")
                    continue
                first_key, first_val = items[0]
                if isinstance(first_val, (dict, list)) and first_val != {} and first_val != []:
                    lines.append(f"{pad}- {first_key}:")
                    lines.append(_serialize_yaml_value(first_val, indent + 4))
                else:
                    lines.append(f"{pad}- {first_key}: {_yaml_quote(first_val)}")
                for k, v in items[1:]:
                    if isinstance(v, (dict, list)) and v != {} and v != []:
                        lines.append(f"{' ' * (indent + 2)}{k}:")
                        lines.append(_serialize_yaml_value(v, indent + 4))
                    else:
                        lines.append(f"{' ' * (indent + 2)}{k}: {_yaml_quote(v)}")
            else:
                lines.append(f"{pad}- {_yaml_quote(item)}")
        return "\n".join(lines)
    return f"{pad}{_yaml_quote(value)}"


# ---------------------------------------------------------------------------
# Markdown body parser (sections by `## Header`)
# ---------------------------------------------------------------------------

_KNOWN_SECTIONS = (
    "Objective",
    "Context",
    "Approach",
    "Acceptance Criteria",
    "Milestones",
    "Out of Scope",
)


def _split_body_sections(body: str) -> tuple[str, dict[str, str]]:
    """Split a markdown body into (title, {section_name: section_text}).

    Title is the first ``# Title`` line (stripped of leading ``#``).
    Sections are introduced by ``## Header`` lines and run until the next
    ``## `` header or end of file.
    """
    lines = body.split("\n")
    title = ""
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("# ") and not stripped.startswith("## ") and not title:
            title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            if current_section is not None:
                sections[current_section] = "\n".join(current_lines).strip("\n")
            current_section = stripped[3:].strip()
            current_lines = []
            continue
        if current_section is not None:
            current_lines.append(line)

    if current_section is not None:
        sections[current_section] = "\n".join(current_lines).strip("\n")

    return title, sections


def _parse_milestones_section(text: str) -> list[dict[str, str]]:
    """Parse a Milestones section into a list of {name, description} dicts.

    Supported forms (mix of variants seen in existing briefs):
      - **M1: Name** -- description text
      - **M1**: name and description
      - **M1: Name** description
    Falls back to capturing each list item as a single description.
    """
    result: list[dict[str, str]] = []
    if not text.strip():
        return result

    # Split by list bullets at start of line
    bullet_re = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
    parts = bullet_re.split(text)
    # First part is whatever preamble before the first bullet; usually empty
    items = [p.strip() for p in parts if p.strip()]

    for idx, item in enumerate(items, start=1):
        # Try to match **M<N>: <Name>** -- <desc>
        m = re.match(r"\*\*([^*:]+)(?::\s*([^*]+))?\*\*\s*[-—–]?\s*(.*)", item, re.DOTALL)
        if m:
            tag = m.group(1).strip() or f"M{idx}"
            name_part = (m.group(2) or "").strip()
            rest = (m.group(3) or "").strip()
            if name_part:
                full_name = f"{tag}: {name_part}"
            else:
                full_name = tag
            result.append({"name": full_name, "description": rest})
        else:
            result.append({"name": f"M{idx}", "description": item})

    return result


def _serialize_milestones_section(milestones: list[dict[str, str]]) -> str:
    """Serialize milestones list back to markdown bullet form."""
    if not milestones:
        return ""
    lines: list[str] = []
    for m in milestones:
        name = m.get("name", "")
        desc = m.get("description", "")
        if desc:
            lines.append(f"- **{name}** -- {desc}")
        else:
            lines.append(f"- **{name}**")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

def parse_brief_markdown(text: str) -> dict[str, Any]:
    """Parse a brief.md file into a dict suitable for DB upsert.

    Returns dict with keys::

        {
          "status":         <str|None>,
          "surface_type":   <str|None>,
          "topic_key":      <str|None>,
          "title":          <str>,
          "objective":      <str>,
          "context":        <str>,
          "approach":       <str>,
          "out_of_scope":   <str>,
          "acceptance_criteria": [
              {"ac_id": "AC-1", "description": "...",
               "evidence_type": "command", "evidence_shape": <dict>,
               "artifact_path": "evidence/AC-1.txt"},
              ...
          ],
          "milestones": [{"name": "M1: ...", "description": "..."}, ...],
          "dependencies": [<bare_brief_name>, ...],   # optional
        }

    Tolerant: missing sections / fields are omitted (not error).
    """
    fm = _parse_yaml_frontmatter(text)

    # Body = everything after the second '---' line
    body_start = 0
    if text.startswith("---"):
        try:
            end = text.index("\n---", 3)
            body_start = end + len("\n---") + 1  # skip past trailing newline
            # tolerate \r\n
            if body_start < len(text) and text[body_start] == "\n":
                body_start += 1
        except ValueError:
            body_start = 0

    body = text[body_start:]
    title, sections = _split_body_sections(body)

    # Acceptance criteria (frontmatter) -> normalized list of dicts
    raw_acs = fm.get("acceptance_criteria") or []
    acs: list[dict[str, Any]] = []
    if isinstance(raw_acs, list):
        for entry in raw_acs:
            if not isinstance(entry, dict):
                continue
            ac_id = str(entry.get("id") or "").strip()
            description = entry.get("description") or ""
            evidence = entry.get("evidence") or {}
            evidence_type = None
            evidence_shape = None
            if isinstance(evidence, dict):
                evidence_type = evidence.get("type")
                shape = evidence.get("shape")
                if shape is not None:
                    evidence_shape = shape
            artifact_path = entry.get("artifact")
            acs.append({
                "ac_id": ac_id,
                "description": str(description) if description is not None else "",
                "evidence_type": str(evidence_type) if evidence_type else None,
                "evidence_shape": evidence_shape,
                "artifact_path": str(artifact_path) if artifact_path else None,
            })

    milestones = _parse_milestones_section(sections.get("Milestones", ""))

    # Dependencies -- not standardized in existing briefs; can come from
    # frontmatter `dependencies: [...]` if present.
    deps_raw = fm.get("dependencies") or []
    deps: list[str] = []
    if isinstance(deps_raw, list):
        for d in deps_raw:
            if isinstance(d, str):
                deps.append(d)
            elif isinstance(d, dict):
                # tolerate {"name": "..."} shape
                n = d.get("name") or d.get("brief")
                if n:
                    deps.append(str(n))

    return {
        "status": fm.get("status"),
        "surface_type": fm.get("surface_type"),
        "topic_key": fm.get("topic_key"),
        "title": title,
        "objective": sections.get("Objective", ""),
        "context": sections.get("Context", ""),
        "approach": sections.get("Approach", ""),
        "out_of_scope": sections.get("Out of Scope", ""),
        "acceptance_criteria": acs,
        "milestones": milestones,
        "dependencies": deps,
    }


def serialize_brief_to_markdown(brief: dict[str, Any]) -> str:
    """Serialize a brief dict back to markdown with frontmatter.

    Inverse of parse_brief_markdown for the fields it understands. The output
    matches the on-disk format closely enough that round-trip preserves all
    structured data; whitespace and YAML key ordering are normalized.
    """
    # Build frontmatter dict in canonical order
    fm: dict[str, Any] = {}
    if brief.get("status"):
        fm["status"] = brief["status"]
    if brief.get("surface_type"):
        fm["surface_type"] = brief["surface_type"]
    if brief.get("topic_key"):
        fm["topic_key"] = brief["topic_key"]

    acs = brief.get("acceptance_criteria") or []
    if acs:
        fm_acs = []
        for ac in acs:
            entry: dict[str, Any] = {"id": ac.get("ac_id", "")}
            if ac.get("description"):
                entry["description"] = ac["description"]
            if ac.get("evidence_type") or ac.get("evidence_shape"):
                ev: dict[str, Any] = {}
                if ac.get("evidence_type"):
                    ev["type"] = ac["evidence_type"]
                if ac.get("evidence_shape"):
                    shape = ac["evidence_shape"]
                    if isinstance(shape, str):
                        try:
                            shape = json.loads(shape)
                        except Exception:
                            pass
                    ev["shape"] = shape
                entry["evidence"] = ev
            if ac.get("artifact_path"):
                entry["artifact"] = ac["artifact_path"]
            fm_acs.append(entry)
        fm["acceptance_criteria"] = fm_acs

    if brief.get("dependencies"):
        fm["dependencies"] = list(brief["dependencies"])

    # Render frontmatter
    fm_text = _serialize_yaml_value(fm, 0) if fm else ""

    parts: list[str] = []
    if fm_text:
        parts.append("---")
        parts.append(fm_text)
        parts.append("---")
        parts.append("")
    title = brief.get("title", "") or ""
    if title:
        parts.append(f"# {title}")
        parts.append("")

    def _section(name: str, body: str | None) -> None:
        if body is None or not str(body).strip():
            return
        parts.append(f"## {name}")
        parts.append("")
        parts.append(str(body).rstrip())
        parts.append("")

    _section("Objective", brief.get("objective"))
    _section("Context", brief.get("context"))
    _section("Approach", brief.get("approach"))

    # Acceptance Criteria section: leave a stub if frontmatter has ACs
    if acs:
        parts.append("## Acceptance Criteria")
        parts.append("")
        parts.append("Source of truth in frontmatter.")
        for ac in acs:
            ac_id = ac.get("ac_id", "")
            desc = ac.get("description", "")
            ev_type = ac.get("evidence_type") or ""
            line = f"- {ac_id}: {desc}".rstrip()
            if ev_type:
                line += f" (evidence: {ev_type})"
            parts.append(line)
        parts.append("")

    milestones = brief.get("milestones") or []
    if milestones:
        parts.append("## Milestones")
        parts.append("")
        parts.append(_serialize_milestones_section(milestones))
        parts.append("")

    _section("Out of Scope", brief.get("out_of_scope"))

    text = "\n".join(parts).rstrip() + "\n"
    return text
