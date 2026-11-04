"""
Flag-dependent command classifiers (git, sed, awk, tar, find, curl, wget, httpie).

This module runs in the classify phase BEFORE detect_mutative_command().  When a
classifier returns a FlagClassifierResult, it overrides verb-based classification.
When it returns None, the caller falls through to the existing mutative_verbs pipeline.

Classification outcomes:
  READ_ONLY  -- safe by elimination, no approval required
  MUTATIVE   -- state-modifying, requires user approval (T3)
  BLOCKED    -- permanently blocked (exit 2), as a categorical flag-classifier denial

Note on BLOCKED git verdicts:
  blocked_commands.py has no git floor entry: a force push signs as
  sign.destroys, and git reset --hard is T3-approvable (see
  test_git_reset_hard_is_t3_approvable). bash_validator.py runs
  detect_mutative_command() before classify_by_flags() and leaves git out of
  the flag classifier's MUTATIVE path, so the BLOCKED verdicts of the git
  classifiers below decide a live tier only for a git form mutative_verbs
  does not already sign.

Dependencies: Python stdlib only.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .mutative_verbs import LocalWorkCaller
    from .program_heredoc import HeredocProgram


OUTCOME_READ_ONLY = "READ_ONLY"
OUTCOME_MUTATIVE = "MUTATIVE"
OUTCOME_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class FlagClassifierResult:
    """Structured result of flag-dependent classification.

    Attributes:
        outcome: One of OUTCOME_READ_ONLY, OUTCOME_MUTATIVE, or OUTCOME_BLOCKED.
        reason: Human-readable explanation.
        matched_pattern: The specific flag or pattern that triggered this
            classification (e.g. "--force", "-i", "-exec").
        command_family: The command family that handled classification
            (e.g. "git_push", "sed", "curl").
    """
    outcome: str
    reason: str
    matched_pattern: str
    command_family: str

    @property
    def is_blocked(self) -> bool:
        return self.outcome == OUTCOME_BLOCKED

    @property
    def is_mutative(self) -> bool:
        return self.outcome in (OUTCOME_MUTATIVE, OUTCOME_BLOCKED)

    @property
    def is_read_only(self) -> bool:
        return self.outcome == OUTCOME_READ_ONLY


def _tokenize(command: str) -> List[str]:
    """Tokenize a shell command using shlex, fallback to split on error."""
    if not command or not command.strip():
        return []
    try:
        return shlex.split(command.strip())
    except ValueError:
        return command.strip().split()


def _has_flag(args: List[str], *flags: str) -> Optional[str]:
    """Return the first matching flag found in args, or None."""
    flag_set = set(flags)
    for a in args:
        if a in flag_set:
            return a
    return None


def _has_short_flag(args: List[str], letter: str) -> bool:
    """Return True if args contain a bundled or standalone short flag.

    Handles both standalone ("-f") and clustered ("-xf", "-fx") forms.
    """
    needle = f"-{letter}"
    for a in args:
        if a == needle:
            return True
        if len(a) >= 2 and a[0] == "-" and a[1] != "-":
            if letter in a[1:]:
                return True
    return False


def _classify_git_push(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "push":
        return None
    args = tokens[2:]

    force_flag = _has_flag(args, "--force", "--mirror", "--prune", "--delete", "-d")
    if force_flag:
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason=f"git push {force_flag} rewrites/destroys remote history; use --force-with-lease",
            matched_pattern=force_flag,
            command_family="git_push",
        )
    if _has_short_flag(args, "f"):
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason="git push -f rewrites remote history; use --force-with-lease",
            matched_pattern="-f",
            command_family="git_push",
        )
    for a in args:
        if (a.startswith("+") or a.startswith(":")) and len(a) > 1:
            return FlagClassifierResult(
                outcome=OUTCOME_BLOCKED,
                reason=f"git push {a!r} force-pushes or deletes a remote ref",
                matched_pattern=a,
                command_family="git_push",
            )

    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git push modifies the remote repository",
        matched_pattern="git push",
        command_family="git_push",
    )


def _classify_git_reset(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "reset":
        return None
    args = tokens[2:]

    if "--hard" in args:
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason="git reset --hard permanently discards uncommitted changes",
            matched_pattern="--hard",
            command_family="git_reset",
        )
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git reset modifies HEAD or the index",
        matched_pattern="git reset",
        command_family="git_reset",
    )


def _classify_git_checkout(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "checkout":
        return None
    args = tokens[2:]

    _DISCARD_FLAGS = {".", "--", "HEAD", "--force", "-f", "--ours", "--theirs"}
    flag = _has_flag(args, *_DISCARD_FLAGS)
    if flag:
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason=f"git checkout {flag} discards uncommitted changes",
            matched_pattern=flag,
            command_family="git_checkout",
        )
    if _has_short_flag(args, "f"):
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason="git checkout -f discards uncommitted changes",
            matched_pattern="-f",
            command_family="git_checkout",
        )

    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git checkout switches branches or restores files",
        matched_pattern="git checkout",
        command_family="git_checkout",
    )


def _classify_git_stash(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "stash":
        return None
    args = tokens[2:]
    sub = args[0].lower() if args else "push"

    if sub in ("drop", "clear"):
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason=f"git stash {sub} permanently removes stashed changes",
            matched_pattern=sub,
            command_family="git_stash",
        )
    if sub in ("list", "show"):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason=f"git stash {sub} is read-only",
            matched_pattern=sub,
            command_family="git_stash",
        )
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason=f"git stash {sub} modifies the stash or working tree",
        matched_pattern=sub,
        command_family="git_stash",
    )


def _classify_git_rebase(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "rebase":
        return None
    args = tokens[2:]

    if "--abort" in args:
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="git rebase --abort cancels in-progress rebase without modifying history",
            matched_pattern="--abort",
            command_family="git_rebase",
        )
    if "--continue" in args or "--skip" in args:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason="git rebase --continue/--skip advances an in-progress rebase",
            matched_pattern="--continue" if "--continue" in args else "--skip",
            command_family="git_rebase",
        )
    if "-i" in args or "--interactive" in args:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason="git rebase -i rewrites commit history interactively",
            matched_pattern="-i" if "-i" in args else "--interactive",
            command_family="git_rebase",
        )
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git rebase rewrites commit history",
        matched_pattern="git rebase",
        command_family="git_rebase",
    )


def _classify_git_tag(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "tag":
        return None
    args = tokens[2:]

    if not args:
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="git tag with no arguments lists tags",
            matched_pattern="git tag",
            command_family="git_tag",
        )

    has_force = "--force" in args or _has_short_flag(args, "f")
    has_delete = "--delete" in args or _has_short_flag(args, "d")
    if has_force:
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason="git tag --force rewrites an existing tag",
            matched_pattern="--force",
            command_family="git_tag",
        )
    if has_delete:
        return FlagClassifierResult(
            outcome=OUTCOME_BLOCKED,
            reason="git tag --delete removes a tag",
            matched_pattern="--delete",
            command_family="git_tag",
        )

    _LIST_FLAGS = {"-l", "--list", "-v", "--verify", "--contains", "--no-contains",
                   "--merged", "--no-merged", "--points-at"}
    if any(a in _LIST_FLAGS or a.startswith("-n") for a in args):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="git tag with listing/verification flags is read-only",
            matched_pattern=next(
                (a for a in args if a in _LIST_FLAGS or a.startswith("-n")), "-l"
            ),
            command_family="git_tag",
        )

    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git tag creates a new tag",
        matched_pattern="git tag",
        command_family="git_tag",
    )


def _classify_git_clean(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "clean":
        return None
    args = tokens[2:]

    if "--dry-run" in args or _has_short_flag(args, "n"):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="git clean --dry-run/-n shows what would be removed without deleting",
            matched_pattern="--dry-run" if "--dry-run" in args else "-n",
            command_family="git_clean",
        )

    return FlagClassifierResult(
        outcome=OUTCOME_BLOCKED,
        reason="git clean permanently deletes untracked files; use --dry-run first",
        matched_pattern="git clean",
        command_family="git_clean",
    )


def _classify_git_remote(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "remote":
        return None
    args = tokens[2:]
    sub = args[0].lower() if args else ""

    if sub in ("remove", "rm", "rename", "set-url", "set-head", "set-branches"):
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"git remote {sub} modifies remote configuration",
            matched_pattern=sub,
            command_family="git_remote",
        )
    if sub in ("show", "get-url", ""):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason=f"git remote {sub or '(list)'} is read-only",
            matched_pattern=sub or "git remote",
            command_family="git_remote",
        )
    if sub == "add":
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason="git remote add registers a new remote",
            matched_pattern="add",
            command_family="git_remote",
        )
    if sub in ("-v", "--verbose"):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="git remote -v lists remotes",
            matched_pattern=sub,
            command_family="git_remote",
        )

    return None


def _classify_sed(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "sed":
        return None
    args = tokens[1:]

    flag = _has_flag(args, "-i", "-I", "--in-place")
    if flag:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"sed {flag} edits files in-place",
            matched_pattern=flag,
            command_family="sed",
        )
    for a in args:
        if a.startswith("-i") and a != "-i" and not a.startswith("--"):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"sed {a} edits files in-place (with backup suffix)",
                matched_pattern=a,
                command_family="sed",
            )
        if len(a) >= 2 and a[0] == "-" and a[1] != "-" and "i" in a[1:]:
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"sed {a} contains -i (in-place editing)",
                matched_pattern=a,
                command_family="sed",
            )

    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="sed without -i writes to stdout, does not modify files",
        matched_pattern="sed",
        command_family="sed",
    )


_AWK_EFFECT_TOKENS = (">", "|", "system", "@", "/inet")
AWK_PROGRAMS = frozenset({
    "awk", "gawk", "mawk", "nawk", "goawk", "original-awk", "onetrue-awk", "bwk-awk",
})
_AWK_NAMES_GAWK_CAN_HOLD = frozenset({"awk", "gawk", "nawk"})
_AWK_STDIN_PATHS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
_AWK_STDIN_PROGRAM_OPTIONS = frozenset({"f", "E", "i"})
_AWK_VALUE_LETTERS = frozenset("FvfEeilW")
_AWK_ATTACHED_VALUE_LETTERS = frozenset("dDLopm")
_AWK_LONG_OPTIONS = {
    "--assign": "v", "--bignum": "M", "--characters-as-bytes": "b", "--copyright": "C", "--csv": "k",
    "--debug": "D", "--dump-variables": "d", "--exec": "E", "--field-separator": "F", "--file": "f",
    "--gen-pot": "g", "--help": "h", "--include": "i", "--lint": "L", "--lint-old": "t", "--load": "l",
    "--no-optimize": "s", "--non-decimal-data": "n", "--optimize": "O", "--posix": "P",
    "--pretty-print": "o", "--profile": "p", "--re-interval": "r", "--sandbox": "S", "--source": "e",
    "--trace": "I", "--traditional": "c", "--use-lc-numeric": "N", "--version": "V",
}
_AWK_UNPROVEN_OPTIONS = {
    "d": "writes a variable dump file", "o": "writes a pretty-printed program file",
    "p": "writes a profile file", "D": "runs the gawk debugger, whose commands can run anything",
    "W": "takes a -W option the classifier does not model", "?": "takes a long option gawk would not resolve",
    "i": "includes a source file whose code is not proven read-only",
    "l": "loads an extension whose code is not proven read-only",
    "E": "runs a program file through -E, which is not proven read-only",
}
_UNPROVEN_WORD = "\0"
_SHELL_COMPUTED = re.compile(r"[$`\\*?\[{()]")
_STDIN_REDIRECT = re.compile(r"(\d*)(<<<|<<-?|<&|<>|<)(.*)", re.S)
_PRINTF_PIECES = re.compile(r"(%%|%s)")


def _awk_long_option(name: str) -> str:
    """The letter of the long option getopt_long resolves *name* to, exact or unique prefix; "?" when it would not."""
    if name in _AWK_LONG_OPTIONS:
        return _AWK_LONG_OPTIONS[name]
    candidates = [letter for option, letter in _AWK_LONG_OPTIONS.items() if option.startswith(name)]
    return candidates[0] if len(candidates) == 1 else "?"


def _awk_options(args: List[str]) -> Tuple[List[Tuple[str, str]], List[str]]:
    options: List[Tuple[str, str]] = []
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            letter = _awk_long_option(name)
            if letter in _AWK_VALUE_LETTERS and not eq and i < len(args):
                value, i = args[i], i + 1
            options.append((letter, value))
        elif a.startswith("-") and len(a) > 1:
            for j, letter in enumerate(a[1:], 2):
                if letter in _AWK_VALUE_LETTERS or letter in _AWK_ATTACHED_VALUE_LETTERS:
                    value = a[j:]
                    if not value and letter in _AWK_VALUE_LETTERS and i < len(args):
                        value, i = args[i], i + 1
                    options.append((letter, value))
                    break
                options.append((letter, ""))
        else:
            i -= 1
            break
    return options, args[i:]


def awk_reads_program_from_stdin(args: List[str]) -> bool:
    """True when awk *args* (program name excluded) take a program or include file from stdin."""
    from ..tools.shell_parser import drop_redirections

    options, _ = _awk_options(drop_redirections(args))
    return any(option in _AWK_STDIN_PROGRAM_OPTIONS and value in _AWK_STDIN_PATHS for option, value in options)


def _tilde_expands(line: str, value: str) -> Optional[bool]:
    """Whether bash expands the leading ``~`` of program path *value*; None when *line* cannot be read word by word.

    Bash expands it only when a word starts with it and nothing before its first slash is quoted.
    """
    if not value.startswith("~"):
        return False
    words = _raw_words(line)
    if words is None:
        return None
    return any(
        word.startswith("~") and not set(word.split("/", 1)[0]) & set("'\"") and _literal_word(word) == value
        for word in words
    )


def _awk_program_file(
    path: str, caller: "Optional[LocalWorkCaller]", expand_tilde: bool = True,
) -> Optional[str]:
    """Return the text of the program file awk would read, or None when that file cannot be proven and read whole.

    A path under /dev or /proc names one of the hook's own descriptors, not the shell's.
    """
    import os
    from .mutative_verbs import _MAX_SCRIPT_READ_BYTES, _read_script_content

    expanded = os.path.expanduser(path) if expand_tilde else path
    cwd = caller.cwd if caller is not None else None
    if not os.path.isabs(expanded) and cwd is None:
        return None
    absolute = os.path.normpath(os.path.join(cwd or "/", expanded))
    if any(p.startswith(("/dev/", "/proc/")) for p in (absolute, os.path.realpath(absolute))):
        return None
    text = _read_script_content(absolute, cwd)
    if text is None or len(text) >= _MAX_SCRIPT_READ_BYTES:
        return None
    return text


def _unquoted_index(word: str, chars: str) -> int:
    quote = ""
    for index, char in enumerate(word):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in chars:
            return index
    return -1


def _raw_words(text: str) -> Optional[List[str]]:
    words, current, quote, escaped = [], "", "", False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in " \t":
            if current:
                words.append(current)
            current = ""
            continue
        current += char
    if quote or escaped:
        return None
    return words + [current] if current else words


def _outside_single_quotes(word: str) -> str:
    kept, quote = [], ""
    for char in word:
        if quote == "'" or (quote and char == quote):
            quote = "" if char == quote else quote
        elif not quote and char in "'\"":
            quote = char
        else:
            kept.append(char)
    return "".join(kept)


def _literal_word(word: str) -> Optional[str]:
    """The value bash gives raw *word*, or None when expansion, escaping or globbing could make it differ."""
    from ..tools.shell_parser import shell_split

    if _SHELL_COMPUTED.search(_outside_single_quotes(word)) or word.startswith(("'~", '"~')):
        return None
    try:
        values = shell_split(word)
    except ValueError:
        return None
    return values[0] if len(values) == 1 else None


def _stdin_redirects(words: List[str]) -> Optional[List[Tuple[str, str]]]:
    redirects: List[Tuple[str, str]] = []
    index = 0
    while index < len(words):
        word = words[index]
        index += 1
        at = _unquoted_index(word, "<")
        if at == -1:
            continue
        match = _STDIN_REDIRECT.fullmatch(word)
        if match is None or at != len(match.group(1)):
            return None
        fd, operator, target = match.groups()
        if fd not in ("", "0"):
            continue
        if operator not in ("<", "<<<"):
            return None
        if not target:
            if index >= len(words):
                return None
            target, index = words[index], index + 1
        value = _literal_word(target)
        if value is None:
            return None
        redirects.append((operator, value))
    return redirects


def _printf_output(form: str, args: List[str]) -> Optional[str]:
    pieces = [piece for piece in _PRINTF_PIECES.split(form) if piece]
    if "\\" in form or any(piece not in ("%s", "%%") and "%" in piece for piece in pieces):
        return None
    slots = pieces.count("%s")
    out: List[str] = []
    remaining = list(args)
    while True:
        for piece in pieces:
            out.append((remaining.pop(0) if remaining else "") if piece == "%s" else piece.replace("%%", "%"))
        if not slots or not remaining:
            return "".join(out)


def _piped_program(stage: str, caller: "Optional[LocalWorkCaller]") -> Optional[str]:
    """What *stage* writes when its text proves it: literal echo or printf no escape can rewrite, or cat of readable files."""
    words = _raw_words(stage)
    if not words or any(_unquoted_index(word, "<>&|;") != -1 for word in words):
        return None
    values = [_literal_word(word) for word in words]
    if any(value is None for value in values):
        return None
    head, args = values[0], values[1:]
    if head == "echo":
        flags = ""
        while args and re.fullmatch(r"-[neE]+", args[0]):
            flags += args.pop(0)
        if "e" in flags or any("\\" in arg for arg in args):
            return None
        return " ".join(args) + ("" if "n" in flags else "\n")
    if head == "printf" and args and not args[0].startswith("-"):
        return _printf_output(args[0], args[1:])
    if head == "cat" and args and not any(arg.startswith("-") for arg in args):
        texts = [_awk_program_file(arg, caller) for arg in args]
        return None if any(text is None for text in texts) else "".join(texts)
    return None


def _awk_stdin_program(
    command: str, heredoc: "Optional[HeredocProgram]", caller: "Optional[LocalWorkCaller]", piped_from: Optional[str],
) -> Optional[str]:
    """The program awk reads from stdin, when exactly one source the command shows proves it; else None."""
    if heredoc is not None:
        return None if heredoc.expands or piped_from is not None else heredoc.body + "\n"
    words = _raw_words(command)
    redirects = _stdin_redirects(words) if words is not None and "\n" not in command else None
    if redirects is None or len(redirects) + (piped_from is not None) != 1:
        return None
    if piped_from is not None:
        return _piped_program(piped_from, caller)
    operator, value = redirects[0]
    return value + "\n" if operator == "<<<" else _awk_program_file(value, caller)


def _awk_signs(reason: str, matched_pattern: str) -> FlagClassifierResult:
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE, reason=reason, matched_pattern=matched_pattern, command_family="awk",
    )


def _classify_awk(
    command: str, caller: "Optional[LocalWorkCaller]" = None, piped_from: Optional[str] = None,
) -> Optional[FlagClassifierResult]:
    """awk runs free only when every program text is proven verbatim and holds none of _AWK_EFFECT_TOKENS."""
    from ..tools.shell_parser import drop_redirections
    from .program_heredoc import heredoc_program

    heredoc = heredoc_program(command)
    if heredoc is not None and heredoc.interpreter not in AWK_PROGRAMS:
        heredoc = None
    declaring_line = command.split("\n", 1)[0] if heredoc is not None else command
    tokens = drop_redirections(_tokenize(declaring_line))
    if not tokens or posixpath.basename(tokens[0]) not in AWK_PROGRAMS:
        return None
    words = _raw_words(declaring_line)
    if words is None:
        return _awk_signs("awk line cannot be read word by word, so its program is unproven", "awk")

    values = [_literal_word(word) for word in drop_redirections(words)[1:]]
    options, operands = _awk_options([_UNPROVEN_WORD if value is None else value for value in values])
    for option, _ in options:
        if option in _AWK_UNPROVEN_OPTIONS:
            return _awk_signs(f"awk -{option} {_AWK_UNPROVEN_OPTIONS[option]}", f"-{option}")

    programs = [value for option, value in options if option == "e"]
    reads_stdin = False
    for option, value in options:
        if option != "f":
            continue
        if value in _AWK_STDIN_PATHS:
            reads_stdin = True
            continue
        if "/" not in value and posixpath.basename(tokens[0]) in _AWK_NAMES_GAWK_CAN_HOLD:
            return _awk_signs(f"gawk looks awk program file {value!r} up through AWKPATH, unproven", f"-f {value}")
        expands = None if value == _UNPROVEN_WORD else _tilde_expands(declaring_line, value)
        text = None if expands is None else _awk_program_file(value, caller, expands)
        if text is None:
            return _awk_signs(f"awk program file {value!r} cannot be read, so its effect is unproven", f"-f {value}")
        programs.append(text)
    if reads_stdin:
        text = _awk_stdin_program(command, heredoc, caller, piped_from)
        if text is None:
            return _awk_signs(
                "awk reads its program from stdin, and no single literal or readable source on the line proves it",
                "stdin program",
            )
        programs.append(text)
    if not programs:
        programs = operands[:1]

    for program in programs:
        if program == _UNPROVEN_WORD:
            return _awk_signs("awk program text is not proven verbatim by the command line", "unproven program")
        token = next((token for token in _AWK_EFFECT_TOKENS if token in program), None)
        if token is not None:
            return _awk_signs(f"awk program contains effect-capable token {token!r}", token)
    if programs:
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="awk program is proven verbatim and holds no effect-capable token",
            matched_pattern="awk",
            command_family="awk",
        )
    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="awk with no identifiable program text is read-only",
        matched_pattern="awk",
        command_family="awk",
    )


_TAR_LOCAL_WORK_LETTERS = frozenset("xvzjJfCkpmoO")
_TAR_LOCAL_WORK_SWITCHES = frozenset({
    "--extract", "--get", "--verbose", "--gzip", "--bzip2", "--xz", "--keep-old-files",
    "--no-same-owner", "--no-same-permissions", "--touch", "--to-stdout",
})
_TAR_VALUE_LETTERS = {"f": "--file", "C": "--directory"}
_TAR_MEMBER_BUDGET = 20000


def _tar_extract_operands(tokens: List[str]) -> Optional[dict]:
    """Parse a getopt-style extraction into its options, or None for anything outside the modelled grammar."""
    args = list(tokens[1:])
    if args and not args[0].startswith("-") and args[0].isalpha():
        args[0] = "-" + args[0]
    seen: dict = {"members_before_directory": False}
    i = 0
    while i < len(args):
        token = args[i]
        i += 1
        if token.startswith("--"):
            name, eq, value = token.partition("=")
            if name in _TAR_LOCAL_WORK_SWITCHES and not eq:
                seen[name] = True
            elif name in ("--file", "--directory", "--one-top-level", "--strip-components") and (eq or i < len(args)):
                if not eq:
                    if name == "--one-top-level":
                        return None
                    value, i = args[i], i + 1
                if name in seen:
                    return None
                seen[name] = value
            else:
                return None
        elif token.startswith("-") and len(token) > 1:
            j = 1
            while j < len(token):
                letter = token[j]
                j += 1
                if letter not in _TAR_LOCAL_WORK_LETTERS:
                    return None
                if letter in _TAR_VALUE_LETTERS:
                    name = _TAR_VALUE_LETTERS[letter]
                    if j < len(token):
                        value, j = token[j:], len(token)
                    elif i < len(args):
                        value, i = args[i], i + 1
                    else:
                        return None
                    if name in seen:
                        return None
                    seen[name] = value
                else:
                    seen[{"x": "--extract", "O": "--to-stdout"}.get(letter, letter)] = True
        elif "--directory" not in seen:
            seen["members_before_directory"] = True
    return seen


def _tar_member_paths(archive: str, strip: int) -> Optional[Tuple[bool, List[str]]]:
    """Return (every member is a plain relative path, their stripped paths), or None when Python cannot list it within budget."""
    import tarfile
    paths = []
    try:
        with tarfile.open(archive, "r:*") as handle:
            for count, member in enumerate(handle):
                if count >= _TAR_MEMBER_BUDGET:
                    return None
                parts = [p for p in member.name.split("/") if p not in ("", ".")]
                if member.issym() or member.islnk() or member.name.startswith("/") or ".." in parts:
                    return False, []
                if parts[strip:]:
                    paths.append("/".join(parts[strip:]))
    except Exception:
        return None
    return True, paths


def _tar_extracts_only_into_local_work(tokens: List[str], caller: "Optional[LocalWorkCaller]") -> bool:
    """True when every member lands in the caller's scratch entry or a managed worktree; an unreadable archive only into its own entry."""
    import os
    from .mutative_verbs import (
        _local_work_root, _resolve_local_work_path, _scratch_entry, _shares_inode, _tree_shares_inode,
    )

    seen = _tar_extract_operands(tokens)
    if caller is None or seen is None or not seen.get("--extract") and not seen.get("--get"):
        return False
    archive = seen.get("--file")
    if not archive or archive == "-" or _resolve_local_work_path(archive, None) is None:
        return False
    if seen.get("--to-stdout"):
        return True
    directory, top_level = seen.get("--directory"), seen.get("--one-top-level")
    strip = seen.get("--strip-components", "0")
    if directory is None or seen["members_before_directory"] or not str(strip).isdigit():
        return False
    target = _resolve_local_work_path(os.path.join(directory, top_level) if top_level else directory, None)
    root = None if target is None else _local_work_root(target, caller, allow_top=True)
    if root is None:
        return False
    real_archive = _resolve_local_work_path(archive, None)
    listing = _tar_member_paths(real_archive, int(strip))
    if listing is None:
        return _scratch_entry(real_archive) == root and not _tree_shares_inode(target)
    plain, members = listing
    if not plain:
        return False
    landed = [os.path.realpath(os.path.join(target, m)) for m in members]
    return all(_local_work_root(path, caller) is not None and not _shares_inode(path) for path in landed)


