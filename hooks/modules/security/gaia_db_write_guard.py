"""
gaia_db_write_guard.py -- PreToolUse Bash hook keeping the shell out of gaia.db.

The sqlite3 CLI never opens the store: a database argument, or any word of what
the line feeds sqlite3 (its operands, a pipe's producers, a heredoc, a
here-string, a redirected file), that is gaia.db by name, realpath or inode is
refused, and a database target (the argument, `.open`, ATTACH, VACUUM INTO) the
guard cannot resolve to a literal path is refused as "could not prove". Reads go
through `gaia context query`. A shell command or file a sqlite3 call names in a
readable payload meets the floors a plain Bash line meets and is refused when
they refuse it. Every other sqlite3 call signs (``sqlite_unproven_effect``): no
form is provably read-only, since `-safe` still reads files through fsdir().
Shell writers whose destination is the store file are refused. The refusals are
categorical, with no approval_id.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Iterator, List, Optional, Tuple
from urllib.parse import unquote

from .canonical_path import canonical_path, command_name
from .data_heredoc import data_heredoc_header
from .mutative_verbs import (
    _executor_floor_payloads,
    _never_free_paths,
    _peel_leading_command_wrappers,
    _peel_wrapper_readings,
    detect_mutative_command,
)
from .shell_substitution import extract_substitutions
from .shell_write_guard import _split_components, _tokenize, _writer_targets, downloader_paths

STORE_REJECTION_MESSAGE = (
    "The sqlite3 CLI is refused on gaia.db, whatever the SQL. Read the store with "
    "`gaia context query \"SELECT ...\"`, which opens it read-only; change it "
    "through the `gaia` CLI or update_contracts."
)

UNPROVEN_DATABASE_MESSAGE = (
    "sqlite3 names a database this guard could not resolve to a literal path (a "
    "glob, variable, substitution, or a relative path from an unknown directory), "
    "so it could not prove it is not gaia.db. Name the database by a literal path; "
    "read gaia.db with `gaia context query`."
)

SIGN_GUIDANCE = (
    "Every sqlite3 call asks for a signature: even `sqlite3 -readonly -safe` reads "
    "any file through fsdir(), so no form of it is provably read-only. Read gaia.db "
    "with `gaia context query`; for another database, request the signature for "
    "this exact command."
)

REACH_REJECTION_PREFIX = "sqlite3 reaches a command or file the shell itself is refused: "

FILE_WRITE_REJECTION_MESSAGE = (
    "Writing the gaia.db file from the shell (copy, move, redirect, truncate, "
    "or an interpreter) is not allowed: it replaces or corrupts the store "
    "without agent_permissions enforcement. Use the `gaia` CLI."
)

_GAIA_DB_FILE_NAMES = frozenset({
    "gaia.db", "gaia.db-wal", "gaia.db-shm", "gaia.db-journal",
})
_GAIA_DB_NAME_RE = re.compile(r"gaia\.db\b", re.IGNORECASE)
# The last operand is the destination.
_COPY_WRITERS = frozenset({
    "cp", "mv", "install", "ln", "rsync",
    "copy", "xcopy", "copy-item", "cpi", "move", "move-item", "mi",
})
# Every operand may be the file written.
_TRUNCATORS = frozenset({
    "truncate", "set-content", "sc", "add-content", "ac", "out-file", "clear-content",
    "clc", "new-item", "ni",
})
_INTERPRETERS = frozenset({"python", "node", "perl", "ruby", "php"})

_SQLITE_PROGRAMS = frozenset({"sqlite3", "sqlite"})
_VALUE_OPTIONS = frozenset({
    "init", "cmd", "separator", "newline", "nullvalue", "vfs", "mmap", "maxsize",
    "heap", "nonce", "escape",
})
_PAIR_OPTIONS = frozenset({"lookaside", "pagecache"})
_REDIRECT_WORD = re.compile(r"^\d*(?:<<<|<<-?|<>|<&|>>|>&|>\||&>>|&>|<|>)")
_NOT_LITERAL = re.compile(r"[$`*?\[\]{}]|^~(?!/|$)")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_CD = re.compile(r"(?:^|[;&|(\s])(?:cd|pushd|popd)\b")
_DOT_COMMAND = re.compile(
    r"(?:(?P<quote>[\"'])|(?<!\S))[ \t]*\.(?P<name>[a-z]+)\b"
    r"(?P<rest>(?(quote)(?:(?!(?P=quote))[^\n])*|[^\n|;&]*))",
    re.IGNORECASE,
)
_SQL_GAP = r"(?:\s|/\*.*?\*/|--[^\n]*\n)*"
_ATTACH = re.compile(rf"\bATTACH\b(?:{_SQL_GAP}DATABASE\b)?{_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP}AS\b))?", re.I | re.S)
_VACUUM_INTO = re.compile(rf"\bVACUUM\b[^;]*?\bINTO\b{_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP}(?:;|$|[\"'])))?", re.I | re.S)
_SQL_STRING = re.compile(r"'((?:[^']|'')*)'")
_SQL_CALL = re.compile(
    rf"[\"\[`]?\b(?P<fn>edit|writefile)\b(?:\\?[\"\]`])?{_SQL_GAP}\({_SQL_GAP}(?P<first>'(?:[^']|'')*')"
    rf"(?:{_SQL_GAP},{_SQL_GAP}(?P<second>'(?:[^']|'')*'))?",
    re.I | re.S,
)
_WORD = re.compile(r"[^\s'\"`(),;|<>=]+")
_LEADING_OPTIONS = re.compile(r"^(?:-\S*\s+)*")
_OUTPUT_SPECIALS = frozenset({"off", "stdout", "stderr"})
_READ_LIMIT = 1 << 20
_MAX_DEPTH = 4


def _store_files() -> Tuple[str, ...]:
    try:
        return _never_free_paths()[0]
    except Exception:
        home = os.path.realpath(os.path.expanduser("~/.gaia/gaia.db"))
        return tuple(home + suffix for suffix in ("", "-wal", "-shm", "-journal"))


def _is_gaia_db_file(token: str) -> bool:
    canonical = canonical_path(token.strip("'\""), _GAIA_DB_FILE_NAMES)
    return canonical.rsplit("/", 1)[-1] in _GAIA_DB_FILE_NAMES


def _uri_path(word: str) -> str:
    if not word.lower().startswith("file:"):
        return word
    path = unquote(re.split(r"[?#]", word[5:], maxsplit=1)[0])
    return "/" + path[2:].partition("/")[2] if path.startswith("//") else path


def _resolves_to_store(path: str, cwd: Optional[str]) -> bool:
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        if cwd is None:
            return False
        path = os.path.join(cwd, path)
    real = os.path.realpath(path)
    stores = _store_files()
    if _is_gaia_db_file(real) or real in stores:
        return True
    try:
        identity = os.stat(real)
    except OSError:
        return False
    for store in stores:
        try:
            if os.path.samestat(identity, os.stat(store)):
                return True
        except OSError:
            continue
    return False


def _database_verdict(word: str, cwd: Optional[str]) -> Optional[str]:
    """Refuse a database target that is gaia.db, or that cannot be resolved to a literal file."""
    path = _uri_path(word)
    if _is_gaia_db_file(path):
        return STORE_REJECTION_MESSAGE
    if _NOT_LITERAL.search(path) or (cwd is None and path not in ("", ":memory:")
                                     and not os.path.isabs(os.path.expanduser(path))):
        return UNPROVEN_DATABASE_MESSAGE
    if path not in ("", ":memory:") and _resolves_to_store(path, cwd):
        return STORE_REJECTION_MESSAGE
    return None


def _names_store(word: str, cwd: Optional[str]) -> bool:
    path = _uri_path(word)
    if _is_gaia_db_file(path):
        return True
    return ("/" in path or path.startswith("~")) and _resolves_to_store(path, cwd)


class _Invocation:
    """One sqlite3 run the line makes, by its argument words."""

    def __init__(self, args: List[str]) -> None:
        self.args = args

    def database(self) -> Optional[str]:
        index = 0
        while index < len(self.args):
            word = self.args[index]
            redirect = _REDIRECT_WORD.match(word)
            if redirect:
                index += 1 if word[redirect.end():] else 2
            elif word.startswith("-") and len(word) > 1:
                name = word.lstrip("-").lower()
                index += 2 if name in _VALUE_OPTIONS else 3 if name in _PAIR_OPTIONS else 1
            else:
                return word
        return None

    def redirected_files(self) -> Iterator[str]:
        for index, word in enumerate(self.args):
            redirect = _REDIRECT_WORD.match(word)
            if redirect and redirect.group(0).lstrip("0123456789") in ("<", "<>"):
                target = word[redirect.end():] or (self.args[index + 1] if index + 1 < len(self.args) else "")
                if target:
                    yield target


def _readings(text: str, depth: int = 0) -> Iterator[str]:
    from .shell_unwrapper import shell_command_string

    if depth > _MAX_DEPTH:
        return
    for body in [text, *extract_substitutions(text)]:
        for component in _split_components(body):
            component = component.strip()
            if not component:
                continue
            yield component
            readings, _ = _peel_wrapper_readings(component)
            for reading in readings:
                if reading and reading != component:
                    yield reading
                for payload in _executor_floor_payloads(reading):
                    yield from _readings(payload, depth + 1)
                inner = shell_command_string(reading)
                if inner:
                    yield from _readings(inner, depth + 1)


def _invocations(command: str) -> List[_Invocation]:
    from ..tools.shell_parser import shell_split

    if "sqlite" not in re.sub(r"[\\'\"]", "", command).lower():
        return []
    found: List[_Invocation] = []
    for reading in _readings(command):
        try:
            words = shell_split(reading)
        except ValueError:
            words = _tokenize(reading)
        head = 0
        while head < len(words) and _ASSIGNMENT.match(words[head]):
            head += 1
        if head < len(words) and command_name(words[head]) in _SQLITE_PROGRAMS:
            found.append(_Invocation(words[head + 1:]))
    return found


def _read_fed_file(path: str, cwd: Optional[str]) -> str:
    path = os.path.expanduser(path)
    if _NOT_LITERAL.search(path) or (not os.path.isabs(path) and cwd is None):
        return ""
    try:
        with open(os.path.join(cwd or "", path), "r", errors="replace") as handle:
            return handle.read(_READ_LIMIT)
    except OSError:
        return ""


def _target_verdicts(text: str, cwd: Optional[str]) -> Iterator[Optional[str]]:
    for match in _DOT_COMMAND.finditer(text):
        typed = match["name"].lower()
        operands = [w.strip("'\"") for w in match["rest"].split() if not w.startswith("-")]
        if len(typed) >= 2 and "open".startswith(typed) and operands:
            yield _database_verdict(operands[-1], cwd)
    for pattern in (_ATTACH, _VACUUM_INTO):
        for match in pattern.finditer(text):
            if match["arg"] is None:
                yield UNPROVEN_DATABASE_MESSAGE
            else:
                yield _database_verdict(match["arg"][1:-1].replace("''", "'"), cwd)


def _command_refusal(command: str, cwd: Optional[str]) -> Optional[str]:
    """Refuse a shell command sqlite3 would run when a plain Bash line running it is refused."""
    from .blocked_commands import is_blocked_command
    from . import protected_path_guard, sensitive_read_guard, shell_write_guard

    for component in _split_components(command):
        component = component.strip()
        if not component:
            continue
        if is_blocked_command(component).is_blocked:
            return REACH_REJECTION_PREFIX + "a permanently blocked command"
        for guard in (check, protected_path_guard.check, shell_write_guard.check, sensitive_read_guard.check):
            allowed, reason = guard(component, cwd)
            if not allowed:
                return REACH_REJECTION_PREFIX + (reason or "")
    return None


def _file_refusal(path: str, cwd: Optional[str]) -> Optional[str]:
    """Refuse a file sqlite3 would write when a shell redirect onto it is refused."""
    from . import protected_path_guard, shell_write_guard

    if _is_gaia_db_file(path):
        return STORE_REJECTION_MESSAGE
    if not path or _NOT_LITERAL.search(path) or path.lower() in _OUTPUT_SPECIALS:
        return None
    for guard in (protected_path_guard.check, shell_write_guard.check):
        allowed, reason = guard(f"echo > {shlex.quote(path)}", cwd)
        if not allowed:
            return REACH_REJECTION_PREFIX + (reason or "")
    return None


def _payload_refusals(text: str, cwd: Optional[str]) -> Iterator[Optional[str]]:
    """Judge each readable shell command and written file in *text* as the shell would."""
    for match in _DOT_COMMAND.finditer(text):
        typed, rest = match["name"].lower(), match["rest"].strip().strip("'\"")
        if "shell".startswith(typed) or "system".startswith(typed):
            yield _command_refusal(rest, cwd)
        if any(name.startswith(typed) for name in ("once", "output", "import")):
            target = _LEADING_OPTIONS.sub("", rest).strip("'\"")
            if target.startswith("|"):
                yield _command_refusal(target[1:].strip().strip("'\""), cwd)
            elif not "import".startswith(typed) and target:
                yield _file_refusal(target.split()[0], cwd)
    for match in _SQL_CALL.finditer(text):
        if match["fn"].lower() == "writefile":
            yield _file_refusal(match["first"][1:-1].replace("''", "'"), cwd)
        elif match["second"]:
            yield _command_refusal(match["second"][1:-1].replace("''", "'"), cwd)


def _sqlite_refusal(command: str, cwd: Optional[str]) -> Optional[str]:
    """Why the line's sqlite3 runs reach gaia.db, an unresolvable target, or something the shell is refused, or None."""
    from ..tools.shell_parser import shell_split

    invocations = _invocations(command)
    if not invocations:
        return None
    cwd = None if _SHELL_CD.search(command) else cwd
    for invocation in invocations:
        database = invocation.database()
        verdict = database is not None and _database_verdict(database, cwd)
        if verdict:
            return verdict
    fed = [_read_fed_file(path, cwd) for inv in invocations for path in inv.redirected_files()]
    texts = [command, *fed]
    words = [w for text in texts for w in _WORD.findall(text)]
    words += [s.replace("''", "'") for text in texts for s in _SQL_STRING.findall(text)]
    for component in _split_components(command):
        try:
            words += shell_split(component)
        except ValueError:
            continue
    if any(_names_store(word, cwd) for word in words):
        return STORE_REJECTION_MESSAGE
    return next((v for text in texts for v in _target_verdicts(text, cwd) if v), None) or next(
        (v for text in texts for v in _payload_refusals(text, cwd) if v), None
    )


