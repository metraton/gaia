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

* Only the JavaScript family (``node`` / ``.js`` / ``.mjs`` / ``.cjs``) is
  registered today, because backticks mean DIFFERENT things per language:
  in JS a backtick is a TEMPLATE LITERAL (a string), while in ruby/perl/php a
  backtick is SHELL EXECUTION.  ``LanguageSpec.backticks_are_exec`` records
  that distinction so the exec-sink scanner can be told whether to treat
  backticks as a shell sink.  ruby/perl/php keep the existing regex lane
  (where their backticks are correctly treated as exec); JS routes here.
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
    """

    name: str
    line_comment: Optional[str]
    block_comment: Optional[Tuple[str, str]]
    string_quotes: Tuple[str, ...]
    backticks_are_exec: bool


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

# Extension / interpreter -> spec.  Registering a new language is adding a
# spec and its keys here; the lexer and the caller are unchanged (Strategy).
_SPEC_BY_EXT = {
    ".js": JS_SPEC,
    ".mjs": JS_SPEC,
    ".cjs": JS_SPEC,
}
_SPEC_BY_INTERPRETER = {
    "node": JS_SPEC,
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
    i = 0
    n = len(content)

    line_open = spec.line_comment
    block = spec.block_comment
    block_open = block[0] if block else None
    block_close = block[1] if block else None
    quotes = spec.string_quotes

    def emit(vch: str, ech: str) -> None:
        verb.append(vch)
        execv.append(ech)

    while i < n:
        c = content[i]

        if state == _CODE:
            if line_open and content.startswith(line_open, i):
                for _ in range(len(line_open)):
                    emit(" ", " ")
                i += len(line_open)
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
