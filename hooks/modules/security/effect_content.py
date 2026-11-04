"""Decide a Python test suite's effect from the files it loads: free only when none carries an effect marker."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

MARKER_SET_VERSION = "2"
MAX_FILES = 4000
MAX_FILE_BYTES = 2_000_000

NETWORK, SUBPROCESS, DATABASE, SECRET, WRITE, CANNOT = (
    "network", "subprocess", "database", "secret", "write outside tmp or scratch", "cannot be analysed",
)

_MODULE_MARKERS: Dict[str, str] = {
    **dict.fromkeys(("subprocess", "pty", "pexpect", "sh", "plumbum", "multiprocessing", "concurrent.futures.process"), SUBPROCESS),
    **dict.fromkeys((
        "requests", "httpx", "aiohttp", "urllib.request", "urllib3", "http.client", "http.server", "socket",
        "socketserver", "ssl", "websocket", "websockets", "grpc", "paramiko", "fabric", "asyncssh", "pycurl",
        "boto3", "botocore", "google.cloud", "googleapiclient", "kubernetes", "ftplib", "smtplib", "poplib",
        "imaplib", "telnetlib", "xmlrpc.client", "webbrowser",
    ), NETWORK),
    **dict.fromkeys((
        "psycopg", "psycopg2", "asyncpg", "pymysql", "MySQLdb", "mysql", "pymongo", "motor", "redis",
        "sqlalchemy", "alembic", "peewee", "cassandra", "elasticsearch",
    ), DATABASE),
    **dict.fromkeys(("dotenv", "keyring", "google.auth", "hvac", "secretstorage", "oauth2client"), SECRET),
    **dict.fromkeys(("ctypes", "cffi", "runpy", "code", "pickle", "marshal", "imp", "pkgutil", "importlib",
                     "zipimport"), CANNOT),
}
_LOADER_NAMES = frozenset({
    "importorskip", "import_module", "exec_module", "load_module", "module_from_spec", "spec_from_file_location",
    "spec_from_loader", "SourceFileLoader", "SourcelessFileLoader", "ExtensionFileLoader", "run_path",
    "run_module", "load_source", "load_compiled", "load_dynamic", "__import__",
})
_PROCESS_CALL_RE = re.compile(r"os\.(system|popen|spawn\w*|exec\w*|fork\w*|posix_spawnp?|kill|killpg)|asyncio\.create_subprocess_\w+")
_DYNAMIC_NAMES = frozenset({"eval", "exec", "compile", "__import__", "globals", "vars", "breakpoint"})
_SECRET_NAME_RE = re.compile(r"SECRET|PASSW|API_?KEY|PRIVATE_KEY|ACCESS_KEY|TOKEN|CREDENTIAL", re.IGNORECASE)
_OPENERS = frozenset({"open", "io.open", "builtins.open", "codecs.open", "os.open", "gzip.open", "bz2.open",
                      "lzma.open", "tarfile.open", "zipfile.ZipFile", "shelve.open", "dbm.open"})
_PATH_WRITE_METHODS = frozenset({"write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "chmod",
                                 "lchmod", "symlink_to", "hardlink_to", "link_to"})
_ONE_ARGUMENT_MOVES = frozenset({"rename", "replace"})
_DESTINATION_SECOND = re.compile(r"shutil\.(copy\w*|move|unpack_archive)|os\.(rename|renames|replace|link|symlink)")
_WRITE_CALL_RE = re.compile(
    r"shutil\.(copy\w*|move|rmtree|make_archive|unpack_archive|chown)"
    r"|os\.(remove|unlink|rename|renames|replace|makedirs|mkdir|rmdir|removedirs|chmod|chown|lchown|symlink|link"
    r"|truncate|mkfifo|mknod|utime|write)"
)
_TMP_FACTORIES = re.compile(r"tempfile\.(mkdtemp|mkstemp|gettempdir|TemporaryDirectory|NamedTemporaryFile|TemporaryFile|SpooledTemporaryFile)")
_TMP_FIXTURES = frozenset({"tmp_path", "tmpdir", "tmp_path_factory", "tmpdir_factory"})
_CONTAINED_METHODS = frozenset({"joinpath", "join", "resolve", "absolute", "with_name", "with_suffix", "with_stem",
                                "mktemp", "mkdir", "__truediv__"})
_PATH_WRAPPERS = frozenset({"str", "os.fspath", "Path", "pathlib.Path", "PurePath", "pathlib.PurePath",
                            "os.path.join", "os.path.abspath", "os.path.realpath", "os.path.normpath"})
_TMP = "<tmp>"
_BUILTIN_HEADS = frozenset({"open", "Path", "str", "PurePath", "pytest", "fixture"})


@dataclass(frozen=True)
class Finding:
    """The first marker of a suite, or none: what the verdict names and where it was found."""

    kind: Optional[str]
    path: str = ""
    line: int = 0
    detail: str = ""
    files: int = 0

    @property
    def free(self) -> bool:
        return self.kind is None


def _data_home() -> Path:
    try:
        from ..core.paths import get_data_home

        return Path(get_data_home())
    except Exception:
        return Path(os.environ.get("GAIA_DATA_DIR") or Path.home() / ".gaia")


def cache_dir() -> Path:
    """The directory of cached file facts; protected_paths refuses every writer but this module."""
    return _data_home() / "cache" / "effect-analysis"


def key_path() -> Path:
    """The HMAC key that authenticates cache entries; its .key suffix makes every read surface refuse it."""
    return _data_home() / "cache" / "effect-analysis-key" / "entries.key"


def protected_dirs() -> Tuple[Path, Path]:
    """The directories only this module writes: the cache and its key."""
    return cache_dir(), key_path().parent


def cache_entry_path(content: bytes) -> Optional[Path]:
    """The cache file holding the facts of a file with this exact content."""
    return cache_dir() / f"{_digest(content)}.json"


def _digest(content: bytes) -> str:
    return hashlib.sha256(MARKER_SET_VERSION.encode() + b"\0" + content).hexdigest()


def _key() -> Optional[bytes]:
    """The entry key, created on first use; None (no caching) unless it is a private regular file reached by no link."""
    path = key_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(handle, "wb") as stream:
                stream.write(os.urandom(32))
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(handle, "rb") as stream:
            status = os.fstat(stream.fileno())
            key = stream.read(64)
    except OSError:
        return None
    private = status.st_uid == os.getuid() and not status.st_mode & 0o077 and status.st_nlink == 1
    if not private or len(key) != 32 or os.path.realpath(path) != os.path.abspath(path):
        return None
    return key


def _mac(key: bytes, content: bytes, facts: dict) -> str:
    import hmac

    message = _digest(content).encode() + b"\0" + json.dumps(facts, sort_keys=True).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _cached_facts(content: bytes, key: Optional[bytes]) -> Optional[dict]:
    """Facts from an entry whose MAC this key verifies; any other entry is ignored as if absent."""
    import hmac

    if key is None:
        return None
    try:
        data = json.loads(cache_entry_path(content).read_text(encoding="utf-8"))
        facts, mac = data["facts"], data["mac"]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if isinstance(facts, dict) and isinstance(mac, str) and hmac.compare_digest(mac, _mac(key, content, facts)):
        return facts
    return None


def _store_facts(content: bytes, facts: dict, key: Optional[bytes]) -> None:
    if key is None:
        return
    entry = cache_entry_path(content)
    try:
        entry.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=entry.parent, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"version": MARKER_SET_VERSION, "facts": facts, "mac": _mac(key, content, facts)}, stream)
        os.replace(temporary, entry)
    except OSError:
        pass


class _FileScan(ast.NodeVisitor):
    """Collect one module's facts: markers, imports, write sites, name bindings and fixtures."""

    def __init__(self) -> None:
        self.aliases: Dict[str, str] = {}
        self.markers: List[list] = []
        self.imports: List[list] = []
        self.sites: List[list] = []
        self.assigns: Dict[str, List[Optional[str]]] = {}
        self.fixtures: Dict[str, List[Optional[str]]] = {}
        self._callees: Set[int] = set()

    def _mark(self, kind: str, node: ast.AST, detail: str) -> None:
        self.markers.append([kind, getattr(node, "lineno", 0), detail])

    def _dotted(self, node: ast.AST) -> Optional[str]:
        """The module path *node* names through this file's imports; a local variable names none."""
        if isinstance(node, ast.Name):
            if node.id in self.aliases:
                return self.aliases[node.id]
            return node.id if node.id in _BUILTIN_HEADS else None
        if isinstance(node, ast.Attribute):
            head = self._dotted(node.value)
            return f"{head}.{node.attr}" if head else None
        return None

    def _module_marker(self, name: str, node: ast.AST) -> None:
        for prefix, kind in _MODULE_MARKERS.items():
            if name == prefix or name.startswith(prefix + "."):
                self._mark(kind, node, name)
                return

    def _bind(self, name: str, root: Optional[str]) -> None:
        self.assigns.setdefault(name, []).append(root)

    def _root(self, node: Optional[ast.AST]) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            temp = tempfile.gettempdir().rstrip("/") + "/"
            inside = text.startswith(("/tmp/", temp)) or "/.gaia/scratch/" in text
            return _TMP if inside and ".." not in text else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
            return self._root(node.left) if _relative_segment(node.right) else None
        if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.FormattedValue):
            rest = node.values[1:]
            if all(isinstance(part, ast.Constant) and ".." not in str(part.value) for part in rest):
                return self._root(node.values[0].value)
            return None
        if isinstance(node, ast.Call):
            dotted = self._dotted(node.func) or ""
            if _TMP_FACTORIES.fullmatch(dotted):
                return _TMP
            if not all(_relative_segment(arg) for arg in node.args[1:]):
                return None
            if dotted in _PATH_WRAPPERS and node.args:
                return self._root(node.args[0]) if _relative_segment_head(node.args[0]) else None
            if isinstance(node.func, ast.Attribute) and node.func.attr in _CONTAINED_METHODS:
                if all(_relative_segment(arg) for arg in node.args):
                    return self._root(node.func.value)
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            self.aliases[alias.asname or top] = alias.name if alias.asname else top
            self.imports.append([alias.name, 0, node.lineno])
            self._module_marker(alias.name, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self.imports.append([module, node.level, node.lineno, "*"])
                self._module_marker(module, node)
                continue
            full = f"{module}.{alias.name}" if module else alias.name
            self.aliases[alias.asname or alias.name] = full if not node.level else alias.asname or alias.name
            self.imports.append([full, node.level, node.lineno])
            if not node.level:
                self._module_marker(full, node)
                self._reference(full, node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        injected = node.name.startswith("test")
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (self._dotted(target) or "").endswith("fixture"):
                injected = True
                name = node.name
                if isinstance(decorator, ast.Call):
                    name = next((kw.value.value for kw in decorator.keywords
                                 if kw.arg == "name" and isinstance(kw.value, ast.Constant)), name)
                values = [each.value for each in ast.walk(node)
                          if isinstance(each, (ast.Return, ast.Yield)) and each.value is not None]
                self.fixtures.setdefault(name, []).extend([self._root(value) for value in values] or [None])
        arguments = node.args
        positional = arguments.posonlyargs + arguments.args
        if not injected:
            for arg in positional + arguments.kwonlyargs:
                self._bind(arg.arg, None)
        for arg, default in zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults):
            self._bind(arg.arg, self._root(default))
        for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
            if default is not None:
                self._bind(arg.arg, self._root(default))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._assign_target(target, node.value)
            if (self._dotted(target) or "") == "sys.path":
                self._mark(CANNOT, node, "sys.path is reassigned")
            if isinstance(target, ast.Name) and target.id == "pytest_plugins":
                self._plugins(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assign_target(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, None)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind(node.target.id, self._root(node.value))
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._assign_target(node.target, None)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._assign_target(node.target, None)
        self.generic_visit(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        if node.optional_vars is not None:
            self._assign_target(node.optional_vars, node.context_expr)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name, None)
        self.generic_visit(node)

    def _assign_target(self, target: ast.AST, value: Optional[ast.AST]) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, self._root(value) if value is not None else None)
            dotted = self._dotted(value) if value is not None else None
            if dotted and dotted != target.id:
                self.aliases[target.id] = dotted
        else:
            for each in ast.walk(target):
                if isinstance(each, ast.Name):
                    self._bind(each.id, None)

    def _plugins(self, value: ast.AST) -> None:
        names = [value] if isinstance(value, ast.Constant) else getattr(value, "elts", None)
        if names is None or not all(isinstance(each, ast.Constant) and isinstance(each.value, str) for each in names):
            self._mark(CANNOT, value, "pytest_plugins is computed")
            return
        for each in names:
            self.imports.append([each.value, 0, value.lineno])
            self._module_marker(each.value, value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (self._dotted(node.value) or "") == "os.environ" and isinstance(node.ctx, ast.Load):
            self._environment_read(node, node.slice)
        if (self._dotted(node.value) or "") in ("sys.modules", "sys.path") and not isinstance(node.ctx, ast.Load):
            self._mark(CANNOT, node, f"{self._dotted(node.value)} is changed")
        self.generic_visit(node)

    def _environment_read(self, node: ast.AST, key: Optional[ast.AST]) -> None:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if _SECRET_NAME_RE.search(key.value):
                self._mark(SECRET, node, f"environment variable {key.value}")
        else:
            self._mark(CANNOT, node, "environment variable read by a computed name")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _LOADER_NAMES:
            self._mark(CANNOT, node, f".{node.attr} loads a module the analysis cannot place")
        if id(node) not in self._callees:
            dotted = self._dotted(node)
            if dotted:
                self._reference(dotted, node)
            if node.attr in _PATH_WRITE_METHODS or node.attr in _ONE_ARGUMENT_MOVES:
                self.sites.append([WRITE, node.lineno, f".{node.attr} referenced, not called", None])
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and id(node) not in self._callees:
            if node.id in _DYNAMIC_NAMES or node.id in ("getattr", "setattr", "delattr"):
                self._mark(CANNOT, node, f"{node.id} is referenced, not called")
            elif self.aliases.get(node.id, node.id) in _OPENERS:
                self.sites.append([WRITE, node.lineno, f"{node.id} referenced by another name", None])
            elif node.id in self.aliases:
                self._reference(self.aliases[node.id], node)
        self.generic_visit(node)

    def _reference(self, dotted: str, node: ast.AST) -> None:
        """Judge a reference to a module attribute whether or not it is called, so an alias cannot hide it."""
        self._module_marker(dotted, node)
        if _PROCESS_CALL_RE.fullmatch(dotted):
            self._mark(SUBPROCESS, node, dotted)
        elif dotted in ("sys.path.insert", "sys.path.append", "sys.path.extend", "site.addsitedir", "sys.meta_path"):
            self._mark(CANNOT, node, f"{dotted} changes where imports resolve")
        elif dotted.rsplit(".", 1)[-1] in _LOADER_NAMES:
            self._mark(CANNOT, node, f"{dotted} loads a module the analysis cannot place")
        elif _WRITE_CALL_RE.fullmatch(dotted) or dotted in _OPENERS or dotted == "sqlite3.connect":
            self.sites.append([WRITE, getattr(node, "lineno", 0), f"{dotted} referenced by another name", None])

    def visit_Call(self, node: ast.Call) -> None:
        self._callees.add(id(node.func))
        dotted = self._dotted(node.func) or ""
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name in _DYNAMIC_NAMES:
            self._mark(CANNOT, node, f"{name}() runs or imports code built at runtime")
        elif name in ("getattr", "setattr", "delattr"):
            self._attribute_call(node, name)
        elif dotted in ("os.getenv", "os.environ.get", "os.environ.pop", "os.environ.setdefault"):
            self._environment_read(node, node.args[0] if node.args else None)
        elif dotted in _OPENERS:
            self._opener(node, dotted)
        elif dotted == "sqlite3.connect":
            target = node.args[0] if node.args else None
            memory = isinstance(target, ast.Constant) and target.value == ":memory:"
            if not memory:
                self.sites.append([DATABASE, node.lineno, "sqlite3.connect", self._root(target)])
        elif _WRITE_CALL_RE.fullmatch(dotted):
            index = 1 if _DESTINATION_SECOND.fullmatch(dotted) else 0
            target = node.args[index] if len(node.args) > index else None
            self.sites.append([WRITE, node.lineno, dotted, self._root(target)])
        elif dotted:
            self._reference(dotted, node.func)
        if isinstance(node.func, ast.Attribute) and not _WRITE_CALL_RE.fullmatch(dotted):
            self._method_write(node)
        self.generic_visit(node)

    def _attribute_call(self, node: ast.Call, name: str) -> None:
        attribute = node.args[1] if len(node.args) > 1 else None
        if not (isinstance(attribute, ast.Constant) and isinstance(attribute.value, str)):
            self._mark(CANNOT, node, f"{name}() with a computed attribute name")
            return
        owner = self._dotted(node.args[0])
        if owner:
            self._reference(f"{owner}.{attribute.value}", node)
        if name == "getattr" and (attribute.value in _PATH_WRITE_METHODS or attribute.value in _ONE_ARGUMENT_MOVES):
            self.sites.append([WRITE, node.lineno, f"getattr(..., {attribute.value!r})", None])

    def _opener(self, node: ast.Call, dotted: str) -> None:
        mode_node = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg in ("mode", "flag", "flags"):
                mode_node = keyword.value
        if dotted == "os.open":
            mode = None
        elif mode_node is None:
            mode = "c" if dotted in ("shelve.open", "dbm.open") else "r"
        else:
            mode = mode_node.value if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str) else None
        if mode is None or set(mode) & set("waxc+"):
            target = node.args[0] if node.args else None
            self.sites.append([WRITE, node.lineno, f"{dotted} in mode {mode!r}", self._root(target)])

    def _method_write(self, node: ast.Call) -> None:
        method = node.func.attr
        receiver = self._root(node.func.value)
        if method in _PATH_WRITE_METHODS:
            self.sites.append([WRITE, node.lineno, f".{method}()", receiver])
        elif method in _ONE_ARGUMENT_MOVES and len(node.args) == 1 and not node.keywords:
            self.sites.append([WRITE, node.lineno, f".{method}() source", receiver])
            self.sites.append([WRITE, node.lineno, f".{method}() destination", self._root(node.args[0])])
        elif method == "open":
            mode_node = node.args[0] if node.args else next(
                (kw.value for kw in node.keywords if kw.arg == "mode"), None)
            mode = "r" if mode_node is None else (
                mode_node.value if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str) else None)
            if mode is None or set(mode) & set("wax+"):
                self.sites.append([WRITE, node.lineno, f".open({mode!r})", receiver])


