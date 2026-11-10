"""Which argv the Gaia CLI receives from one shell component, whatever entry point reaches it.

The CLI code is reachable as the ``gaia`` launcher, a symlink or renamed copy of
it, a ``bin/cli/<group>.py`` script (which runs as ``gaia <group>``), and
``python3 -m cli.<group>``. A guard that names only the ``gaia`` word leaves the
others open, and the CLI cannot tell a subagent from the orchestrator once the
subagent unsets ``GAIA_DISPATCH_AGENT``, so the guard is the only boundary.

A word is an entry point only where the line starts a program: the command
word past assignments and wrappers, the script or module a Python interpreter
is handed, and each command a carrier runs (``mutative_verbs.handed_commands``).
An argument of a program that only reads its arguments is data.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .shell_substitution import _heredoc_body_span, _read_heredoc_opener

_SHELL_RESOLVED = ("$", "`", "(", ")", "*", "?", "[")
_PYTHON_MODULE_FLAG = re.compile(r"^-[bBdEIiOPqRsSuvx]*m(?P<module>.*)$")
_CLI_MODULE = re.compile(r"^(?:bin\.)?cli\.(?P<group>\w+)$")
_DISPATCHER_SIGNATURE = ("Unified Gaia CLI", "_discover_plugins")
_SIGNATURE_READ_LIMIT = 64 * 1024


def is_shell_resolved(word: str) -> bool:
    """True when only the shell can say what *word* becomes: an expansion, a substitution or a glob."""
    return any(c in word for c in _SHELL_RESOLVED)


def _is_dispatcher_file(path: str) -> bool:
    try:
        if not os.path.isfile(path):
            return False
        with open(path, encoding="utf-8", errors="replace") as handle:
            head = handle.read(_SIGNATURE_READ_LIMIT)
    except OSError:
        return False
    return all(marker in head for marker in _DISPATCHER_SIGNATURE)


def _cannot_start_cli(path: str) -> bool:
    """True when an absolute *path* exists as a directory or as a non-executable file without the dispatcher signature.

    A relative or missing path stays a possible entry point: the hook does not
    run in the command's cwd, and a path can be created earlier in the chain.
    """
    if not os.path.isabs(path) or not os.path.exists(path):
        return False
    if os.path.isdir(path):
        return True
    return not os.access(path, os.X_OK) and not _is_dispatcher_file(path)


def entry_group(word: str) -> Optional[str]:
    """``""`` when *word* runs the dispatcher, ``"<group>"`` for a ``<group>.py`` script, else None.

    A shell-resolved word counts as the dispatcher, since nothing static can
    show it is not one. Any ``<stem>.py`` maps to ``<stem>``; callers match the
    group word literally, so an unrelated script never forms a Gaia argv.
    """
    if is_shell_resolved(word):
        return ""
    from .cli_aliases import aliased_cli
    from .gaia_cli_only_guard import is_trusted_gaia_binary

    if is_trusted_gaia_binary(word):
        return ""
    for candidate in (word, os.path.realpath(word)) if os.path.islink(word) else (word,):
        name = os.path.basename(candidate)
        if name == "gaia" or aliased_cli(name) == "gaia":
            return None if _cannot_start_cli(word) else ""
        if name.endswith(".py") and len(name) > 3:
            return name[:-3]
    return "" if _is_dispatcher_file(word) else None


def _module_argv(words: Sequence[str], index: int) -> Optional[List[str]]:
    match = _PYTHON_MODULE_FLAG.match(words[index])
    if match is None:
        return None
    if match["module"]:
        module, rest = match["module"], words[index + 1:]
    elif index + 1 < len(words):
        module, rest = words[index + 1], words[index + 2:]
    else:
        return None
    cli = _CLI_MODULE.match(module)
    return [cli["group"], *rest] if cli else None


_UNQUOTED_COMPUTED = set("$`()*?[")
_BRACE_EXPANSION = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
_ARGV_FEEDERS = frozenset({"xargs", "parallel"})
_IDENTITY = "GAIA_DISPATCH_AGENT"
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_DECLARERS = frozenset({"export", "declare", "typeset", "local", "readonly", "unset"})
_CAT_HEREDOC_WORD = re.compile(r'(?:^|(?<=[ \t]))"\$\([ \t]*cat[ \t]+(?=<<)')
_CLOSING_WORD = re.compile(r'[ \t]*\)(")(?=[ \t\n;&|]|$)')
_CLI_MODULES = frozenset(
    path.stem for path in (Path(__file__).resolve().parents[3] / "bin" / "cli").glob("*.py"))
_PYTHON_CODE_FLAG = re.compile(r"^-[A-Za-z]*c$")
_PYTHON_VALUE_OPTIONS = frozenset({"-W", "-X"})
_MAX_CARRIER_DEPTH = 4
_QUOTING = str.maketrans("", "", "'\"\\")
_COMPUTING = _UNQUOTED_COMPUTED | {"{"}
_IDENTITY_TOUCHING = re.compile(rf"{_IDENTITY}|\b(?:env|exec|{'|'.join(sorted(_DECLARERS))})\b")


def literal_words(component: str) -> List[Tuple[str, bool]]:
    """``(word, computed)`` per shell word: computed when an expansion, substitution, glob or brace makes it."""
    pairs: List[Tuple[str, bool]] = []
    text, unquoted, computed, started, quote = [], [], False, False, None
    index, size = 0, len(component)
    while index < size:
        char = component[index]
        escaped = char == "\\" and index + 1 < size and quote != "'"
        if escaped:
            index += 1
            text.append(component[index])
        elif quote == "'":
            quote = None if char == "'" else quote
            text.extend(char if quote else "")
        elif quote == '"':
            quote = None if char == '"' else quote
            computed = computed or (quote is not None and char in "$`")
            text.extend(char if quote else "")
        elif char in " \t\n":
            if started:
                pairs.append(("".join(text),
                              computed or bool(_BRACE_EXPANSION.search("".join(unquoted)))))
            text, unquoted, computed, started = [], [], False, False
        elif char in "'\"":
            quote = char
        else:
            computed = computed or char in _UNQUOTED_COMPUTED
            text.append(char)
            unquoted.append(char)
        started = started or char not in " \t\n"
        index += 1
    if started:
        pairs.append(("".join(text), computed or quote is not None
                      or bool(_BRACE_EXPANSION.search("".join(unquoted)))))
    return pairs


def resolve_cat_heredocs(command: str, keep_newlines: bool = False) -> str:
    """*command* with each whole word ``"$(cat <<'D' ... D)"`` written as the single-quoted value it prints, its lines joined by spaces unless *keep_newlines*."""
    pieces, copied = [], 0
    for match in _CAT_HEREDOC_WORD.finditer(command):
        if match.start() < copied:
            continue
        value_end = _cat_heredoc_value(command, match.end(), keep_newlines)
        if value_end is None:
            continue
        value, end = value_end
        pieces += [command[copied:match.start()], shlex.quote(value)]
        copied = end
    return "".join(pieces) + command[copied:]


def _cat_heredoc_value(command: str, opener_at: int, keep_newlines: bool) -> Optional[Tuple[str, int]]:
    opener = _read_heredoc_opener(command, opener_at)
    if opener is None:
        return None
    delimiter, expands, strip_tabs, after = opener
    line_end = command.find("\n", after)
    if expands or line_end == -1 or command[after:line_end].strip():
        return None
    body_start, resume, terminated = _heredoc_body_span(command, line_end + 1, delimiter, strip_tabs)
    closing = _CLOSING_WORD.match(command, resume)
    if not terminated or closing is None:
        return None
    terminator_end = resume - 1 if command[resume - 1:resume] == "\n" else resume
    lines = command[body_start:command.rfind("\n", 0, terminator_end) + 1].split("\n")
    value = "\n".join(line.lstrip("\t") if strip_tabs else line for line in lines)
    value = value.rstrip("\n")
    return (value if keep_newlines else value.replace("\n", " ")), closing.end(1)


def _is_gaia_entry(words: Sequence[str], index: int) -> bool:
    if is_shell_resolved(words[index]):
        return False
    group = entry_group(words[index])
    if group == "":
        return True
    if group is not None:
        return group in _CLI_MODULES or os.path.basename(os.path.dirname(words[index])) == "cli"
    return _module_argv(words, index) is not None


def _words(text: str) -> List[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _program_index(words: Sequence[str]) -> Optional[int]:
    """Index of the word naming what *words* run: the command word, or the script or ``-m`` flag a Python interpreter is handed."""
    from .canonical_path import program_identity
    from .mutative_verbs import _interpreter_family

    if not words:
        return None
    if not _interpreter_family(program_identity(words[0]).lower()).startswith("python"):
        return 0
    index = 1
    while index < len(words):
        word = words[index]
        if _PYTHON_MODULE_FLAG.match(word) or not word.startswith("-"):
            return index
        if _PYTHON_CODE_FLAG.match(word):
            return None
        index += 2 if word in _PYTHON_VALUE_OPTIONS else 1
    return None


def _entry_argv(words: Sequence[str]) -> Optional[List[str]]:
    """The argv the Gaia CLI receives when *words* start it, or None when they start another program."""
    index = _program_index(words)
    if index is None:
        return None
    module_argv = _module_argv(words, index)
    if module_argv is not None:
        return module_argv
    group = entry_group(words[index])
    return None if group is None else ([group] if group else []) + list(words[index + 1:])


def _commands_run(component: str) -> List[Tuple[str, bool]]:
    """Every command *component* runs as ``(text, fed)``: itself, and each command a carrier in it is handed; *fed* when xargs or parallel supplies arguments."""
    from ..tools.shell_parser import parse_command
    from .mutative_verbs import _peel_wrapper_readings, handed_commands

    run: List[Tuple[str, bool]] = []
    pending = [(part, False, 0) for part in parse_command(component) or [component]]
    seen = {part for part, _, _ in pending}
    offered = set()
    while pending:
        text, fed, depth = pending.pop()
        run.append((text, fed))
        for reading in _peel_wrapper_readings(text)[0] if depth < _MAX_CARRIER_DEPTH else ():
            words = _words(reading)
            index = _program_index(words)
            if index is None or _is_gaia_entry(words, index):
                continue
            feeds = fed or os.path.basename(words[0]) in _ARGV_FEEDERS
            for handed in handed_commands(reading, _may_start_command):
                if handed in offered:
                    continue
                offered.add(handed)
                for part in parse_command(handed) or [handed]:
                    if part not in seen:
                        seen.add(part)
                        pending.append((part, feeds, depth + 1))
    return run


def _may_start_command(words: Sequence[str], index: int) -> bool:
    """True when a command begun at ``words[index]`` can touch the identity or reach the CLI by more than the tails its caller already offers."""
    from .canonical_path import program_identity
    from .mutative_verbs import _interpreter_family, starts_carrier

    word, name = words[index], program_identity(words[index])
    respelled = name != word and _is_gaia_entry([name, *words[index + 1:index + 2]], 0)
    return (starts_carrier(words, index) or bool(_ASSIGNMENT.match(word)) or word in _DECLARERS
            or _interpreter_family(name.lower()).startswith("python") or _is_gaia_entry(words, index) or respelled)


def _programs_started(component: str) -> List[Tuple[str, bool]]:
    """``(reading, fed)`` for each command *component* runs, read past its assignments and wrappers."""
    from .mutative_verbs import _peel_wrapper_readings

    return [(reading, fed) for text, fed in _commands_run(component) for reading in _peel_wrapper_readings(text)[0]]


def _unquoted(text: str) -> str:
    return text.translate(_QUOTING)


def computes_gaia_words(component: str) -> bool:
    """True when a literally named Gaia CLI entry point *component* starts takes computed or xargs-fed words."""
    bare = _unquoted(component)
    if not _COMPUTING & set(component) and not any(feeder in bare for feeder in _ARGV_FEEDERS):
        return False
    computed = {word for word, is_computed in literal_words(component) if is_computed}
    for reading, fed in _programs_started(component):
        pairs = [(word, is_computed or word in computed) for word, is_computed in literal_words(reading)]
        words = [word for word, _ in pairs]
        index = _program_index(words)
        if index is None or pairs[index][1] or not _is_gaia_entry(words, index):
            continue
        if fed or any(later for _, later in pairs[index + 1:]):
            return True
    return False


_NAMES_IDENTITY = "names"
_CLEARS_ENVIRONMENT = "clears"


def clears_dispatch_identity(component: str) -> bool:
    """True when a command *component* runs names ``GAIA_DISPATCH_AGENT``, or clears the environment around a Gaia CLI call."""
    if not _IDENTITY_TOUCHING.search(_unquoted(component)):
        return False
    kinds = {_identity_touch(text) for text, _ in _commands_run(component)}
    return _NAMES_IDENTITY in kinds or (_CLEARS_ENVIRONMENT in kinds and _reaches_gaia(component))


def _reaches_gaia(component: str) -> bool:
    for reading, _ in _programs_started(component):
        words = _words(reading)
        index = _program_index(words)
        if index is not None and _is_gaia_entry(words, index):
            return True
    return False


def _identity_touch(text: str) -> Optional[str]:
    """How one command touches the dispatch identity: a leading assignment, a declarer, ``exec -c``, or an ``env`` among its wrappers."""
    from .mutative_verbs import _expand_split_strings

    dropped, expanded, wrapped_at, _ = _expand_split_strings(text)
    pairs = literal_words(f"{dropped} {expanded}")
    wrappers = len(literal_words(f"{dropped} {expanded[:wrapped_at]}"))
    words = [word for word, _ in pairs]
    leading = 0
    while leading < len(words) and _ASSIGNMENT.match(words[leading]):
        if words[leading].startswith(_IDENTITY + "="):
            return _NAMES_IDENTITY
        leading += 1
    rest = pairs[leading:]
    if rest and rest[0][0] in _DECLARERS:
        if any(word == _IDENTITY or word.startswith(_IDENTITY + "=") for word, _ in rest[1:]):
            return _NAMES_IDENTITY
        if any(computed for _, computed in rest[1:]):
            return _CLEARS_ENVIRONMENT
    if rest and rest[0][0] == "exec" and any(
            word.startswith("-") and "c" in word for word, _ in rest[1:2]):
        return _CLEARS_ENVIRONMENT
    kinds = {_env_touch(pairs[index + 1:]) for index, (word, _) in enumerate(pairs[:wrappers])
             if os.path.basename(word) == "env"}
    for kind in (_NAMES_IDENTITY, _CLEARS_ENVIRONMENT):
        if kind in kinds:
            return kind
    return None


def _env_touch(args: Sequence[Tuple[str, bool]]) -> Optional[str]:
    from .mutative_verbs import spells_long_option

    index = 0
    while index < len(args):
        word = args[index][0]
        value = args[index + 1] if index + 1 < len(args) else ("", False)
        if word.startswith(_IDENTITY + "="):
            return _NAMES_IDENTITY
        if word in ("-", "-i") or spells_long_option(word, "--ignore-environment"):
            return _CLEARS_ENVIRONMENT
        if word.startswith("--unset"):
            named = word.partition("=")[2] if "=" in word else value[0]
            if named == _IDENTITY:
                return _NAMES_IDENTITY
            if args[index][1] or ("=" not in word and value[1]):
                return _CLEARS_ENVIRONMENT
            index += 1 if "=" in word else 2
            continue
        if word in ("-C", "--chdir"):
            index += 2
            continue
        if word.startswith("-") and not word.startswith("--"):
            before, unset, attached = word[1:].partition("u")
            if unset and (attached or value[0]) == _IDENTITY:
                return _NAMES_IDENTITY
            if "i" in before or (not unset and "i" in word[1:]):
                return _CLEARS_ENVIRONMENT
            if unset and (args[index][1] or (not attached and value[1])):
                return _CLEARS_ENVIRONMENT
            index += 1 if attached or not unset else 2
            continue
        if not _ASSIGNMENT.match(word):
            return None
        index += 1
    return None


def gaia_cli_argvs(words: Sequence[str]) -> List[List[str]]:
    """Every argv the Gaia CLI may receive from *words*, one per command they run that starts an entry point."""
    started = (_entry_argv(_words(reading)) for reading, _ in _programs_started(shlex.join(words)))
    return [argv for argv in started if argv is not None]
