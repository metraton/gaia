"""One-shot rewrite: route module-level agent_id constants through the fixture.

Test modules pinned their handle as a bare literal (``VALID_AGENT_ID =
"a1234abcd"``). That shape is invisible to a textual sweep keyed on inline
usages, which is why raising the handle floor left it behind. This rewrites
each such constant to ``valid_agent_id("<original>")`` so the value is minted
at the current floor instead of frozen at the one it was written against.

Reports every short handle it did NOT rewrite, per file, so the residual is
enumerated rather than assumed.
"""

import re
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent

SHORT = r"a[0-9a-f]{1,15}"
CONST_RE = re.compile(rf'^([A-Z][A-Z0-9_]*)(\s*=\s*)"({SHORT})"(\s*(?:#.*)?)$')
ANY_SHORT_RE = re.compile(rf'["\']({SHORT})["\']')
IMPORT_RE = re.compile(r"^(?:import |from )\S")
IMPORT_LINE = "from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402"


def last_import_end(lines):
    """Index of the line that CLOSES the last top-level import statement.

    A parenthesized ``from x import (\\n a,\\n b,\\n)`` spans several lines.
    Anchoring on the line that OPENS it inserts the new import into the middle
    of the name list, which is a SyntaxError; tracking paren depth anchors
    after the statement ends instead.
    """
    last = None
    depth = 0
    in_import = False
    for i, line in enumerate(lines):
        if depth == 0 and IMPORT_RE.match(line):
            in_import = True
        if in_import:
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                last, in_import, depth = i, False, 0
    return last


def rewrite(path):
    lines = path.read_text().splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        m = CONST_RE.match(line.rstrip("\n"))
        if not m:
            continue
        name, eq, literal, tail = m.groups()
        lines[i] = f'{name}{eq}valid_agent_id("{literal}"){tail}\n'
        changed = True
    if not changed:
        return None
    if IMPORT_LINE not in "".join(lines):
        last = last_import_end(lines)
        if last is None:
            print(f"SKIP (no import anchor): {path}")
            return None
        lines.insert(last + 1, IMPORT_LINE + "\n")
    with open(path, "w") as fh:
        fh.write("".join(lines))
    return path


def main():
    rewritten = []
    for path in sorted(TESTS.rglob("test_*.py")):
        if rewrite(path):
            rewritten.append(path)
    print(f"rewritten: {len(rewritten)}")
    for p in rewritten:
        print(f"  {p.relative_to(TESTS)}")
    print("\nresidual short handles (NOT rewritten):")
    for path in sorted(TESTS.rglob("test_*.py")):
        hits = sorted(set(ANY_SHORT_RE.findall(path.read_text())))
        if hits:
            print(f"  {path.relative_to(TESTS)}: {hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
