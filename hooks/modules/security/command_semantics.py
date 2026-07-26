"""
Semantic command analysis helpers for security decisions.

This module builds an analysis-friendly representation of a shell command
without mutating the original command string that will be executed.

Key properties:
- Idempotent: analyzing a normalized command produces the same semantic view.
- CLI-agnostic: relies on token structure, not a large per-CLI global-flag table.
- Non-destructive: the real command is never rewritten for execution.
"""

import functools
import re
import shlex
from dataclasses import dataclass
from typing import Iterable, Tuple

# Scan enough semantic tokens to cover CLIs with multiple resource segments and
# several global flag/value pairs before the real verb.
SEMANTIC_SCAN_LIMIT = 12


@dataclass(frozen=True)
class CommandSemantics:
    """Semantic view of a shell command for policy analysis."""

    raw_command: str = ""
    tokens: Tuple[str, ...] = ()
    base_cmd: str = ""
    args: Tuple[str, ...] = ()
    flag_tokens: Tuple[str, ...] = ()
    non_flag_tokens: Tuple[str, ...] = ()
    semantic_tokens: Tuple[str, ...] = ()
    semantic_head_tokens: Tuple[str, ...] = ()
    # Same as semantic_head_tokens but preserves original token casing.
    # Used for camelCase splitting where lowercase destroys word boundaries.
    semantic_head_tokens_raw: Tuple[str, ...] = ()

    @property
    def normalized_command(self) -> str:
        """Return the canonical analysis form of the command."""
        return " ".join(self.semantic_tokens)


def tokenize_command(command: str) -> Tuple[str, ...]:
    """Tokenize a shell command safely, preserving quoted substrings.

    Shell redirect tokens (``2>&1``, ``>foo``, ``2>foo``, ``>> log``, ...) are
    stripped here so they never bind into downstream semantic analysis or the
    approval signature.  A redirect is a side-effect on the command's I/O, not
    part of its identity: ``git push`` and ``git push 2>&1`` are the SAME
    operation and MUST produce the same signature, so a grant minted for one
    matches the other (double-approval fix, A).  Pipes (``|``), chaining
    (``&&``, ``;``), and command substitution (``$(...)``) are NOT redirects --
    they change the command's identity and are deliberately left intact so a
    decorated form re-triggers T3.
    """
    if not command or not command.strip():
        return ()
    try:
        raw = list(shlex.split(command.strip()))
    except ValueError:
        raw = list(_degraded_tokenize(command.strip()))
    return strip_redirect_tokens(raw)


_QUOTE_CHARS: Tuple[str, ...] = ("'", '"')


def _degraded_tokenize(command: str) -> Tuple[str, ...]:
    """Tokenize a command whose quoting ``shlex`` could not resolve.

    ``shlex.split`` raises only on unresolvable quoting/escaping ("No closing
    quotation", "No escaped character").  The historical fallback -- a naive
    whitespace split of the WHOLE command -- keeps the security layer
    best-effort instead of crashing, and that property is preserved here; what
    it must NOT keep doing is promote the contents of a data payload to
    command syntax.  An apostrophe inside an argument (``it's``) is enough to
    make shlex give up, and the naive split then exposes every word of that
    argument as a standalone token: a ``--force`` merely QUOTED in a report
    registers as a real flag, and any prose word that happens to be in
    MUTATIVE_VERBS registers as a real verb.  That taxes precisely the agents
    that report a blocked command verbatim -- the more faithful the report,
    the likelier the spurious T3.

    The split this uses instead rests on what is still knowable after shlex
    gives up: text BEFORE the first quote character contains no quoting at
    all, so shlex, bash and a whitespace split all read it identically -- it
    is unambiguous syntax.  From that quote onward the token boundaries are
    exactly what could not be resolved, so the remainder is emitted as ONE
    opaque datum rather than as N invented words.  A real command's verb and
    flags precede its quoted arguments, so the head that drives classification
    survives (``kubectl delete ns prod --now 'it's`` still yields the
    ``delete`` verb), while payload contents stop being scanned as syntax.
    The remainder is kept VERBATIM (never dropped) so the approval signature
    still binds the full command text and two different payloads never collapse
    onto one grant.

    Accepted narrowing, deliberate: a mutative verb reachable ONLY from inside
    the unresolved region -- a heredoc body fed to an interpreter, or text
    after an unterminated quote -- is no longer surfaced by the verb scanner.
    Three layers still cover it: ``is_blocked_command`` regexes the raw command
    string (tokenization-independent), the compound splitter classifies each
    ``&&``/``;``/``|`` component on its own, and the inline-code and
    script-file lanes re-classify an interpreter's payload as a command.  When
    no unambiguous head can be established (the command opens on a quote, or
    carries no quote at all), this returns the naive whole-command split so the
    conservative posture is kept exactly where nothing better is known.
    """
    quote_positions = [pos for pos in (command.find(q) for q in _QUOTE_CHARS) if pos != -1]
    if not quote_positions:
        return tuple(command.split())

    # Rewind to the start of the WORD carrying the quote: `--json='{...}'` must
    # stay one token so the flag keeps its normal shape instead of being cut
    # into a bare `--json=` plus a payload.
    boundary = min(quote_positions)
    while boundary > 0 and not command[boundary - 1].isspace():
        boundary -= 1

    head = command[:boundary].split()
    if not head:
        return tuple(command.split())
    return (*head, command[boundary:])


