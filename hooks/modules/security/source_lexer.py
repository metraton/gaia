"""
Lightweight source lexer for the script-content classification lane.

The script-file classifier (``mutative_verbs._check_script_file``) scans a
non-shell SOURCE file (``.js``/``.mjs``/``.cjs`` ...) for mutative verbs and
exec-sink calls.  A pure line-by-line regex scan cannot tell CODE from a
COMMENT or a STRING LITERAL, so it false-positives on lexical collisions --
the word ``edit`` inside a template literal, or ``npm install`` inside a
``//`` comment, are read as if they were executable tokens.  (This is the
classic static-analysis failure mode: a generic regex matches a *mention*,
not an *invocation* -- see the AST precedent in ``inline_ast_analyzer``.)

This module removes that collision BEFORE the verb scanner runs, by lexing
the source with a per-language :class:`LanguageSpec` (Strategy) and producing
two projections of the file:

* ``verb_view``  -- comments blanked, STRING/TEMPLATE CONTENTS blanked
  (delimiters kept).  Used for whole-token verb / blocked-command scanning:
  a verb that lives only inside a comment or a string can no longer match.
* ``exec_view``  -- comments blanked, string CONTENTS KEPT verbatim.  Used
  for exec-sink extraction (``execSync("kubectl delete ...")``): the command
  handed to a subprocess sink as a string literal is preserved and still
  re-classified, so a REAL mutation stays T3.  String tracking is required
  here too, so a ``//`` inside a string (``"http://..."``) is NOT mistaken
  for a comment and does not blank a following sink call.

Both views preserve newline positions exactly, so a caller can ``splitlines``
either view and the line indices stay aligned with the original.

Design notes / accepted limitations:

* Four language families are registered: the JavaScript family (``node`` /
  ``.js`` / ``.mjs`` / ``.cjs``) and ruby / perl / php.  Backticks mean
  DIFFERENT things per language: in JS a backtick is a TEMPLATE LITERAL (a
  string), while in ruby/perl/php a backtick is SHELL EXECUTION.
  ``LanguageSpec.backticks_are_exec`` records that distinction so the exec-sink
  scanner can be told whether to treat backticks as a shell sink.  For JS the
  backtick is therefore listed in ``string_quotes`` (blanked in ``verb_view``,
  kept in ``exec_view`` as a template string) and ``backticks_are_exec`` is
  False; for ruby/perl/php the backtick is NOT a string delimiter -- it is left
  verbatim in BOTH views as executable text and ``backticks_are_exec`` is True,
  so ``_scan_exec_sink_string_args`` re-classifies the backtick body as a shell
  command.  ``%x{...}`` (ruby) is likewise an exec sink, so the lexer leaves it
  in place for the same scanner rather than blanking it as a comment/string.
* Comment grammars beyond JS's ``//`` + ``/* */`` are covered by three
  spec extensions (all defaulting off, so JS is unchanged): ``extra_line_comments``
  for PHP's second line marker ``#``; ``line_block_comments`` for a
  column-0-anchored block comment delimited by whole-line markers (ruby's
  ``=begin`` / ``=end``); and ``pod_style`` for perl POD (a line starting with
  ``=`` + a letter opens a documentation block that runs until a line starting
  with ``=cut``).  These blank documentation prose so a mutative verb mentioned
  only inside a comment can no longer be read as an invocation.
* Accepted limitations for ruby/perl/php: heredocs (``<<<``/``<<~``) and the
  ``q{}``/``qq{}`` string forms are NOT lexed as strings, and a paren-less sink
  call (ruby ``system "cmd"`` without parentheses) is not extracted -- these
  mirror the pre-existing regex-lane limitations and can only cost a residual
  false POSITIVE (heredoc body scanned as code) or a pre-existing false
  NEGATIVE already present before this lane existed, never a NEW false negative.
* Regex literals in JS (``/foo/``) are not disambiguated from division; an
  unescaped ``//`` inside a regex literal could be mis-read as a line comment.
  This is a well-known hard case in JS lexing.  It cannot open a false
  NEGATIVE for the exec-sink lane: a subprocess sink takes a STRING literal
  (tracked correctly), not a regex literal, so a real mutation is preserved.
* Nested template interpolation (``` `${`x`}` ```) may leave some template
  content un-blanked in ``verb_view``; that can only cost a residual false
  POSITIVE (never a false negative), because the exec_view is what guards
  real mutations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ============================================================================
# Language strategy
# ============================================================================

@dataclass(frozen=True)
class LanguageSpec:
    """Comment / string syntax for one source language family (Strategy).

    Attributes:
        name: Human-readable language family name (diagnostics only).
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
        line_block_comments: Column-0-anchored block comments delimited by
            WHOLE-LINE markers ``(open, close)``.  A line whose start matches
            ``open`` begins the block; every line through the one whose start
            matches ``close`` is blanked (delimiter lines included).  Ruby's
            ``("=begin", "=end")`` is the case; defaults to empty.
        pod_style: True for perl POD.  A line starting with ``=`` immediately
            followed by an ASCII letter opens a documentation block that runs
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


