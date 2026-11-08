"""Python a command runs is free only when its whole import closure is proven; code that reaches Gaia's store is refused."""

from __future__ import annotations

import ast
import functools
import os
import re
import shutil
import site
import sys
from importlib.machinery import PathFinder
from typing import Dict, Iterable, List, Optional, Set, Tuple

STORE = "store"
UNPROVEN = "unproven"

_PYTHON = re.compile(r"^(?:i?python|pypy|py|micropython|jython|graalpy|bpython|ptpython)[0-9.]*(?:\.exe)?$")
_RUNNERS = frozenset({"uv", "poetry", "pipenv", "pdm", "hatch", "rye", "pixi", "conda", "mamba", "micromamba"})
_GAIA_PACKAGE = "gaia"
_UNPROVABLE_MODULES = frozenset({
    "importlib", "runpy", "pkgutil", "imp", "zipimport", "code", "codeop", "builtins", "ctypes",
    "marshal", "pickle", "shelve", "dill", "cloudpickle",
})
_UNPROVABLE_NAMES = frozenset({
    "exec", "eval", "compile", "execfile", "__import__", "globals", "locals", "vars", "breakpoint",
})
_INERT_DUNDERS = frozenset({
    "__name__", "__file__", "__doc__", "__version__", "__all__", "__init__", "__enter__", "__exit__",
    "__post_init__", "__main__",
})
_IMPORT_STATE = frozenset({"path", "modules", "meta_path", "path_hooks", "path_importer_cache"})
_READ_METHODS = {"path": frozenset({"index", "count", "copy"}), "modules": frozenset({"keys"})}
_STATE_READERS = frozenset({
    "print", "len", "list", "tuple", "sorted", "set", "frozenset", "str", "repr", "any", "all", "enumerate",
})
_CODE_OBJECTS = frozenset({"CodeType", "FunctionType"})
_ATTRIBUTE_READERS = frozenset({"getattr", "setattr", "delattr"})
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__", "run_module", "run_path"})
_SQLITE_MODULES = frozenset({"sqlite3", "apsw", "pysqlite2", "pysqlite3", "sqlalchemy", "sqlite_utils", "peewee", "aiosqlite"})
_OPENERS = frozenset({"connect", "Connection", "create_engine", "Database", "SqliteDatabase"})
_PATH_ENVIRONMENT = re.compile(r"(?:^|[\s;&|(])(PYTHON(?:PATH|HOME|STARTUP|USERBASE|SAFEPATH|PLATLIBDIR|BREAKPOINT))=")
_WARNINGS_ENVIRONMENT = re.compile(r"(?:^|[\s;&|(])PYTHONWARNINGS=(['\"]?)([^\s'\"]*)\1")
_COOKIE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
_PLAIN_ENCODINGS = frozenset({
    "utf-8", "utf8", "utf-8-sig", "ascii", "us-ascii", "latin-1", "latin1", "iso-8859-1", "iso8859-1", "l1",
})
_MODELLED_FLAGS = frozenset("IEsSBuObqvdPRVh")
_INERT_X_OPTIONS = frozenset({"utf8", "utf8=0", "utf8=1", "dev"})
_LANE_MODULES = frozenset({"pytest", "pip"})
_PYTEST_PROGRAMS = frozenset({"pytest", "py.test"})
_REDIRECT = re.compile(r"^\d*(?:>>?|<>|>&|&>>?|<&)")
_HEREDOC_OPENER = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
_MAX_FILES = 200
_MAX_BYTES = 1 << 20

STORE_CODE_REJECTION = (
    "This Python reaches Gaia's store in-process: it imports Gaia's own package (by name, through a "
    "local module or a link to it) or opens the store through a sqlite API. Gaia's store is written "
    "only through the `gaia` CLI, whatever the environment says. Do NOT retry -- this is not approvable."
)


def _worst(outcomes: Iterable[Optional[str]]) -> Optional[str]:
    outcomes = set(outcomes)
    return STORE if STORE in outcomes else UNPROVEN if UNPROVEN in outcomes else None


