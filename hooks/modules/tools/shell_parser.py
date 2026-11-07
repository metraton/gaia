"""
Shell Command Parser - Native Python Implementation

Parses bash commands into individual components for permission validation.

Features:
- Parse piped commands: "cmd1 | cmd2" -> ["cmd1", "cmd2"]
- Parse chained commands: "cmd1 && cmd2" -> ["cmd1", "cmd2"]
- Parse semicolon-separated: "cmd1; cmd2" -> ["cmd1", "cmd2"]
- Handle OR chains: "cmd1 || cmd2" -> ["cmd1", "cmd2"]
- Preserve quoted strings: echo 'a|b' -> ["echo 'a|b'"]

Dependencies: Python stdlib only (shlex, re)

This module is the one place shell words are decided, for the classifier and
every guard alike: an unquoted `#` at word start begins a comment, a single
`&` separates commands, and redirections with their targets are not argv
(`program_argv`). A rule about what bash passes to a program belongs here, not
in a caller's own split.
"""

import re
import shlex
from typing import List, Optional
from dataclasses import dataclass

# Characters after which a `#` starts a word, and so a comment. `<` and `>` are
# left out: bash discards a `#` after them too, but keeping it costs only a word.
_WORD_START_BEFORE = " \t\n;&|()"

# A redirection operator alone (its target is the next word) and one with the
# target attached (`2>/dev/null`, `<in`, `2>&1`).
_REDIRECT_OPERATOR = re.compile(r"^(?:\d+|&)?(?:>>|>&|>\||>|<<<|<<-?|<&|<>|<)$")
_REDIRECT_ATTACHED = re.compile(r"^(?:\d+|&)?(?:>>|>&|>\||>|<<<|<<-?|<&|<>|<).+$")
_HEREDOC_OPENER = re.compile(
    r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\\$`\n]*)\"|([^\s'\"\\<>;&|()`$]+))(?=[\s;&|()<>]|$)"
)
_CASE_WORD = re.compile(r"case(?=[\s;&|()<>])")
_GROUP_OPENER = re.compile(r"(?:[{}](?=\s|$)|\((?!\())\s*")


def strip_comment(command: str) -> str:
    """*command* without the comments bash discards; from where it cannot prove bash's reading, the text is kept whole."""
    out: List[str] = []
    i, n = 0, len(command)
    word_start = True
    heredocs: List["tuple[str, bool]"] = []
    while i < n:
        ch = command[i]
        if ch == "\n" and heredocs:
            end = _heredoc_bodies_end(command, i + 1, heredocs)
            out.append(command[i:end])
            i, word_start, heredocs = end, True, []
            continue
        if ch == "#" and word_start:
            end = command.find("\n", i)
            if end == -1:
                break
            i = end
            continue
        if command.startswith("<<", i) and not command.startswith("<<<", i):
            opener = _HEREDOC_OPENER.match(command, i)
            if opener is None:
                return "".join(out) + command[i:]
            heredocs.append((next(g for g in opener.groups()[1:] if g is not None), opener.group(1) == "-"))
            out.append(opener.group(0))
            i, word_start = opener.end(), False
            continue
        end = _construct_end(command, i, word_start)
        if end is None:
            return "".join(out) + command[i:]
        out.append(command[i:end])
        word_start = end == i + 1 and ch in _WORD_START_BEFORE
        i = end
    return "".join(out)


def _construct_end(text: str, i: int, word_start: bool) -> Optional[int]:
    """Index after the quote, escape or expansion opening at *i* (i + 1 for a plain character), or None when its end is unproven."""
    ch = text[i]
    if ch == "\\":
        return min(i + 2, len(text))
    if ch == "'":
        end = text.find("'", i + 1)
        return None if end == -1 else end + 1
    if text.startswith("$'", i):
        try:
            return _ansi_c_body(text, i + 2)[1]
        except ValueError:
            return None
    if ch == '"' or text.startswith('$"', i):
        return _double_quote_end(text, i + (2 if ch == "$" else 1))
    if ch == "`":
        return _backtick_end(text, i + 1)
    if text.startswith(("$((", "$["), i):
        return _arithmetic_end(text, i + 3 if text[i + 1] == "(" else i + 2, text[i + 1])
    if word_start and text.startswith("((", i):
        return _arithmetic_end(text, i + 2, "(")
    if text.startswith(("$(", "<(", ">("), i):
        return _substitution_end(text, i + 2)
    if text.startswith("${", i):
        return _parameter_end(text, i + 2)
    return i + 1


