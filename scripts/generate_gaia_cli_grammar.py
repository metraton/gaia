#!/usr/bin/env python3
"""Regenerate hooks/modules/events/gaia_cli_grammar.py from bin/gaia's argparse parser.

The cli.invoked recorder names a call by this grammar, so it must equal the
parser: run this after adding or renaming a subcommand or an option.
``--check`` writes nothing and exits 1 when the committed file has drifted.

Exit codes: 0 = written or in sync, 1 = drift detected (--check).
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_PATH = REPO_ROOT / "hooks" / "modules" / "events" / "gaia_cli_grammar.py"
REGENERATE_COMMAND = "python3 scripts/generate_gaia_cli_grammar.py"
_HEADER = '"""The gaia CLI\'s argparse grammar by subcommand path: its subcommands and its declared option strings."""'


def parser_grammar() -> tuple[dict[str, str], dict[str, str]]:
    """The (subcommands, options) of every parser path, as space-separated sorted names."""
    loader = importlib.machinery.SourceFileLoader("gaia_cli_entry", str(REPO_ROOT / "bin" / "gaia"))
    spec = importlib.util.spec_from_loader("gaia_cli_entry", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    parser = module._build_parser(module._discover_plugins())
    children: dict[str, str] = {}
    options: dict[str, str] = {}

    def walk(node, prefix):
        key = " ".join(prefix)
        options[key] = " ".join(sorted({o for a in node._actions for o in a.option_strings}))
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                children[key] = " ".join(sorted(action.choices))
                for name, child in action.choices.items():
                    walk(child, prefix + (name,))

    walk(parser, ())
    return children, options


def _table(name: str, table: dict[str, str]) -> list[str]:
    lines = [f"{name} = {{"]
    for key in sorted(table):
        value = table[key]
        if not value:
            lines.append(f'    {key!r}: "",')
            continue
        wrapped = textwrap.wrap(value, 84, break_on_hyphens=False)
        if len(wrapped) == 1:
            lines.append(f"    {key!r}: {value!r},")
            continue
        lines.append(f"    {key!r}: (")
        for index, part in enumerate(wrapped):
            lines.append(f"        {(part + (' ' if index < len(wrapped) - 1 else ''))!r}")
        lines.append("    ),")
    lines.append("}")
    return lines


def render() -> str:
    """The grammar module's full text for the current parser."""
    children, options = parser_grammar()
    return "\n".join([_HEADER, "", *_table("SUBCOMMANDS", children), "", *_table("OPTIONS", options)]) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if GRAMMAR_PATH.read_text(encoding="utf-8") != text:
            print(f"{GRAMMAR_PATH} drifted from the CLI parser; run: {REGENERATE_COMMAND}", file=sys.stderr)
            return 1
        return 0
    GRAMMAR_PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