def _classify_tar(tokens: List[str], caller: "Optional[LocalWorkCaller]" = None) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "tar":
        return None
    args = tokens[1:]

    if _tar_extracts_only_into_local_work(tokens, caller):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="tar extraction writing only inside the Gaia scratch directory or a Gaia-managed worktree",
            matched_pattern="tar-local-work-extract",
            command_family="tar",
        )

    long_mutative = _has_flag(args, "--create", "--extract", "--append", "--update",
                               "--concatenate", "--delete")
    if long_mutative:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"tar {long_mutative} creates or modifies an archive",
            matched_pattern=long_mutative,
            command_family="tar",
        )
    if _has_flag(args, "--list"):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="tar --list reads archive contents without extracting",
            matched_pattern="--list",
            command_family="tar",
        )

    for a in args:
        if len(a) >= 2 and a[0] == "-" and a[1] != "-":
            letters = a[1:]
            if any(c in letters for c in "cxrua"):
                return FlagClassifierResult(
                    outcome=OUTCOME_MUTATIVE,
                    reason=f"tar {a} creates or modifies an archive",
                    matched_pattern=a,
                    command_family="tar",
                )
            if "t" in letters:
                return FlagClassifierResult(
                    outcome=OUTCOME_READ_ONLY,
                    reason=f"tar {a} lists archive contents without extracting",
                    matched_pattern=a,
                    command_family="tar",
                )
        # tar also takes its operation letters without a dash: tar czf out.tar dir
        if not a.startswith("-") and len(a) >= 1 and a[0] in "cxtrua":
            if any(c in a for c in "cxrua"):
                return FlagClassifierResult(
                    outcome=OUTCOME_MUTATIVE,
                    reason=f"tar operation '{a[0]}' creates or modifies an archive",
                    matched_pattern=a[0],
                    command_family="tar",
                )
            if "t" in a:
                return FlagClassifierResult(
                    outcome=OUTCOME_READ_ONLY,
                    reason="tar operation 't' lists archive contents",
                    matched_pattern="t",
                    command_family="tar",
                )
            break

    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="tar with unrecognized operation flags",
        matched_pattern="tar",
        command_family="tar",
    )


