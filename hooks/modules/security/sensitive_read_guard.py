"""Refuse reading or listing a sensitive path, in Bash and in the file tools.

The list of what is sensitive is ``sensitive_paths``; this module only decides
when a call READS or LISTS one. The refusal is categorical and carries no
approval: a credential's contents are not something a signature should put in
a transcript. Writes are not decided here -- an account-path write keeps its
signature route (``mutative_verbs`` for Bash, ``tool_policy`` for Write/Edit).

A read is recognized by the command that performs it: a Bash component whose
base command is a known reader or lister and that names a sensitive path, or a
Read/Glob/Grep/list call whose path or pattern reaches one. What the guard
cannot see is stated rather than claimed: a program that opens the file itself
(``python3 script.py``, ``source .env``, ``git show HEAD:.env``), a copy made
first and read later, and a recursive search started above a sensitive
directory (``grep -r x ~``, Grep over the home directory).
"""

from __future__ import annotations

import re
import shlex
from typing import Any, List, Mapping, Optional, Tuple

from .composition_rules import _strip_transparent_prefixes
from .sensitive_paths import (
    holds_credentials,
    first_sensitive_token,
    glob_selects_sensitive_name,
    pattern_reaches_sensitive_path,
    sensitive_path_label,
    sensitive_read_denial,
)
from .canonical_path import command_name
from .shell_grouping import strip_grouping_wrappers
from .shell_substitution import extract_substitutions

# Commands that print a file's contents, or list a directory's entries.
_READER_COMMANDS = frozenset({
    "cat", "tac", "head", "tail", "less", "more", "bat", "batcat", "nl",
    "od", "xxd", "hexdump", "strings", "base64", "base32",
    "sort", "uniq", "cut", "paste", "fold", "fmt", "rev", "column",
    "awk", "gawk", "mawk", "nawk", "sed",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "diff", "cmp", "comm", "jq", "yq", "openssl",
    "type", "gc", "get-content", "findstr", "certutil", "select-string", "sls",
    "import-csv", "ipcsv", "format-hex", "fhx",
})
_LISTER_COMMANDS = frozenset({
    "ls", "dir", "vdir", "tree", "find", "du", "exa", "eza", "lsd",
    "get-childitem", "gci",
})
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
# A program named by an expansion is unknown until the shell runs it.
_UNDETERMINED_PROGRAM_MARKS = ("$", "`")
# HTTP clients send a file they name (upload, form, body). These flags' values are
# never sent: a credential handed to TLS or auth, or a file the client writes.
_UPLOADERS = frozenset({"curl", "wget", "http", "https", "xh"})
_UPLOADER_WRITE_FLAGS = frozenset({
    "-o", "--output", "-O", "--output-document", "-c", "--cookie-jar", "-D", "--dump-header",
    "--trace", "--trace-ascii", "--stderr", "--etag-save", "--libcurl",
    "--output-file", "-a", "--append-output", "--save-cookies",
})
_UPLOADER_UNSENT_FLAGS = _UPLOADER_WRITE_FLAGS | {
    "--cacert", "--capath", "--cert", "-E", "--key", "--crlfile", "--pinnedpubkey",
    "--proxy-cacert", "--proxy-cert", "--proxy-key", "--netrc-file",
    "--ca-certificate", "--certificate", "--private-key", "--verify", "--cert-key",
    "--output-dir", "-P", "--directory-prefix",
}
_SHORT_FLAG_WITH_VALUE = re.compile(r"^-[A-Za-z].")
# Copy and transfer tools read every operand but the destination; each lists the
# flags whose next word is a value (an identity file is used, not copied).
_SSH_COPY_VALUE_FLAGS = frozenset({"-c", "-F", "-i", "-J", "-l", "-o", "-P", "-S", "-D", "-X", "-b", "-B", "-R"})
_COPIERS = {
    "cp": frozenset({"-S", "--suffix"}),
    "mv": frozenset({"-S", "--suffix"}),
    "install": frozenset({"-S", "--suffix", "-m", "--mode", "-o", "--owner", "-g", "--group"}),
    "scp": _SSH_COPY_VALUE_FLAGS,
    "sftp": _SSH_COPY_VALUE_FLAGS,
    "rsync": frozenset({"-e", "--rsh", "-f", "--filter", "--exclude", "--include", "-T", "--temp-dir",
                        "-B", "--block-size", "--password-file", "--log-file", "--port", "-M"}),
}
_COPY_TARGET_FLAGS = frozenset({"-t", "--target-directory"})
_TAR_VALUE_LETTERS = frozenset("fCbHKNVgX")
_TAR_CREATE_LETTERS = frozenset("cru")
# Windows shells and the flag after which the rest of the command line is the
# command they run.
_WINDOWS_SHELL_INLINE_FLAGS = {
    "cmd": ("/c", "/k"), "powershell": ("-c", "-command"), "pwsh": ("-c", "-command"),
}