def _relative_segment(node: ast.AST) -> bool:
    """True for a literal path segment that cannot leave the directory it is joined to."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return not node.value.startswith(("/", "~", "\\")) and ".." not in node.value
    return False


def _relative_segment_head(node: ast.AST) -> bool:
    return not isinstance(node, ast.Constant) or isinstance(node.value, str)


def file_facts(content: bytes) -> dict:
    """Facts of one Python file, read from the cache by content hash or analysed and stored."""
    key = _key()
    cached = _cached_facts(content, key)
    if cached is not None:
        return cached
    try:
        tree = _parse(content)
    except (SyntaxError, ValueError) as error:
        facts = {"markers": [[CANNOT, getattr(error, "lineno", 0) or 0, "the file does not parse"]],
                 "imports": [], "sites": [], "assigns": {}, "fixtures": {}}
    else:
        scan = _FileScan()
        scan.visit(tree)
        facts = {"markers": scan.markers, "imports": scan.imports, "sites": scan.sites,
                 "assigns": scan.assigns, "fixtures": scan.fixtures}
    _store_facts(content, facts, key)
    return facts


def _parse(content: bytes) -> ast.AST:
    return ast.parse(content)


def _ini_settings(start: Path) -> Dict[str, List[str]]:
    """``pythonpath``, ``python_files`` and ``addopts`` of the ini governing *start*; ValueError if it cannot be read."""
    import configparser

    for directory in [start, *start.parents]:
        for name, section in (("pytest.ini", "pytest"), ("pyproject.toml", None), ("tox.ini", "pytest"),
                              ("setup.cfg", "tool:pytest")):
            path = directory / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
                if section is None:
                    import tomllib

                    options = tomllib.loads(text).get("tool", {}).get("pytest", {}).get("ini_options")
                    if options is None:
                        continue
                else:
                    parser = configparser.ConfigParser(interpolation=None)
                    parser.read_string(text)
                    if not parser.has_section(section):
                        if name == "pytest.ini":
                            return {"pythonpath": [], "python_files": [], "addopts": []}
                        continue
                    options = dict(parser[section])
                settings = {
                    key: value.split() if isinstance(value, str) else [str(each) for each in value]
                    for key in ("pythonpath", "python_files", "addopts")
                    for value in [options.get(key, [])]
                }
            except (OSError, UnicodeDecodeError, ValueError, AttributeError, ImportError, configparser.Error) as error:
                raise ValueError(f"{path} cannot be read") from error
            settings["pythonpath"] = [os.path.realpath(directory / entry) for entry in settings["pythonpath"]]
            return settings
    return {"pythonpath": [], "python_files": [], "addopts": []}


def _module_files(name: str, roots: Sequence[Path]) -> Optional[List[Path]]:
    """The files importing *name* runs from the first root that holds it, package __init__ chain included."""
    parts = name.split(".")
    for root in roots:
        files: List[Path] = []
        directory = root
        for index, part in enumerate(parts):
            package, module = directory / part, directory / f"{part}.py"
            if (package / "__init__.py").is_file():
                files.append(package / "__init__.py")
                directory = package
            elif module.is_file():
                files.append(module)
                break
            elif package.is_dir():
                directory = package
            elif any(directory.glob(f"{part}.*so")) or any(directory.glob(f"{part}.*pyd")):
                return files + [directory / f"{part}.compiled"]
            elif index == 0:
                break
            else:
                return files
        else:
            return files
        if files:
            return files
    return None


def _is_library(name: str, rootdir: Path) -> bool:
    top = name.split(".")[0]
    if top in sys.stdlib_module_names or top in ("pytest", "_pytest", "pluggy"):
        return True
    try:
        import importlib.util

        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return False
    origin = getattr(spec, "origin", None) if spec else None
    if not origin or origin in ("built-in", "frozen"):
        return spec is not None
    real = Path(os.path.realpath(origin))
    return rootdir not in real.parents and ("site-packages" in real.parts or "dist-packages" in real.parts)


def _search_roots(path: Path, rootdir: Path, extra: Sequence[Path]) -> List[Path]:
    basedir = path.parent
    while (basedir / "__init__.py").is_file() and basedir != rootdir:
        basedir = basedir.parent
    return list(dict.fromkeys([path.parent, basedir, rootdir, *extra]))


def collect(
    roots: Iterable[Path], rootdir: Path, patterns: Optional[Sequence[str]],
) -> Tuple[List[Path], Optional[Finding]]:
    """The .py files under the roots whose names match *patterns* (None: all), plus conftest and __init__ chains."""
    import fnmatch

    files: List[Path] = []
    for root in roots:
        if root.is_dir():
            for directory, subdirs, names in os.walk(root):
                subdirs[:] = sorted(each for each in subdirs if each not in (".git", "__pycache__"))
                files.extend(Path(directory, name) for name in sorted(names) if name.endswith(".py") and (
                    patterns is None or any(fnmatch.fnmatch(name, pattern) for pattern in patterns)))
                if len(files) > MAX_FILES:
                    return [], Finding(CANNOT, str(root), 0, f"more than {MAX_FILES} Python files")
        elif root.is_file():
            files.append(root)
        elif root.exists():
            return [], Finding(CANNOT, str(root), 0, "the path is neither a file nor a directory")
    files.extend(candidate for candidate in (rootdir / "conftest.py",) if candidate.is_file())
    chain: List[Path] = []
    for directory in dict.fromkeys(each.parent for each in files):
        chain.extend(each / "conftest.py" for each in [directory, *directory.parents] if (each / "conftest.py").is_file())
        while (directory / "__init__.py").is_file():
            chain.append(directory / "__init__.py")
            directory = directory.parent
    return list(dict.fromkeys(files + chain)), None


PYTEST_FILES = ("test_*.py", "*_test.py")


def judge_suite(
    roots: Sequence[Path], rootdir: Path, plugins: Sequence[str] = (), import_roots: Sequence[Path] = (),
    patterns: Optional[Sequence[str]] = PYTEST_FILES, pytest_config: bool = True,
) -> Finding:
    """First effect marker in the closure of the suite under *roots*, or a free Finding counting its files."""
    rootdir = Path(os.path.realpath(rootdir))
    try:
        settings = _ini_settings(rootdir) if pytest_config else {"pythonpath": [], "python_files": [], "addopts": []}
    except ValueError as error:
        return Finding(CANNOT, str(rootdir), 0, str(error))
    if patterns is not None and any("doctest-modules" in each for each in settings["addopts"]):
        patterns = None
    elif patterns is not None and settings["python_files"]:
        patterns = list(settings["python_files"])
    extra = [Path(os.path.realpath(each)) for each in [*import_roots, *settings["pythonpath"]]]
    queue, early = collect([Path(os.path.realpath(each)) for each in roots], rootdir, patterns)
    if early is not None:
        return early
    for plugin in plugins:
        located = _module_files(plugin, [rootdir])
        if located:
            queue.extend(located)
        elif not _is_library(plugin, rootdir):
            return Finding(CANNOT, str(rootdir), 0, f"plugin {plugin} cannot be placed")
    seen: Dict[Path, dict] = {}
    findings: List[Finding] = []
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        if len(seen) >= MAX_FILES:
            return Finding(CANNOT, _show(path, rootdir), 0, f"more than {MAX_FILES} files in the closure")
        try:
            if path.suffix != ".py" or path.stat().st_size > MAX_FILE_BYTES:
                raise OSError("not a readable Python source")
            content = path.read_bytes()
        except OSError:
            findings.append(Finding(CANNOT, _show(path, rootdir), 0, "the file cannot be read as Python source"))
            seen[path] = {}
            continue
        facts = seen[path] = file_facts(content)
        findings.extend(Finding(kind, _show(path, rootdir), line, detail) for kind, line, detail in facts.get("markers", []))
        package = path.parent
        for name, level, line, *star in facts.get("imports", []):
            if level:
                base = package
                for _ in range(level - 1):
                    base = base.parent
                located = _module_files(name, [base]) if name else [base / "__init__.py"]
            else:
                located = _module_files(name, _search_roots(path, rootdir, extra))
            if located is not None:
                queue.extend(each for each in located if each not in seen)
            elif star:
                findings.append(Finding(CANNOT, _show(path, rootdir), line, f"star import from {name} binds names the analysis cannot see"))
            elif level or not _is_library(name, rootdir):
                findings.append(Finding(CANNOT, _show(path, rootdir), line, f"import {name} cannot be placed"))
    findings.extend(_site_findings(seen, rootdir))
    if findings:
        first = min(findings, key=lambda each: (each.kind == CANNOT, each.path, each.line))
        return Finding(first.kind, first.path, first.line, first.detail, len(seen))
    return Finding(None, files=len(seen))


def _site_findings(seen: Dict[Path, dict], rootdir: Path) -> List[Finding]:
    fixtures: Dict[str, List[Optional[str]]] = {}
    for facts in seen.values():
        for name, roots in facts.get("fixtures", {}).items():
            fixtures.setdefault(name, []).extend(roots)
    findings = []
    for path, facts in seen.items():
        assigns = facts.get("assigns", {})
        for kind, line, detail, root in facts.get("sites", []):
            if not _is_tmp(root, assigns, fixtures, frozenset()):
                findings.append(Finding(kind, _show(path, rootdir), line, detail))
    return findings


def _is_tmp(root: Optional[str], assigns: dict, fixtures: dict, visiting: frozenset) -> Optional[bool]:
    """True when every non-circular binding of *root* is a temp or scratch location; None for a circular one."""
    if root is None:
        return False
    if root == _TMP:
        return True
    if root in visiting:
        return None
    bindings = list(assigns.get(root, [])) + list(fixtures.get(root, []))
    if root in _TMP_FIXTURES and root not in fixtures:
        bindings.append(_TMP)
    decided = [each for each in (_is_tmp(b, assigns, fixtures, visiting | {root}) for b in bindings) if each is not None]
    return bool(decided) and all(decided)


def _show(path: Path, rootdir: Path) -> str:
    try:
        return str(path.relative_to(rootdir))
    except ValueError:
        return str(path)