def _string(node: Optional[ast.AST]) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _root(node: ast.AST) -> Optional[str]:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and name not in _INERT_DUNDERS


def _bound_names(tree: ast.AST) -> Set[str]:
    bound: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound |= _STATE_READERS if alias.name == "*" else {(alias.asname or alias.name).split(".")[0]}
    return bound


def _reads_import_state(node: ast.Attribute, parents: Dict[int, ast.AST], bound: Set[str]) -> bool:
    """True when *node* (``sys.path`` or ``sys.modules``) is only inspected: never changed, aliased or handed to unknown code."""
    if node.attr not in _READ_METHODS or not isinstance(node.ctx, ast.Load):
        return False
    parent = parents.get(id(node))
    if isinstance(parent, ast.Compare):
        return True
    if isinstance(parent, (ast.For, ast.comprehension)):
        return parent.iter is node
    if isinstance(parent, ast.Call):
        func = parent.func
        return isinstance(func, ast.Name) and func.id in _STATE_READERS and func.id not in bound and node in parent.args
    if isinstance(parent, ast.Attribute) and parent.attr in _READ_METHODS[node.attr]:
        call = parents.get(id(parent))
        return isinstance(call, ast.Call) and call.func is parent
    if isinstance(parent, ast.Subscript) and node.attr == "path":
        return parent.value is node and isinstance(parent.ctx, ast.Load)
    return False


def _decode(raw: bytes) -> Optional[str]:
    """*raw* as CPython decodes source, or None for an encoding the guard does not model or bytes that do not decode."""
    encoding = "utf-8-sig"
    for line in raw.split(b"\n")[:2]:
        cookie = _COOKIE.match(line)
        if cookie:
            name = cookie.group(1).decode("ascii", "replace").lower().replace("_", "-")
            if name not in _PLAIN_ENCODINGS:
                return None
            encoding = "utf-8-sig" if name.startswith("utf") or name in ("ascii", "us-ascii") else "latin-1"
            break
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return None


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return _decode(handle.read(_MAX_BYTES))
    except OSError:
        return None