def _classify_find(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "find":
        return None
    args = tokens[1:]

    # -exec/-execdir/-ok/-okdir are judged by their payload in detect_mutative_command's executor lane.
    mutative_actions = ("-delete", "-fprint", "-fprint0", "-fprintf")
    flag = _has_flag(args, *mutative_actions)
    if flag:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"find {flag} executes commands or modifies the filesystem",
            matched_pattern=flag,
            command_family="find",
        )

    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="find without -exec/-delete is read-only",
        matched_pattern="find",
        command_family="find",
    )


_CURL_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
_CURL_QUERY_DATA_FLAGS = frozenset({"-d", "--data", "--data-ascii", "--data-binary", "--data-raw", "--data-urlencode"})


_CURL_GET_SAFE_SWITCHES = frozenset({
    "-G", "--get", "-s", "-S", "-sS", "-Ss", "-L", "-sL", "-sSL", "-f", "-fsSL", "--fail", "--silent",
    "--show-error", "--location", "--compressed", "-v", "--verbose", "-i", "--include", "-k", "--insecure",
    "-g", "--globoff",
})
_CURL_GET_SAFE_VALUE_FLAGS = frozenset({
    "-H", "--header", "-A", "--user-agent", "-e", "--referer", "-m", "--max-time",
    "--connect-timeout", "-u", "--user", "-w", "--write-out", "--retry", "-x", "--proxy",
})
_SHELL_RESOLVED = re.compile(r"[$`*?\[\]{}]")


