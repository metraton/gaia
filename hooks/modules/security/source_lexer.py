"""Comment and string lexer for the non-shell script lanes of ``mutative_verbs``.

``strip_source`` returns two projections of a source text, each as long as the
source with every newline in place, so ``splitlines`` gives aligned line lists:

* ``verb_view`` -- comments and string contents blanked, delimiters kept. The
  script lane runs the blocked-command screen and the direct filesystem-mutation
  patterns on it, so a word that lives in a comment or a string cannot match.
* ``exec_view`` -- comments blanked, string contents kept. Exec-sink extraction
  (``execSync("kubectl delete ...")``) runs on it; tracking strings keeps a ``//``
  inside one (``"http://..."``) from reading as a comment.

Four families are registered: JavaScript (``node``, ``deno``, ``bun``, ``.js``,
``.mjs``, ``.cjs``), php, ruby and perl. A backtick is a template-literal quote in
JS (blanked in ``verb_view``, kept in ``exec_view``, ``backticks_are_exec`` False).
In php, ruby and perl it is shell execution: not a string quote, left verbatim in
both views for ``_scan_exec_sink_string_args``, which reads ``backticks_are_exec``.
Ruby's ``%x{...}`` has no lexer rule and stays plain code for the same scanner.

Comment grammars beyond JS's ``//`` and ``/* */`` are spec fields, off by default:
``extra_line_comments`` (php's ``#``), ``line_block_comments`` (ruby's
``=begin``/``=end``) and ``pod_style`` (perl POD).

Not lexed:

* php, ruby and perl heredocs and ``q{}``/``qq{}`` strings are read as code, so a
  quote or comment marker inside one opens a string or comment.
* JS regex literals are not told from division, so a ``//`` or a quote inside one
  opens a comment or a string.
* A JS template nested in ``${...}`` is not tracked: its first backtick closes the
  outer template and the text up to the next backtick is read as code.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LanguageSpec:
    """Comment / string syntax for one source language family (Strategy).

    Attributes:
        name: Family name (``javascript``, ``php``, ``ruby``, ``perl``); the
            script lane keys its per-language rules on it.
        line_comment: The line-comment marker (``"//"``), or ``None``.
        block_comment: ``(open, close)`` markers (``("/*", "*/")``), or ``None``.
        string_quotes: String-literal delimiter characters.  Each is a
            single character opened and closed by the same character, with
            ``\\`` as the escape character.  For JS this includes the
            backtick (template literal) -- a STRING, not shell execution.
        backticks_are_exec: True when a backtick delimits a SHELL command
            (ruby/perl/php); False when it delimits a string/template (JS).
            The exec-sink scanner reads this to decide whether backtick
            bodies should be re-classified as commands.
        extra_line_comments: Additional line-comment markers beyond
            ``line_comment`` (PHP supports both ``//`` and ``#``).  Each is
            treated exactly like ``line_comment``: it blanks the remainder of
            the line in both views.  Defaults to empty (JS uses only ``//``).
        line_block_comments: Block comments delimited by markers ``(open, close)``
            at the start of a line.  A line whose start matches
            ``open`` begins the block; every line through the one whose start
            matches ``close`` is blanked (delimiter lines included).  Ruby's
            ``("=begin", "=end")`` is the case; defaults to empty.
        pod_style: True for perl POD.  A line starting with ``=`` immediately
            followed by a letter opens a documentation block that runs
            (blanked) until a line starting with ``=cut``.  Defaults to False.
    """

    name: str
    line_comment: Optional[str]
    block_comment: Optional[Tuple[str, str]]
    string_quotes: Tuple[str, ...]
    backticks_are_exec: bool
    extra_line_comments: Tuple[str, ...] = ()
    line_block_comments: Tuple[Tuple[str, str], ...] = ()
    pod_style: bool = False


@dataclass(frozen=True)
class StrippedSource:
    """Two lexed projections of a source file (see module docstring).

    ``verb_view`` and ``exec_view`` have identical length and identical
    newline positions to the original content, so ``splitlines()`` on either
    yields line lists aligned with each other and with the source.
    """

    verb_view: str
    exec_view: str


JS_SPEC = LanguageSpec(
    name="javascript",
    line_comment="//",
    block_comment=("/*", "*/"),
    string_quotes=("'", '"', "`"),
    backticks_are_exec=False,
)

PHP_SPEC = LanguageSpec(
    name="php",
    line_comment="//",
    block_comment=("/*", "*/"),
    string_quotes=("'", '"'),
    backticks_are_exec=True,
    extra_line_comments=("#",),
)

RUBY_SPEC = LanguageSpec(
    name="ruby",
    line_comment="#",
    block_comment=None,
    string_quotes=("'", '"'),
    backticks_are_exec=True,
    line_block_comments=(("=begin", "=end"),),
)

PERL_SPEC = LanguageSpec(
    name="perl",
    line_comment="#",
    block_comment=None,
    string_quotes=("'", '"'),
    backticks_are_exec=True,
    pod_style=True,
)

_SPEC_BY_EXT = {
    ".js": JS_SPEC,
    ".mjs": JS_SPEC,
    ".cjs": JS_SPEC,
    ".php": PHP_SPEC,
    ".rb": RUBY_SPEC,
    ".pl": PERL_SPEC,
    ".pm": PERL_SPEC,
}
_SPEC_BY_INTERPRETER = {
    "node": JS_SPEC,
    "deno": JS_SPEC,
    "bun": JS_SPEC,
    "php": PHP_SPEC,
    "ruby": RUBY_SPEC,
    "perl": PERL_SPEC,
}


def spec_for_script(interpreter: str, script_path: str) -> Optional[LanguageSpec]:
    """Resolve the :class:`LanguageSpec` for a script, or ``None``.

    Prefers the interpreter token (``node foo``) and falls back to the file
    extension (direct ``./foo.mjs`` invocation, where the interpreter token
    IS the path).  Returns ``None`` for any language the lexer does not handle;
    the caller then classifies it with the regex lane.
    """
    spec = _SPEC_BY_INTERPRETER.get(interpreter)
    if spec is not None:
        return spec
    lowered = script_path.lower()
    for ext, ext_spec in _SPEC_BY_EXT.items():
        if lowered.endswith(ext):
            return ext_spec
    return None


_CODE = 0
_LINE_COMMENT = 1
_BLOCK_COMMENT = 2
_STRING = 3
_LINE_BLOCK_COMMENT = 4  # column-0 block comment (=begin/=end) and perl POD

_NOT_NEWLINE = re.compile(r"[^\n]")


def _blank(text: str) -> str:
    """*text* with every character but newlines replaced by a space."""
    return _NOT_NEWLINE.sub(" ", text)


@functools.lru_cache(maxsize=None)
def _code_run_regex(spec: LanguageSpec) -> Optional["re.Pattern"]:
    """Matches the next character in code that can start a comment or string.

    Everything before it is plain code, copied to both views in one step.  A
    spec with column-0 comments needs a per-line check the run skip would
    bypass, so it gets ``None`` and the character loop.
    """
    if spec.line_block_comments or spec.pod_style:
        return None
    starters = {m[0] for m in (spec.line_comment, *spec.extra_line_comments) if m}
    if spec.block_comment:
        starters.add(spec.block_comment[0][0])
    starters.update(spec.string_quotes)
    return re.compile("[" + "".join(re.escape(ch) for ch in sorted(starters)) + "]")


@functools.lru_cache(maxsize=None)
def _string_run_regex(quote: str) -> "re.Pattern":
    """Matches the next escape or closing quote of a string opened by *quote*."""
    return re.compile("[\\\\" + re.escape(quote) + "]")


def strip_source(content: str, spec: LanguageSpec) -> StrippedSource:
    """Lex ``content`` per ``spec`` into ``verb_view`` and ``exec_view``.

    A single left-to-right character state machine.  Comments are blanked in
    both views; string CONTENTS are blanked in ``verb_view`` and kept in
    ``exec_view``; newline positions are preserved in both.

    Args:
        content: Raw source text.
        spec: The language strategy (comment/string syntax).

    Returns:
        :class:`StrippedSource` with the two aligned projections.
    """
    verb: list = []
    execv: list = []
    state = _CODE
    quote = ""
    close_marker = ""  # active close marker while in _LINE_BLOCK_COMMENT
    i = 0
    n = len(content)

    # Line-comment markers: the primary plus any extras (PHP's ``#``).  Longest
    # first so a longer marker is preferred when two share a prefix.
    line_opens = tuple(
        sorted(
            (m for m in (spec.line_comment, *spec.extra_line_comments) if m),
            key=len,
            reverse=True,
        )
    )
    block = spec.block_comment
    block_open = block[0] if block else None
    block_close = block[1] if block else None
    quotes = spec.string_quotes
    line_block_comments = spec.line_block_comments
    pod_style = spec.pod_style

    def emit(vch: str, ech: str) -> None:
        verb.append(vch)
        execv.append(ech)

    code_run = _code_run_regex(spec)

    while i < n:
        if state == _CODE and code_run is not None:
            found = code_run.search(content, i)
            stop = found.start() if found else n
            if stop > i:
                plain = content[i:stop]
                verb.append(plain)
                execv.append(plain)
                i = stop
            if i >= n:
                break
        elif state == _STRING:
            found = _string_run_regex(quote).search(content, i)
            stop = found.start() if found else n
            if stop > i:
                text = content[i:stop]
                verb.append(_blank(text))
                execv.append(text)
                i = stop
            if i >= n:
                break

        c = content[i]

        if state == _CODE:
            at_line_start = i == 0 or content[i - 1] == "\n"

            # Column-0 block comments (ruby ``=begin`` .. ``=end``) and perl POD
            # (``=<letter>`` .. ``=cut``) are anchored to the start of a line.
            if at_line_start:
                entered = False
                for open_marker, cls in line_block_comments:
                    if content.startswith(open_marker, i):
                        for _ in range(len(open_marker)):
                            emit(" ", " ")
                        i += len(open_marker)
                        state = _LINE_BLOCK_COMMENT
                        close_marker = cls
                        entered = True
                        break
                if entered:
                    continue
                if (
                    pod_style
                    and c == "="
                    and i + 1 < n
                    and content[i + 1].isalpha()
                ):
                    emit(" ", " ")
                    i += 1
                    state = _LINE_BLOCK_COMMENT
                    close_marker = "=cut"
                    continue

            matched_line_open = next(
                (m for m in line_opens if content.startswith(m, i)), None
            )
            if matched_line_open:
                for _ in range(len(matched_line_open)):
                    emit(" ", " ")
                i += len(matched_line_open)
                state = _LINE_COMMENT
                continue
            if block_open and content.startswith(block_open, i):
                for _ in range(len(block_open)):
                    emit(" ", " ")
                i += len(block_open)
                state = _BLOCK_COMMENT
                continue
            if c in quotes:
                emit(c, c)
                quote = c
                state = _STRING
                i += 1
                continue
            emit(c, c)
            i += 1
            continue

        if state == _LINE_COMMENT:
            stop = content.find("\n", i)
            if stop == -1:
                stop = n
            if stop > i:
                blanks = " " * (stop - i)
                emit(blanks, blanks)
                i = stop
            if i < n:
                emit("\n", "\n")
                state = _CODE
                i += 1
            continue

        if state == _BLOCK_COMMENT:
            stop = content.find(block_close, i) if block_close else -1
            if stop == -1:
                stop = n
            if stop > i:
                blanked = _blank(content[i:stop])
                emit(blanked, blanked)
                i = stop
            if i < n:
                emit(" " * len(block_close), " " * len(block_close))
                i += len(block_close)
                state = _CODE
            continue

        if state == _LINE_BLOCK_COMMENT:
            # A column-0 close marker (``=end`` / ``=cut``) ends the block.  The
            # close marker and the remainder of ITS line are blanked (delegated
            # to _LINE_COMMENT), then code resumes on the next line.
            at_line_start = i == 0 or content[i - 1] == "\n"
            if at_line_start and content.startswith(close_marker, i):
                for _ in range(len(close_marker)):
                    emit(" ", " ")
                i += len(close_marker)
                state = _LINE_COMMENT
                close_marker = ""
                continue
            emit("\n" if c == "\n" else " ", "\n" if c == "\n" else " ")
            i += 1
            continue

        # state == _STRING
        if c == "\\" and i + 1 < n:
            nxt = content[i + 1]
            # Escaped pair: keep verbatim in exec_view, blank in verb_view
            # (preserving any newline so line indices stay aligned).
            emit(" " if c != "\n" else "\n", c)
            emit(" " if nxt != "\n" else "\n", nxt)
            i += 2
            continue
        if c == quote:
            emit(c, c)
            state = _CODE
            quote = ""
            i += 1
            continue
        emit("\n" if c == "\n" else " ", c)
        i += 1
        continue

    return StrippedSource("".join(verb), "".join(execv))