class _Closure:
    """One program's import closure, read from *root* (the interpreter's first search directory)."""

    def __init__(self, root: str, cwd: Optional[str]):
        self.root, self.cwd = root, cwd
        self.seen: Set[str] = set()

    def file(self, path: str) -> Optional[str]:
        real = os.path.realpath(path)
        if real in self.seen:
            return None
        self.seen.add(real)
        if len(self.seen) > _MAX_FILES:
            return UNPROVEN
        code = _read(real)
        return UNPROVEN if code is None else self.code(code, os.path.dirname(real))

    def code(self, code: str, package_dir: str) -> Optional[str]:
        if _decode(code.encode("utf-8", "surrogatepass")) is None:
            return UNPROVEN
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError):
            return UNPROVEN
        parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        bound = _bound_names(tree)
        sqlite_modules, sqlite_openers = set(), set()
        outcomes: List[Optional[str]] = []
        literal_runs = {id(n.func) for n in ast.walk(tree) if self.literal_run(n)}
        for node in ast.walk(tree):
            if id(node) in literal_runs:
                continue
            if isinstance(node, ast.Call) and id(node.func) in literal_runs:
                outcomes.append(self.code(self.literal_payload(node), package_dir))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    outcomes.append(self.module(alias.name, self.root))
                    if alias.name.split(".")[0] in _SQLITE_MODULES:
                        sqlite_modules.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                outcomes.append(self.import_from(node, package_dir))
                if not node.level and (node.module or "").split(".")[0] in _SQLITE_MODULES:
                    sqlite_openers.update(a.asname or a.name for a in node.names if a.name in _OPENERS)
                    sqlite_modules.update(a.asname or a.name for a in node.names if a.name not in _OPENERS)
            elif isinstance(node, ast.Name) and (node.id in _UNPROVABLE_NAMES or _dunder(node.id)):
                outcomes.append(UNPROVEN)
            elif isinstance(node, ast.Attribute) and (
                    _dunder(node.attr) or node.attr in _CODE_OBJECTS
                    or (_root(node) == "sys" and node.attr in _IMPORT_STATE
                        and not _reads_import_state(node, parents, bound))):
                outcomes.append(UNPROVEN)
            elif isinstance(node, ast.Call):
                outcomes.append(self.call(node, sqlite_modules, sqlite_openers))
        return _worst(outcomes)

    @staticmethod
    def literal_payload(node: ast.AST) -> Optional[str]:
        """The code an ``exec``/``eval``/``compile`` call runs when it is written out, else None."""
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args):
            return None
        if node.func.id not in ("exec", "eval", "compile"):
            return None
        argument = node.args[0]
        if node.func.id != "compile" and isinstance(argument, ast.Call):
            return _Closure.literal_payload(argument) if getattr(argument.func, "id", None) == "compile" else None
        return _string(argument)

    def literal_run(self, node: ast.AST) -> bool:
        return self.literal_payload(node) is not None

    def call(self, node: ast.Call, sqlite_modules: Set[str], sqlite_openers: Set[str]) -> Optional[str]:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in _DYNAMIC_IMPORTERS and node.args and (_string(node.args[0]) or "").split(".")[0] == _GAIA_PACKAGE:
            return STORE
        if name in _ATTRIBUTE_READERS and len(node.args) > 1:
            attribute = _string(node.args[1])
            if attribute is None or _dunder(attribute) or attribute in _UNPROVABLE_NAMES | _DYNAMIC_IMPORTERS:
                return UNPROVEN
        opener = (name in sqlite_openers and isinstance(func, ast.Name)) or (
            name in _OPENERS and isinstance(func, ast.Attribute) and _root(func) in sqlite_modules)
        return STORE if opener and _opens_store(node, self.cwd) else None

    def import_from(self, node: ast.ImportFrom, package_dir: str) -> Optional[str]:
        if not node.level:
            module = node.module or ""
            return _worst([self.module(module, self.root),
                           *(self.module(f"{module}.{a.name}", self.root, submodule=True) for a in node.names)])
        base = package_dir
        for _ in range(node.level - 1):
            base = os.path.dirname(base)
        parts = (node.module or "").split(".") if node.module else []
        names = [parts] if parts else [[a.name] for a in node.names]
        return _worst(self.local(base, dotted) for dotted in names)

    def module(self, dotted: str, root: str, submodule: bool = False) -> Optional[str]:
        top = dotted.split(".")[0]
        if top == _GAIA_PACKAGE:
            return STORE
        if not submodule and top in _UNPROVABLE_MODULES:
            return UNPROVEN
        found = self.local(root, dotted.split("."), optional=True)
        if found != "absent":
            return found
        return None if submodule or _installed(top) else UNPROVEN

    def local(self, base: str, parts: List[str], optional: bool = False) -> Optional[str]:
        """Analyse the local module *parts* under *base*: each package __init__ on the way and the module itself."""
        outcomes: List[Optional[str]] = []
        directory = base
        for index, part in enumerate(parts):
            package, source = os.path.join(directory, part), os.path.join(directory, part + ".py")
            if os.path.isdir(package):
                if os.path.isfile(os.path.join(os.path.realpath(package), "store", "schema.sql")):
                    return STORE
                init = os.path.join(package, "__init__.py")
                if os.path.isfile(init):
                    outcomes.append(self.file(init))
                directory = package
            elif os.path.isfile(source):
                outcomes.append(self.file(source))
                break
            elif _compiled(directory, part):
                return UNPROVEN
            elif index == 0 and optional:
                return "absent"
            else:
                break
        return _worst(outcomes)