def _curl_get_sends_only_literals(args: List[str]) -> bool:
    """Return True when curl's own option walk meets -G as an option and every option is a known read option or literal query data.

    Nothing that writes a file and nothing the shell or curl's URL globbing resolves may appear.
    """
    if any(_SHELL_RESOLVED.search(a) for a in args):
        return False
    get_seen = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in _CURL_QUERY_DATA_FLAGS or a in _CURL_GET_SAFE_VALUE_FLAGS:
            if i + 1 >= len(args):
                return False
            value = args[i + 1]
            if a in _CURL_QUERY_DATA_FLAGS and _curl_data_reads_file(a, value):
                return False
            if a in ("-H", "--header") and ("method" in value.lower() or value.startswith("@")):
                return False
            i += 2
            continue
        if a.startswith("-") and a not in _CURL_GET_SAFE_SWITCHES:
            return False
        get_seen = get_seen or a in ("-G", "--get")
        i += 1
    return get_seen


_CURL_SHORT_DATA_LETTERS = frozenset("dFT")
_CURL_SHORT_VALUE_LETTERS = frozenset("AbcCDeEHKmoPQrtuUwxXyYz")


def _curl_bundle_sends_data(option: str) -> bool:
    """Return True when a short-option bundle (-Gd) reaches a data letter before a letter that takes the rest as its value."""
    if option.startswith("--") or len(option) < 3 or not option[1:].isalpha():
        return False
    for letter in option[1:]:
        if letter in _CURL_SHORT_DATA_LETTERS:
            return True
        if letter in _CURL_SHORT_VALUE_LETTERS:
            return False
    return False


