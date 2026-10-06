"""Guardian: the layer-1 suite must give the same verdict on any machine.

Three couplings make a verdict depend on who runs it, each one a rule:

  R1  a user path (/home/<name>, /Users/<name>, C:\\Users\\<name>) whose account
      is not one of the declared neutral names, in any text file of the suite;
  R2  a test that depends on a program outside the own toolchain (OWN_TOOLCHAIN
      in tests/conftest.py): a shutil.which of one, a skip on the absence of any
      program (the own toolchain fails through require_tool instead), or a
      literal argv[0] handed to subprocess / os.exec;
  R3  a read of the user's live state (~/.claude/projects, ~/.gaia/gaia.db)
      through the real home, in a test that does not set its own HOME.

A program named inside a string is data for the classifier, and a script a test
writes in tmp_path and runs by its path is a fixture; neither is flagged.

Limits: R2 and R3 read Python by AST, so the TypeScript tests get R1 only; a
path or program assembled at run time, and prose that only claims independence
from the machine, are not detected here: prose is a review matter.
"""

import ast
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tests.conftest import LAYER1_EXCLUDED, OWN_TOOLCHAIN

REPO = Path(__file__).resolve().parents[1]

# A fixture may name any account that belongs to no one; naming another one is
# a person's machine leaking into the suite.
PLACEHOLDER_ACCOUNTS = frozenset({"user", "u", "x", "j", "jo", "someone"})

# Directory names the anchor-tracker fixtures put under a home-shaped root as
# project names, not as accounts.
FIXTURE_DIRECTORIES = frozenset({"terraform", "qxo-monorepo", "random", "oci-pos-dev-cluster"})

NEUTRAL_HOME_NAMES = PLACEHOLDER_ACCOUNTS | FIXTURE_DIRECTORIES

# What the user's real sessions and database leave on disk, as adjacent path parts.
LIVE_STATE_TAILS = ((".claude", "projects"), (".gaia", "gaia.db"))

HOME_VARIABLES = ("HOME", "USERPROFILE")


@dataclass(frozen=True)
class Allowed:
    file: str
    rule: str
    reason: str


# Closed set: a file under a LAYER1_EXCLUDED layer (live, opt-in: it needs a real
# host) or one of the UNCOLLECTED_CI_SCRIPTS (workflow steps run them, pytest
# never collects them). A classifier test never belongs here: it carries a tool
# name as data, which no rule flags.
UNCOLLECTED_CI_SCRIPTS = ("tests/ci/upgrade_smoke.py", "tests/ci/windows_smoke.py")

ALLOWLIST: tuple[Allowed, ...] = (
    Allowed(
        "tests/layer3_e2e/test_hook_lifecycle.py",
        "R2",
        "live layer, opt-in: it drives a real Claude Code session, so it needs the claude CLI and skips where there is none",
    ),
)

_SKIPPED_DIRECTORIES = {"node_modules", "__pycache__", ".pytest_cache", ".venv"}

_ACCOUNT_PATH = re.compile(
    r"(?<![\w.:/-])(?:/home/|/Users/|[A-Za-z]:[\\/][Uu]sers[\\/])([A-Za-z0-9_][A-Za-z0-9_.-]*)"
)