def _compiled(directory: str, name: str) -> bool:
    try:
        entries = os.listdir(directory)
    except OSError:
        return False
    cache = os.path.join(directory, "__pycache__")
    cached = os.listdir(cache) if os.path.isdir(cache) else []
    return any(e.startswith(name + ".") and e.endswith((".so", ".pyd", ".pyc")) for e in entries) or any(
        e.startswith(name + ".") for e in cached)


def _opens_store(call: ast.Call, cwd: Optional[str]) -> bool:
    from .gaia_db_write_guard import _resolves_to_store, _uri_path

    target = call.args[0] if call.args else next(
        (kw.value for kw in call.keywords if kw.arg in ("database", "url", "filename")), None)
    path = _string(target)
    if path is None:
        return True
    path = re.sub(r"^sqlite[^:]*:/{2,3}", "/", path) if path.startswith("sqlite") else _uri_path(path)
    return path not in ("", ":memory:") and _resolves_to_store(path, cwd)


def _gaia_cli_shaped(path: str) -> bool:
    parent = os.path.dirname(path)
    if os.path.basename(path) == "gaia" and os.path.basename(parent) == "bin":
        return True
    return path.endswith(".py") and os.path.basename(parent) == "cli" and os.path.basename(os.path.dirname(parent)) == "bin"


def _shebang_interpreter(path: str) -> Optional[str]:
    """The real path of the program a ``#!`` line runs (through ``env`` too), ``""`` when unresolvable, None without one."""
    try:
        with open(path, "rb") as handle:
            first = handle.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    words = first[2:].decode("utf-8", "replace").split()
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-") and "=" not in w]
    if not words:
        return ""
    program = words[0] if "/" in words[0] else shutil.which(words[0])
    if program and os.path.exists(program):
        return os.path.realpath(program)
    return words[0] if _PYTHON.match(os.path.basename(words[0])) else ""


def _script_lane(path: str) -> Optional[str]:
    """``"python"`` for a Python script, ``"unknown"`` for a shebang no lane models, None for anything else."""
    from .mutative_verbs import _classifier_rule_names

    interpreter = _shebang_interpreter(path)
    if interpreter is None:
        return "python" if path.endswith(".py") else None
    name = os.path.basename(interpreter)
    if _PYTHON.match(name):
        return "python"
    return None if name and _classifier_rule_names(name, programs_only=True) else "unknown"


def _absolute(path: str, cwd: str) -> str:
    return os.path.join(cwd, os.path.expanduser(path))


def _script(path: str, cwd: str) -> Optional[str]:
    from pathlib import Path

    from .gaia_cli_only_guard import is_trusted_gaia_binary
    from .mutative_verbs import _gaia_shipped_script

    full = _absolute(path, cwd)
    if _gaia_cli_shaped(full) or _gaia_cli_shaped(os.path.realpath(full)):
        linked = os.path.islink(full) and is_trusted_gaia_binary(full)
        return None if linked or _gaia_shipped_script(Path(full)) else UNPROVEN
    if not os.path.isfile(full):
        return UNPROVEN
    return _Closure(os.path.dirname(os.path.realpath(full)), cwd).file(full)


def _split_heredocs(command: str) -> Tuple[str, Dict[str, str]]:
    """*command* with every heredoc body removed, and each body by its delimiter."""
    lines, kept, bodies, index = command.split("\n"), [], {}, 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for opener in _HEREDOC_OPENER.finditer(line):
            body: List[str] = []
            while index < len(lines):
                current = lines[index]
                index += 1
                if (current.lstrip("\t") if opener.group(1) else current) == opener.group(3):
                    break
                body.append(current)
            bodies[opener.group(3)] = "\n".join(body)
    return "\n".join(kept), bodies