def _curl_data_reads_file(flag: str, value: str) -> bool:
    """Return True when a curl data value names a file or stdin whose contents curl sends."""
    if flag == "--data-raw":
        return False
    if flag == "--data-urlencode":
        name, at, _ = value.partition("@")
        return bool(at) and "=" not in name
    return value.startswith("@")

def _classify_curl(tokens: List[str], wrapped: bool = False) -> Optional[FlagClassifierResult]:
    """Classify curl by the data and method its options send; -G reads only on a bare, fully literal line."""
    if not tokens or tokens[0] != "curl":
        return None
    args = tokens[1:]
    get_query = not wrapped and _curl_get_sends_only_literals(args)
    i = 0
    while i < len(args):
        a = args[i]
        if get_query and a in _CURL_QUERY_DATA_FLAGS:
            i += 2
            continue
        if _curl_bundle_sends_data(a):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"curl {a} bundles an option that sends data to the server",
                matched_pattern=a,
                command_family="curl",
            )
        if a[:2] in ("-d", "-F", "-T") and len(a) > 2:
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"curl {a[:2]} with an attached value sends data to the server",
                matched_pattern=a[:2],
                command_family="curl",
            )
        if a.startswith("-X") and len(a) > 2 and a[2:].upper() in _CURL_WRITE_METHODS:
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"curl {a} sends a write request",
                matched_pattern=a,
                command_family="curl",
            )
        if a in ("-X", "--request"):
            if i + 1 < len(args) and args[i + 1].upper() in _CURL_WRITE_METHODS:
                method = args[i + 1].upper()
                return FlagClassifierResult(
                    outcome=OUTCOME_MUTATIVE,
                    reason=f"curl -X {method} sends a write request",
                    matched_pattern=f"-X {method}",
                    command_family="curl",
                )
            i += 2
            continue
        if a.startswith("--request="):
            method = a.split("=", 1)[1].upper()
            if method in _CURL_WRITE_METHODS:
                return FlagClassifierResult(
                    outcome=OUTCOME_MUTATIVE,
                    reason=f"curl --request={method} sends a write request",
                    matched_pattern=f"--request={method}",
                    command_family="curl",
                )
            i += 1
            continue
        if a in ("-d", "--data", "--data-binary", "--data-raw", "--data-urlencode",
                 "-F", "--form", "--form-string",
                 "-T", "--upload-file", "--json"):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"curl {a} sends data to the server (implies write)",
                matched_pattern=a,
                command_family="curl",
            )
        i += 1

    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="curl without write flags performs a network read (GET)",
        matched_pattern="curl",
        command_family="curl",
    )


