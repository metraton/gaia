"""
gaia_db_write_guard.py -- PreToolUse Bash hook keeping the shell out of gaia.db.

The sqlite3 CLI never opens the store, whatever its SQL: a database argument,
`.open`, ATTACH, VACUUM INTO or file operand that resolves to gaia.db (by name,
realpath or inode) is refused, and one the guard cannot resolve to a literal path
is refused as "could not prove". Reads go through `gaia context query`. On any
other database, what sqlite3 reaches beyond it -- a shell command, a written file,
a script or extension loaded -- is judged by the command or path reached: refused
when the shell spelling would be, signed (``sqlite_unproven_effect``) when it
cannot be proven. Shell writers whose destination is the store file are refused.
The refusals are categorical, with no approval_id.
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
    CATEGORY_UNKNOWN,
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
_RC_OVERRIDE = re.compile(
    r"(?:^|[\s;&|(])(?:HOME|XDG_CONFIG_HOME)="
    r"|\benv\s+(?:\S+\s+)*?-(?:-unset|-ignore-environment|[a-zA-Z]*[iu]\b)"
)
_SHELL_CD = re.compile(r"(?:^|[;&|(\s])(?:cd|pushd|popd)\b")
_COMPUTED_WORDS = re.compile(r"[$`]|[<>]\(")
_PIPE_INTO_SQLITE = re.compile(
    r"(?P<producer>(?:\"[^\"]*\"|'[^']*'|[^|;&\n\"'])*)(?<!\|)\|(?!\|)\s*sqlite3?\b", re.IGNORECASE
)
_LITERAL_PRODUCERS = frozenset({"echo", "printf"})
_DOT_COMMAND = re.compile(
    r"(?:^[ \t]*|(?P<quote>[\"'])[ \t]*)\.(?P<name>[a-z]+)\b"
    r"(?P<rest>(?(quote)(?:(?!(?P=quote))[^\n])*|[^\n]*))",
    re.IGNORECASE | re.MULTILINE,
)
_SQL_STRING = re.compile(r"'((?:[^']|'')*)'")
_SQL_GAP = r"(?:\s|/\*.*?\*/|--[^\n]*\n)*"
_ATTACH = re.compile(rf"\bATTACH\b(?:{_SQL_GAP}DATABASE\b)?{_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP}AS\b))?", re.I | re.S)
_VACUUM_INTO = re.compile(rf"\bVACUUM\b[^;]*?\bINTO\b{_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP}(?:;|$)))?", re.I | re.S)
_SQL_REACH = re.compile(rf"\b(?P<fn>writefile|edit|load_extension){_SQL_GAP}\({_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP},))?", re.I | re.S)
_DOT_SIGNED = {
    "read": "sqlite3 reads a script this guard does not open",
    "load": "sqlite3 loads a native extension",
    "cd": "sqlite3 changes directory, so later relative paths are unknown",
    "archive": "sqlite3 extracts or writes archive members",
    "excel": "sqlite3 opens its output in another program",
}
_DOT_FILE_WRITERS = ("log", "trace", "backup", "save", "clone")
_OUTPUT_SPECIALS = frozenset({"off", "stdout", "stderr"})
_MAX_DEPTH = 4

_REFUSE, _SIGN = "refuse", "sign"
_Verdict = Tuple[str, str]


def _store_files() -> Tuple[str, ...]:
    try:
        return _never_free_paths()[0]
    except Exception:
        home = os.path.realpath(os.path.expanduser("~/.gaia/gaia.db"))
        return tuple(home + suffix for suffix in ("", "-wal", "-shm", "-journal"))


def _is_gaia_db_file(token: str) -> bool:
    canonical = canonical_path(token.strip("'\""), _GAIA_DB_FILE_NAMES)
    return canonical.rsplit("/", 1)[-1] in _GAIA_DB_FILE_NAMES


def _database_verdict(word: str, cwd: Optional[str]) -> Optional[_Verdict]:
    """Refuse a database path that is gaia.db, or that cannot be resolved to a literal file."""
    path = word
    if path.lower().startswith("file:"):
        path = unquote(re.split(r"[?#]", path[5:], maxsplit=1)[0])
        if path.startswith("//"):
            path = "/" + path[2:].partition("/")[2]
    if _is_gaia_db_file(path):
        return _REFUSE, STORE_REJECTION_MESSAGE
    if _NOT_LITERAL.search(path):
        return _REFUSE, UNPROVEN_DATABASE_MESSAGE
    if path in ("", ":memory:"):
        return None
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        if cwd is None:
            return _REFUSE, UNPROVEN_DATABASE_MESSAGE
        path = os.path.join(cwd, path)
    real = os.path.realpath(path)
    stores = _store_files()
    if _is_gaia_db_file(real) or real in stores:
        return _REFUSE, STORE_REJECTION_MESSAGE
    try:
        identity = os.stat(real)
    except OSError:
        return None
    for store in stores:
        try:
            if os.path.samestat(identity, os.stat(store)):
                return _REFUSE, STORE_REJECTION_MESSAGE
        except OSError:
            continue
    return None


def _command_verdict(command: str, cwd: Optional[str]) -> Optional[_Verdict]:
    """Judge a command sqlite3 would run as the shell judges it run directly."""
    from .blocked_commands import is_blocked_command
    from . import protected_path_guard, sensitive_read_guard, shell_write_guard

    if not command or command.endswith("\\") or _COMPUTED_WORDS.search(command):
        return _SIGN, "sqlite3 runs a shell command this guard cannot read"
    for component in _split_components(command):
        component = component.strip()
        if not component:
            continue
        if is_blocked_command(component).is_blocked:
            return _REFUSE, REACH_REJECTION_PREFIX + "a permanently blocked command"
        for guard in (check, protected_path_guard.check, shell_write_guard.check, sensitive_read_guard.check):
            allowed, reason = guard(component, cwd)
            if not allowed:
                return _REFUSE, REACH_REJECTION_PREFIX + (reason or "")
        verdict = detect_mutative_command(component, cwd=cwd)
        if verdict.is_mutative or verdict.category == CATEGORY_UNKNOWN:
            return _SIGN, "sqlite3 runs a shell command that is not provably a read"
    return None


def _write_verdict(path: str, cwd: Optional[str]) -> Optional[_Verdict]:
    """Judge a file sqlite3 would write as a shell redirect onto the same path."""
    from . import protected_path_guard, shell_write_guard

    if _is_gaia_db_file(path):
        return _REFUSE, STORE_REJECTION_MESSAGE
    if _NOT_LITERAL.search(path) or (cwd is None and not os.path.isabs(os.path.expanduser(path))):
        return _SIGN, "sqlite3 writes a file this guard cannot place"
    redirect = f"echo > {shlex.quote(path)}"
    for guard in (protected_path_guard.check, shell_write_guard.check):
        allowed, reason = guard(redirect, cwd)
        if not allowed:
            return _REFUSE, REACH_REJECTION_PREFIX + (reason or "")
    return None


def _pipe_verdict(command: str, cwd: Optional[str]) -> Optional[_Verdict]:
    """Judge the command `.once |cmd` / `.output |cmd` feeds the query output to."""
    from .mutative_verbs import _argv_runs_unnamed_program

    tokens = _tokenize(command)
    if tokens and _argv_runs_unnamed_program(tuple(tokens)):
        return _SIGN, "sqlite3 pipes its output into a program that runs it"
    return _command_verdict(command, cwd)


def _dot_verdicts(text: str, cwd: Optional[str]) -> Iterator[Optional[_Verdict]]:
    for match in _DOT_COMMAND.finditer(text):
        typed, rest = match["name"].lower(), match["rest"].strip()
        words = [w.strip("'\"") for w in rest.split()]
        operands = [w for w in words if w and not w.startswith("-")]
        if any(_is_gaia_db_file(w) for w in operands):
            yield _REFUSE, STORE_REJECTION_MESSAGE
        if "open".startswith(typed) and operands:
            yield _database_verdict(operands[-1], cwd)
        if "shell".startswith(typed) or "system".startswith(typed):
            yield _command_verdict(rest, cwd)
        if "once".startswith(typed) or "output".startswith(typed):
            if "-e" in words or "-x" in words:
                yield _SIGN, "sqlite3 opens its output in another program"
            elif rest.lstrip("'\"").startswith("|"):
                yield _pipe_verdict(rest.strip("'\"")[1:].strip(), cwd)
            elif operands and operands[0].lower() not in _OUTPUT_SPECIALS:
                yield _write_verdict(operands[0], cwd)
        if any(name.startswith(typed) for name in _DOT_FILE_WRITERS) and operands:
            if operands[-1].lower() not in _OUTPUT_SPECIALS:
                yield _write_verdict(operands[-1], cwd)
        for name, why in _DOT_SIGNED.items():
            if name.startswith(typed):
                yield _SIGN, why


def _sql_verdicts(text: str, cwd: Optional[str]) -> Iterator[Optional[_Verdict]]:
    if any(_is_gaia_db_file(literal) for literal in _SQL_STRING.findall(text)):
        yield _REFUSE, STORE_REJECTION_MESSAGE
    for pattern in (_ATTACH, _VACUUM_INTO):
        for match in pattern.finditer(text):
            if match["arg"] is None:
                yield _REFUSE, UNPROVEN_DATABASE_MESSAGE
            else:
                yield _database_verdict(match["arg"][1:-1].replace("''", "'"), cwd)
    for match in _SQL_REACH.finditer(text):
        if match["fn"].lower() != "writefile":
            yield _SIGN, f"sqlite3 calls {match['fn'].lower()}(), which runs code outside the database"
        elif match["arg"] is None:
            yield _SIGN, "sqlite3 writes a file through writefile() whose path is computed"
        else:
            yield _write_verdict(match["arg"][1:-1].replace("''", "'"), cwd)


def _invocation_verdicts(args: List[str], line: str, cwd: Optional[str]) -> Iterator[Optional[_Verdict]]:
    database: Optional[str] = None
    statements: List[str] = []
    visible_stdin = False
    index = 0
    while index < len(args):
        word = args[index]
        redirect = _REDIRECT_WORD.match(word)
        if redirect:
            operator = redirect.group(0).lstrip("0123456789")
            index += 1 if word[redirect.end():] else 2
            if operator.startswith("<<"):
                visible_stdin = True
            elif operator in ("<", "<>"):
                yield _SIGN, "sqlite3 reads its SQL from a file this guard does not open"
            continue
        if word.startswith("-") and len(word) > 1:
            name = word.lstrip("-")
            if name.startswith("A") or name.lower() == "archive":
                yield _SIGN, "sqlite3 runs in archive mode, which writes files"
                return
            name = name.lower()
            if name in _VALUE_OPTIONS:
                if name == "cmd" and index + 1 < len(args):
                    statements.append(args[index + 1])
                if name == "init":
                    yield _SIGN, "sqlite3 runs an -init script this guard does not open"
                index += 2
            else:
                index += 3 if name in _PAIR_OPTIONS else 1
            continue
        if database is None:
            database = word
        else:
            statements.append(word)
        index += 1

    if database is not None:
        verdict = _database_verdict(database, cwd)
        if verdict:
            yield verdict
            return
    if _RC_OVERRIDE.search(line):
        yield _SIGN, "sqlite3 reads a start-up file from an overridden home directory"
    for producer in _PIPE_INTO_SQLITE.finditer(line):
        tokens = _tokenize(producer["producer"].strip())
        literal = (bool(tokens) and command_name(tokens[0]) in _LITERAL_PRODUCERS
                   and not _COMPUTED_WORDS.search(producer["producer"]) and "<" not in producer["producer"])
        if literal:
            visible_stdin = True
        else:
            yield _SIGN, "sqlite3 reads its SQL from a command this guard does not run"
    texts = statements + ([line] if visible_stdin else [])
    for text in texts:
        yield from _dot_verdicts(text, cwd)
        yield from _sql_verdicts(text, cwd)


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


def _sqlite_verdicts(command: str, cwd: Optional[str]) -> Iterator[_Verdict]:
    from ..tools.shell_parser import shell_split

    if "sqlite" not in re.sub(r"[\\'\"]", "", command).lower():
        return
    cwd = None if _SHELL_CD.search(command) else cwd
    for reading in _readings(command):
        try:
            words = shell_split(reading)
        except ValueError:
            words = _tokenize(reading)
        head = 0
        while head < len(words) and _ASSIGNMENT.match(words[head]):
            head += 1
        if head < len(words) and command_name(words[head]) in _SQLITE_PROGRAMS:
            yield from filter(None, _invocation_verdicts(words[head + 1:], command, cwd))


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
    """Why a sqlite3 call on another database reaches beyond it in a way this guard cannot prove harmless, or None."""
    for kind, why in _sqlite_verdicts(_sql_scope(command or ""), cwd):
        if kind == _SIGN:
            return why
    return None


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Return ``(False, reason)`` when *command* opens gaia.db through sqlite3, cannot be proven not to, reaches a refused command or file through it, or writes the store file; else ``(True, None)``."""
    if not command:
        return True, None
    for kind, reason in _sqlite_verdicts(_sql_scope(command), cwd):
        if kind == _REFUSE:
            return False, reason
    if writes_db_file(command):
        return False, FILE_WRITE_REJECTION_MESSAGE
    return True, None