def _stdin(tokens: List[str], bodies: Dict[str, str]) -> Tuple[List[str], Optional[Tuple[str, str]]]:
    """*tokens* without redirections, and what feeds stdin: ``("body", code)``, ``("file", path)`` or None."""
    rest: List[str] = []
    stdin: Optional[Tuple[str, str]] = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token.startswith("<<") and not token.startswith("<<<"):
            delimiter = token.lstrip("<").lstrip("-").strip("'\"") or following.strip("'\"")
            stdin = ("body", bodies.get(delimiter, "\0"))
            index += 1 if token.lstrip("<").lstrip("-") else 2
        elif token.startswith("<<<"):
            stdin = ("body", "\0")
            index += 1 if token[3:] else 2
        elif token == "<" or (token.startswith("<") and not token.startswith("<&")):
            stdin = ("file", token[1:] or following)
            index += 1 if token[1:] else 2
        elif _REDIRECT.match(token):
            index += 1 if _REDIRECT.sub("", token) else 2
        else:
            rest.append(token)
            index += 1
    return rest, stdin


def _parse(tokens: List[str]) -> Tuple[str, str, List[str]]:
    """How python *tokens* start their program: ``(kind, value, -W specs)``, kind c, m, script, stdin or unproven."""
    warnings: List[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-":
            break
        if token.startswith("--"):
            return "unproven", "", warnings
        if not token.startswith("-"):
            return "script", token, warnings
        letters = token[1:]
        for position, letter in enumerate(letters):
            attached = letters[position + 1:]
            value = attached or (tokens[index + 1] if index + 1 < len(tokens) else "")
            if letter in "cm":
                return letter, value, warnings
            if letter in "WX":
                if letter == "X" and value not in _INERT_X_OPTIONS:
                    return "unproven", "", warnings
                if letter == "W":
                    warnings.append(value)
                index += 0 if attached else 1
                break
            if letter not in _MODELLED_FLAGS:
                return "unproven", "", warnings
        index += 1
    return "stdin", "", warnings


def _program(kind: str, value: str, stdin: Optional[Tuple[str, str]], cwd: str) -> Optional[str]:
    if kind == "c":
        return _Closure(cwd, cwd).code(value, cwd)
    if kind == "m":
        return _run_module(value, cwd) if value else UNPROVEN
    if kind == "script":
        return _script(value, cwd)
    if kind == "unproven" or stdin is None:
        return UNPROVEN
    source, payload = stdin
    if source == "file":
        return _script(payload, cwd) if payload else UNPROVEN
    return UNPROVEN if payload == "\0" else _Closure(cwd, cwd).code(payload, cwd)


def _interpreter(tokens: List[str], bodies: Dict[str, str], cwd: str) -> Optional[str]:
    """The closure outcome of one python invocation *tokens* (``tokens[0]`` is the interpreter)."""
    if any(t in ("--version", "-V", "-VV", "--help", "-h") for t in tokens[1:]):
        return None
    tokens, stdin = _stdin(tokens, bodies)
    kind, value, warnings = _parse(tokens)
    return _worst([*(_warning_imports(w, cwd) for w in warnings), _program(kind, value, stdin, cwd)])


def _runs_pytest(tokens: List[str]) -> bool:
    tokens = (_runner_tail(tokens) or (tokens, None))[0]
    if not _PYTHON.match(os.path.basename(tokens[0])):
        return False
    kind, module, _ = _parse(_stdin(tokens, {})[0])
    return kind == "m" and module.split(".")[0] == "pytest"


def _run_module(module: str, cwd: str) -> Optional[str]:
    if module.split(".")[0] in _LANE_MODULES:
        return None
    closure = _Closure(cwd, cwd)
    main = closure.local(cwd, [*module.split("."), "__main__"], optional=True)
    return _worst([closure.module(module, cwd), None if main == "absent" else main])


def _warning_imports(spec: str, cwd: str) -> Optional[str]:
    categories = [entry.split(":")[2] for entry in spec.split(",") if entry.count(":") >= 2]
    return _worst(_Closure(cwd, cwd).module(c.rsplit(".", 1)[0], cwd) for c in categories if "." in c)


@functools.lru_cache(maxsize=256)
def _installed(name: str) -> bool:
    """True for a stdlib module or one found in a system or virtualenv site-packages, never the user site."""
    if name in sys.stdlib_module_names or name in sys.builtin_module_names:
        return True
    try:
        directories = [d for d in site.getsitepackages() if os.path.isdir(d)]
        return PathFinder.find_spec(name, directories) is not None
    except Exception:
        return False


def _uv_directory(options: List[str]) -> Optional[str]:
    directory = None
    for index, option in enumerate(options):
        if option == "--directory" and index + 1 < len(options):
            directory = options[index + 1]
        elif option.startswith("--directory="):
            directory = option.split("=", 1)[1]
    return directory


def _runner_tail(tokens: List[str]) -> Optional[Tuple[List[str], Optional[str]]]:
    runner = os.path.basename(tokens[0])
    if runner not in _RUNNERS or "run" not in tokens:
        return None
    start = tokens.index("run") + 1
    for index in range(start, len(tokens)):
        token = tokens[index]
        if os.path.basename(token) in _PYTEST_PROGRAMS:
            tail = ["python3", "-m", "pytest", *tokens[index + 1:]]
        elif _PYTHON.match(os.path.basename(token)):
            tail = tokens[index:]
        elif token.endswith(".py"):
            tail = ["python3", *tokens[index:]]
        else:
            continue
        return tail, _uv_directory(tokens[:index]) if runner == "uv" else None
    return None


def _component(tokens: List[str], bodies: Dict[str, str], cwd: str) -> Optional[str]:
    run = _runner_tail(tokens)
    if run:
        tokens, directory = run
        cwd = _absolute(directory, cwd) if directory else cwd
    head = os.path.basename(tokens[0])
    if _PYTHON.match(head):
        return _interpreter(tokens, bodies, cwd)
    full = _absolute(tokens[0], cwd)
    if not (("/" in tokens[0] or head.endswith(".py")) and os.path.isfile(full)):
        return None
    lane = _script_lane(full)
    return _script(tokens[0], cwd) if lane == "python" else UNPROVEN if lane == "unknown" else None


def closure(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """``STORE`` when a Python program *command* runs reaches Gaia's store, ``UNPROVEN`` when its closure is not proven, else None."""
    from ..tools.shell_parser import shell_split
    from .gaia_cli_invocation import resolve_cat_heredocs
    from .mutative_verbs import _peel_leading_command_wrappers
    from .shell_write_guard import _split_components

    if not command:
        return None
    cwd = cwd or os.getcwd()
    lines, bodies = _split_heredocs(resolve_cat_heredocs(command, keep_newlines=True))
    outcomes: List[Optional[str]] = []
    pytest_runs = 0
    for component in _split_components(lines):
        peeled, _ = _peel_leading_command_wrappers(component.strip())
        try:
            tokens = shell_split(peeled)
        except ValueError:
            tokens = peeled.split()
        if tokens:
            outcomes.append(_component(tokens, bodies, cwd))
            pytest_runs += _runs_pytest(tokens)
    pythons = sum(1 for w in lines.split() if _PYTHON.match(os.path.basename(w)))
    if pythons:
        for match in _PATH_ENVIRONMENT.finditer(lines):
            if match.group(1) != "PYTHONPATH" or pythons != pytest_runs:
                outcomes.append(UNPROVEN)
        outcomes.extend(_warning_imports(m.group(2), cwd) for m in _WARNINGS_ENVIRONMENT.finditer(lines))
    return _worst(outcomes)


def check(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """``STORE_CODE_REJECTION`` when a Python program *command* runs reaches Gaia's store in-process, else None."""
    return STORE_CODE_REJECTION if closure(command, cwd) == STORE else None


def unproven_reason(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """Why a Python program *command* runs cannot run free: its closure is not proven; None when proven or no Python runs."""
    if closure(command, cwd) is None:
        return None
    return ("Python whose whole closure is not proven (a local module, a dynamic import, exec of a string, "
            "a changed import path, or a program read from stdin) asks for a signature")