_WHICH_CALLS = {"shutil.which", "distutils.spawn.find_executable"}
_SKIP_DECORATORS = {"skipif", "skipIf", "skipUnless"}
_SKIP_CALLS = {"skip", "skipTest"}
_SPAWN_CALLS = {
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen",
    "os.system", "os.popen",
    "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "os.execl", "os.execle", "os.execlp", "os.execlpe",
}
_FILESYSTEM_VERBS = {
    "open", "connect", "read_text", "read_bytes", "exists", "is_file", "is_dir",
    "iterdir", "glob", "rglob", "stat", "listdir", "scandir", "walk",
    "copy", "copy2", "copyfile", "copytree", "write_text", "write_bytes",
    "unlink", "rmtree", "mkdir", "touch",
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    rule: str
    detail: str

    def __str__(self):
        return f"{self.file}:{self.line} {self.rule} {self.detail}"


def _outside_chain(program: str) -> bool:
    if program.startswith(("./", "../")):
        return False
    name = re.split(r"[\\/]", program)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return bool(name) and name not in OWN_TOOLCHAIN


def _first_token(text: str) -> str | None:
    for token in text.split():
        if "=" not in token:
            return token
    return None


def _string_constants(expr: ast.AST) -> list[str]:
    nodes = sorted(
        (n for n in ast.walk(expr) if isinstance(n, ast.Constant) and isinstance(n.value, str)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    return [n.value for n in nodes]


class _Collector(ast.NodeVisitor):
    """One pass over a module: its calls with their enclosing functions, its ifs, assigns and imports.

    The rules cross-reference these lists instead of re-walking the tree per
    function, which made the scan several times slower than its budget.
    """

    def __init__(self):
        self.scope: list[ast.AST] = []
        self.calls: list[tuple[ast.Call, tuple[ast.AST, ...]]] = []
        self.ifs: list[ast.If] = []
        self.assigns: list[ast.Assign] = []
        self.aliases: dict[str, str] = {}

    def visit_FunctionDef(self, node):
        self.scope.append(node)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        self.calls.append((node, tuple(self.scope)))
        self.generic_visit(node)

    def visit_If(self, node):
        self.ifs.append(node)
        self.generic_visit(node)

    def visit_Assign(self, node):
        self.assigns.append(node)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            self.aliases[alias.asname or root] = alias.name if alias.asname else root

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if node.module:
                self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"


class _PythonModule:
    """One parsed test module and what its rules need to know about it."""

    def __init__(self, file: str, text: str):
        self.file = file
        collected = _Collector()
        collected.visit(ast.parse(text, filename=file))
        self.calls = collected.calls
        self.ifs = collected.ifs
        self.aliases = collected.aliases
        self.which_helpers = {
            fn.name for call, scope in self.calls if self._is_which(call) for fn in scope
        }
        self.names_live_state = any(tail[1] in text for tail in LIVE_STATE_TAILS)
        self.live_names = {
            target.id
            for node in collected.assigns
            if self.names_live_state and self._live_state(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.home_fixtured = {
            id(fn) for call, scope in self.calls if self._sets_home(call) for fn in scope
        }

    def _dotted(self, node: ast.AST) -> str | None:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(self.aliases.get(node.id, node.id))
        return ".".join(reversed(parts))

    def _last_name(self, call: ast.Call) -> str:
        func = call.func
        return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

    def _is_which(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and self._dotted(node.func) in _WHICH_CALLS

    def _probes_program(self, expr: ast.AST) -> bool:
        return any(
            self._is_which(n)
            or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in self.which_helpers)
            for n in ast.walk(expr)
        )

    def _is_home_base(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            dotted = self._dotted(node.func)
            if dotted in ("pathlib.Path.home", "os.path.expanduser", "os.getenv"):
                return dotted != "os.getenv" or self._names_home_variable(node)
            if dotted == "os.environ.get":
                return self._names_home_variable(node)
            return isinstance(node.func, ast.Attribute) and node.func.attr == "expanduser"
        return (
            isinstance(node, ast.Subscript)
            and self._dotted(node.value) == "os.environ"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in HOME_VARIABLES
        )

    @staticmethod
    def _names_home_variable(call: ast.Call) -> bool:
        return bool(call.args) and isinstance(call.args[0], ast.Constant) and call.args[0].value in HOME_VARIABLES

    def _live_state(self, expr: ast.AST) -> bool:
        if not any(self._is_home_base(n) for n in ast.walk(expr)):
            return False
        parts = [p for text in _string_constants(expr) for p in re.split(r"[\\/]", text) if p]
        return any(pair in LIVE_STATE_TAILS for pair in zip(parts, parts[1:]))

    def _sets_home(self, call: ast.Call) -> bool:
        last = self._last_name(call)
        if last in ("setenv", "setitem") and self._names_home_variable(call):
            return True
        if last == "setattr" and len(call.args) > 1 and getattr(call.args[1], "value", None) == "home":
            return True
        return (self._dotted(call.func) or "").endswith("patch.dict") and any(
            text in HOME_VARIABLES for arg in call.args for text in _string_constants(arg)
        )

    def findings(self) -> list[Finding]:
        found = []
        for call, scope in self.calls:
            found += self._which_findings(call)
            found += self._skip_findings(call)
            found += self._spawn_findings(call)
            if self.names_live_state and not any(id(fn) in self.home_fixtured for fn in scope):
                found += self._live_state_findings(call)
        for branch in self.ifs:
            if self._probes_program(branch.test):
                found += self._skip_under_probe(branch)
        return found

    def _at(self, node: ast.AST, rule: str, detail: str) -> Finding:
        return Finding(self.file, node.lineno, rule, detail)

    def _which_findings(self, call: ast.Call) -> list[Finding]:
        if not self._is_which(call) or not call.args:
            return []
        program = call.args[0]
        if isinstance(program, ast.Constant) and isinstance(program.value, str) and _outside_chain(program.value):
            return [self._at(call, "R2", f"looks up {program.value!r}, which is not in the own toolchain")]
        return []

    def _skip_findings(self, call: ast.Call) -> list[Finding]:
        if self._last_name(call) not in _SKIP_DECORATORS:
            return []
        condition = [*call.args[:1], *(k.value for k in call.keywords if k.arg == "condition")]
        if any(self._probes_program(expr) for expr in condition):
            return [self._at(call, "R2", "skips on the absence of a program; use require_tool for the own toolchain")]
        return []

    def _skip_under_probe(self, branch: ast.If) -> list[Finding]:
        return [
            self._at(n, "R2", "skips on the absence of a program; use require_tool for the own toolchain")
            for stmt in (*branch.body, *branch.orelse)
            for n in ast.walk(stmt)
            if isinstance(n, ast.Call) and self._last_name(n) in _SKIP_CALLS
        ]

    def _spawn_findings(self, call: ast.Call) -> list[Finding]:
        if self._dotted(call.func) not in _SPAWN_CALLS or not call.args:
            return []
        program = self._literal_program(call.args[0])
        if program is not None and _outside_chain(program):
            return [self._at(call, "R2", f"runs {program!r}, which is not in the own toolchain, by name")]
        return []

    @staticmethod
    def _literal_program(arg: ast.AST) -> str | None:
        if isinstance(arg, (ast.List, ast.Tuple)):
            arg = arg.elts[0] if arg.elts else None
        if isinstance(arg, ast.JoinedStr):
            arg = arg.values[0] if arg.values else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return _first_token(arg.value)
        return None

    def _live_state_findings(self, call: ast.Call) -> list[Finding]:
        if self._last_name(call) not in _FILESYSTEM_VERBS:
            return []
        reaches_live_state = self._live_state(call) or any(
            isinstance(n, ast.Name) and n.id in self.live_names for n in ast.walk(call)
        )
        if reaches_live_state:
            return [self._at(call, "R3", "touches the user's live state through the real home")]
        return []


def _user_path_findings(file: str, text: str) -> list[Finding]:
    return [
        Finding(file, text.count("\n", 0, match.start()) + 1, "R1",
                f"user path {match.group(0)!r} is outside the declared neutral names")
        for match in _ACCOUNT_PATH.finditer(text)
        if match.group(1).rstrip(".").lower() not in NEUTRAL_HOME_NAMES
    ]


def _testpaths(root: Path) -> tuple[str, ...]:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(pyproject["tool"]["pytest"]["ini_options"]["testpaths"])


def scan_tree(root: Path, testpaths: tuple[str, ...]) -> list[Finding]:
    """Every coupling found in the text files under ``testpaths``, before the allowlist."""
    found = []
    for testpath in testpaths:
        for path in sorted((root / testpath).rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or _SKIPPED_DIRECTORIES & set(relative.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            file = relative.as_posix()
            found += _user_path_findings(file, text)
            if path.suffix == ".py":
                found += _PythonModule(file, text).findings()
    return sorted(set(found), key=lambda f: (f.file, f.line, f.rule))


def _allowed(finding: Finding) -> bool:
    return any(entry.file == finding.file and entry.rule == finding.rule for entry in ALLOWLIST)


@lru_cache(maxsize=1)
def _repository_findings() -> tuple[Finding, ...]:
    return tuple(scan_tree(REPO, _testpaths(REPO)))


def test_layer1_suite_has_no_machine_coupling():
    """Fail naming file:line and rule for every coupling the allowlist does not cover."""
    offenders = [f for f in _repository_findings() if not _allowed(f)]
    assert not offenders, "the suite depends on the machine:\n" + "\n".join(str(f) for f in offenders)


def test_allowlist_is_the_closed_set_and_every_entry_still_suppresses_something():
    """An entry needs a live layer or an uncollected CI script, a reason, and a finding to cover."""
    live_layers = tuple(entry for entry in LAYER1_EXCLUDED if "::" not in entry)
    problems = []
    for entry in ALLOWLIST:
        in_closed_set = entry.file.startswith(tuple(layer + "/" for layer in live_layers)) or (
            entry.file in UNCOLLECTED_CI_SCRIPTS
        )
        if not in_closed_set:
            problems.append(f"{entry.file}: neither a LAYER1_EXCLUDED layer nor an uncollected CI script")
        if not entry.reason.strip():
            problems.append(f"{entry.file} {entry.rule}: no reason")
        if not any(f.file == entry.file and f.rule == entry.rule for f in _repository_findings()):
            problems.append(f"{entry.file} {entry.rule}: covers no finding, remove it")
    assert not problems, "\n".join(problems)


_HOME_ROOT = "/home/"

_PLANTED = {
    "tests/r1_unknown_account.py": (f'PATH = "{_HOME_ROOT}alice/x"\n', {"R1"}),
    "tests/fixtures/r1_unknown_account.jsonl": (f'{{"cwd": "{_HOME_ROOT}alice/x"}}\n', {"R1"}),
    "tests/r1_neutral_account.py": (f'PATH = "{_HOME_ROOT}user/ws"\n', set()),
    "tests/r2_skipif_which.py": (
        "import shutil\nimport pytest\n\n"
        '@pytest.mark.skipif(shutil.which("kubectl") is None, reason="needs it")\n'
        "def test_a():\n    pass\n",
        {"R2"},
    ),
    "tests/r2_skip_on_own_toolchain.py": (
        "import shutil\nimport pytest\n\n"
        "def test_a():\n"
        '    if shutil.which("bun") is None:\n        pytest.skip("no bun")\n',
        {"R2"},
    ),
    "tests/r2_skip_through_helper.py": (
        "import shutil\nimport unittest\n\n"
        'def _npm_available():\n    return shutil.which("npm") is not None\n\n'
        '@unittest.skipUnless(_npm_available(), "no npm")\n'
        "class T(unittest.TestCase):\n    pass\n",
        {"R2"},
    ),
    "tests/r2_literal_argv0.py": (
        'import subprocess\n\ndef test_a():\n    subprocess.run(["kubectl", "get", "pods"])\n',
        {"R2"},
    ),
    "tests/r3_reads_live_db.py": (
        "import sqlite3\nfrom pathlib import Path\n\n"
        'def test_a():\n    sqlite3.connect(Path.home() / ".gaia" / "gaia.db")\n',
        {"R3"},
    ),
    "tests/r3_reads_live_db_through_name.py": (
        "from pathlib import Path\n\n"
        'DB = Path.home() / ".gaia" / "gaia.db"\n\n'
        "def test_a():\n    DB.read_bytes()\n",
        {"R3"},
    ),
    "tests/r3_fixture_home.py": (
        "from pathlib import Path\n\n"
        "def test_a(monkeypatch, tmp_path):\n"
        '    monkeypatch.setenv("HOME", str(tmp_path))\n'
        '    (Path.home() / ".claude" / "projects").mkdir(parents=True)\n',
        set(),
    ),
    "tests/data_tool_name_in_string.py": (
        "from modules.security.mutative_verbs import detect_mutative_command\n\n"
        "def test_a():\n"
        '    assert detect_mutative_command("kubectl delete namespace prod").tier == "T3"\n',
        set(),
    ),
    "tests/fixture_script_in_tmp_path.py": (
        "import subprocess\n\n"
        "def test_a(tmp_path):\n"
        '    tool = tmp_path / "kubectl"\n'
        '    tool.write_text("#!/bin/sh\\nexit 0\\n")\n'
        "    tool.chmod(0o755)\n"
        '    subprocess.run([str(tool), "get"], check=True)\n',
        set(),
    ),
    "tests/own_toolchain_by_name.py": (
        'import subprocess\n\ndef test_a():\n    subprocess.run(["git", "--version"])\n',
        set(),
    ),
}


def test_each_rule_fires_on_a_coupling_and_stays_quiet_on_data_and_fixtures(tmp_path):
    """A guardian that cannot fail proves nothing: plant each case and read the verdict per file."""
    for relative, (source, _) in _PLANTED.items():
        planted = tmp_path / relative
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text(source, encoding="utf-8")

    rules_by_file: dict[str, set[str]] = {}
    for finding in scan_tree(tmp_path, ("tests",)):
        rules_by_file.setdefault(finding.file, set()).add(finding.rule)

    wrong = [
        f"{relative}: expected {sorted(expected)}, got {sorted(rules_by_file.get(relative, set()))}"
        for relative, (_, expected) in _PLANTED.items()
        if rules_by_file.get(relative, set()) != expected
    ]
    assert not wrong, "\n".join(wrong)
