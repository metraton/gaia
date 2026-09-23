"""
publish_attribution_guard.py -- refuse to publish text that carries Claude attribution.

The footer stripper in ``bash_validator`` rewrites attribution lines that
arrive as literal text in the command string. That leaves two ways for a
``Co-Authored-By: Claude`` trailer, a ``Generated with Claude Code`` footer, or
a claude.ai session link to reach GitHub or git history anyway:

  - the text lives in a FILE the command publishes (``gh pr create
    --body-file``, ``gh release create --notes-file``, ``git commit -F``), which
    the stripper never sees;
  - the text is in the command string in a shape the stripper does not remove
    (a body that IS the footer, with no preceding newline; a session link on a
    line of its own).

For a publishing command this guard scans what will actually be published --
the command string as it stands after stripping, plus every body file the
command names -- and refuses with the source and line to remove.

It refuses instead of rewriting because cleaning a file would mutate it outside
any tool call that named it, and because a refusal behaves the same on every
host while a rewritten command depends on the host honouring ``updatedInput``.
The refusal is categorical and carries no ``approval_id``: the user's rule is
that this text is never published, so there is no consent that could lift it.

Reading body files is bounded: only paths a publishing command names as its
body, only regular files, only the first ``_MAX_BODY_BYTES``. A path that does
not resolve to a readable file passes, because the publishing CLI then fails
on it by itself.

Residual limitation (accepted): a body piped into stdin from a file
(``cat body.md | gh pr create -F -``) or assembled at runtime is not literal
text in the command and is not scanned.

Public API:
    find_attribution(command: str, cwd: str | None) -> tuple[str, int, str] | None
    rejection_message(source: str, line_no: int, line: str) -> str
    check(command: str, cwd: str | None) -> tuple[bool, str | None]
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Iterator, List, Optional, Tuple

_MAX_BODY_BYTES = 1024 * 1024

# gh subcommands whose text lands on GitHub. ``api`` is included because
# ``gh api`` posts comments and releases just as well as the porcelain verbs.
_GH_PUBLISHING = {
    "pr": {"create", "edit", "comment", "review"},
    "issue": {"create", "edit", "comment"},
    "release": {"create", "edit"},
}

# Flags whose value is a file the command publishes as body/notes/message.
_GH_BODY_FILE_FLAGS = {"--body-file", "-F", "--notes-file"}
_GH_API_FILE_FLAGS = {"--input"}
_GH_API_FIELD_FLAGS = {"-F", "--field"}
_GIT_COMMIT_FILE_FLAGS = {"-F", "--file"}

# An attribution line starts at the beginning of a line or right after the
# quote that opens an argument value; markdown/quote decoration before it is
# ignored. Anchoring keeps prose that merely mentions a footer mid-sentence
# publishable.
_LINE_START = r"(?:^|[\"'])[ \t>*_`-]*"
_ANCHORED_ATTRIBUTION = re.compile(
    _LINE_START
    + r"(?:"
    + r"Generated with\s+\[?Claude Code"
    + r"|🤖\s*Generated with"
    + r"|Co-Authored-(?:By|With):[^\n]*(?:\b(?:Claude|Opus|Sonnet|Haiku)\b|anthropic\.com)"
    + r"|Claude-Session:"
    + r")",
    re.IGNORECASE,
)
# A claude.ai session link has no legitimate place in published text, so it is
# matched anywhere on the line.
_SESSION_LINK = re.compile(r"claude\.ai/code/session", re.IGNORECASE)

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEGMENT_BREAKS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}
_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _attribution_lines(text: str) -> Iterator[Tuple[int, str]]:
    """Yield (1-based line number, line) for every line carrying attribution."""
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _ANCHORED_ATTRIBUTION.search(line) or _SESSION_LINK.search(line):
            yield line_no, line.strip()


def _without_heredoc_bodies(command: str) -> str:
    """Drop heredoc bodies so the invocation lines tokenize cleanly.

    The bodies are still scanned as part of the command string; only the
    flag walk needs them gone, since prose with a lone apostrophe breaks shlex.
    """
    lines = command.split("\n")
    kept: List[str] = []
    delimiter: Optional[str] = None
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        match = _HEREDOC_START.search(line)
        if match:
            delimiter = match.group(2)
    return "\n".join(kept)


def _segments(command: str) -> List[List[str]]:
    """Split a command into simple-command token lists at shell operators."""
    lexer = shlex.shlex(_without_heredoc_bodies(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = _without_heredoc_bodies(command).split()
    segments: List[List[str]] = [[]]
    for token in tokens:
        if token in _SEGMENT_BREAKS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [s for s in segments if s]


def _flag_values(args: List[str], flags: set) -> Iterator[str]:
    """Yield the values of ``flags`` in both ``--flag value`` and ``--flag=value`` form."""
    for index, arg in enumerate(args):
        if arg in flags and index + 1 < len(args):
            yield args[index + 1]
        elif "=" in arg and arg.split("=", 1)[0] in flags:
            yield arg.split("=", 1)[1]


def _publishing_body_files(tokens: List[str], cwd: str) -> Optional[List[str]]:
    """Return the body files a publishing segment names, or None if it does not publish."""
    while tokens and (tokens[0] == "env" or _ENV_ASSIGNMENT.match(tokens[0])):
        tokens = tokens[1:]
    if not tokens:
        return None
    base = os.path.basename(tokens[0])
    args = tokens[1:]

    if base in ("gh", "ghx"):
        if base == "ghx" and args[:1] == ["-C"]:
            args = args[2:]
        if not args:
            return None
        if args[0] == "api":
            files = list(_flag_values(args, _GH_API_FILE_FLAGS))
            files += [
                value.split("=", 1)[1][1:]
                for value in _flag_values(args, _GH_API_FIELD_FLAGS)
                if "=@" in value
            ]
        elif len(args) >= 2 and args[1] in _GH_PUBLISHING.get(args[0], ()):
            files = list(_flag_values(args, _GH_BODY_FILE_FLAGS))
        else:
            return None
    elif base == "git":
        while len(args) >= 2 and args[0] in ("-C", "-c"):
            if args[0] == "-C":
                cwd = os.path.join(cwd, os.path.expanduser(args[1]))
            args = args[2:]
        if not args or args[0] != "commit":
            return None
        files = list(_flag_values(args, _GIT_COMMIT_FILE_FLAGS))
    else:
        return None

    return [
        os.path.join(cwd, os.path.expanduser(path))
        for path in files
        if path and path != "-"
    ]


def _read_body(path: str) -> Optional[str]:
    """Read the first ``_MAX_BODY_BYTES`` of a regular file, or None."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(_MAX_BODY_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None