def _component_writes_db(component: str) -> bool:
    """Report whether one command component writes the Gaia DB file by any shell writer.

    Keyed on the DESTINATION: a component that only reads the file (``ls``,
    ``cp gaia.db /tmp/x``) or only names it in quoted text is not a write.
    """
    peeled, _ = _peel_leading_command_wrappers(component)
    if any(_is_gaia_db_file(t) for t in _writer_targets(peeled)):
        return True

    tokens = _tokenize(peeled)
    if not tokens:
        return False
    if any(_is_gaia_db_file(t) for t in downloader_paths(tokens)):
        return True
    base = command_name(tokens[0])
    operands =[t for t in tokens[1:] if not t.startswith("-")]
    if base in _COPY_WRITERS:
        return bool(operands) and _is_gaia_db_file(operands[-1])
    if base in _TRUNCATORS:
        return any(_is_gaia_db_file(t) for t in operands)
    if base.rstrip("0123456789.") in _INTERPRETERS and _GAIA_DB_NAME_RE.search(peeled):
        return detect_mutative_command(peeled).is_mutative
    return False


def writes_db_file(command: str) -> bool:
    """Return True iff some component of *command*, with the variables the line assigns read in, writes the Gaia DB file through the shell."""
    from .protected_path_guard import _substituted_lines

    return any(
        _component_writes_db(component.strip())
        for line in _substituted_lines(command or "")
        for component in _split_components(line)
        if component.strip()
    )