def process_substitutions(command: str) -> List["tuple[str, str]"]:
    """Each top-level ``<(...)`` or ``>(...)`` bash runs in *command*, as (opener, body); the scan stops at a comment or an unproven span."""
    found: List["tuple[str, str]"] = []
    i, word_start = 0, True
    while i < len(command):
        if command[i] == "#" and word_start:
            break
        end = _construct_end(command, i, word_start)
        if end is None:
            break
        if command.startswith(("<(", ">("), i):
            found.append((command[i:i + 2], command[i + 2:end - 1]))
        word_start = end == i + 1 and command[i] in _WORD_START_BEFORE
        i = end
    return found


def _double_quote_end(text: str, i: int) -> Optional[int]:
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return i + 1
        if ch == "\\":
            i += 2
            continue
        expands = ch == "`" or (ch == "$" and text[i + 1:i + 2] in ("(", "[", "{"))
        end = _construct_end(text, i, False) if expands else i + 1
        if end is None:
            return None
        i = end
    return None


def _backtick_end(text: str, i: int) -> Optional[int]:
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i + 1
        i += 1
    return None


def _arithmetic_end(text: str, i: int, opener: str) -> Optional[int]:
    closer = ")" if opener == "(" else "]"
    depth = 2 if opener == "(" else 1
    while i < len(text):
        ch = text[i]
        if ch == "#" and text[i - 1] in _WORD_START_BEFORE:
            return None
        if ch in "\"'`$\\":
            end = _construct_end(text, i, False)
            if end is None:
                return None
            i = end
            continue
        depth += {opener: 1, closer: -1}.get(ch, 0)
        i += 1
        if depth == 0:
            return i
    return None


def _substitution_end(text: str, i: int) -> Optional[int]:
    depth, word_start = 1, True
    while i < len(text):
        ch = text[i]
        if ch == "#" and word_start:
            i = text.find("\n", i)
            if i == -1:
                return None
            continue
        if text.startswith("<<", i) or (word_start and _CASE_WORD.match(text, i)):
            return None
        if ch in "()" and not (word_start and text.startswith("((", i)):
            depth += 1 if ch == "(" else -1
            i += 1
            if depth == 0:
                return i
            word_start = True
            continue
        end = _construct_end(text, i, word_start)
        if end is None:
            return None
        word_start = end == i + 1 and ch in _WORD_START_BEFORE
        i = end
    return None


def _parameter_end(text: str, i: int) -> Optional[int]:
    depth = 1
    while i < len(text):
        ch = text[i]
        if ch in "{}":
            depth += 1 if ch == "{" else -1
            i += 1
            if depth == 0:
                return i
            continue
        end = _construct_end(text, i, False)
        if end is None:
            return None
        i = end
    return None


def _heredoc_bodies_end(text: str, i: int, heredocs: List["tuple[str, bool]"]) -> int:
    for delimiter, strip_tabs in heredocs:
        while i < len(text):
            newline = text.find("\n", i)
            line = text[i:] if newline == -1 else text[i:newline]
            i = len(text) if newline == -1 else newline + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                break
    return i


def strip_grouping(command: str) -> str:
    """*command* without the `{`, `}`, `(` and `)` words that group it with others, which no program receives."""
    text = command.strip()
    while True:
        opener = _GROUP_OPENER.match(text)
        if opener is None:
            break
        text = text[opener.end():]
    depth = 0
    for char in _outside_quotes(text):
        depth += {"(": 1, ")": -1}.get(char, 0)
    while depth < 0 and text.endswith(")"):
        text, depth = text[:-1].rstrip(), depth + 1
    return text


def _outside_quotes(text: str) -> str:
    kept, quote, escaped = [], "", False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        else:
            kept.append(char)
    return "".join(kept)


def is_lone_ampersand(command: str, i: int) -> bool:
    """True when the `&` at *i* separates commands: not `&&`, `&>`, or the `&` of `>&`, `<&` and `|&`."""
    before = command[i - 1] if i > 0 else ""
    after = command[i + 1] if i + 1 < len(command) else ""
    return after not in ("&", ">") and before not in ("&", ">", "<", "|")


