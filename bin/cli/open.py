"""gaia open -- open anything Gaia stores by one reference, KIND:SCOPE/ID#FIELD, read-only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def register(subparsers) -> None:
    """Register the `open` subcommand with the root parser."""
    from gaia.refs import KINDS

    p = subparsers.add_parser(
        "open",
        help="Open a stored thing by reference: KIND:SCOPE/ID#FIELD (read-only)",
        description=(
            "Resolve any id spelling of a stored thing and print its canonical "
            "reference, every sibling id with its label, the per-kind verb that "
            "acts on it, and the row (or only #FIELD). SCOPE is a hint: a row "
            "that moved resolves and says where it lives now. An unresolvable "
            "reference exits 1 naming the nearest match. KIND is one of: "
            + ", ".join(KINDS) + "."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "SCOPE is a workspace for memory, context and brief; user and gaia "
            "name the _gaia_user and _gaia_host rows.\n\n"
            "Examples:\n"
            "  gaia open contract:34522\n"
            "  gaia open contract:a173f63dee5740a6f.9301a33c2ddb#conclusion\n"
            "  gaia open memory:ws/decision_gaia_referencias_por_coordenadas_20261028\n"
            "  gaia open context:ws/gaia/stack\n"
            "  gaia open task:1044\n"
            "  gaia open plan:94 --line\n"
        ),
    )
    p.add_argument("ref", metavar="REF", help="KIND:SCOPE/ID#FIELD, e.g. memory:ws/slug or contract:34522.")
    shape = p.add_mutually_exclusive_group()
    shape.add_argument("--line", action="store_true", default=False,
                       help="Print only the why line generated from the row: WHY -- gaia open REF.")
    shape.add_argument("--json", action="store_true", default=False,
                       help="Emit reference, ids, notes, verb, why and document as JSON.")


def cmd_open(args) -> int:
    """Dispatch handler for `gaia open`."""
    from gaia.refs import RefError, format_ref, parse_ref, resolve, select_field

    try:
        ref = parse_ref(args.ref)
        found = resolve(ref)
        document = select_field(found.document, ref.field) if ref.field else found.document
    except RefError as exc:
        print(f"gaia open: {exc}", file=sys.stderr)
        return 1
    try:
        reference = format_ref(found.ref)
    except RefError:
        reference = None
        found.notes.append("the id holds characters outside [A-Za-z0-9_.:/#@-]; no reference is printed")

    if args.json:
        print(json.dumps({
            "reference": reference, "ids": found.ids, "notes": found.notes,
            "verb": found.verb, "why": found.why, "document": document,
        }, indent=2, default=str))
        return 0
    if args.line:
        print(f"{found.why} -- gaia open {reference}" if reference else found.why)
        return 0
    if reference:
        print(f"reference: {reference}")
    for label, value in found.ids.items():
        print(f"{label}: {_labelled(label, value)}")
    for note in found.notes:
        print(f"note: {note}")
    if found.verb:
        print(f"verb: {found.verb}")
    print(f"why: {found.why}")
    print()
    print(document if isinstance(document, str) else json.dumps(document, indent=2, default=str))
    return 0


def _labelled(label: str, value: str) -> str:
    if label == "resume":
        return f"SendMessage to {value}"
    return f"gaia open {value}" if ":" in value else value