_WGET_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

def _classify_wget(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "wget":
        return None
    args = tokens[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--post-data", "--post-file"):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"wget {a} sends a POST request",
                matched_pattern=a,
                command_family="wget",
            )
        if a.startswith("--post-data=") or a.startswith("--post-file="):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"wget {a.split('=')[0]} sends a POST request",
                matched_pattern=a.split("=")[0],
                command_family="wget",
            )
        if a == "--method":
            if i + 1 < len(args) and args[i + 1].upper() in _WGET_WRITE_METHODS:
                method = args[i + 1].upper()
                return FlagClassifierResult(
                    outcome=OUTCOME_MUTATIVE,
                    reason=f"wget --method {method} sends a write request",
                    matched_pattern=f"--method {method}",
                    command_family="wget",
                )
            i += 2
            continue
        if a.startswith("--method="):
            method = a.split("=", 1)[1].upper()
            if method in _WGET_WRITE_METHODS:
                return FlagClassifierResult(
                    outcome=OUTCOME_MUTATIVE,
                    reason=f"wget --method={method} sends a write request",
                    matched_pattern=f"--method={method}",
                    command_family="wget",
                )
            i += 1
            continue
        if a in ("--body-data", "--body-file"):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"wget {a} sends request body data",
                matched_pattern=a,
                command_family="wget",
            )
        if a.startswith("--body-data=") or a.startswith("--body-file="):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"wget {a.split('=')[0]} sends request body data",
                matched_pattern=a.split("=")[0],
                command_family="wget",
            )
        i += 1

    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="wget without write flags performs a network read (GET/download)",
        matched_pattern="wget",
        command_family="wget",
    )