# The JavaScript family: ``//`` and ``/* */`` comments; ``'`` ``"`` and the
# backtick (template literal) as strings.  Backticks are NOT shell execution.
JS_SPEC = LanguageSpec(
    name="javascript",
    line_comment="//",
    block_comment=("/*", "*/"),
    string_quotes=("'", '"', "`"),
    backticks_are_exec=False,
)

# PHP: ``//`` AND ``#`` line comments, ``/* */`` block comments, ``'`` ``"``
# strings.  The backtick is SHELL EXECUTION (``shell_exec`` sugar), NOT a
# string -- so it is deliberately absent from ``string_quotes`` and left
# verbatim in both views for the exec-sink scanner (``backticks_are_exec``).
PHP_SPEC = LanguageSpec(
    name="php",
    line_comment="//",
    block_comment=("/*", "*/"),
    string_quotes=("'", '"'),
    backticks_are_exec=True,
    extra_line_comments=("#",),
)

# Ruby: ``#`` line comments, ``=begin`` / ``=end`` column-0 block comments,
# ``'`` ``"`` strings.  Backticks (and ``%x{...}``, handled by the exec-sink
# scanner, not the lexer) are SHELL EXECUTION, so the backtick is not a string
# delimiter here.
RUBY_SPEC = LanguageSpec(
    name="ruby",
    line_comment="#",
    block_comment=None,
    string_quotes=("'", '"'),
    backticks_are_exec=True,
    line_block_comments=(("=begin", "=end"),),
)

# Perl: ``#`` line comments, POD documentation blocks (``=pod`` / ``=head1`` /
# ... through ``=cut``), ``'`` ``"`` strings.  Backticks are SHELL EXECUTION.
PERL_SPEC = LanguageSpec(
    name="perl",
    line_comment="#",
    block_comment=None,
    string_quotes=("'", '"'),
    backticks_are_exec=True,
    pod_style=True,
)

# Extension / interpreter -> spec.  Registering a new language is adding a
# spec and its keys here; the lexer and the caller are unchanged (Strategy).
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
    "php": PHP_SPEC,
    "ruby": RUBY_SPEC,
    "perl": PERL_SPEC,
}


def spec_for_script(interpreter: str, script_path: str) -> Optional[LanguageSpec]:
    """Resolve the :class:`LanguageSpec` for a script, or ``None``.

    Prefers the interpreter token (``node foo``) and falls back to the file
    extension (direct ``./foo.mjs`` invocation, where the interpreter token
    IS the path).  Returns ``None`` for any language not handled by the lexer,
    so the caller keeps its existing regex lane for those.
    """
    spec = _SPEC_BY_INTERPRETER.get(interpreter)
    if spec is not None:
        return spec
    lowered = script_path.lower()
    for ext, ext_spec in _SPEC_BY_EXT.items():
        if lowered.endswith(ext):
            return ext_spec
    return None


# ============================================================================
# Lexer
# ============================================================================

# Lexer states.
_CODE = 0
_LINE_COMMENT = 1
_BLOCK_COMMENT = 2
_STRING = 3
_LINE_BLOCK_COMMENT = 4  # column-0 block comment (=begin/=end) and perl POD


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

    while i < n:
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
                # Keep the delimiter in BOTH views (structure preserved).
                emit(c, c)
                quote = c
                state = _STRING
                i += 1
                continue
            emit(c, c)
            i += 1
            continue

        if state == _LINE_COMMENT:
            if c == "\n":
                emit("\n", "\n")
                state = _CODE
            else:
                emit(" ", " ")
            i += 1
            continue

        if state == _BLOCK_COMMENT:
            if block_close and content.startswith(block_close, i):
                for _ in range(len(block_close)):
                    emit(" ", " ")
                i += len(block_close)
                state = _CODE
                continue
            emit("\n" if c == "\n" else " ", "\n" if c == "\n" else " ")
            i += 1
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
        # String content: blank in verb_view, keep in exec_view; newline kept.
        emit("\n" if c == "\n" else " ", c)
        i += 1
        continue

    return StrippedSource("".join(verb), "".join(execv))