# An OUTPUT redirect operator token carrying an attached target or fd-duplication:
#   2>&1  1>&2  >&  >foo  2>foo  >>append  2>>append  &>out  &>>out
# Leading group: optional fd digits or '&'; operator: >>|>&|>; optional attached
# target (an fd like &1, or a filename that is not itself an operator).
# INPUT redirects (`<`, `<&`, `<<`) are deliberately NOT matched: they feed data
# INTO the command (e.g. `sqlite3 db < migration.sql`), which materially changes
# what the command does -- that is identity, not a side-effect, so it must bind.
_REDIRECT_ATTACHED_RE = re.compile(r"^(?:\d+|&)?(?:>>|>&|>)(?:&\d+|[^|;&<>(){}].*)?$")
# A bare OUTPUT redirect operator with no attached target -- consumes the NEXT
# token as its target:  >  2>  >>  2>>  &>
_REDIRECT_BARE_OP_RE = re.compile(r"^(?:\d+|&)?(?:>>|>&|>)$")


def strip_redirect_tokens(tokens: Iterable[str]) -> Tuple[str, ...]:
    """Drop shell OUTPUT redirect tokens (and their detached targets) from a list.

    Handles three shapes of OUTPUT redirection:
      - fd-duplication:        ``2>&1``, ``1>&2``, ``>&``  (single token)
      - attached target:       ``>foo``, ``2>foo``, ``>>log``, ``&>out``
      - detached operator+arg: ``>`` ``foo``  /  ``2>`` ``foo`` (two tokens)

    INPUT redirects (``<``, ``<&``), pipes, ``&&``, ``;``, and ``$(...)`` are
    intentionally preserved -- only OUTPUT redirections are removed, because only
    they are pure side-effects that do not change which operation the command
    performs. An input redirect feeds data into the command and is part of its
    identity (e.g. the migration file in ``sqlite3 db < file.sql``).
    """
    out: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if _REDIRECT_BARE_OP_RE.match(token):
            skip_next = True  # the following token is the redirect target
            continue
        if _REDIRECT_ATTACHED_RE.match(token):
            continue
        out.append(token)
    return tuple(out)


@functools.lru_cache(maxsize=128)
def analyze_command(command: str, semantic_scan_limit: int = SEMANTIC_SCAN_LIMIT) -> CommandSemantics:
    """Build an idempotent semantic representation for security analysis."""
    raw_command = command.strip() if command else ""
    tokens = tokenize_command(raw_command)
    if not tokens:
        return CommandSemantics(raw_command=raw_command)

    base_cmd = _pathless(tokens[0]).lower()
    args = tuple(tokens[1:])

    flag_tokens = []
    non_flag_tokens = []
    non_flag_tokens_raw = []  # preserve original casing for camelCase splitting
    skip_next = False
    seen_non_flag = False
    for i, token in enumerate(args):
        if skip_next:
            # This token is the value argument of a preceding short flag.
            # Absorb it into flag_tokens so it does not pollute semantic
            # analysis (e.g., the path after ``git -C <path>`` is not a
            # subcommand or positional argument).
            flag_tokens.append(token.lower())
            skip_next = False
            continue
        if _is_flag(token):
            flag_tokens.extend(_normalize_flag_token(token))
            # A single-letter short flag appearing *before* the first
            # non-flag token (the subcommand) typically consumes the next
            # token as its value argument (POSIX convention).  Examples:
            #   git -C <path>   kubectl -n <namespace>   tar -f <file>
            # Mark the next token for absorption if:
            #   1. It is a single-letter short flag (not combined like -rf)
            #   2. No non-flag token (subcommand) has been seen yet
            #   3. The next token exists and is not itself a flag
            if (
                not seen_non_flag
                and _is_short_value_flag(token)
                and i + 1 < len(args)
                and not _is_flag(args[i + 1])
            ):
                skip_next = True
            continue
        non_flag_tokens.append(token.lower())
        non_flag_tokens_raw.append(token)
        seen_non_flag = True

    semantic_tokens = (base_cmd, *non_flag_tokens)
    semantic_tokens_raw = (tokens[0], *non_flag_tokens_raw)
    head_size = max(1, semantic_scan_limit + 1)

    return CommandSemantics(
        raw_command=raw_command,
        tokens=tokens,
        base_cmd=base_cmd,
        args=args,
        flag_tokens=tuple(flag_tokens),
        non_flag_tokens=tuple(non_flag_tokens),
        semantic_tokens=tuple(semantic_tokens),
        semantic_head_tokens=tuple(semantic_tokens[:head_size]),
        semantic_head_tokens_raw=tuple(semantic_tokens_raw[:head_size]),
    )


