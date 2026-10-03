"""A new layer-1 test is refused when it pins prose instead of protecting a behaviour a user or a release would feel.

The admission rule is written in skills/gaia-patterns/reference.md ("Layer-1
test admission"). This check enforces its three mechanical parts on every
test file layer 1 collects, so a violation fails `npm test` and CI naming the
file, the line and the rule:

  R1  a test that reads a Markdown document of skills/ or agents/ asserts a
      literal phrase with `in` / `not in`;
  R2  a test that reads a workflow under .github/workflows asserts a literal;
  R3  a test declares nowhere -- its own docstring, its class's or its
      module's -- the behaviour it protects.

Asserting prose that a program parses (a denial's approval_id line, a CLI's
JSON) stays admitted: R1 and R2 look only at documents and workflows.
"""

import ast
from pathlib import Path

from tests.conftest import LAYER1_EXCLUDED

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOTS = ("tests", "tools/scan/tests")
MIN_PHRASE = 4

# Closed allowlist: (file, rule, reason). An entry exists only while the
# pinned text is itself a contract a program reads; add none to silence a
# prose pin.
ADMISSION_ALLOWLIST: tuple[tuple[str, str, str], ...] = ()

_DOC_DIRS = ("skills", "agents")
_WORKFLOW_MARKERS = (".github/workflows", "ci.yml", "nightly.yml", "publish.yml")


def _layer1_test_files():
    excluded = tuple(entry for entry in LAYER1_EXCLUDED if "::" not in entry)
    for root in TEST_ROOTS:
        for path in sorted((REPO_ROOT / root).rglob("test_*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if any(relative == entry or relative.startswith(entry + "/") for entry in excluded):
                continue
            yield relative, path


def _strings(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _module_strings(tree):
    return [s for stmt in tree.body if isinstance(stmt, (ast.Assign, ast.AnnAssign)) for s in _strings(stmt)]


def _reads_repo_doc(strings):
    names_md = any(s.endswith(".md") for s in strings)
    names_dir = any(
        s in _DOC_DIRS or s.startswith(tuple(d + "/" for d in _DOC_DIRS)) or any(f"/{d}/" in s for d in _DOC_DIRS)
        for s in strings
    )
    return names_md and names_dir


def _reads_workflow(strings):
    return any(marker in s for s in strings for marker in _WORKFLOW_MARKERS)


def _authors_files(func):
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("write_text", "write", "unlink")
        for n in ast.walk(func)
    )


def _is_read(node):
    """A call that returns a file's text or its parsed YAML: read_text(), open(...).read(), yaml.safe_load(...)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    return name in ("read_text", "read", "safe_load", "load")


def _read_names(scope):
    """Names bound directly to a read in ``scope``: the text or document a literal is asserted against."""
    names = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and _is_read(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _root_name(node):
    while isinstance(node, (ast.Subscript, ast.Attribute)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _literal_asserts(func, ops, read_names):
    """Lines asserting a literal of MIN_PHRASE+ characters against read document text."""
    for node in ast.walk(func):
        if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)):
            continue
        sides = [node.test.left, *node.test.comparators]
        has_literal = any(
            isinstance(s, ast.Constant) and isinstance(s.value, str) and len(s.value) >= MIN_PHRASE for s in sides
        )
        against_read = any(_is_read(s) or _root_name(s) in read_names for s in sides)
        if has_literal and against_read and any(isinstance(op, ops) for op in node.test.ops):
            yield node.lineno


def _test_functions(tree):
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name.startswith("test"):
            yield stmt, None
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            for inner in stmt.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner.name.startswith("test"):
                    yield inner, stmt


def admission_violations(relative, source):
    """Every (file:line, rule) the admission rule refuses in one test file's source."""
    tree = ast.parse(source)
    module_strings = _module_strings(tree)
    module_reads = {n for stmt in tree.body if isinstance(stmt, ast.Assign) for n in _read_names(stmt)}
    module_declares = bool((ast.get_docstring(tree) or "").strip())
    found = []
    for func, cls in _test_functions(tree):
        strings = _strings(func) + module_strings
        reads = _read_names(func) | module_reads
        if _reads_repo_doc(strings) and not _authors_files(func):
            found += [(f"{relative}:{line}", "R1") for line in _literal_asserts(func, (ast.In, ast.NotIn), reads)]
        if _reads_workflow(strings) and not _authors_files(func):
            found += [
                (f"{relative}:{line}", "R2") for line in _literal_asserts(func, (ast.In, ast.NotIn, ast.Eq), reads)
            ]
        declared = module_declares or ast.get_docstring(func) or (cls is not None and ast.get_docstring(cls))
        if not declared:
            found.append((f"{relative}:{func.lineno}", "R3"))
    return found


def test_every_layer1_test_is_admitted():
    """A text pin or an undeclared test fails the suite with its file:line and rule."""
    allowed = {(entry[0], entry[1]) for entry in ADMISSION_ALLOWLIST}
    violations = []
    for relative, path in _layer1_test_files():
        for where, rule in admission_violations(relative, path.read_text(encoding="utf-8")):
            if (where.split(":")[0], rule) not in allowed:
                violations.append(f"{where} {rule}")
    assert not violations, "layer-1 admission refused (skills/gaia-patterns/reference.md):\n" + "\n".join(violations)


def test_the_check_refuses_each_rule_and_admits_a_parsed_contract():
    """Each rule bites on its own shape, and a CLI exit-code test or an approval_id assert is admitted."""
    doc_pin = (
        '"""Pins a skill."""\n'
        "def test_x():\n"
        '    text = (ROOT / "skills" / "agent-protocol" / "SKILL.md").read_text()\n'
        '    assert "contract" in text\n'
    )
    workflow_pin = '"""Pins CI."""\ndef test_x():\n    assert "pytest" in open(".github/workflows/ci.yml").read()\n'
    undeclared = "def test_x():\n    assert run() == 0\n"
    admitted = (
        '"""gaia approvals list reports a pending grant."""\n'
        "def test_x():\n"
        '    result = run(["gaia", "approvals", "list"])\n'
        "    assert result.returncode == 0\n"
        '    assert "approval_id:" in result.stdout\n'
    )
    assert [rule for _, rule in admission_violations("t.py", doc_pin)] == ["R1"]
    assert [rule for _, rule in admission_violations("t.py", workflow_pin)] == ["R2"]
    assert admission_violations("t.py", undeclared) == [("t.py:1", "R3")]
    assert admission_violations("t.py", admitted) == []
