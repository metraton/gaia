"""
gaia_db_write_guard.py -- PreToolUse Bash hook.

Rejects commands writing directly to ~/.gaia/gaia.db bypassing the store API.

Patterns detected:
    a sqlite3 invocation naming gaia.db (or a .gaia/*.db) with a _WRITE_VERBS
    word or a PRAGMA that sets a value or rewrites the file anywhere in the
    command, case-insensitive, inside quotes / heredocs; a dot-command that
    rewrites the open store (.restore, .import), a .read or `<` script that
    writes or cannot be read, a .shell / .system command that is not provably
    a read, or one that writes the gaia.db file (.backup, .clone,
    .save, .output, .once, .log, .trace); and any shell writer whose
    DESTINATION is the gaia.db file or a side file -- cp/mv/install/ln/rsync,
    redirect, tee, dd of=, truncate, or a mutative inline interpreter naming it
    -- behind any wrapper the classifier peels.

Read-only SQL (SELECT, a read PRAGMA) is allowed. The write block is categorical and NOT
approvable -- like blocked_commands.py, it denies with SecurityTier.T3_BLOCKED
and no approval_id, so there is no T3 grant that lifts it. Legitimate cases
go through the sanctioned paths instead:
    - DDL / migrations: scripts/bootstrap_database.sh and tools/migration/*
    - data writes: the `gaia context` CLI / update_contracts, backed by the
      typed API in gaia/store/writer.py (which enforces _is_authorized())

Public API:
    is_db_write_attempt(command: str) -> bool
    writes_db_file(command: str) -> bool
    rejection_message() -> str
    check(command: str, cwd: str | None = None) -> tuple[bool, str | None]   -- main entrypoint
"""

from __future__ import annotations

import os
import re
from typing import Iterator, List, Optional, Tuple

from .canonical_path import canonical_path, command_name
from .data_heredoc import data_heredoc_header
from .mutative_verbs import CATEGORY_UNKNOWN, _peel_leading_command_wrappers, detect_mutative_command
from .shell_substitution import extract_substitutions
from .shell_write_guard import _split_components, _tokenize, _writer_targets, downloader_paths

_SQLITE_INVOCATION = re.compile(
    r"sqlite3\b[^&;|\n]*?(gaia\.db|~/\.gaia/[^\s'\"]*\.db|\.gaia/[^\s'\"]*\.db)",
    re.IGNORECASE,
)
_WRITE_VERBS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|VACUUM|ATTACH|REINDEX|ANALYZE)\b",
    re.IGNORECASE,
)
_DOT_COMMAND = re.compile(
    r"(?:^[ \t]*|(?P<quote>[\"'])[ \t]*)\.(?P<name>[a-z]+)\b"
    r"(?P<rest>(?(quote)(?:(?!(?P=quote))[^\n])*|[^\n]*))",
    re.IGNORECASE | re.MULTILINE,
)
_DOT_STORE_REWRITERS = ("restore", "import")
_DOT_FILE_WRITERS = ("backup", "clone", "save", "output", "once", "log", "trace")
_DOT_SHELLS = ("shell", "system")
_SQLITE_SEGMENT = re.compile(r"sqlite3\b[^&;|\n]*", re.IGNORECASE)
_QUOTED_OR_STDIN_REDIRECT = re.compile(
    r"\"[^\"]*\"|'[^']*'"
    r"|(?<![<\d])0?<(?![<&])\s*(?P<path>\"[^\"]*\"|'[^']*'|\S+)"
)
_COMPUTED_WORDS = re.compile(r"[$`]|[<>]\(")
_PIPE_INTO_SQLITE = re.compile(
    r"(?P<producer>(?:\"[^\"]*\"|'[^']*'|[^|;&\n\"'])*)(?<!\|)\|(?!\|)\s*(?P<sqlite>sqlite3\b[^&;|\n]*)", re.IGNORECASE
)
_LITERAL_PRODUCERS = frozenset({"echo", "printf"})
_MAX_READ_DEPTH = 3
_MAX_SCRIPT_BYTES = 1 << 20
_SHELL_CD = re.compile(r"(?:^|[;&|(\s])(?:cd|pushd|popd)\b")
_PRAGMA = re.compile(r"\bPRAGMA\s+(?:\w+\s*\.\s*)?(\w+)\s*([=(])?", re.IGNORECASE)
_PRAGMA_READ_ARGUMENT = frozenset({
    "table_info", "table_xinfo", "table_list", "index_info", "index_xinfo",
    "index_list", "foreign_key_list", "foreign_key_check", "integrity_check",
    "quick_check",
})
_PRAGMA_BARE_WRITERS = frozenset({"optimize", "wal_checkpoint", "incremental_vacuum"})