def _contains_ordered_sequence(tokens: Iterable[str], sequence: Iterable[str]) -> bool:
    """Return True when all sequence tokens appear in order, allowing gaps.

    Internal helper -- callers must supply pre-lowercased inputs.
    Both ``tokens`` (semantic_head_tokens) and ``sequence``
    (SemanticBlockedRule.sequence) are already lowercase when produced by
    :func:`analyze_command` and :class:`SemanticBlockedRule`.
    """
    needles = tuple(sequence)
    if not needles:
        return False

    index = 0
    for token in tokens:
        if token == needles[index]:
            index += 1
            if index == len(needles):
                return True
    return False


def _pathless(token: str) -> str:
    """Strip a leading path prefix from an executable token."""
    return token.rsplit("/", 1)[-1] if "/" in token else token


def _is_flag(token: str) -> bool:
    """Check whether a token is flag-shaped."""
    return token.startswith("-") and token != "-"


def _is_short_value_flag(token: str) -> bool:
    """Return True when *token* is a single-letter short flag.

    Single-letter short flags (``-C``, ``-n``, ``-f``, ...) that appear
    before the first positional argument typically consume the next token
    as their value in POSIX-style CLIs.  Combined flags (``-rf``,
    ``-av``) and long flags (``--chdir``) are excluded -- combined flags
    are boolean bundles, and long flags use ``=`` for values which is
    already handled by ``_normalize_flag_token``.
    """
    # Must start with a single dash, NOT "--"
    if not token.startswith("-") or token.startswith("--"):
        return False
    body = token[1:]
    # Exactly one character (letter or uppercase, e.g., -C, -n, -f)
    return len(body) == 1 and body.isalpha()


def _normalize_flag_token(token: str) -> Tuple[str, ...]:
    """Normalize flag tokens for matching while preserving exact variants.

    For a long flag carrying an inline value (``--data=amount=10``), this emits
    BOTH forms:
      * the bare key ``--data`` -- so membership/classification checks in
        blocked_commands.py and mutative_verbs.py (e.g. ``flag in flag_set`` or
        ``override_flags & set(flag_tokens)``) keep matching regardless of value;
      * the whole ``--data=amount=10`` token -- so the VALUE is bound into the
        flag set and feeds into the approval signature.

    Binding the value is security-critical (Brief 71 over-match fix): the
    approval signature is derived from the full flag set, so without the whole
    token two commands differing only in a ``--flag=value`` value would collapse
    to the same signature and one grant would authorize the other (e.g.
    ``--data=amount=10`` vs ``--data=amount=1000000``). The space-form value
    (``-d '{...}'``) is already safe because it lands as a separate non-flag
    semantic token; only the inline ``--flag=value`` form was unbound.

    Build (approval_scopes.build_approval_signature) and match
    (approval_scopes.matches_approval_signature) both derive flag_tokens from
    this same function via analyze_command, so the two paths stay symmetric --
    a command still matches its own grant (reflexivity preserved).
    """
    token_lower = token.lower()

    if token_lower.startswith("--"):
        key = token_lower.split("=", 1)[0]
        if "=" in token_lower:
            # Emit both the bare key (for classification membership) and the
            # whole key=value token (to bind the value into the signature).
            return (key, token_lower)
        return (key,)

    normalized = [token_lower]
    short_body = token_lower[1:]
    if len(short_body) > 1 and short_body.isalpha():
        normalized.extend(f"-{char}" for char in short_body)
    return tuple(normalized)
