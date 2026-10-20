"""Recognize reads whose output can carry secrets, and run them with that output redacted.

A Bash result reaches the host whatever the command's exit code, and a hook
cannot rewrite the output of a Bash call that exits non-zero. So the command
itself is rewritten before it runs: this file, executed as a script, runs the
original command, redacts what it printed and exits with the original code.
"""

from __future__ import annotations

import ntpath
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

PROFILE_TEXT = "text"
PROFILE_SECRET_STORE = "secret-store"
PROFILE_SECRET_VALUE = "secret-value"
#: Strongest first: a command is redacted by the strongest profile any of its stages needs.
_PROFILES_BY_STRENGTH = (PROFILE_SECRET_VALUE, PROFILE_SECRET_STORE, PROFILE_TEXT)

_MAX_NESTING = 10
_SUBSTITUTED = "$(..)"
_RESERVED_LEADS = frozenset({"{", "}", "!", "if", "then", "elif", "else", "while", "until", "do"})
_LAUNCHER_VALUE_FLAGS = {
    "command": frozenset(),
    "builtin": frozenset(),
    "setsid": frozenset(),
    "exec": frozenset({"-a"}),
    "flock": frozenset({"-w", "-E", "--timeout", "--wait", "--conflict-exit-code"}),
    "watch": frozenset({"-n", "--interval"}),
    "xargs": frozenset(
        {
            "-a", "-d", "-E", "-e", "-I", "-L", "-l", "-n", "-P", "-s",
            "--arg-file", "--delimiter", "--eof", "--max-args", "--max-lines",
            "--max-procs", "--max-chars",
        }
    ),
}
_LAUNCHER_POSITIONALS = {"flock": 1}
_FIND_EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_KUBECTL_VERBS = frozenset({"get", "describe"})
_KUBECTL_RESOURCES = frozenset(
    {"secret", "secrets", "deployment", "deployments", "deploy", "configmap", "configmaps", "cm"}
)
_KUBECTL_FLAGS_WITH_VALUES = frozenset(
    {
        "--as", "--as-group", "--cluster", "--context", "--field-selector", "--kubeconfig",
        "--namespace", "--output", "--selector", "--server", "--token", "--user",
        "-l", "-n", "-o",
    }
)
_SECRET_STORES = frozenset({"vault", "bao"})
_STORE_KV_READS = frozenset({"get", "read"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
_GIT_FLAGS_WITH_VALUES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_GCLOUD_SECRET_READ = ("secrets", "versions", "access")
_AWS_SECRET_READS = (
    ("secretsmanager", "get-secret-value"),
    ("secretsmanager", "batch-get-secret-value"),
)
_AWS_DECRYPTING_READS = frozenset(
    {"get-parameter", "get-parameters", "get-parameters-by-path", "get-parameter-history"}
)
_SOPS_DECRYPT_FLAGS = frozenset({"-d", "--decrypt"})
_SOPS_DECRYPT_WORDS = frozenset({"decrypt", "exec-env", "exec-file"})


def redaction_profile(command: str) -> str | None:
    """Return how a command's output must be redacted, or None when it reads no secrets."""
    profiles = {_stage_profile(tokens) for tokens in simple_commands(command)}
    return next((profile for profile in _PROFILES_BY_STRENGTH if profile in profiles), None)


def redacted_command(command: str, profile: str) -> str:
    """Wrap a command so it runs unchanged and only its redacted output is printed."""
    return " ".join(
        shlex.quote(part) for part in (sys.executable, str(Path(__file__).resolve()), profile, command)
    )


@dataclass
class _Stage:
    """One simple command as the shell splits it, with the text it hands to other commands."""

    words: list[str] = field(default_factory=list)
    substitutions: list[str] = field(default_factory=list)
    stdin: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.words or self.substitutions or self.stdin)


class _Scanner:
    """Split a command line into stages the way a shell does, quotes included.

    Command substitutions are collected wherever the shell runs them,
    including inside double quotes and in the body of any heredoc, quoted
    or not, because a word-splitting lexer would lose them there.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.stages = [_Stage()]
        self.chars: list[str] = []
        self.has_word = False
        self.target: str | None = None
        self.heredocs: list[tuple[str, bool, _Stage]] = []
        self.strip_tabs = False

    def run(self) -> list[_Stage]:
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            if char in " \t":
                self._end_word()
                self.pos += 1
            elif char == "\n":
                self._end_word()
                self.pos += 1
                self._read_heredocs()
                self._new_stage()
            elif char == "\\":
                self._escape()
            elif char == "'":
                self._single_quoted()
            elif char == '"':
                self._double_quoted()
            elif char in "$`" and self._substitution():
                pass
            elif char in "<>":
                self._redirect()
            elif char in "|;&()":
                self._operator()
            else:
                self._add(char)
                self.pos += 1
        self._end_word()
        return [stage for stage in self.stages if not stage.is_empty()]

    def _add(self, text: str) -> None:
        self.chars.append(text)
        self.has_word = True

    def _end_word(self) -> None:
        if not self.has_word:
            return
        word = "".join(self.chars)
        self.chars, self.has_word = [], False
        stage = self.stages[-1]
        if self.target == "delimiter":
            self.heredocs.append((word, self.strip_tabs, stage))
        elif self.target == "here-string":
            stage.stdin.append(word)
        elif self.target is None:
            stage.words.append(word)
        self.target = None

    def _new_stage(self) -> None:
        self._end_word()
        self.target = None
        if not self.stages[-1].is_empty():
            self.stages.append(_Stage())

    def _escape(self) -> None:
        following = self.text[self.pos + 1 : self.pos + 2]
        if following not in ("", "\n"):
            self._add(following)
        self.pos += 2

    def _single_quoted(self) -> None:
        close = self.text.find("'", self.pos + 1)
        close = len(self.text) if close < 0 else close
        self._add(self.text[self.pos + 1 : close])
        self.pos = close + 1

    def _double_quoted(self) -> None:
        self._add("")
        self.pos += 1
        while self.pos < len(self.text) and self.text[self.pos] != '"':
            if self.text[self.pos] == "\\":
                self._escape()
            elif not (self.text[self.pos] in "$`" and self._substitution()):
                self._add(self.text[self.pos])
                self.pos += 1
        self.pos += 1

    def _substitution(self) -> bool:
        span = _substitution_span(self.text, self.pos)
        if span is None:
            return False
        inner, self.pos = span
        self.stages[-1].substitutions.append(inner)
        self._add(_SUBSTITUTED)
        return True

    def _redirect(self) -> None:
        text, pos = self.text, self.pos
        if text[pos + 1 : pos + 2] == "(":
            close = _closing_paren(text, pos + 1)
            self.stages[-1].substitutions.append(text[pos + 2 : close])
            self._add(_SUBSTITUTED)
            self.pos = close + 1
            return
        if self.has_word and "".join(self.chars).isdigit():
            self.chars, self.has_word = [], False
        self._end_word()
        if text.startswith("<<<", pos):
            self.target, self.pos = "here-string", pos + 3
        elif text.startswith("<<", pos):
            self.strip_tabs = text.startswith("<<-", pos)
            self.target, self.pos = "delimiter", pos + (3 if self.strip_tabs else 2)
        else:
            self.target = "redirect"
            self.pos = pos + 1
            while self.pos < len(text) and text[self.pos] in "<>&|":
                self.pos += 1

    def _operator(self) -> None:
        if self.text[self.pos] == "&" and self.text[self.pos + 1 : self.pos + 2] == ">":
            self._end_word()
            self.pos += 1
            return
        self._new_stage()
        self.pos += 1
        while self.pos < len(self.text) and self.text[self.pos] in "|;&":
            self.pos += 1

    def _read_heredocs(self) -> None:
        text = self.text
        for delimiter, strip_tabs, stage in self.heredocs:
            body: list[str] = []
            while self.pos < len(text):
                end = text.find("\n", self.pos)
                end = len(text) if end < 0 else end
                line = text[self.pos : end]
                self.pos = end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            stage.stdin.append("\n".join(body))
        self.heredocs.clear()


def _substitution_span(text: str, pos: int) -> tuple[str, int] | None:
    """The command text of a ``$(...)`` or backtick substitution starting at ``pos``, and where it ends."""
    if text.startswith("$(", pos):
        close = _closing_paren(text, pos + 1)
        return text[pos + 2 : close], close + 1
    if text[pos] == "`":
        close = pos + 1
        while close < len(text) and text[close] != "`":
            close += 2 if text[close] == "\\" else 1
        close = min(close, len(text))
        return text[pos + 1 : close], close + 1
    return None


def _closing_paren(text: str, open_at: int) -> int:
    """Index of the parenthesis closing the one at ``open_at``, or the text length when unterminated."""
    depth, pos = 0, open_at
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 1
        elif char in "'\"":
            pos = _closing_quote(text, pos)
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    return len(text)


def _closing_quote(text: str, open_at: int) -> int:
    quote, pos = text[open_at], open_at + 1
    while pos < len(text) and text[pos] != quote:
        pos += 2 if quote == '"' and text[pos] == "\\" else 1
    return min(pos, len(text))


def _substitutions_in(text: str) -> list[str]:
    """Command text of every substitution a shell would run while expanding ``text``."""
    found, pos = [], 0
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
            continue
        span = _substitution_span(text, pos)
        if span is None:
            pos += 1
        else:
            found.append(span[0])
            pos = span[1]
    return found


def simple_commands(command: str, _depth: int = 0) -> list[list[str]]:
    """Every simple command a command line can run, however it is wrapped.

    Beyond its own stages this follows substitutions, process substitutions,
    launchers (``env``, ``command``, ``exec``, ``xargs``, ``watch``, ``find -exec``),
    ``sh -c`` and ``eval`` payloads, and the text fed to a shell through a pipe,
    here-string or heredoc, so a read is seen whichever way it is spelled.
    """
    if _depth > _MAX_NESTING:
        return []
    stages = _Scanner(command).run()
    found: list[list[str]] = []
    for index, stage in enumerate(stages):
        for nested in stage.substitutions:
            found += simple_commands(nested, _depth + 1)
        found += _expanded(stage, stages[index - 1] if index else None, _depth)
    return found


def _expanded(stage: _Stage, previous: _Stage | None, depth: int) -> list[list[str]]:
    from modules.security.shell_unwrapper import _SHELLS, shell_command_string

    leading = _after_reserved(stage.words)
    payloads = _env_split_strings(leading)
    words, launched = _unwrapped(leading)
    found = [words] if words else []
    program = os.path.basename(words[0]).lower() if words else ""
    if launched and len(words) == 1 and " " in words[0]:
        payloads.append(words[0])
    if program in _SHELLS:
        payload = shell_command_string(shlex.join(words))
        if payload is not None:
            payloads.append(payload)
        elif not [word for word in words[1:] if not word.startswith("-")] or "-s" in words[1:]:
            payloads += stage.stdin
            if previous is not None:
                payloads += [" ".join(previous.words[1:]), *(w for w in previous.words[1:] if " " in w)]
    else:
        payloads += (inner for text in stage.stdin for inner in _substitutions_in(text))
        if program == "eval":
            payloads.append(" ".join(words[1:]))
        if program == "find":
            found += _find_exec_commands(words, depth)
        if program == "script":
            payloads += _option_values(words[1:], "c", "--command")
    for payload in payloads:
        found += simple_commands(payload, depth + 1)
    return found


def _after_reserved(words: list[str]) -> list[str]:
    start = 0
    while start < len(words) and words[start] in _RESERVED_LEADS:
        start += 1
    return words[start:]


def _find_exec_commands(words: list[str], depth: int) -> list[list[str]]:
    found: list[list[str]] = []
    index = 0
    while index < len(words):
        if words[index] in _FIND_EXEC_FLAGS:
            end = next((i for i in range(index + 1, len(words)) if words[i] in {";", "+"}), len(words))
            found += _expanded(_Stage(words=words[index + 1 : end]), None, depth + 1)
            index = end
        index += 1
    return found


def _unwrapped(words: list[str]) -> tuple[list[str], bool]:
    """Peel the wrappers and launchers in front of a read, and say whether a launcher was among them."""
    from modules.security.mutative_verbs import _peel_leading_command_wrappers

    launched = False
    while words:
        remainder, _ = _peel_leading_command_wrappers(shlex.join(words))
        try:
            words = shlex.split(remainder)
        except ValueError:
            return words, launched
        value_flags = _LAUNCHER_VALUE_FLAGS.get(os.path.basename(words[0]).lower()) if words else None
        if value_flags is None:
            return words, launched
        words, launched = _after_launcher(words, value_flags), True
    return words, launched


def _after_launcher(words: list[str], value_flags: frozenset[str]) -> list[str]:
    args = words[1:]
    start = _launcher_flags_end(args, value_flags)
    for _ in range(_LAUNCHER_POSITIONALS.get(os.path.basename(words[0]).lower(), 0)):
        start += 1
        start += _launcher_flags_end(args[start:], value_flags)
    return args[start:]


def _option_values(args: list[str], short: str, long: str) -> list[str]:
    """Values given to an option, in ``-c cmd``, ``-qc cmd``, ``-ccmd``, ``--long cmd`` or ``--long=cmd`` form."""
    values: list[str] = []
    for index, arg in enumerate(args):
        following = args[index + 1 : index + 2]
        if arg == long:
            values += following
        elif arg.startswith(long + "="):
            values.append(arg.split("=", 1)[1])
        elif arg.startswith("-") and not arg.startswith("--") and short in arg[1:]:
            tail = arg[arg.index(short) + 1 :]
            values += [tail] if tail else following
    return values


def _env_split_strings(words: list[str]) -> list[str]:
    """Command strings ``env -S`` splits and runs, also behind other wrappers such as ``nice``."""
    from modules.security.mutative_verbs import _COMMAND_WRAPPERS

    for index, word in enumerate(words):
        if os.path.basename(word) == "env":
            options = words[index + 1 :]
            end = 0
            while end < len(options):
                if options[end] in {"-S", "--split-string", "-u", "-C", "--unset", "--chdir"}:
                    end += 2
                elif options[end].startswith("-") or "=" in options[end]:
                    end += 1
                else:
                    break
            return _option_values(options[:end], "S", "--split-string")
        if word not in _COMMAND_WRAPPERS and not word.startswith("-") and not word.isdigit():
            return []
    return []


def _launcher_flags_end(args: list[str], value_flags: frozenset[str]) -> int:
    index = 0
    while index < len(args):
        if args[index] == "--":
            return index + 1
        if not args[index].startswith("-") or args[index] == "-":
            return index
        index += 2 if args[index] in value_flags else 1
    return index


def _stage_profile(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    program = os.path.basename(tokens[0]).lower()
    args = tokens[1:]
    if program == "kubectl" and _kubectl_reads_values(args):
        return PROFILE_TEXT
    if program in _SECRET_STORES and _store_reads_secret(args):
        return PROFILE_SECRET_STORE
    if _prints_secret_value(program, args):
        return PROFILE_SECRET_VALUE
    if program == "rg" or (program in _GREPS and _grep_is_recursive(args)):
        return PROFILE_TEXT
    if program == "git" and _positionals(args, _GIT_FLAGS_WITH_VALUES)[:1] == ["grep"]:
        return PROFILE_TEXT
    return None


def _kubectl_reads_values(args: list[str]) -> bool:
    positionals = _positionals(args, _KUBECTL_FLAGS_WITH_VALUES)
    if len(positionals) < 2 or positionals[0].lower() not in _KUBECTL_VERBS:
        return False
    kinds = (part.split("/", 1)[0].split(".", 1)[0].lower() for part in positionals[1].split(","))
    return any(kind in _KUBECTL_RESOURCES for kind in kinds)


def _store_reads_secret(args: list[str]) -> bool:
    positionals = [token.lower() for token in _positionals(args)]
    if positionals[:1] == ["read"]:
        return True
    return positionals[:1] == ["kv"] and len(positionals) > 1 and positionals[1] in _STORE_KV_READS


def _prints_secret_value(program: str, args: list[str]) -> bool:
    """Whether a cloud secret manager or sops read prints a decrypted secret value.

    The subcommand words are searched as a run anywhere among the positionals,
    so a global flag's value before them (``--project prod``) does not hide them.
    """
    words = [token.lower() for token in _positionals(args)]
    if program == "gcloud":
        return _has_run(words, _GCLOUD_SECRET_READ)
    if program == "aws":
        decrypting = "--with-decryption" in args and any(
            _has_run(words, ("ssm", verb)) for verb in _AWS_DECRYPTING_READS
        )
        return decrypting or any(_has_run(words, read) for read in _AWS_SECRET_READS)
    if program == "sops":
        return bool(_SOPS_DECRYPT_FLAGS.intersection(args)) or bool(_SOPS_DECRYPT_WORDS.intersection(words))
    return False


def _has_run(words: list[str], run: tuple[str, ...]) -> bool:
    return any(tuple(words[start : start + len(run)]) == run for start in range(len(words)))


def _grep_is_recursive(args: list[str]) -> bool:
    for token in args:
        if token in {"--recursive", "--dereference-recursive"}:
            return True
        if token.startswith("-") and not token.startswith("--") and token[1:].isalpha():
            if "r" in token or "R" in token:
                return True
    return False


def _positionals(args: list[str], flags_with_values: frozenset[str] = frozenset()) -> list[str]:
    positionals: list[str] = []
    skip = False
    for token in args:
        if skip:
            skip = False
        elif token in flags_with_values:
            skip = True
        elif not token.startswith("-"):
            positionals.append(token)
    return positionals


def _bash_program(windows: bool, environ: Mapping[str, str]) -> str | None:
    """The bash the command was written for, or None; on Windows never the WSL launcher.

    CreateProcess searches the system directory before PATH, so a bare
    ``bash`` resolves to System32's ``bash.exe``, which starts WSL.
    """
    if not windows:
        return "bash"
    configured = environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if configured:
        return configured
    system_root = ntpath.normcase(environ.get("SystemRoot", r"C:\Windows")).rstrip("\\") + "\\"
    outside = [
        entry for entry in environ.get("PATH", "").split(";")
        if entry and not (ntpath.normcase(entry).rstrip("\\") + "\\").startswith(system_root)
    ]
    return shutil.which("bash", path=";".join(outside))


def _run(profile: str, command: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gaia.redaction import redact_secret_store_output, redact_secret_value_output, redact_text

    redact = {
        PROFILE_SECRET_VALUE: redact_secret_value_output,
        PROFILE_SECRET_STORE: redact_secret_store_output,
    }.get(profile, redact_text)
    bash = _bash_program(os.name == "nt", os.environ)
    if bash is None:
        sys.stderr.write("secret_reads: no Git Bash found; set CLAUDE_CODE_GIT_BASH_PATH to its bash.exe\n")
        return 127
    completed = subprocess.run([bash, "-c", command], capture_output=True)
    for stream, data in ((sys.stdout, completed.stdout), (sys.stderr, completed.stderr)):
        stream.write(redact(data.decode("utf-8", errors="replace")))
        stream.flush()
    code = completed.returncode
    return 128 - code if code < 0 else code


if __name__ == "__main__":
    sys.exit(_run(sys.argv[1], sys.argv[2]))