def drop_redirections(words: List[str]) -> List[str]:
    """*words* without redirection operators and their targets, which bash never passes to the program."""
    out: List[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if _REDIRECT_OPERATOR.match(word):
            skip_next = True
            continue
        if _REDIRECT_ATTACHED.match(word):
            continue
        out.append(word)
    return out


_ANSI_C_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r",
                   "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
_ANSI_C_NUMERIC = re.compile(r"x([0-9a-fA-F]{1,2})|([0-7]{1,3})")
# An unquoted IFS expansion splits words under the default IFS.
_IFS_EXPANSION = re.compile(r"\$(?:IFS(?![A-Za-z0-9_])|\{IFS[^}]*\})")
_DOUBLE_QUOTE_ESCAPABLE = '$`"\\\n'


def _ansi_c_body(text: str, i: int) -> "tuple[str, int]":
    """The value of a ``$'...'`` word whose body starts at *i*, and the index after its closing quote."""
    out: List[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "'":
            return "".join(out), i + 1
        if ch == "\\" and i + 1 < len(text):
            numeric = _ANSI_C_NUMERIC.match(text, i + 1)
            if text[i + 1] in _ANSI_C_ESCAPES:
                out.append(_ANSI_C_ESCAPES[text[i + 1]])
                i += 2
            elif numeric:
                out.append(chr(int(numeric.group(1), 16) if numeric.group(1) else int(numeric.group(2), 8)))
                i = numeric.end()
            else:
                out.append(text[i:i + 2])
                i += 2
            continue
        out.append(ch)
        i += 1
    raise ValueError("No closing quotation")


def shell_split(text: str) -> List[str]:
    """The words bash makes of *text* after quote removal (backslash, '', "", $'', $"") and default-IFS splitting; raises ValueError like shlex.split."""
    words: List[str] = []
    current: List[str] = []
    started = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        ifs = _IFS_EXPANSION.match(text, i) if ch == "$" else None
        if ch in " \t\n" or ifs:
            if started:
                words.append("".join(current))
                current, started = [], False
            i = ifs.end() if ifs else i + 1
            continue
        started = True
        if ch == "\\":
            if i + 1 >= n:
                raise ValueError("No escaped character")
            current.append("" if text[i + 1] == "\n" else text[i + 1])
            i += 2
        elif ch == "'":
            end = text.find("'", i + 1)
            if end == -1:
                raise ValueError("No closing quotation")
            current.append(text[i + 1:end])
            i = end + 1
        elif ch == "$" and text.startswith("'", i + 1):
            body, i = _ansi_c_body(text, i + 2)
            current.append(body)
        elif ch == '"' or (ch == "$" and text.startswith('"', i + 1)):
            i += 2 if ch == "$" else 1
            while True:
                if i >= n:
                    raise ValueError("No closing quotation")
                if text[i] == '"':
                    i += 1
                    break
                if text[i] == "\\" and i + 1 < n and text[i + 1] in _DOUBLE_QUOTE_ESCAPABLE:
                    current.append("" if text[i + 1] == "\n" else text[i + 1])
                    i += 2
                    continue
                current.append(text[i])
                i += 1
        else:
            current.append(ch)
            i += 1
    if started:
        words.append("".join(current))
    return words


def shell_words(component: str) -> List[str]:
    """The unquoted words of one operator-free component, redirections removed."""
    try:
        words = shell_split(component)
    except ValueError:
        words = component.split()
    return drop_redirections(words)


def unquoted_readings(command: str) -> List[str]:
    """Each component of *command* re-spelled from its words with minimal quoting, where that differs from how it was written."""
    readings: List[str] = []
    for component in parse_command(command):
        try:
            words = shell_split(component)
        except ValueError:
            continue
        spelled = " ".join(
            word if _REDIRECT_OPERATOR.match(word) or _REDIRECT_ATTACHED.match(word) else shlex.quote(word)
            for word in words
        )
        if spelled and spelled != component.strip():
            readings.append(spelled)
    return readings


def program_argv(command: str) -> List[str]:
    """The words bash passes to the first program of *command*: comments, later commands and redirections removed."""
    components = parse_command(command)
    return shell_words(components[0]) if components else []


@dataclass
class ParsedCommand:
    """A parsed shell command component; ``parse_with_operators`` never sets ``operator``."""
    command: str
    operator: Optional[str] = None

    def __str__(self) -> str:
        return self.command


class ShellCommandParser:
    """
    Native Python shell command parser.

    Parses bash commands into individual components while respecting:
    - Single quotes (preserve everything inside)
    - Double quotes (preserve everything inside)
    - Escape sequences
    - ANSI-C ``$'...'`` words

    Parentheses and backticks are not tracked: an operator inside ``$(...)``
    or a subshell splits the command there.

    Zero external dependencies - uses Python stdlib only.
    """

    OPERATORS = {
        '|': 'pipe',
        '&&': 'and',
        '||': 'or',
        ';': 'sequence',
        '\n': 'newline'
    }

    def __init__(self):
        """Initialize parser."""
        pass

    def parse(self, command: str) -> List[str]:
        """
        Parse shell command into individual components.

        Args:
            command: Full shell command string

        Returns:
            List of individual command strings

        Example:
            >>> parser = ShellCommandParser()
            >>> parser.parse("ls | grep foo && wc -l")
            ["ls", "grep foo", "wc -l"]

            >>> parser.parse("echo 'test | grep' | cat")
            ["echo 'test | grep'", "cat"]
        """
        if not command or not command.strip():
            return []

        command = strip_comment(command).strip()

        components = self._split_on_operators(command)

        result = [comp.strip() for comp in components if comp.strip()]

        return result

    def _split_on_operators(self, command: str) -> List[str]:
        """
        Split command on operators (|, &&, ||, ;) while preserving quoted strings.

        Uses state machine to track quote context.
        """
        components = []
        current = []
        i = 0

        in_single_quote = False
        in_double_quote = False

        while i < len(command):
            char = command[i]

            if char == '\\' and not in_single_quote and i + 1 < len(command):
                current.append(char)
                current.append(command[i + 1])
                i += 2
                continue

            if char == '$' and command.startswith("'", i + 1) and not (in_single_quote or in_double_quote):
                try:
                    end = _ansi_c_body(command, i + 2)[1]
                except ValueError:
                    end = len(command)
                current.append(command[i:end])
                i = end
                continue

            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(char)
                i += 1
                continue

            if char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(char)
                i += 1
                continue

            if in_single_quote or in_double_quote:
                current.append(char)
                i += 1
                continue

            if i + 1 < len(command):
                two_char = command[i:i+2]
                if two_char in ['&&', '||']:
                    if current:
                        components.append(''.join(current))
                        current = []
                    i += 2
                    continue

            if char in ['|', ';', '\n'] or (char == '&' and is_lone_ampersand(command, i)):
                if current:
                    components.append(''.join(current))
                    current = []
                i += 1
                continue

            current.append(char)
            i += 1

        if current:
            components.append(''.join(current))

        return components

    def parse_with_operators(self, command: str) -> List[ParsedCommand]:
        """
        Parse command into ParsedCommand objects, one per component.

        The operators are discarded: ``operator`` stays None. StageDecomposer
        is what recovers them.
        """
        if not command or not command.strip():
            return []

        commands = self.parse(command)
        return [ParsedCommand(command=cmd) for cmd in commands]

    def contains_operators(self, command: str) -> bool:
        """Check if command contains shell operators (outside of quotes)."""
        components = self.parse(command)
        return len(components) > 1

    def is_simple_command(self, command: str) -> bool:
        """Check if command is a simple command (no operators)."""
        return not self.contains_operators(command)


_shell_parser: Optional[ShellCommandParser] = None


def get_shell_parser() -> ShellCommandParser:
    """
    Get singleton ShellCommandParser instance.

    Returns:
        ShellCommandParser instance
    """
    global _shell_parser
    if _shell_parser is None:
        _shell_parser = ShellCommandParser()
    return _shell_parser


def parse_command(command: str) -> List[str]:
    """
    Parse shell command into components (convenience function).

    Args:
        command: Shell command string

    Returns:
        List of command components
    """
    return get_shell_parser().parse(command)


def is_compound_command(command: str) -> bool:
    """
    Check if command is compound (contains operators).

    Args:
        command: Shell command string

    Returns:
        True if compound, False if simple
    """
    return get_shell_parser().contains_operators(command)