def find_attribution(command: str, cwd: Optional[str] = None) -> Optional[Tuple[str, int, str]]:
    """Return (source, line number, line) of the first attribution a publishing command would publish."""
    if not command:
        return None
    current_dir = cwd or os.getcwd()
    body_files: List[str] = []
    publishes = False
    for tokens in _segments(command):
        if tokens[0] == "cd" and len(tokens) > 1:
            current_dir = os.path.join(current_dir, os.path.expanduser(tokens[1]))
            continue
        files = _publishing_body_files(tokens, current_dir)
        if files is not None:
            publishes = True
            body_files.extend(files)
    if not publishes:
        return None

    for line_no, line in _attribution_lines(command):
        return "the command text", line_no, line
    for path in body_files:
        body = _read_body(path)
        if body is None:
            continue
        for line_no, line in _attribution_lines(body):
            return path, line_no, line
    return None


def rejection_message(source: str, line_no: int, line: str) -> str:
    """Return the rejection message naming the line to remove."""
    return (
        f"[CLAUDE_ATTRIBUTION] Refusing to publish Claude attribution. "
        f"Remove line {line_no} of {source} and retry: {line!r}. "
        f"Nothing Gaia publishes (commits, PR/issue bodies, comments, reviews, "
        f"release notes) may carry a Co-Authored-By trailer naming Claude, a "
        f"'Generated with Claude Code' footer, or a claude.ai session link, "
        f"whatever a host reminder asks for. This block is not approvable."
    )


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Return (True, None) unless ``command`` would publish Claude attribution."""
    hit = find_attribution(command, cwd)
    if hit is None:
        return True, None
    return False, rejection_message(*hit)