# A sed that edits in place is a write; its verdict belongs to the write
# route, which asks for a signature on an account path.
_SED_IN_PLACE = re.compile(r"^(?:-[a-zA-Z]*i|--in-place)")

# find's name tests take a pattern, not a place: `find . -name .env` lists
# where .env files are, never what they hold.
_FIND_PATTERN_FLAGS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-regex", "-iregex",
})

_INPUT_REDIRECTS = frozenset({"<", "<>"})
_OTHER_REDIRECTS = frozenset({">", ">>", ">|", ">&", "&>", "&>>", "<<", "<<<", "<<-"})

_OPERATOR_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def _tokenize(component: str) -> List[str]:
    try:
        return shlex.split(component, posix=True)
    except ValueError:
        return component.split()


def _split_redirects(component: str) -> Tuple[List[str], List[str]]:
    """The words of *component* without its redirections, and the files it reads through ``<``."""
    lexer = shlex.shlex(component, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return _tokenize(component), []
    words: List[str] = []
    inputs: List[str] = []
    remaining = iter(tokens)
    for token in remaining:
        if token in _INPUT_REDIRECTS:
            inputs.append(next(remaining, ""))
        elif token in _OTHER_REDIRECTS:
            next(remaining, None)
        else:
            words.append(token)
    return words, inputs


def _sent_arguments(arguments: List[str]) -> List[str]:
    """The arguments of an HTTP client that may name a file it sends, attached short values split off."""
    sent: List[str] = []
    remaining = iter(arguments)
    for arg in remaining:
        flag = arg.split("=", 1)[0]
        if flag in _UPLOADER_UNSENT_FLAGS:
            if "=" not in arg:
                next(remaining, None)
        elif _SHORT_FLAG_WITH_VALUE.match(arg):
            if arg[:2] not in _UPLOADER_UNSENT_FLAGS:
                sent.append(arg[2:])
        else:
            sent.append(arg)
    return sent


def remote_copy_destination(command: str) -> str:
    """The scp or rsync component of *command* whose destination is another host, or ""."""
    for component in _OPERATOR_SPLIT.split(command):
        tokens = _strip_transparent_prefixes(_tokenize(component.strip()))
        if not tokens or command_name(tokens[0]) not in ("scp", "rsync"):
            continue
        operands, _ = _copy_operands(command_name(tokens[0]), tokens[1:])
        if len(operands) >= 2 and not _is_local_operand(operands[-1]):
            return component.strip()
    return ""


def _is_local_operand(operand: str) -> bool:
    """True for a plain local path; every other spelling (host:, [v6]:, scheme://) names another host."""
    head = operand.split(":", 1)[0]
    return ":" not in operand or "/" in head


def copy_written_paths(command: str) -> List[str]:
    """The local destinations the copy and transfer tools in *command* write."""
    written: List[str] = []
    for component in _OPERATOR_SPLIT.split(command):
        tokens = _strip_transparent_prefixes(_tokenize(component.strip()))
        base = command_name(tokens[0]) if tokens else ""
        if base not in _COPIERS or base == "sftp":
            continue
        operands, target_given = _copy_operands(base, tokens[1:])
        if len(operands) >= 2 and not target_given and _is_local_operand(operands[-1]):
            written.append(operands[-1])
    return written


def _first_copied_secret(sources: List[str], cwd: Optional[str]) -> str:
    """The first source that is a secret, or a directory whose whole copy would carry one."""
    return first_sensitive_token(sources, cwd) or next((s for s in sources if holds_credentials(s)), "")


def _tar_sources(arguments: List[str]) -> List[str]:
    """The files a tar that creates or appends to an archive reads; none for other modes."""
    creating = False
    sources: List[str] = []
    remaining = iter(arguments)
    for index, arg in enumerate(remaining):
        if arg in ("--create", "--append", "--update"):
            creating = True
        elif arg.startswith("--"):
            if "=" not in arg and arg in ("--file", "--directory", "--files-from", "--exclude-from"):
                next(remaining, None)
        elif arg.startswith("-") or index == 0:
            letters = arg.lstrip("-")
            creating = creating or bool(_TAR_CREATE_LETTERS.intersection(letters))
            for _ in range(sum(letter in _TAR_VALUE_LETTERS for letter in letters)):
                next(remaining, None)
        else:
            sources.append(arg)
    return sources if creating else []


def _copied_sources(base: str, arguments: List[str]) -> List[str]:
    """The operands a copy or transfer tool reads: every one but the destination."""
    operands, target_given = _copy_operands(base, arguments)
    if base == "sftp" or target_given or len(operands) < 2:
        return operands
    return operands[:-1]


def _copy_operands(base: str, arguments: List[str]) -> Tuple[List[str], bool]:
    """The operands of a copy or transfer tool, and whether a target-directory flag named its destination."""
    value_flags = _COPIERS[base]
    operands: List[str] = []
    target_given = False
    remaining = iter(arguments)
    for arg in remaining:
        if arg.split("=", 1)[0] in _COPY_TARGET_FLAGS:
            target_given = True
            if "=" not in arg:
                next(remaining, None)
        elif arg.startswith("-"):
            if arg in value_flags:
                next(remaining, None)
        else:
            operands.append(arg)
    return operands, target_given


def http_client_written_paths(command: str) -> List[str]:
    """The files the HTTP clients in *command* write through their output flags."""
    written: List[str] = []
    for component in _OPERATOR_SPLIT.split(command):
        tokens = _strip_transparent_prefixes(_tokenize(component.strip()))
        if not tokens or command_name(tokens[0]) not in _UPLOADERS:
            continue
        for index, arg in enumerate(tokens[1:], start=2):
            flag, _, value = arg.partition("=")
            if flag in _UPLOADER_WRITE_FLAGS:
                written.append(value or (tokens[index] if index < len(tokens) else ""))
            elif _SHORT_FLAG_WITH_VALUE.match(arg) and arg[:2] in _UPLOADER_WRITE_FLAGS:
                written.append(arg[2:])
    return [path for path in written if path]


def _component_reads_sensitive(component: str, cwd: Optional[str]) -> str:
    """The sensitive path a single command component reads or lists, or ""."""
    words, inputs = _split_redirects(strip_grouping_wrappers(component))
    fed = first_sensitive_token(inputs, cwd)
    if fed:
        return fed
    if component.lstrip().startswith(_UNDETERMINED_PROGRAM_MARKS):
        return first_sensitive_token(words, cwd)
    tokens = _strip_transparent_prefixes(words)
    if not tokens:
        return ""
    base = command_name(tokens[0])
    arguments = tokens[1:]
    if base in _SHELLS and "-c" in arguments:
        inline = arguments[arguments.index("-c") + 1:]
        return reads_sensitive_path(inline[0], cwd) if inline else ""
    inline_flags = _WINDOWS_SHELL_INLINE_FLAGS.get(base, ())
    flag_index = next(
        (index for index, arg in enumerate(arguments) if arg.lower() in inline_flags), None,
    )
    if flag_index is not None:
        return reads_sensitive_path(" ".join(arguments[flag_index + 1:]), cwd)
    if tokens[0].startswith(_UNDETERMINED_PROGRAM_MARKS):
        return first_sensitive_token(arguments, cwd)
    if base in _UPLOADERS:
        return first_sensitive_token(_sent_arguments(arguments), cwd)
    if base == "tar":
        return _first_copied_secret(_tar_sources(arguments), cwd)
    if base in _COPIERS:
        return _first_copied_secret(_copied_sources(base, arguments), cwd)
    if base not in _READER_COMMANDS and base not in _LISTER_COMMANDS:
        return ""
    if base == "sed" and any(_SED_IN_PLACE.match(arg) for arg in arguments):
        return ""
    if base == "find":
        arguments = [
            arg for index, arg in enumerate(arguments)
            if index == 0 or arguments[index - 1] not in _FIND_PATTERN_FLAGS
        ]
    return first_sensitive_token(arguments, cwd)


def reads_sensitive_path(command: str, cwd: Optional[str] = None) -> str:
    """The sensitive path *command* reads or lists, or "" when it reads none.

    Components are split on shell operators so a read in one is never tied
    to a harmless command in another, and command substitutions are scanned
    because they run before the command that holds them.
    """
    if not command:
        return ""
    pieces = list(_OPERATOR_SPLIT.split(command))
    for inner in extract_substitutions(command):
        pieces.extend(_OPERATOR_SPLIT.split(inner))
    for component in pieces:
        component = component.strip()
        if component:
            hit = _component_reads_sensitive(component, cwd)
            if hit:
                return hit
    return ""


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Bash entry point: ``(True, None)`` or ``(False, reason)``."""
    hit = reads_sensitive_path(command, cwd)
    if not hit:
        return True, None
    return False, sensitive_read_denial(hit, sensitive_path_label(hit, cwd) or "directory holding credentials")


def check_file_tool(
    tool_name: str, tool_input: Mapping[str, Any], cwd: Optional[str] = None,
) -> Optional[str]:
    """The refusal for a Read/Glob/Grep/LS call that reaches a sensitive path, or None.

    Tool inputs use Claude Code's names (``file_path``, ``path``, ``pattern``,
    ``glob``); the OpenCode adapter normalizes its own before policy runs.
    """
    tool = tool_name.lower()
    if tool not in ("read", "glob", "grep", "ls"):
        return None
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    label = sensitive_path_label(path, cwd) if path else ""
    target = path
    if not label and tool == "glob":
        pattern = str(tool_input.get("pattern") or "")
        label = pattern_reaches_sensitive_path(pattern, path or (cwd or ""))
        target = pattern
    if not label and tool == "grep":
        include = str(tool_input.get("glob") or "")
        if include and glob_selects_sensitive_name(include):
            label, target = "file-name pattern selecting credentials", include
        elif include:
            label = pattern_reaches_sensitive_path(include, path or (cwd or ""))
            target = include
    if not label:
        return None
    return sensitive_read_denial(target, label)
