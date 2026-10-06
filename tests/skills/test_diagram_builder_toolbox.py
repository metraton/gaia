"""diagram-builder's toolbox.md must name every property the deck engine accepts.

``skills/diagram-builder/toolbox.md`` is the one reference a deck author learns
the pieces from, and the skill's claim is that a reader who knows every property
can compose what no template foresaw. That claim rots silently: a field added to
a build whitelist produces no error anywhere, only a property nobody is taught.

So the whitelists and value sets are read from the code that enforces them
(``assets/engine/build-data.mjs``, ``assets/engine/tokens.mjs``,
``assets/tools/static-census.cjs``), and each one is held against the piece
section of toolbox.md that owns it. Extraction fails loudly: a constant that can
no longer be found is a failure, never an empty set that passes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parents[2] / "skills" / "diagram-builder"
_TOOLBOX = _SKILL / "toolbox.md"
_BUILD_DATA = _SKILL / "assets" / "engine" / "build-data.mjs"
_TOKENS = _SKILL / "assets" / "engine" / "tokens.mjs"
_STATIC_CENSUS = _SKILL / "assets" / "tools" / "static-census.cjs"

# Each key whitelist and the toolbox sections whose property tables must name
# all of its keys. The separator shares the box's whitelist, so the two tables
# together cover it.
_FIELD_OWNERS = {
    "MANIFEST_FIELDS": ("Document",),
    "MANIFEST_PAGE_FIELDS": ("Page",),
    "PAGE_FIELDS": ("Page",),
    "SECTION_FIELDS": ("Section",),
    "COMPONENT_FIELDS": ("Box", "Separator"),
    "RAIL_FIELDS": ("Rail",),
    "SPACER_FIELDS": ("Spacer",),
    "FILTER_FIELDS": ("Chip",),
}

# Each closed value set and the toolbox section that must spell every value.
_VALUE_OWNERS = {
    "COMPONENT_VARIANTS": "Box",
    "COMPONENT_TREATMENTS": "Box",
    "COMPONENT_TYPES": "Box",
    "SECTION_VARIANTS": "Section",
    "SECTION_TREATMENTS": "Section",
    "RAIL_VARIANTS": "Rail",
    "SEPARATOR_STYLES": "Separator",
    "TEXT_FIT": "Page",
    "LAYOUTS": "Page",
    "PALETTES": "Look",
}

_ANCHOR = re.compile(r"`(assets/[\w./-]+)::(\w+)`")


def _sections(markdown: str) -> dict[str, str]:
    parts = re.split(r"^## ", markdown, flags=re.MULTILINE)
    return {part.split("\n", 1)[0].strip(): part for part in parts[1:]}


def _table_properties(section: str) -> set[str]:
    return set(re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE))


def _backticked(section: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", section))


def _js_set(source: str, name: str) -> set[str]:
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"build-data.mjs no longer declares {name} as a literal Set"
    values = set(re.findall(r"'([^']+)'", match.group(1)))
    assert values, f"{name} parsed empty"
    return values


def _block(source: str, start: str) -> str:
    at = source.index(start)
    return source[at:source.index("\n});", at)]


def _forms() -> set[str]:
    match = re.search(r"const FORMS = \[(.*?)\]", _STATIC_CENSUS.read_text(), re.DOTALL)
    assert match, "static-census.cjs no longer declares FORMS as a literal list"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _token_keys() -> set[str]:
    keys = set(re.findall(r"'([a-z_.]+)':", _block(_TOKENS.read_text(), "export const TOKEN_SCHEMA")))
    assert keys, "TOKEN_SCHEMA parsed empty"
    return keys


def _look_names() -> set[str]:
    names = set(re.findall(r"^  (\w+): \{", _block(_TOKENS.read_text(), "export const LOOKS"), re.MULTILINE))
    assert names, "LOOKS parsed empty"
    return names


@pytest.fixture(scope="module")
def toolbox() -> dict[str, str]:
    return _sections(_TOOLBOX.read_text())


@pytest.mark.parametrize("whitelist", sorted(_FIELD_OWNERS))
def test_every_accepted_field_has_a_property_row(toolbox, whitelist):
    owners = _FIELD_OWNERS[whitelist]
    named = set().union(*(_table_properties(toolbox[o]) for o in owners))
    missing = _js_set(_BUILD_DATA.read_text(), whitelist) - named
    assert not missing, f"toolbox.md {'/'.join(owners)} does not name {sorted(missing)} ({whitelist})"


def test_every_token_has_a_property_row(toolbox):
    missing = _token_keys() - _table_properties(toolbox["Look"])
    assert not missing, f"toolbox.md Look does not name the tokens {sorted(missing)}"


@pytest.mark.parametrize("value_set", sorted(_VALUE_OWNERS))
def test_every_accepted_value_is_spelled(toolbox, value_set):
    owner = _VALUE_OWNERS[value_set]
    missing = _js_set(_BUILD_DATA.read_text(), value_set) - _backticked(toolbox[owner])
    assert not missing, f"toolbox.md {owner} does not spell {sorted(missing)} ({value_set})"


def test_every_form_and_look_is_spelled(toolbox):
    assert not _forms() - _backticked(toolbox["Page"]) - _backticked(toolbox["Forms"])
    assert not _look_names() - _backticked(toolbox["Look"])


@pytest.mark.parametrize("doc", ["SKILL.md", "toolbox.md", "build.md", "video.md"])
def test_every_code_anchor_resolves(doc):
    unresolved = []
    for path, symbol in _ANCHOR.findall((_SKILL / doc).read_text()):
        target = _SKILL / path
        if not target.is_file() or not re.search(rf"\b{symbol}\b", target.read_text()):
            unresolved.append(f"{path}::{symbol}")
    assert not unresolved, f"{doc} anchors that no longer resolve: {unresolved}"