def _sql_scope(command: str) -> str:
    """Return the part of *command* the shell would run, for the SQL-shell check.

    A lone Gaia CLI call stores its argument values and runs none of them, so
    only its substitutions are read -- and of those, a quoted heredoc echoed by
    a bare ``cat`` contributes nothing but its header. Anything else is read whole.
    """
    components = [c for c in _split_components(command) if c.strip()]
    if len(components) != 1:
        return command
    peeled, _ = _peel_leading_command_wrappers(components[0].strip())
    tokens = _tokenize(peeled)
    if not tokens or os.path.basename(tokens[0]) != "gaia":
        return command
    return "\n".join(
        data_heredoc_header(body) or body for body in extract_substitutions(command)
    )


def sqlite_unproven_effect(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """``SIGN_GUIDANCE`` when the line runs sqlite3 at all, else None."""
    return SIGN_GUIDANCE if _invocations(_sql_scope(command or "")) else None


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Return ``(False, reason)`` when *command*'s sqlite3 runs reach gaia.db, a database it cannot resolve, or a command or file the shell is refused, or when it writes the store file; else ``(True, None)``."""
    if not command:
        return True, None
    reason = _sqlite_refusal(_sql_scope(command), cwd)
    if reason:
        return False, reason
    if writes_db_file(command):
        return False, FILE_WRITE_REJECTION_MESSAGE
    return True, None