# httpie takes the method as its first positional (http POST url) and sends a body when data items follow (key=value, key:=json, key@file).
_HTTPIE_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

_HTTPIE_DATA_ITEM = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*(:=|==|=@|:@|@|:=@|=)")


def _classify_httpie(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] not in ("http", "https"):
        return None
    args = tokens[1:]

    non_flag_args = [a for a in args if not a.startswith("-")]

    if not non_flag_args:
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="httpie with no positional arguments is read-only",
            matched_pattern="http",
            command_family="httpie",
        )

    first = non_flag_args[0].upper()
    if first in _HTTPIE_WRITE_METHODS:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"httpie {first} sends a write request",
            matched_pattern=first,
            command_family="httpie",
        )
    if first in ("GET", "HEAD", "OPTIONS"):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason=f"httpie {first} is a read-only method",
            matched_pattern=first,
            command_family="httpie",
        )

    for a in non_flag_args[1:]:
        if _HTTPIE_DATA_ITEM.match(a):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"httpie data item {a!r} implies a POST request",
                matched_pattern=a,
                command_family="httpie",
            )

    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="httpie GET request (no write method or data items)",
        matched_pattern="http",
        command_family="httpie",
    )


_GIT_SUBCOMMAND_CLASSIFIERS: Dict[str, Callable[[List[str]], Optional[FlagClassifierResult]]] = {
    "push": _classify_git_push,
    "reset": _classify_git_reset,
    "checkout": _classify_git_checkout,
    "stash": _classify_git_stash,
    "rebase": _classify_git_rebase,
    "tag": _classify_git_tag,
    "clean": _classify_git_clean,
    "remote": _classify_git_remote,
}