REJECTION_MESSAGE = (
    "Direct SQL writes to gaia.db are not allowed. "
    "Use `gaia context` CLI or emit update_contracts. "
    "Raw SQL bypasses agent_permissions enforcement."
)

UNREADABLE_SCRIPT_MESSAGE = (
    "A `.read` or `<` redirect against gaia.db names a script this guard could "
    "not read, so it could not prove the script does not write the store. Use "
    "the `gaia` CLI, or run the statements directly."
)

UNPROVEN_STDIN_MESSAGE = (
    "Input piped into sqlite3 against gaia.db comes from something other than "
    "one readable file or inline literal SQL, so this guard could not prove it "
    "does not write the store. Use the `gaia` CLI, or run the statements directly."
)

UNJUDGEABLE_SHELL_MESSAGE = (
    "A `.shell` or `.system` against gaia.db runs a command this guard could "
    "not prove is a read, so it could not prove the store stays untouched. Run "
    "the command directly, outside the SQL shell."
)

FILE_WRITE_REJECTION_MESSAGE = (
    "Writing the gaia.db file from the shell (copy, move, redirect, truncate, "
    "or an interpreter) is not allowed: it replaces or corrupts the store "
    "without agent_permissions enforcement. Use the `gaia` CLI."
)

# The store and its SQLite side files; a write to any of them corrupts the store.
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


def _writes_pragma(command: str) -> bool:
    """Whether some PRAGMA in *command* sets a value or is one that rewrites the file bare."""
    for match in _PRAGMA.finditer(command):
        name, form = match.group(1).lower(), match.group(2)
        if form == "=" or (form == "(" and name not in _PRAGMA_READ_ARGUMENT):
            return True
        if form is None and name in _PRAGMA_BARE_WRITERS:
            return True
    return False


def _is_abbreviation_of(typed: str, full: str) -> bool:
    return full.startswith(typed)


def _dot_commands(text: str) -> Iterator[Tuple[str, List[str], str]]:
    for match in _DOT_COMMAND.finditer(text):
        operands = [word.strip("'\"\\") for word in match["rest"].split()]
        yield match["name"].lower(), [w for w in operands if w and not w.startswith("-")], match["rest"]


def _script_text(path: str, cwd: Optional[str]) -> Optional[str]:
    path = os.path.expanduser(path)
    if "$" in path or "`" in path:
        return None
    if not os.path.isabs(path):
        if cwd is None:
            return None
        path = os.path.join(cwd, path)
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > _MAX_SCRIPT_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _script_rejection(script: str, cwd: Optional[str], depth: int) -> Optional[str]:
    if _WRITE_VERBS.search(script) or _writes_pragma(script):
        return REJECTION_MESSAGE
    return _dot_rejection(script, cwd, depth, store_open=True)


def _shell_rejection(command: str, cwd: Optional[str]) -> Optional[str]:
    """Judge a `.shell` / `.system` command as the shell would judge it run directly."""
    from .blocked_commands import is_blocked_command
    from . import protected_path_guard, sensitive_read_guard, shell_write_guard

    if command.endswith("\\") or _COMPUTED_WORDS.search(command):
        return UNJUDGEABLE_SHELL_MESSAGE
    for component in _split_components(command):
        component = component.strip()
        if not component:
            continue
        if is_blocked_command(component).is_blocked:
            return REJECTION_MESSAGE
        verdict = detect_mutative_command(component, cwd=cwd)
        if verdict.is_mutative:
            return REJECTION_MESSAGE
        if verdict.category == CATEGORY_UNKNOWN:
            return UNJUDGEABLE_SHELL_MESSAGE
        for guard in (check, protected_path_guard.check, shell_write_guard.check, sensitive_read_guard.check):
            if not guard(component, cwd)[0]:
                return REJECTION_MESSAGE
    return None


