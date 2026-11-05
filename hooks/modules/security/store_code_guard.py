"""Python a command runs that reaches Gaia's store in-process: an import of Gaia's own package, or a sqlite open it cannot prove is elsewhere."""

from __future__ import annotations

import ast
import os
import re
import shlex
from typing import Iterator, List, Optional

_PYTHON = re.compile(r"^(?:python|pypy|py)[0-9.]*(?:\.exe)?$")
_GAIA_PACKAGE = "gaia"
_SQLITE_MODULES = frozenset({"sqlite3", "apsw", "pysqlite2", "pysqlite3", "sqlalchemy", "sqlite_utils", "peewee", "aiosqlite"})
_OPENERS = frozenset({"connect", "Connection", "create_engine", "Database", "SqliteDatabase"})
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__", "run_module"})
_VALUE_OPTIONS = frozenset({"-X", "-W", "-Q"})

STORE_CODE_REJECTION = (
    "This Python reaches Gaia's store in-process: it imports Gaia's own package or opens a "
    "database it cannot prove is not the store. Gaia's store is written only through the "
    "`gaia` CLI, whatever the environment says. Do NOT retry -- this is not approvable."
)


def _top(name: str) -> str:
    return name.split(".", 1)[0]


def _string(node: Optional[ast.AST]) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _root(node: ast.AST) -> Optional[str]:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _opens_store(call: ast.Call, cwd: Optional[str]) -> bool:
    from .gaia_db_write_guard import _resolves_to_store, _uri_path

    target = call.args[0] if call.args else next(
        (kw.value for kw in call.keywords if kw.arg in ("database", "url", "filename")), None)
    path = _string(target)
    if path is None:
        return True
    path = re.sub(r"^sqlite[^:]*:/{2,3}", "/", path) if path.startswith("sqlite") else _uri_path(path)
    return path not in ("", ":memory:") and _resolves_to_store(path, cwd)


def reaches_store(code: str, cwd: Optional[str]) -> bool:
    """True when *code* imports Gaia's package, names it to a dynamic importer, or opens a sqlite path not proven outside the store."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    modules, openers = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _top(alias.name) == _GAIA_PACKAGE:
                    return True
                if _top(alias.name) in _SQLITE_MODULES:
                    modules.add((alias.asname or alias.name).split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            if _top(node.module) == _GAIA_PACKAGE:
                return True
            if _top(node.module) in _SQLITE_MODULES:
                openers.update(a.asname or a.name for a in node.names if a.name in _OPENERS)
                modules.update(a.asname or a.name for a in node.names if a.name not in _OPENERS)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in _DYNAMIC_IMPORTERS and node.args:
            imported = _string(node.args[0])
            if imported is None or _top(imported) == _GAIA_PACKAGE:
                return True
        if (name in openers and isinstance(func, ast.Name)) or (
                name in _OPENERS and isinstance(func, ast.Attribute) and _root(func) in modules):
            if _opens_store(node, cwd):
                return True
    return False


def _is_gaia_cli_script(path: str) -> bool:
    real = os.path.realpath(path)
    parent = os.path.dirname(real)
    if os.path.basename(real) == "gaia" and os.path.basename(parent) == "bin":
        root = os.path.dirname(parent)
    elif real.endswith(".py") and os.path.basename(parent) == "cli" and os.path.basename(os.path.dirname(parent)) == "bin":
        root = os.path.dirname(os.path.dirname(parent))
    else:
        return False
    return os.path.isfile(os.path.join(root, "gaia", "store", "schema.sql"))


def _read(path: str, cwd: Optional[str]) -> str:
    full = path if os.path.isabs(path) or cwd is None else os.path.join(cwd, path)
    try:
        with open(os.path.expanduser(full), encoding="utf-8", errors="replace") as handle:
            return handle.read(1 << 20)
    except OSError:
        return ""


def _programs(tokens: List[str], heredocs: List[str], cwd: Optional[str]) -> Iterator[str]:
    head = os.path.basename(tokens[0])
    if not _PYTHON.match(head):
        if head.endswith(".py") and not _is_gaia_cli_script(tokens[0]):
            yield _read(tokens[0], cwd)
        return
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-c" or (token.startswith("-c") and len(token) > 2):
            yield token[2:] or (tokens[index + 1] if index + 1 < len(tokens) else "")
            return
        if token == "-m" or (token.startswith("-m") and len(token) > 2):
            module = token[2:] or (tokens[index + 1] if index + 1 < len(tokens) else "")
            yield f"import {module}" if module and _top(module) == _GAIA_PACKAGE else ""
            return
        if token == "-":
            break
        if token in _VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if not _is_gaia_cli_script(token):
            yield _read(token, cwd)
        return
    yield from heredocs


def check(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """``STORE_CODE_REJECTION`` when a Python program *command* runs reaches Gaia's store in-process, else None."""
    from .gaia_db_write_guard import _heredoc_bodies
    from .mutative_verbs import _peel_leading_command_wrappers
    from .shell_write_guard import _split_components

    if not command:
        return None
    heredocs = list(_heredoc_bodies(command))
    for component in _split_components(command):
        peeled, _ = _peel_leading_command_wrappers(component.strip())
        try:
            tokens = shlex.split(peeled, comments=False)
        except ValueError:
            tokens = peeled.split()
        if tokens and any(reaches_store(code, cwd) for code in _programs(tokens, heredocs, cwd) if code):
            return STORE_CODE_REJECTION
    return None