def _classify_git_dispatch(tokens: List[str]) -> Optional[FlagClassifierResult]:
    """Dispatch to the appropriate git sub-command classifier."""
    if len(tokens) < 2 or tokens[0] != "git":
        return None
    sub = tokens[1].lower()
    classifier = _GIT_SUBCOMMAND_CLASSIFIERS.get(sub)
    if classifier is None:
        return None
    return classifier(tokens)


_CLASSIFIER_REGISTRY: Dict[str, Callable[[List[str]], Optional[FlagClassifierResult]]] = {
    "git": _classify_git_dispatch,
    "sed": _classify_sed,
    **{name: _classify_awk for name in AWK_PROGRAMS},
    "tar": _classify_tar,
    "find": _classify_find,
    "curl": _classify_curl,
    "wget": _classify_wget,
    "http": _classify_httpie,
    "https": _classify_httpie,
}


def classify_by_flags(
    command: str, caller: "Optional[LocalWorkCaller]" = None, wrapped: bool = False,
    piped_from: Optional[str] = None,
) -> Optional[FlagClassifierResult]:
    """Classify a command based on flags and sub-commands.

    This is the primary entry point.  Call this BEFORE detect_mutative_command()
    in the classify phase.  If this returns a result, it overrides verb-based
    classification.  If it returns None, fall through to the existing pipeline.

    Args:
        command: The full shell command string.  Behind a transparent wrapper
            (``sudo``, ``env -C``) each wrapped reading is classified with no
            caller, and only a blocked or mutative verdict is adopted.
        caller: Who runs it and from where; tar's relief and relative awk files need it.
        wrapped: The command is a reading peeled from behind a wrapper, so no
            relief that needs the bare line applies (curl -G).
        piped_from: The stage whose output alone is piped into *command*; awk
            judges a program it reads from stdin by what that stage writes.

    Returns:
        FlagClassifierResult if the program's base name (``/usr/bin/sed`` is sed) is a known family, else None.
    """
    from ..tools.shell_parser import strip_comment_before_heredoc, strip_grouping

    if not command or not command.strip():
        return None
    command = strip_grouping(strip_comment_before_heredoc(command))
    tokens = _tokenize(command)
    if not tokens:
        return None

    tokens[0] = posixpath.basename(tokens[0])
    classifier = _CLASSIFIER_REGISTRY.get(tokens[0])
    if classifier is None:
        from .mutative_verbs import _peel_wrapper_readings
        readings, peeled = _peel_wrapper_readings(command)
        wrapped = [classify_by_flags(r, wrapped=True) for r in readings if peeled and r.strip() != command.strip()]
        for outcome in (OUTCOME_BLOCKED, OUTCOME_MUTATIVE):
            for result in wrapped:
                if result is not None and result.outcome == outcome:
                    return result
        return None
    if classifier is _classify_awk:
        return _classify_awk(command, caller, piped_from)
    if classifier is _classify_tar:
        return _classify_tar(tokens, caller)
    if classifier is _classify_curl:
        return _classify_curl(tokens, wrapped)
    return classifier(tokens)
