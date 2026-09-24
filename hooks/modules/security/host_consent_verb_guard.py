"""
host_consent_verb_guard.py -- the host consent verbs belong to the plugin alone.

``gaia approvals opencode-present`` records that a signature was SHOWN and
``gaia approvals opencode-decide`` applies the user's reply to it. The CLI can
check only that the caller names the requesting session and agent; the call id
and the token are whatever the caller passes. So a requester reaching these
verbs from a shell could present its own request to itself and approve it with
no user in the loop. The OpenCode plugin runs them through its own process
spawn, which no tool hook sees, so refusing them on every model-issued shell
call, in both hosts and for every role, leaves the plugin as their only caller.

The CLI cannot hold the line itself: whatever proof the plugin hands its own
child (argv, environment, stdin) a same-user shell can read from ``/proc``
while that child runs, and OpenCode 1.18.32 lets any local client answer a
pending question through its server, so the host's stored reply is not the
user's by construction either. The fence is therefore this hook.

Categorical, not approvable: a signature over "record that the user answered"
would be the user's answer to a different question.

Detection reads the words bash would pass, per simple command: quotes removed
(``'…'``, ``"…"``, ``$'…'`` decoded), backslashes and line continuations
applied, and every shell the command reaches read the same way -- ``bash -c``
/ ``sh -c`` payloads, ``eval``, command substitutions, heredoc and script
bodies fed to a shell, ``xargs`` and ``find -exec`` commands. A command is
refused when a program other than a pure reader receives a consent verb as a
word; when the word after ``approvals``, or a Gaia CLI's group or verb, is
only known at run time (a variable, a substitution, brace or glob expansion);
when ``xargs`` builds a Gaia CLI call whose verb comes from its input; or when
code run by an interpreter, or a shell reading its program from input, spells
a consent verb. A reader that merely mentions the verb (``grep``, ``cat``,
``git log``) is left alone.

What remains open: a program that assembles the verb from fragments at run
time and runs it through a channel no word here names -- a script written and
run by one interpreter call, an encoded payload decoded and executed. Reaching
the verbs that way is the elusion ``security-tiers`` forbids.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

CONSENT_VERBS = frozenset({"opencode-present", "opencode-decide"})

REJECTION_MESSAGE = (
    "`gaia approvals opencode-present` and `opencode-decide` record a "
    "presentation and apply the user's reply; only the OpenCode plugin runs "
    "them, from its own process, never a model's shell. Ask for the signature "
    "the usual way (`gaia approvals request-set`, then close APPROVAL_REQUEST). "
    "Do NOT retry -- this is not approvable."
)

_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "mksh", "fish"})
_INTERPRETER_RE = re.compile(r"^(python[\d.]*|node|nodejs|bun|deno|perl|ruby|php|lua|tclsh)$")
_INTERPRETER_CODE_FLAGS = frozenset({"-c", "-e", "-E", "--eval", "-r", "--command"})
_WRAPPERS = frozenset({
    "env", "command", "exec", "nohup", "nice", "ionice", "stdbuf", "timeout",
    "sudo", "doas", "time", "builtin", "setsid", "unbuffer", "chronic", "watch",
})
_READERS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "cat", "bat", "less", "more",
    "head", "tail", "wc", "ls", "file", "stat", "echo", "printf", "diff", "cmp",
})
_GIT_READ_SUBCOMMANDS = frozenset({"log", "show", "grep", "diff", "blame", "status"})
_XARGS_VALUE_FLAGS = frozenset({"-I", "-n", "-L", "-P", "-d", "-E", "-s", "-a"})
_MAX_SCRIPT_BYTES = 1_000_000
_MAX_DEPTH = 8
_PLAIN_VERB = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass
class _Word:
    text: str = ""
    dynamic: bool = False


@dataclass
class _Command:
    words: List[_Word] = field(default_factory=list)
    heredoc: Optional[str] = None
    stdin_file: Optional[str] = None


@dataclass
class _Parsed:
    commands: List[_Command] = field(default_factory=list)
    substitutions: List[str] = field(default_factory=list)


def _ansi_c(body: str) -> str:
    """Decode the escapes of a ``$'…'`` word the way bash does."""
    out, i = [], 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b",
              "E": "\x1b", "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
    while i < len(body):
        char = body[i]
        if char != "\\" or i + 1 >= len(body):
            out.append(char)
            i += 1
            continue
        nxt = body[i + 1]
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt == "x":
            digits = re.match(r"[0-9a-fA-F]{1,2}", body[i + 2:])
            out.append(chr(int(digits.group(), 16)) if digits else "\\x")
            i += 2 + (len(digits.group()) if digits else 0)
        elif nxt in "uU":
            digits = re.match(r"[0-9a-fA-F]{1,8}" if nxt == "U" else r"[0-9a-fA-F]{1,4}", body[i + 2:])
            out.append(chr(int(digits.group(), 16)) if digits else "\\" + nxt)
            i += 2 + (len(digits.group()) if digits else 0)
        elif nxt in "01234567":
            digits = re.match(r"[0-7]{1,3}", body[i + 1:]).group()
            out.append(chr(int(digits, 8)))
            i += 1 + len(digits)
        else:
            out.append("\\" + nxt)
            i += 2
    return "".join(out)


def _closing_paren(text: str, start: int) -> int:
    """Index just past the ``)`` closing the ``(`` before ``start``, quote-aware."""
    depth, i, quote = 1, start, None
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\" and quote == '"':
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "\\":
            i += 2
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _parse(command: str) -> _Parsed:
    """Split ``command`` into simple commands of bash words, quotes removed."""
    parsed = _Parsed()
    current = _Command()
    word: Optional[_Word] = None
    pending_heredocs: List[Tuple[str, _Command]] = []
    redirect_in = False
    i, n = 0, len(command)

    def end_word():
        nonlocal word, redirect_in
        if word is not None:
            if redirect_in:
                current.stdin_file = word.text
                redirect_in = False
            else:
                current.words.append(word)
        word = None

    def end_command():
        nonlocal current
        end_word()
        if current.words:
            parsed.commands.append(current)
        current = _Command()

    def ensure_word() -> _Word:
        nonlocal word
        if word is None:
            word = _Word()
        return word

    while i < n:
        char = command[i]
        if char == "\\":
            if i + 1 < n and command[i + 1] == "\n":
                i += 2
                continue
            if i + 1 < n:
                ensure_word().text += command[i + 1]
            i += 2
            continue
        if char == "'":
            end = command.find("'", i + 1)
            end = n if end < 0 else end
            ensure_word().text += command[i + 1:end]
            i = end + 1
            continue
        if char == "$" and command.startswith("$'", i):
            end, j = n, i + 2
            while j < n:
                if command[j] == "\\":
                    j += 2
                    continue
                if command[j] == "'":
                    end = j
                    break
                j += 1
            ensure_word().text += _ansi_c(command[i + 2:end])
            i = end + 1
            continue
        if char == '"' or command.startswith('$"', i):
            j = i + (2 if char == "$" else 1)
            target = ensure_word()
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    if command[j + 1] == "\n":
                        j += 2
                        continue
                    target.text += command[j + 1] if command[j + 1] in '$`"\\' else command[j:j + 2]
                    j += 2
                    continue
                if command[j] == "$" and command.startswith("$(", j):
                    close = _closing_paren(command, j + 2)
                    parsed.substitutions.append(command[j + 2:close - 1])
                    target.dynamic = True
                    target.text += command[j:close]
                    j = close
                    continue
                if command[j] in "$`":
                    target.dynamic = True
                    if command[j] == "`":
                        close = command.find("`", j + 1)
                        close = n if close < 0 else close
                        parsed.substitutions.append(command[j + 1:close])
                        target.text += command[j:close + 1]
                        j = close + 1
                        continue
                target.text += command[j]
                j += 1
            i = j + 1
            continue
        if char == "$" and command.startswith("$(", i):
            close = _closing_paren(command, i + 2)
            parsed.substitutions.append(command[i + 2:close - 1])
            target = ensure_word()
            target.dynamic = True
            target.text += command[i:close]
            i = close
            continue
        if char == "`":
            close = command.find("`", i + 1)
            close = n if close < 0 else close
            parsed.substitutions.append(command[i + 1:close])
            target = ensure_word()
            target.dynamic = True
            target.text += command[i:close + 1]
            i = close + 1
            continue
        if char in "<>" and command.startswith("(", i + 1):
            close = _closing_paren(command, i + 2)
            parsed.substitutions.append(command[i + 2:close - 1])
            i = close
            continue
        if char == "<" and command.startswith("<<", i) and not command.startswith("<<<", i):
            end_word()
            match = re.match(r"<<-?\s*(['\"]?)([^\s'\"<>;&|()]+)\1", command[i:])
            if match:
                pending_heredocs.append((match.group(2), current))
                i += match.end()
                continue
            i += 2
            continue
        if char == "<":
            end_word()
            redirect_in = not command.startswith("<<<", i)
            i += 3 if command.startswith("<<<", i) else 1
            continue
        if char == ">":
            end_word()
            i += 1
            continue
        if char == "#" and word is None:
            newline = command.find("\n", i)
            i = n if newline < 0 else newline
            continue
        if char == "\n":
            end_command()
            i += 1
            for delimiter, owner in pending_heredocs:
                body = []
                while i < n:
                    newline = command.find("\n", i)
                    line = command[i:] if newline < 0 else command[i:newline]
                    i = n if newline < 0 else newline + 1
                    if line.strip() == delimiter:
                        break
                    body.append(line)
                owner.heredoc = "\n".join(body)
            pending_heredocs = []
            continue
        if char in ";&|()":
            end_command()
            i += 1
            continue
        if char in " \t":
            end_word()
            i += 1
            continue
        target = ensure_word()
        if char == "$" or char in "*?[" or (char == "{" and re.match(r"\{[^{}\s]*(,|\.\.)[^{}\s]*\}", command[i:])):
            target.dynamic = True
        target.text += char
        i += 1
    end_command()
    return parsed


def _basename(word: _Word) -> str:
    return os.path.basename(word.text)


def _peel(words: List[_Word]) -> List[_Word]:
    """Drop leading assignments and wrapper programs, down to the program that runs."""
    i = 0
    while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i].text):
        i += 1
    while i < len(words) and _basename(words[i]) in _WRAPPERS:
        i += 1
        while i < len(words) and (
            words[i].text.startswith("-") or "=" in words[i].text
            or re.match(r"^\d+(\.\d+)?[smhd]?$", words[i].text)
        ):
            i += 1
    return words[i:]


def _is_gaia(words: List[_Word]) -> Optional[List[_Word]]:
    """The arguments of a Gaia CLI call, or ``None`` when ``words`` is not one."""
    if not words:
        return None
    if _basename(words[0]) == "gaia":
        return words[1:]
    if _INTERPRETER_RE.match(_basename(words[0])):
        for index, candidate in enumerate(words[1:], start=1):
            if candidate.text.startswith("-"):
                continue
            return words[index + 1:] if _basename(candidate) == "gaia" else None
    return None


def _non_flags(words: Iterable[_Word]) -> List[_Word]:
    return [word for word in words if not word.text.startswith("-") or word.dynamic]


def _code_spells_verb(code: str) -> bool:
    """Whether interpreter code spells a consent verb, even split across literals."""
    squeezed = re.sub(r"['\"\\+\s,]", "", code)
    return any(verb in squeezed for verb in CONSENT_VERBS)


def _read_script(path: str, cwd: Optional[str]) -> Optional[str]:
    resolved = os.path.join(cwd or os.getcwd(), os.path.expanduser(path))
    try:
        # A FIFO or device would block the hook on open, and is no script to read.
        if not os.path.isfile(resolved) or os.path.getsize(resolved) > _MAX_SCRIPT_BYTES:
            return None
        with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _is_reader(words: List[_Word]) -> bool:
    program = _basename(words[0])
    if program in _READERS:
        return True
    if program == "git":
        subcommand = next((w.text for w in words[1:] if not w.text.startswith("-")), "")
        return subcommand in _GIT_READ_SUBCOMMANDS
    return False


def _gaia_call_refused(args: List[_Word], *, verb_from_input: bool) -> bool:
    """A Gaia CLI call whose group or verb is a consent verb or unknown until it runs."""
    positional = _non_flags(args)
    if any(word.dynamic for word in positional[:2]):
        return True
    if verb_from_input and (
        len(positional) < 2
        or not _PLAIN_VERB.match(positional[1].text)
    ):
        return True
    return any(word.text in CONSENT_VERBS for word in positional[:2])


def _command_refused(words: List[_Word], command: _Command, cwd: Optional[str], depth: int) -> bool:
    words = _peel(words)
    if not words:
        return False
    program = _basename(words[0])

    if program == "xargs":
        inner, i = words[1:], 0
        while i < len(inner) and inner[i].text.startswith("-"):
            i += 2 if inner[i].text in _XARGS_VALUE_FLAGS else 1
        inner = _peel(inner[i:])
        gaia_args = _is_gaia(inner)
        if gaia_args is not None and _gaia_call_refused(gaia_args, verb_from_input=True):
            return True
        return bool(inner) and _command_refused(inner, _Command(words=inner), cwd, depth)

    if program == "find":
        for index, word in enumerate(words):
            if word.text in {"-exec", "-execdir", "-ok", "-okdir"}:
                inner = []
                for candidate in words[index + 1:]:
                    if candidate.text in {";", "+"}:
                        break
                    inner.append(candidate)
                if inner and _command_refused(inner, _Command(words=inner), cwd, depth):
                    return True
        return False

    if program == "eval" or program in {"source", "."}:
        if program == "eval":
            payload = " ".join(word.text for word in words[1:])
        else:
            payload = _read_script(words[1].text, cwd) if len(words) > 1 else None
        return payload is not None and _refused(payload, cwd, depth + 1)

    if program in _SHELLS:
        for index, word in enumerate(words[1:-1], start=1):
            if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", word.text):
                return _refused(words[index + 1].text, cwd, depth + 1)
        positional = _non_flags(words[1:])
        program_text = None
        if positional:
            program_text = _read_script(positional[0].text, cwd)
        elif command.heredoc is not None:
            program_text = command.heredoc
        elif command.stdin_file:
            program_text = _read_script(command.stdin_file, cwd)
        return program_text is not None and _refused(program_text, cwd, depth + 1)

    gaia_args = _is_gaia(words)
    if gaia_args is not None:
        return _gaia_call_refused(gaia_args, verb_from_input=False)

    if _INTERPRETER_RE.match(program):
        for index, word in enumerate(words[1:], start=1):
            if word.text in _INTERPRETER_CODE_FLAGS and index + 1 < len(words):
                return _code_spells_verb(words[index + 1].text)
            if word.text == "-m":
                break
            if not word.text.startswith("-"):
                script = _read_script(word.text, cwd)
                return script is not None and _code_spells_verb(script)
        code = command.heredoc
        if code is None and command.stdin_file:
            code = _read_script(command.stdin_file, cwd)
        return code is not None and _code_spells_verb(code)

    if _is_reader(words):
        return False
    arguments = _non_flags(words[1:])
    if words[0].dynamic and arguments and arguments[0].dynamic:
        return True
    for index, word in enumerate(words):
        if word.text in CONSENT_VERBS:
            return True
        if word.text == "approvals":
            following = _non_flags(words[index + 1:])
            if following and following[0].dynamic:
                return True
    return False


def _feeds_a_program_from_input(commands: List[_Command]) -> bool:
    """Whether some command reads the program it runs from its standard input."""
    for command in commands:
        words = _peel(command.words)
        if not words or command.heredoc is not None or command.stdin_file:
            continue
        program = _basename(words[0])
        rest = [w.text for w in words[1:]]
        if program in _SHELLS and (not _non_flags(words[1:]) or "-s" in rest) and not any(
            flag.startswith("-") and "c" in flag[1:] for flag in rest
        ):
            return True
        if _INTERPRETER_RE.match(program) and (not rest or rest[-1] == "-"):
            return True
    return False


def _refused(command: str, cwd: Optional[str], depth: int) -> bool:
    if depth > _MAX_DEPTH:
        return True
    parsed = _parse(command)
    if any(_refused(inner, cwd, depth + 1) for inner in parsed.substitutions):
        return True
    if any(_command_refused(c.words, c, cwd, depth) for c in parsed.commands):
        return True
    return _feeds_a_program_from_input(parsed.commands) and _code_spells_verb(command)


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Refuse a shell command that reaches a host consent verb; allow anything else."""
    if command and _refused(command, cwd, 0):
        return False, REJECTION_MESSAGE
    return True, None
