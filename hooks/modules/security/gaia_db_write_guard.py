"""
gaia_db_write_guard.py -- PreToolUse Bash hook keeping the shell out of gaia.db.

The sqlite3 CLI never opens the store: a database argument, or any word of what
the line feeds sqlite3, that is gaia.db by name, realpath or inode is refused,
and a database target (the argument, `.open`, ATTACH, VACUUM INTO) the guard
cannot resolve to a literal path is refused as "could not prove". What it feeds
is its operands, a pipe's producers, a heredoc, a here-string, and every file it
reads as SQL whose path and content are literal: a `<` or `-init` file, a
`.read` target, the sqliterc under a HOME or XDG_CONFIG_HOME the line assigns,
and the files a `cat` on the line prints, followed through `.read` recursively.
A literal sqlite3 `.cd` target is one more folder every relative path is judged
in; an unresolvable one, a run not the line's first and only sqlite3, computed
text, a program's output or a missing input file leaves its input unproven.
Reads go through `gaia context query`. A shell command or file a sqlite3 call
names in a readable payload -- `.shell`, `.system`, any dot-command operand
starting with `|`, edit()'s editor, a VISUAL or EDITOR the line assigns --
meets the floors a plain Bash line meets and is refused when they refuse it.
Any other call must stay in a read subset (OUTSIDE_SUBSET_MESSAGE lists it) and
then signs (``sqlite_unproven_effect``): `-safe` still reads files via fsdir().
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
from .sqlite_subset import DOT_COMMANDS, argument_outside_subset, script_outside_subset

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

UNPROVEN_FOLDER_MESSAGE = (
    "sqlite3 moves its folder with `.cd` to a target this guard could not resolve to "
    "a literal path (a variable, glob, substitution, or a relative path from an "
    "unknown directory), so it could not prove where a later relative file operand "
    "lands. Name the folder or the file by a literal path; read gaia.db with "
    "`gaia context query`."
)

UNPROVEN_INPUT_MESSAGE = (
    "sqlite3 reads SQL this guard could not read in full (a program's output, a "
    "descriptor, a process substitution, or a file that is missing, unreadable or not "
    "named literally), so it could not prove the SQL leaves gaia.db alone. Feed it a "
    "literal file, `cat` of literal files, or literal SQL; read gaia.db with "
    "`gaia context query`."
)

UNPROVEN_START_MESSAGE = (
    "sqlite3 is not the first and only command on this line, or no working folder "
    "was given, so this guard could not prove where a relative file operand lands. "
    "Name the file by an absolute path; read gaia.db with `gaia context query`."
)

OUTSIDE_SUBSET_MESSAGE = (
    "sqlite3 runs only in a provable read subset: an absolute database other than "
    "gaia.db, SQL given literally or as a literal file read in full, no environment "
    "assignment, the options {options} and the dot-commands {dots}. This call uses "
    "{part}, so this guard could not prove it leaves gaia.db alone. Read gaia.db "
    "with `gaia context query`."
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
_MOVERS = frozenset({"mv", "move", "move-item", "mi"})
# Every operand may be the file written.
_TRUNCATORS = frozenset({
    "truncate", "set-content", "sc", "add-content", "ac", "out-file", "clear-content",
    "clc", "new-item", "ni",
})

_SQLITE_PROGRAMS = frozenset({"sqlite3", "sqlite"})
_LEAD_WORDS = frozenset({"{", "(", "!", "if", "then", "else", "elif", "do", "while", "until", "time"})
_SUBSET_OPTIONS = frozenset({
    "ascii", "bail", "batch", "box", "column", "csv", "echo", "header", "noheader", "help",
    "html", "json", "line", "list", "markdown", "nofollow", "quote", "readonly", "safe",
    "table", "tabs", "version",
})
_SUBSET_VALUE_OPTIONS = frozenset({"separator", "newline", "nullvalue"})
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z_]\w*)\2[^\n]*\n")
_VALUE_OPTIONS = frozenset({
    "init", "cmd", "separator", "newline", "nullvalue", "vfs", "mmap", "maxsize",
    "heap", "nonce", "escape",
})
_PAIR_OPTIONS = frozenset({"lookaside", "pagecache"})
_REDIRECT_WORD = re.compile(r"^(?:\d*|\{\w+\})(?P<op><<<|<<-?|<>|<&|>>|>&|>\||&>>|&>|<|>)")
_NOT_LITERAL = re.compile(r"[$`*?\[\]{}]|^~(?!/|$)")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_DOT_COMMAND = re.compile(
    r"(?:(?P<quote>[\"'])|(?<!\S))[ \t]*\.(?P<name>[a-z]+)\b"
    r"(?P<rest>(?(quote)(?:(?!(?P=quote))[^\n])*|[^\n|;&]*))",
    re.IGNORECASE,
)
_SCRIPT_DOT_COMMAND = re.compile(r"^[ \t]*\.(?P<name>[a-z]+)\b(?P<rest>[^\n]*)", re.IGNORECASE | re.MULTILINE)
_LINE_ASSIGNMENT = re.compile(r"^(?P<name>VISUAL|EDITOR|HOME|XDG_CONFIG_HOME)=(?P<value>.*)$", re.DOTALL)
_SQLITERC = {"HOME": (".sqliterc",), "XDG_CONFIG_HOME": ("sqlite3", "sqliterc")}
_SQL_GAP = r"(?:\s|/\*.*?\*/|--[^\n]*\n)*"
_ATTACH = re.compile(rf"\bATTACH\b(?:{_SQL_GAP}DATABASE\b)?{_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP}AS\b))?", re.I | re.S)
_VACUUM_INTO = re.compile(rf"\bVACUUM\b[^;]*?\bINTO\b{_SQL_GAP}(?P<arg>'(?:[^']|'')*'(?={_SQL_GAP}(?:;|$|[\"'])))?", re.I | re.S)
_SQL_STRING = re.compile(r"'((?:[^']|'')*)'")
_PATH_FUNCTION = re.compile(
    rf"[\"\[`]?\b(?:load_extension|readfile|writefile)\b(?:\\?[\"\]`])?{_SQL_GAP}\({_SQL_GAP}'((?:[^']|'')*)'",
    re.I | re.S,
)
_UNQUOTED_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*[A-Za-z_]")
_EXPANSION = re.compile(r"\$[A-Za-z_{(0-9@*#?!$'\"-]|`")
_SQL_CALL = re.compile(
    rf"[\"\[`]?\b(?P<fn>edit|writefile)\b(?:\\?[\"\]`])?{_SQL_GAP}\({_SQL_GAP}(?P<first>'(?:[^']|'')*')"
    rf"(?:{_SQL_GAP},{_SQL_GAP}(?P<second>'(?:[^']|'')*'))?",
    re.I | re.S,
)
_WORD = re.compile(r"[^\s'\"`(),;|<>=]+")
_OUTPUT_SPECIALS = frozenset({"off", "stdout", "stderr"})
_FILE_OPERAND_INDEX = {
    "open": -1, "read": 0, "once": 0, "output": 0, "import": 0, "log": 0, "load": 0,
    "save": -1, "backup": -1, "restore": -1,
}
_WRITTEN_FILE_COMMANDS = frozenset({"once", "output", "log", "save", "backup"})
_DOT_VALUE_OPTIONS = frozenset({"skip", "schema", "maxsize"})
_UNREAD_FOLDER = re.compile(r"^~|[%\\]")
_MAX_FOLDERS = 16
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

    def read_files(self) -> Iterator[str]:
        """The files named on the line that sqlite3 reads as SQL: a ``<`` redirect and the ``-init`` file."""
        for index, word in enumerate(self.args):
            following = self.args[index + 1] if index + 1 < len(self.args) else ""
            redirect = _REDIRECT_WORD.match(word)
            if redirect and redirect["op"] in ("<", "<>"):
                target = word[redirect.end():] or following
                if target:
                    yield target
            elif word.lstrip("-").lower() == "init" and word.startswith("-") and following:
                yield following


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


def _sqlite_args(reading: str) -> Optional[List[str]]:
    """The argument words of *reading* when sqlite3 is its program, else None."""
    from ..tools.shell_parser import shell_split

    try:
        words = shell_split(reading)
    except ValueError:
        words = _tokenize(reading)
    head, redirects = 0, []
    while head < len(words):
        redirect = _REDIRECT_WORD.match(words[head])
        if redirect:
            width = 1 if words[head][redirect.end():] else 2
            redirects += words[head:head + width]
            head += width
        elif _ASSIGNMENT.match(words[head]) or words[head] in _LEAD_WORDS:
            head += 1
        else:
            break
    if head < len(words) and command_name(words[head].lstrip("(")) in _SQLITE_PROGRAMS:
        return redirects + words[head + 1:]
    return None


def _invocations(command: str) -> List[_Invocation]:
    if "sqlite" not in re.sub(r"[\\'\"]", "", command).lower():
        return []
    return [_Invocation(args) for args in map(_sqlite_args, _readings(command)) if args is not None]


def _starts_in_shell_folder(command: str) -> bool:
    """Whether the line holds exactly one sqlite3 run and it is the first command, so nothing before it can move its folder."""
    components = [c for c in map(str.strip, _split_components(command)) if c]
    if not components or _sqlite_args(components[0]) is None:
        return False
    runs = sum(
        any(_sqlite_args(reading) is not None for reading in _readings(component))
        for body in [command, *extract_substitutions(command)]
        for component in filter(None, map(str.strip, _split_components(body)))
    )
    return runs == 1


def _read_fed_file(path: str, cwd: Optional[str]) -> str:
    """The text of a literal regular file, or "" when its path or content cannot be proven."""
    from pathlib import Path

    from .targets import _read_file

    path = os.path.expanduser(path)
    if not path or _NOT_LITERAL.search(path) or (not os.path.isabs(path) and cwd is None):
        return ""
    return _read_file(Path(cwd or "", path))[1] or ""


def _read_in_full(path: Optional[str], cwd: Optional[str]) -> bool:
    from pathlib import Path

    from .targets import _read_file

    path = os.path.expanduser(path or "")
    if not path or _NOT_LITERAL.search(path) or path.startswith(("<(", "(")):
        return False
    if not os.path.isabs(path) and cwd is None:
        return False
    return _read_file(Path(cwd or "", path))[2] is not None


def _stdin_sources(reading: str) -> Iterator[Optional[str]]:
    """Each file the shell feeds to a stdin in *reading*, or None for a descriptor it duplicates."""
    from ..tools.shell_parser import shell_split

    try:
        words = shell_split(reading)
    except ValueError:
        words = _tokenize(reading)
    for index, word in enumerate(words):
        redirect = _REDIRECT_WORD.match(word)
        target = redirect and (word[redirect.end():] or (words[index + 1] if index + 1 < len(words) else ""))
        if redirect and redirect["op"] in ("<", "<>"):
            yield target
        elif redirect and redirect["op"] == "<&" and target != "-":
            yield None


def _pipes(text: str) -> Iterator[Tuple[str, str]]:
    segments, operators, current, quote, index = [], [], [], None, 0
    while index < len(text):
        char = text[index]
        if quote != "'" and char == "\\":
            current.append(text[index:index + 2])
            index += 2
            continue
        if quote is None and char in ("'", '"'):
            quote = char
        elif quote is not None and char == quote:
            quote = None
        elif quote is None and (char in ";\n|" or text[index:index + 2] == "&&"):
            operator = text[index:index + 2] if text[index:index + 2] in ("&&", "||", "|&") else char
            segments.append("".join(current))
            operators.append(operator)
            current = []
            index += len(operator)
            continue
        current.append(char)
        index += 1
    segments.append("".join(current))
    for position, operator in enumerate(operators):
        if operator in ("|", "|&"):
            yield segments[position], segments[position + 1]


def _expands(text: str) -> bool:
    quote, index = None, 0
    while index < len(text):
        char = text[index]
        if quote != "'" and char == "\\":
            index += 2
            continue
        if quote is None and char in ("'", '"'):
            quote = char
        elif quote is not None and char == quote:
            quote = None
        elif quote != "'" and _EXPANSION.match(text, index):
            return True
        index += 1
    return False


def _computes_sqlite_text(command: str) -> bool:
    from ..tools.shell_parser import shell_split

    for body in [command, *extract_substitutions(command)]:
        for component in filter(None, map(str.strip, _split_components(body))):
            own = component
            for substitution in extract_substitutions(component):
                own = own.replace(substitution, "")
            try:
                words = shell_split(own)
            except ValueError:
                words = _tokenize(own)
            if any(command_name(w.lstrip("(")) in _SQLITE_PROGRAMS for w in words) and (
                _expands(component) or (_UNQUOTED_HEREDOC.search(component) and _EXPANSION.search(command))
            ):
                return True
    return False


def _assigns_before_sqlite(reading: str) -> bool:
    from ..tools.shell_parser import shell_split

    try:
        words = shell_split(reading)
    except ValueError:
        return True
    for word in words:
        if command_name(word.lstrip("(")) in _SQLITE_PROGRAMS:
            return False
        if _ASSIGNMENT.match(word):
            return True
    return False


def _invocation_part(invocation: "_Invocation", arguments: List[str], scripts: List[str]) -> Optional[str]:
    database, index, args = None, 0, invocation.args
    while index < len(args):
        word = args[index]
        redirect = _REDIRECT_WORD.match(word)
        name = word.lstrip("-").lower()
        if redirect:
            target = word[redirect.end():] or (args[index + 1] if index + 1 < len(args) else "")
            scripts += [target] if redirect["op"] == "<<<" else []
            index += 1 if word[redirect.end():] else 2
            continue
        if word.startswith("-") and name in _SUBSET_VALUE_OPTIONS:
            index += 2
            continue
        if word.startswith("-") and name not in _SUBSET_OPTIONS:
            return f"the option `{word}`"
        if not word.startswith("-") and database is None:
            database = word
        elif not word.startswith("-"):
            arguments.append(word)
        index += 1
    path = os.path.expanduser(database or ":memory:")
    if path.lower().startswith("file:"):
        return f"the URI database `{database}`"
    if path not in ("", ":memory:") and not os.path.isabs(path):
        return f"the relative database `{database}`"
    return None


def _heredoc_bodies(command: str) -> Iterator[str]:
    for match in _HEREDOC.finditer(command):
        body: List[str] = []
        for line in command[match.end():].split("\n"):
            if (line.lstrip("\t") if match[1] else line) == match[3]:
                break
            body.append(line)
        yield "\n".join(body)


def _outside_subset(
    command: str, invocations: List["_Invocation"], fed_texts: List[str], printed: List[str],
) -> Optional[str]:
    if any(_assigns_before_sqlite(r) for r in _readings(command) if _sqlite_args(r) is not None):
        return "an environment assignment"
    arguments: List[str] = []
    scripts: List[str] = []
    part = next(filter(None, (_invocation_part(inv, arguments, scripts) for inv in invocations)), None)
    part = part or next(filter(None, map(argument_outside_subset, arguments)), None)
    if part:
        return part
    rc_files = [os.path.expanduser("~/.sqliterc"), os.path.expanduser("~/.config/sqlite3/sqliterc")]
    present = [path for path in rc_files if os.path.lexists(path)]
    if not all(_read_in_full(path, None) for path in present):
        return "an init file it could not read"
    scripts += [*_heredoc_bodies(command), *printed, *fed_texts]
    scripts += [_read_fed_file(path, None) for path in present]
    return next(filter(None, map(script_outside_subset, scripts)), None)


def _literal_printer(command: str) -> bool:
    from ..tools.shell_parser import shell_split

    try:
        words = shell_split(command)
    except ValueError:
        return False
    if not words or command_name(words[0]) not in ("echo", "printf") or "\\" in command:
        return False
    return not any(w.startswith("-") or _NOT_LITERAL.search(w) or "%" in w for w in words[1:])


def _feeds(command: str) -> Tuple[List[str], List[str], bool]:
    """The files and printed text proven feeders pipe into a sqlite3 run on the line, and whether any feeder is unproven."""
    from ..tools.shell_parser import shell_split
    from .shell_unwrapper import shell_command_string

    texts = [command, *extract_substitutions(command)]
    for reading in _readings(command):
        texts += [*_executor_floor_payloads(reading), shell_command_string(reading) or ""]
    files, printed, unproven = [], [], False
    for text in filter(None, texts):
        for producer, consumer in _pipes(text):
            if any(_sqlite_args(r) is not None for r in _readings(consumer)):
                catted = _proven_output_files(producer.strip())
                files += catted
                if not catted and _literal_printer(producer.strip()):
                    printed.append(" ".join(shell_split(producer.strip())[1:]))
                unproven = unproven or not (catted or _literal_printer(producer.strip()))
    return files, printed, unproven


def _dot_commands(text: str) -> Iterator[Tuple[str, str]]:
    """Each dot-command in *text* as ``(name, arguments)``, read as shell text and as a sqlite3 script, with ``\\n`` also read as a line break."""
    for body in dict.fromkeys((text, text.replace("\\n", "\n"))):
        for pattern in (_DOT_COMMAND, _SCRIPT_DOT_COMMAND):
            for match in pattern.finditer(body):
                yield match["name"].lower(), match["rest"]


def _dot_operands(arguments: str) -> List[str]:
    try:
        return shlex.split(arguments)
    except ValueError:
        return [word.strip("'\"") for word in arguments.split()]


def _file_operands(text: str) -> Iterator[Tuple[str, str]]:
    """Each ``(dot-command, path)`` a file dot-command in *text* names, read up to a ``|`` command."""
    for typed, arguments in _dot_commands(text):
        names = [name for name in _FILE_OPERAND_INDEX if name.startswith(typed)]
        operands: List[str] = []
        skip_value = False
        for word in _dot_operands(arguments) if names else ():
            if word.startswith("|"):
                break
            if not (skip_value or word.startswith("-")):
                operands.append(word)
            skip_value = word.lstrip("-").lower() in _DOT_VALUE_OPTIONS
        for name in names:
            if operands and operands[_FILE_OPERAND_INDEX[name]].lower() not in _OUTPUT_SPECIALS:
                yield name, operands[_FILE_OPERAND_INDEX[name]]


def _relative(path: str) -> bool:
    path = _uri_path(path)
    return path not in ("", ":memory:") and not os.path.isabs(os.path.expanduser(path))


def _moved_folder(target: str, folder: Optional[str]) -> Optional[str]:
    """The folder a sqlite3 ``.cd`` to *target* enters from *folder*, or None when the guard cannot resolve it."""
    if not target or _NOT_LITERAL.search(target) or _UNREAD_FOLDER.search(target):
        return None
    if folder is None and not os.path.isabs(target):
        return None
    return os.path.realpath(os.path.join(folder or "", target))


def _cd_targets(texts: List[str]) -> Iterator[str]:
    for text in texts:
        for typed, arguments in _dot_commands(text):
            operands = _dot_operands(arguments)
            if typed == "cd" and operands:
                yield operands[0] if len(operands) == 1 else ""


def _proven_output_files(command: str) -> List[str]:
    """The files a ``cat`` of literal paths prints; empty for any other command."""
    from ..tools.shell_parser import shell_split

    try:
        words = shell_split(command)
    except ValueError:
        return []
    operands = words[2:] if words[1:2] == ["--"] else words[1:]
    if not operands or command_name(words[0]) != "cat":
        return []
    if any(word.startswith("-") or _NOT_LITERAL.search(word) for word in operands):
        return []
    return operands


def _read_targets(text: str) -> Iterator[str]:
    for typed, arguments in _dot_commands(text):
        operands = _dot_operands(arguments)
        if operands and "read".startswith(typed):
            if operands[0].startswith("|"):
                yield from _proven_output_files(" ".join(operands)[1:]) or [""]
            else:
                yield operands[0]


def _fed_texts(sources: List[str], paths: List[str], cwd: Optional[str]) -> List[str]:
    """The text of every file sqlite3 reads as SQL, from *paths* and the ``.read`` targets of *sources*, recursively.

    A file whose path or content cannot be proven contributes nothing, so its
    call is left to the signature rather than refused.
    """
    pending = [(path, 0) for path in paths] + [(path, 0) for text in sources for path in _read_targets(text)]
    seen, texts = set(), []
    while pending:
        path, depth = pending.pop()
        if depth > _MAX_DEPTH or path in seen:
            continue
        seen.add(path)
        text = _read_fed_file(path, cwd)
        if text:
            texts.append(text)
            pending += [(target, depth + 1) for target in _read_targets(text)]
    return texts


def _target_verdicts(text: str, cwd: Optional[str]) -> Iterator[Optional[str]]:
    for typed, arguments in _dot_commands(text):
        operands = [w for w in _dot_operands(arguments) if not w.startswith("-")]
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
    if cwd is None and not os.path.isabs(os.path.expanduser(path)):
        return UNPROVEN_FOLDER_MESSAGE
    for guard in (protected_path_guard.check, shell_write_guard.check):
        allowed, reason = guard(f"echo > {shlex.quote(path)}", cwd)
        if not allowed:
            return REACH_REJECTION_PREFIX + (reason or "")
    return None


def _payload_refusals(text: str, cwd: Optional[str]) -> Iterator[Optional[str]]:
    """Judge each readable shell command and written file in *text* as the shell would."""
    for typed, arguments in _dot_commands(text):
        if "shell".startswith(typed) or "system".startswith(typed):
            yield _command_refusal(arguments.strip().strip("'\""), cwd)
            continue
        operands = _dot_operands(arguments)
        for index, operand in enumerate(operands):
            if operand.startswith("|"):
                yield _command_refusal(" ".join(operands[index:])[1:].strip(), cwd)
    for name, path in _file_operands(text):
        if name in _WRITTEN_FILE_COMMANDS:
            yield _file_refusal(path, cwd)
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
    line_words: List[str] = []
    for reading in _readings(command):
        try:
            line_words += shell_split(reading)
        except ValueError:
            continue
    assigned = [(m["name"], m["value"]) for m in map(_LINE_ASSIGNMENT.match, line_words) if m]
    paths = [path for inv in invocations for path in inv.read_files()]
    paths += [os.path.join(value, *_SQLITERC[name]) for name, value in assigned if name in _SQLITERC and value]
    paths += [path for reading in _readings(command) for path in _proven_output_files(reading)]
    fed, printed, unproven_feed = _feeds(command)
    stdin = [source for reading in _readings(command) for source in _stdin_sources(reading)]
    paths += fed + [source for source in stdin if source]
    sources = [command, *(word for inv in invocations for word in inv.args)]
    start = cwd if _starts_in_shell_folder(command) else None
    folders, texts, folder_unproven = _folders(sources, paths, start)
    folder_message = UNPROVEN_FOLDER_MESSAGE if any(True for _ in _cd_targets(texts)) else UNPROVEN_START_MESSAGE
    verdicts = [v for f in folders if (v := _folder_refusal(invocations, texts, line_words, assigned, f))]
    if verdicts:
        verdict = STORE_REJECTION_MESSAGE if STORE_REJECTION_MESSAGE in verdicts else verdicts[0]
        return folder_message if verdict == UNPROVEN_FOLDER_MESSAGE else verdict
    read_as_sql = stdin + fed + [p for inv in invocations for p in inv.read_files()]
    read_as_sql += [path for text in texts for path in _read_targets(text)]
    inputs = paths + read_as_sql + [path for text in texts for _, path in _file_operands(text)]
    inputs += [m[1].replace("''", "'") for text in texts for m in _PATH_FUNCTION.finditer(text)]
    if (folder_unproven or start is None) and any(p and _relative(p) and not p.startswith(("<(", "(")) for p in inputs):
        return folder_message
    if unproven_feed or _computes_sqlite_text(command) or not all(
        _read_in_full(p, f) for p in read_as_sql for f in folders
    ):
        return UNPROVEN_INPUT_MESSAGE
    part = _outside_subset(command, invocations, texts[len(sources):], printed)
    if part:
        return OUTSIDE_SUBSET_MESSAGE.format(
            options=", ".join(f"-{o}" for o in sorted(_SUBSET_OPTIONS | _SUBSET_VALUE_OPTIONS)),
            dots=", ".join(f".{d}" for d in sorted(DOT_COMMANDS)),
            part=part,
        )
    return None


def _folders(
    sources: List[str], paths: List[str], cwd: Optional[str],
) -> Tuple[List[Optional[str]], List[str], bool]:
    """Every folder sqlite3's ``.cd`` can make current, every text it reads as SQL from any of them, and whether a target was unresolvable.

    Every text is judged in every folder, so a ``.cd`` anywhere stands for one
    before each relative operand; None among the folders is an unknown one.
    """
    folders: List[Optional[str]] = [cwd]
    texts, unproven = list(sources), False
    for _ in range(_MAX_DEPTH):
        known = (len(folders), len(texts), unproven)
        for folder in list(folders):
            texts += [text for text in _fed_texts(texts, paths, folder) if text not in texts]
        for target in list(_cd_targets(texts)):
            for folder in list(folders):
                moved = _moved_folder(target, folder)
                if moved is None or (moved not in folders and len(folders) >= _MAX_FOLDERS):
                    unproven = True
                elif moved not in folders:
                    folders.append(moved)
        if unproven and None not in folders:
            folders.append(None)
        if known == (len(folders), len(texts), unproven):
            return folders, texts, unproven
    return folders + ([] if None in folders else [None]), texts, True


def _folder_refusal(
    invocations: List[_Invocation], texts: List[str], line_words: List[str],
    assigned: List[Tuple[str, str]], cwd: Optional[str],
) -> Optional[str]:
    """Why the sqlite3 runs reading *texts* refuse when relative paths resolve against *cwd*, or None."""
    for invocation in invocations:
        database = invocation.database()
        verdict = database is not None and _database_verdict(database, cwd)
        if verdict:
            return verdict
    words = line_words + [w for text in texts for w in _WORD.findall(text)]
    words += [s.replace("''", "'") for text in texts for s in _SQL_STRING.findall(text)]
    if any(_names_store(word, cwd) for word in words) or any(
        _relative(path) and _resolves_to_store(_uri_path(path), cwd) for text in texts for _, path in _file_operands(text)
    ):
        return STORE_REJECTION_MESSAGE
    editors = (_command_refusal(value, cwd) for name, value in assigned if name in ("VISUAL", "EDITOR"))
    return next((v for text in texts for v in _target_verdicts(text, cwd) if v), None) or next(
        (v for v in editors if v), None
    ) or next((v for text in texts for v in _payload_refusals(text, cwd) if v), None)


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
    from .protected_path_guard import INTERPRETERS, unnamed_program_words

    base = command_name(tokens[0])
    operands =[t for t in tokens[1:] if not t.startswith("-")]
    if base in _MOVERS and any(_names_store_file(t) for t in operands):
        return True
    if base in _COPY_WRITERS:
        return bool(operands) and (_names_store_file(operands[-1]) or _copies_into_store_name(operands))
    if base in _TRUNCATORS:
        return any(_names_store_file(t) for t in operands)
    if base.rstrip("0123456789.") in INTERPRETERS and _GAIA_DB_NAME_RE.search(peeled):
        return detect_mutative_command(peeled).is_mutative
    if base in _SQLITE_PROGRAMS:
        return False
    return any(_names_store_file(word) for word in unnamed_program_words(tokens))


def _names_store_file(word: str) -> bool:
    from .protected_path_guard import operand_place

    place = operand_place(word)
    return _is_gaia_db_file(place) or (os.path.isabs(place) and _resolves_to_store(place, None))


def _copies_into_store_name(operands: List[str]) -> bool:
    from .protected_path_guard import operand_place

    destination = operand_place(operands[-1])
    if len(operands) < 2 or not (destination.endswith("/") or os.path.isdir(destination)):
        return False
    return any(_names_store_file(os.path.join(destination, os.path.basename(s.rstrip("/")))) for s in operands[:-1])


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
    from .store_code_guard import check as store_code_refusal

    reason = store_code_refusal(command, cwd)
    return (False, reason) if reason else (True, None)