def _redirected_script_rejection(command: str, cwd: Optional[str]) -> Optional[str]:
    for segment in _SQLITE_SEGMENT.finditer(command):
        if not _SQLITE_INVOCATION.search(segment[0]):
            continue
        for match in _QUOTED_OR_STDIN_REDIRECT.finditer(segment[0]):
            if match["path"] is None:
                continue
            script = _script_text(match["path"].strip("'\""), cwd)
            if script is None:
                return UNREADABLE_SCRIPT_MESSAGE
            rejection = _script_rejection(script, cwd, 1)
            if rejection:
                return rejection
    return None


def _piped_script_rejection(command: str, cwd: Optional[str]) -> Optional[str]:
    for match in _PIPE_INTO_SQLITE.finditer(command):
        if not _SQLITE_INVOCATION.search(match["sqlite"]):
            continue
        producer = match["producer"].strip()
        tokens = _tokenize(producer)
        name = command_name(tokens[0]) if tokens else ""
        operands = [t for t in tokens[1:] if not t.startswith("-")]
        if name == "cat" and len(operands) == 1:
            script = _script_text(operands[0], cwd)
            if script is None:
                return UNREADABLE_SCRIPT_MESSAGE
            rejection = _script_rejection(script, cwd, 1)
            if rejection:
                return rejection
        elif name not in _LITERAL_PRODUCERS or _COMPUTED_WORDS.search(producer) or "<" in producer:
            return UNPROVEN_STDIN_MESSAGE
    return None


def _dot_rejection(text: str, cwd: Optional[str], depth: int, store_open: bool) -> Optional[str]:
    for typed, operands, rest in _dot_commands(text):
        if operands and _is_gaia_db_file(operands[-1]) and any(
            _is_abbreviation_of(typed, name) for name in _DOT_FILE_WRITERS
        ):
            return REJECTION_MESSAGE
        if not store_open:
            continue
        if any(_is_abbreviation_of(typed, name) for name in _DOT_STORE_REWRITERS):
            return REJECTION_MESSAGE
        if rest.strip() and any(_is_abbreviation_of(typed, name) for name in _DOT_SHELLS):
            rejection = _shell_rejection(rest.strip(), cwd)
            if rejection:
                return rejection
        if operands and _is_abbreviation_of(typed, "read"):
            script = _script_text(operands[0], cwd) if depth < _MAX_READ_DEPTH else None
            if script is None:
                return UNREADABLE_SCRIPT_MESSAGE
            rejection = _script_rejection(script, cwd, depth + 1)
            if rejection:
                return rejection
    return None


def _sql_rejection(command: str, cwd: Optional[str] = None) -> Optional[str]:
    invoked = _SQLITE_INVOCATION.search(command) is not None
    # Searched in the whole command, so a heredoc body counts.
    if invoked and (_WRITE_VERBS.search(command) or _writes_pragma(command)):
        return REJECTION_MESSAGE
    if not re.search(r"sqlite3\b", command, re.IGNORECASE):
        return None
    shell_cwd = None if _SHELL_CD.search(command) else cwd
    if invoked:
        rejection = _redirected_script_rejection(command, shell_cwd) or _piped_script_rejection(command, shell_cwd)
        if rejection:
            return rejection
    return _dot_rejection(command, shell_cwd, 0, store_open=invoked)


def is_db_write_attempt(command: str) -> bool:
    """Return True iff `command` writes gaia.db through the sqlite3 shell, or cannot be shown not to."""
    return bool(command) and _sql_rejection(command) is not None


def _is_gaia_db_file(token: str) -> bool:
    canonical = canonical_path(token.strip("'\""), _GAIA_DB_FILE_NAMES)
    return canonical.rsplit("/", 1)[-1] in _GAIA_DB_FILE_NAMES


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


def rejection_message() -> str:
    """Return the canonical rejection message."""
    return REJECTION_MESSAGE


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash hook integration.

    Args:
        command: The Bash command line.
        cwd: The shell's directory, which a relative `.read` path resolves against.

    Returns:
        (allowed, reason)
        - (True, None)  if command is safe
        - (False, msg)  if command writes gaia.db outside the store API --
          SQL through the SQL shell, or any shell writer over the file
    """
    rejection = _sql_rejection(_sql_scope(command), cwd) if command else None
    if rejection:
        return False, rejection
    if writes_db_file(command):
        return False, FILE_WRITE_REJECTION_MESSAGE
    return True, None
