"""Python a command runs is free only when its whole import closure is proven; code that reaches Gaia's store is refused."""

from __future__ import annotations

import ast
import functools
import os
import re
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
_CODE_OBJECTS = frozenset({"CodeType", "FunctionType"})
_ATTRIBUTE_READERS = frozenset({"getattr", "setattr", "delattr"})
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__", "run_module", "run_path"})
_SQLITE_MODULES = frozenset({"sqlite3", "apsw", "pysqlite2", "pysqlite3", "sqlalchemy", "sqlite_utils", "peewee", "aiosqlite"})
_OPENERS = frozenset({"connect", "Connection", "create_engine", "Database", "SqliteDatabase"})
_PATH_ENVIRONMENT = re.compile(r"(?:^|[\s;&|(])PYTHON(?:PATH|HOME|STARTUP|USERBASE|SAFEPATH|PLATLIBDIR|BREAKPOINT)=")
_WARNINGS_ENVIRONMENT = re.compile(r"(?:^|[\s;&|(])PYTHONWARNINGS=(['\"]?)([^\s'\"]*)\1")
_COOKIE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
_PLAIN_ENCODINGS = frozenset({
    "utf-8", "utf8", "utf-8-sig", "ascii", "us-ascii", "latin-1", "latin1", "iso-8859-1", "iso8859-1", "l1",
})
_MODELLED_FLAGS = frozenset("IEsSBuObqvdPRVh")
_LANE_MODULES = frozenset({"pytest", "pip"})
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
                    or (_root(node) == "sys" and node.attr in _IMPORT_STATE)):
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


def _python_script(path: str) -> bool:
    if path.endswith(".py"):
        return True
    head = (_read(path) or "")[:200]
    return head.startswith("#!") and bool(re.search(r"\b(?:i?python|pypy)[0-9.]*\b", head.splitlines()[0]))


def _absolute(path: str, cwd: str) -> str:
    return os.path.join(cwd, os.path.expanduser(path))


def _script(path: str, cwd: str) -> Optional[str]:
    from pathlib import Path

    from .mutative_verbs import _gaia_shipped_script

    full = _absolute(path, cwd)
    if _gaia_cli_shaped(full) or _gaia_cli_shaped(os.path.realpath(full)):
        return None if _gaia_shipped_script(Path(full)) else UNPROVEN
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


def _interpreter(tokens: List[str], bodies: Dict[str, str], cwd: str) -> Optional[str]:
    """The closure outcome of one python invocation *tokens* (``tokens[0]`` is the interpreter)."""
    if any(t in ("--version", "-V", "-VV", "--help", "-h") for t in tokens[1:]):
        return None
    tokens, stdin = _stdin(tokens, bodies)
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-":
            break
        if token.startswith("-") and not token.startswith("--"):
            letters = token[1:]
            for position, letter in enumerate(letters):
                rest = letters[position + 1:] or (tokens[index + 1] if index + 1 < len(tokens) else "")
                if letter == "c":
                    return _Closure(cwd, cwd).code(rest, cwd)
                if letter == "m":
                    return _run_module(rest, cwd) if rest else UNPROVEN
                if letter == "W":
                    warned = _warning_imports(rest, cwd)
                    if warned:
                        return warned
                    index += 0 if letters[position + 1:] else 1
                    break
                if letter not in _MODELLED_FLAGS:
                    return UNPROVEN
            index += 1
            continue
        if token.startswith("--"):
            return UNPROVEN
        return _script(token, cwd)
    if stdin is None:
        return UNPROVEN
    kind, value = stdin
    if kind == "file":
        return _script(value, cwd) if value else UNPROVEN
    return UNPROVEN if value == "\0" else _Closure(cwd, cwd).code(value, cwd)


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


def _runner_tail(tokens: List[str]) -> Optional[List[str]]:
    if os.path.basename(tokens[0]) not in _RUNNERS or "run" not in tokens:
        return None
    for token in tokens[tokens.index("run") + 1:]:
        if _PYTHON.match(os.path.basename(token)):
            return tokens[tokens.index(token):]
        if token.endswith(".py"):
            return ["python3", *tokens[tokens.index(token):]]
    return None


def _component(tokens: List[str], bodies: Dict[str, str], cwd: str) -> Optional[str]:
    tokens = _runner_tail(tokens) or tokens
    head = os.path.basename(tokens[0])
    if _PYTHON.match(head):
        return _interpreter(tokens, bodies, cwd)
    full = _absolute(tokens[0], cwd)
    if ("/" in tokens[0] or head.endswith(".py")) and os.path.isfile(full) and _python_script(full):
        return _script(tokens[0], cwd)
    return None


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
    for component in _split_components(lines):
        peeled, _ = _peel_leading_command_wrappers(component.strip())
        try:
            tokens = shell_split(peeled)
        except ValueError:
            tokens = peeled.split()
        if tokens:
            outcomes.append(_component(tokens, bodies, cwd))
    if any(_PYTHON.match(os.path.basename(w)) for w in lines.split()):
        if _PATH_ENVIRONMENT.search(lines):
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
