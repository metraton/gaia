"""
protected_path_guard.py -- Bash command-string protected-path guard.

The Write/Edit sensitive-path backstop (`_is_protected` in
adapters/claude_code.py) only inspects the ``file_path`` parameter of Write and
Edit tool calls. It never sees a Bash ``command`` string, so a shell command
that writes into the protected ``.claude/`` tree bypasses it entirely.

That gap became reachable once ``git mv`` was routed through
GIT_LOCAL_SAFE_SUBCOMMANDS: ``git mv payload.py .claude/hooks/pre_tool_use.py``
short-circuits to non-mutative (T0) and never reaches the tier gate, so hook
code could be overwritten through Bash with no consent. Plain ``mv``/``cp`` were
"only" T3-approvable, and shell redirects into any path are blocked generically
by the cloud-pipe validator -- but nothing enforced the ``.claude/`` boundary
for the git working-tree writers.

This guard closes that hole independently of the mutative classifier: it scans
the raw command string (per operator-split component) and CATEGORICALLY denies
any WRITE-capable command whose target is a protected Gaia path. The scope is
not restated here: it is ``protected_paths.is_protected_hook_path``, the same
predicate the Write/Edit gate calls. Two surfaces, one predicate -- because the
scope used to be duplicated in prose between them ("mirrors the Write/Edit
backstop") and the drift that produced was a security control whose reach
depended on the deployment layout. Widening one surface now widens both, so the
shell route cannot stay open against a tree the file-write route protects.

Like gaia_db_write_guard and the subagent memory-write guard, the block is
categorical and NOT approvable -- there is no T3 grant that lifts it. This is
the faithful implementation of the ``.claude/`` hard-boundary policy for shell
mechanisms: "do not attempt it", not "run it and let the hook decide".

Write capability is judged by the component's BASE TOKEN, with no flag
inspection, so do NOT expect reads to pass untouched. A command whose base is
not a listed writer does pass (``git diff .claude/hooks/x.py``,
``cat .claude/settings.json``, ``grep -r x .claude/``), but a READ-ONLY
SPELLING OF A LISTED WRITER is denied all the same: ``sed -n '1,40p'
.claude/settings.json`` prints and mutates nothing and is still categorically
denied, because the base token is ``sed``. The read itself is not withheld --
reach it with the Read tool, ``cat``, ``grep`` or ``awk``, a token outside the
write set. Do not make the guard flag-aware to soften this: ``sed`` writes its
target from a ``w`` script command with no in-place flag anywhere in the
invocation, so a flag-based guard would pass a real write through a boundary
that is categorical and therefore not approvable.

Residual limitation (accepted): a write assembled indirectly -- a protected
path reconstructed by variable interpolation or read from a file
(``git apply patch`` whose diff targets ``.claude/``) -- is not visible as a
literal token and is not caught here. The literal-token slice is the bounded,
low-false-positive portion that is closed.

A line naming an installed Gaia root, or running inside one, must be all known
readers (``_names_install_root_unread``); a hardlink made earlier is a known limit.

Public API:
    targets_protected_path(command: str, cwd: str | None = None) -> str | None
    rejection_message(path: str) -> str
    check(command: str, cwd: str | None = None) -> tuple[bool, str | None]
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from typing import List, Optional, Tuple

from .canonical_path import command_name
from .mutative_verbs import UNKNOWN_CWD, cwd_after_component, detect_mutative_command
from .protected_paths import (
    is_install_root_container,
    is_install_root_path,
    is_protected_hook_path,
    is_read_spool_path,
)
from .sensitive_paths import expand_home
from .shell_grouping import strip_grouping_wrappers
from .shell_substitution import extract_substitutions
from .shell_write_guard import downloader_paths

# ---------------------------------------------------------------------------
# Write-capability sets
# ---------------------------------------------------------------------------
# Git subcommands that write/replace working-tree files (the ones that live in
# GIT_LOCAL_SAFE_SUBCOMMANDS and therefore short-circuit the tier gate). A read
# subcommand (diff, log, show, status, blame) is deliberately absent so a read
# targeting a protected path is never blocked. ``add`` is absent for the same
# reason: it reads the working tree and writes the INDEX, so it can never
# overwrite a protected file's bytes -- and because the block is not
# approvable, listing it would leave a turn authoring a new hook module with no
# way to stage it except from the repo root, sweeping in unrelated work.
_GIT_WRITE_SUBCOMMANDS = frozenset({
    "mv", "checkout", "switch", "restore", "stash", "reset", "revert",
    "cherry-pick", "apply", "am", "rebase", "merge", "pull", "clone",
})

# Non-git base commands that write files. Plain mv/cp were only T3-approvable;
# scoping them here makes a ``.claude/`` target a categorical block regardless
# of the tier classifier -- one coherent rule: ".claude/ writes via Bash are
# blocked".
_FILESYSTEM_WRITE_COMMANDS = frozenset({
    "mv", "cp", "install", "dd", "tee", "ln", "rsync", "touch", "truncate",
    "mkdir", "sed", "chmod", "chown", "chgrp", "shred", "unlink", "rm", "tar",
    # cmd.exe builtins and PowerShell cmdlets with their aliases.
    "copy", "xcopy", "robocopy", "copy-item", "cpi", "move", "move-item", "mi",
    "ren", "rename", "rename-item", "rni", "del", "erase", "rd", "rmdir",
    "remove-item", "ri", "new-item", "ni", "set-content", "sc", "add-content", "ac",
    "out-file", "clear-content", "clc", "icacls", "attrib",
})

# Shell operators that separate independent command components. Splitting on
# these keeps a read in one component from being associated with a writer in
# another (``cat .claude/settings.json && ls`` must not fire on ``ls``).
_OPERATOR_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


_UNKNOWN_DIR = " (relative to a directory left unknown by cd -, popd or pushd)"


def _protected_spelling(token: str, cwd: Optional[str] = None) -> Optional[str]:
    """The protected path `token` names (absolute when only `cwd` made it so), or None."""
    if not token or token.startswith("-"):
        return None

    cleaned = token.lstrip("<>&|")
    cleaned = cleaned.strip("'\"")
    if not cleaned:
        return None

    if is_protected_hook_path(cleaned) or is_read_spool_path(cleaned):
        return cleaned
    if cwd and not os.path.isabs(expand_home(cleaned)):
        if cwd == UNKNOWN_CWD:
            return cleaned + _UNKNOWN_DIR
        anchored = os.path.join(cwd, cleaned)
        if is_protected_hook_path(anchored) or is_read_spool_path(anchored):
            return anchored
    return None


def _is_protected_claude_path(token: str, cwd: Optional[str] = None) -> bool:
    """Return True iff `token` names a protected Gaia path."""
    return _protected_spelling(token, cwd) is not None


def _tokenize(component: str) -> List[str]:
    """Best-effort tokenization of a single command component."""
    try:
        return shlex.split(component, posix=True)
    except ValueError:
        # Unbalanced quotes -- fall back to whitespace split so a malformed
        # command still gets scanned rather than silently skipped.
        return component.split()


_REDIRECT = re.compile(r"^(?:(?P<out>\d*>>?|&>>?|>\|)|\d*<<?<?)(?P<rest>.*)$")
_SED_SCRIPT_FLAG = re.compile(r"^-[a-zA-Z]*[ef]$")
_SED_WRITE_COMMAND = re.compile(r"(?:^|[\s;{}/])[wW]\s+(\S+)")


def _write_targets(tokens: List[str], base: str) -> Tuple[List[str], List[str]]:
    redirects: List[str] = []
    operands: List[str] = []
    scripts: List[str] = []
    pending = ""
    script_given = False
    for tok in tokens[1:]:
        match = _REDIRECT.match(tok)
        if pending:
            {"write": redirects, "script": scripts, "skip": []}[pending].append(tok)
            pending = ""
        elif match:
            rest = match.group("rest")
            if rest and not rest.startswith("&") and match.group("out"):
                redirects.append(rest)
            elif not rest:
                pending = "write" if match.group("out") else "skip"
        elif base == "sed" and _SED_SCRIPT_FLAG.match(tok):
            pending, script_given = "script", True
        elif not tok.startswith("-"):
            operands.append(tok)
    if base == "sed":
        if operands and not script_given:
            scripts.append(operands.pop(0))
        redirects += [name for script in scripts for name in _SED_WRITE_COMMAND.findall(script)]
    if base == "dd":
        operands = [op[3:] for op in operands if op.startswith("of=")]
    if base == "tar":
        directories = [tok.split("=", 1)[1] for tok in tokens if tok.startswith("--directory=")]
        operands += directories
        if _tar_extracts(tokens) and not directories and "-C" not in tokens:
            operands.append(".")
    return redirects, operands


def _tar_extracts(tokens: List[str]) -> bool:
    bundles = [tok for tok in tokens[1:] if tok.startswith("-") and not tok.startswith("--")]
    if len(tokens) > 1 and not tokens[1].startswith("-"):
        bundles.append(tokens[1])
    return any(tok in ("--extract", "--get") for tok in tokens) or any("x" in b for b in bundles)


_INTERPRETERS = frozenset({"python", "node", "perl", "ruby", "php", "deno", "bun"})
_CODE_WORD = re.compile(r"[^\s'\"(),;=`]+")
_LITERAL_CONCATENATION = re.compile(r"""(['"])\s*\+\s*(['"])""")
_NON_LITERAL_DIR = re.compile(r"[$`*?\[{]")


def _fold_cwd(component: str, cwd: Optional[str]) -> Optional[str]:
    """The cwd after `component`, UNKNOWN_CWD when a cd moved it to a directory only the shell can name."""
    folded = cwd_after_component(component, cwd)
    if folded not in (cwd, UNKNOWN_CWD) and folded and _NON_LITERAL_DIR.search(folded):
        return UNKNOWN_CWD
    return folded


def _proven_cwd(command: str, cwd: Optional[str]) -> Optional[str]:
    """The cwd in effect after the command's leading cd clauses."""
    for component in _OPERATOR_SPLIT.split(command):
        component = component.strip()
        if not component or not re.match(r"(?:cd|pushd|popd)(?:\s|$)", component):
            break
        cwd = _fold_cwd(component, cwd)
    return cwd


def _has_interpreter(command: str) -> bool:
    return any(command_name(word).rstrip("0123456789.") in _INTERPRETERS for word in command.split())


def _code_words(command: str) -> List[str]:
    """Every path-like word of inline code, with adjacent string literals joined."""
    joined = _LITERAL_CONCATENATION.sub("", command)
    return [word.lstrip("<>&|+") for word in _CODE_WORD.findall(joined)]


def _interpreter_writes_protected_path(command: str, cwd: Optional[str]) -> Optional[str]:
    """The protected path a mutating interpreter run names in its code, or None."""
    if not _has_interpreter(command):
        return None
    cwd = _proven_cwd(command, cwd)
    named = [
        hit for word in _code_words(command)
        if (os.path.isabs(expand_home(word)) or "/" in word or "." in word)
        and (hit := _protected_spelling(word, cwd))
    ]
    if named and detect_mutative_command(command).is_mutative:
        return named[0]
    return None


# Readers, not writers, are listed: every writer left out of a list was a way into an install root.
_READ_ONLY_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more", "ls", "stat", "file", "wc", "grep", "egrep",
    "fgrep", "rg", "diff", "cmp", "du", "df", "readlink", "realpath", "tree", "jq",
    "echo", "printf", "pwd", "test", "[", "true", "md5sum", "sha1sum", "sha256sum",
    "basename", "dirname", "which", "type", "cd", "pushd", "popd", "find",
    "export", "declare", "local", "readonly", "typeset",
})
_GIT_READ_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "blame", "ls-files", "ls-tree", "rev-parse",
    "cat-file", "describe", "shortlog", "grep",
})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_FIND_RUNS_OR_WRITES = frozenset({
    "-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls",
})
_NOT_READ_ONLY = " (an installed Gaia root, named by a command that is not provably read-only)"


def _git_subcommand(tokens: List[str]) -> str:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in _GIT_VALUE_OPTIONS:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token
    return ""


def _is_read_only(tokens: List[str]) -> bool:
    base = command_name(tokens[0])
    if base == "git":
        return _git_subcommand(tokens) in _GIT_READ_SUBCOMMANDS
    if base == "find":
        return not any(token in _FIND_RUNS_OR_WRITES for token in tokens)
    return base in _READ_ONLY_COMMANDS


def _program_words(tokens: List[str]) -> List[str]:
    """The program a component runs, and an interpreter's script: running installed code is not writing it."""
    interpreted = command_name(tokens[0]).rstrip("0123456789.") in _INTERPRETERS
    if interpreted and len(tokens) > 1 and not tokens[1].startswith("-"):
        return tokens[:2]
    return tokens[:1]


def _named_words(tokens: List[str]) -> List[str]:
    """Every argument word, plus the value of an attached option (-C<dir>, --directory=<dir>, of=<file>)."""
    words = []
    for token in tokens[len(_program_words(tokens)):]:
        if not token.startswith("-"):
            words.append(token)
        if "=" in token:
            words.append(token.split("=", 1)[1])
        elif token.startswith("-") and not token.startswith("--") and len(token) > 2:
            words.append(token[2:])
    return words


_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")
_GLOB = re.compile(r"[*?\[]")
_MAX_SPELLINGS = 64
_UNRESOLVED = re.compile(r"\$\{?[A-Za-z_]")
_COMPUTED = re.compile(r"\$\(|`|\$\{(?![A-Za-z_][A-Za-z0-9_]*\})")
_BACKTICK = re.compile(r"`([^`]*)`")
_WELL_KNOWN_VARIABLE = re.compile(
    r"HOME|TMPDIR|TMP|TEMP|PWD|OLDPWD|XDG_\w+|GAIA_\w+|npm_config_prefix|NPM_CONFIG_PREFIX|PREFIX"
)


def unprovable_write_target(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """A write target holding a variable neither the line nor a well-known environment variable resolves, or a substitution."""
    for line in _substituted_lines(command):
        for component in _OPERATOR_SPLIT.split(line):
            tokens = _tokenize(strip_grouping_wrappers(component.strip())) if component.strip() else []
            while tokens and _ASSIGNMENT.fullmatch(tokens[0]):
                tokens = tokens[1:]
            if not tokens:
                continue
            base = command_name(tokens[0])
            redirects, operands = _write_targets(tokens, base)
            if base == "git":
                writes = bool(operands) and operands[0] in _GIT_WRITE_SUBCOMMANDS
                operands = operands[1:]
            else:
                writes = base in _FILESYSTEM_WRITE_COMMANDS
            for target in redirects + (operands if writes else []):
                spelled = target.strip("'\"")
                if _UNRESOLVED.search(spelled) or _COMPUTED.search(spelled):
                    return target
    return None


def _computed_target_names_root(command: str, cwd: Optional[str]) -> Optional[str]:
    """The install root any word or substitution names when the line writes to an unprovable target."""
    if unprovable_write_target(command, cwd) is None:
        return None
    pieces = extract_substitutions(command) + _BACKTICK.findall(command) + _OPERATOR_SPLIT.split(command)
    for piece in pieces:
        for word in _tokenize(piece.strip()) if piece.strip() else []:
            hit = _install_root_spelling(word.split("=", 1)[-1].strip("()"), cwd)
            if hit is not None:
                return hit + _NOT_READ_ONLY
    return None


def _line_assignments(command: str) -> dict:
    values: dict = {}
    for component in _OPERATOR_SPLIT.split(command):
        for token in _tokenize(strip_grouping_wrappers(component.strip())) if component.strip() else []:
            match = _ASSIGNMENT.fullmatch(token)
            if match:
                values.setdefault(match.group(1), []).append(match.group(2))
    return values


def _substituted_lines(command: str) -> List[str]:
    """The line with $NAME and ${NAME} replaced by each value the line assigns NAME."""
    values = _line_assignments(command)
    lines = []
    for index in range(min(max([len(v) for v in values.values()] + [1]), 4)):
        def value(match, index=index):
            name = match.group(1) or match.group(2)
            assigned = values.get(name)
            if assigned:
                return assigned[min(index, len(assigned) - 1)]
            if _WELL_KNOWN_VARIABLE.fullmatch(name) and os.environ.get(name):
                return os.environ[name]
            return match.group(0)

        line = command
        for _ in range(3):
            line = _VARIABLE.sub(value, line)
        lines.append(line)
    return lines


def _brace_spellings(word: str) -> List[str]:
    match = _BRACE.search(word)
    if not match:
        return [word]
    spellings: List[str] = []
    for alternative in match.group(1).split(","):
        spellings += _brace_spellings(word[:match.start()] + alternative + word[match.end():])
        if len(spellings) >= _MAX_SPELLINGS:
            break
    return spellings[:_MAX_SPELLINGS]


def _pattern_spellings(word: str) -> List[str]:
    spellings: List[str] = []
    for spelling in _brace_spellings(word):
        parts = spelling.split("/")
        globbed = next((index for index, part in enumerate(parts) if _GLOB.search(part)), None)
        if globbed is None:
            spellings.append(spelling)
            continue
        head, tail = "/".join(parts[:globbed + 1]), parts[globbed + 1:]
        spellings += ["/".join([match] + tail) for match in glob.glob(head)[:_MAX_SPELLINGS]]
    return spellings


def _deepest_existing(path: str) -> str:
    path = os.path.abspath(path)
    while not os.path.lexists(path) and path != os.path.dirname(path):
        path = os.path.dirname(path)
    return path


def _install_root_spelling(word: str, cwd: Optional[str]) -> Optional[str]:
    """`word` when its deepest existing directory, or the static prefix before an unassigned variable, holds a root."""
    word = word.lstrip("<>&|+").strip("'\"")
    if not word:
        return None
    path = os.path.expanduser(expand_home(word))
    if not os.path.isabs(path) and not path.startswith("$") and cwd:
        if cwd == UNKNOWN_CWD:
            return None
        path = os.path.join(cwd, path)
    if "$" in path:
        prefix = path[:path.index("$")]
        directory = prefix if prefix.endswith("/") else os.path.dirname(prefix)
        if prefix and (is_install_root_path(directory) or is_install_root_container(directory)):
            return word
        return None
    for spelling in _pattern_spellings(path):
        if is_install_root_path(_deepest_existing(spelling)):
            return word
    return None


def _names_install_root_unread(command: str, cwd: Optional[str]) -> Optional[str]:
    """The install-root path a line names or runs in when the line is not provably read-only."""
    for line in _substituted_lines(command):
        hit = _line_names_install_root_unread(line, cwd)
        if hit is not None:
            return hit
    return None


def _line_names_install_root_unread(command: str, cwd: Optional[str]) -> Optional[str]:
    named: List[str] = []
    read_only = True
    programs: List[str] = []
    running_cwd = cwd
    for component in _OPERATOR_SPLIT.split(command):
        component = component.strip()
        tokens = _tokenize(strip_grouping_wrappers(component)) if component else []
        assignments = 0
        while assignments < len(tokens) and _ASSIGNMENT.fullmatch(tokens[assignments]):
            assignments += 1
        words = [token.split("=", 1)[1] for token in tokens[:assignments]]
        tokens = tokens[assignments:]
        if tokens:
            redirects, _ = _write_targets(tokens, command_name(tokens[0]))
            writes_root = any(_install_root_spelling(target, running_cwd) for target in redirects)
            read_only = read_only and not writes_root and _is_read_only(tokens)
            programs += _program_words(tokens)
            words += _named_words(tokens) + downloader_paths(tokens)
            if command_name(tokens[0]) not in ("cd", "pushd", "popd") and running_cwd \
                    and running_cwd != UNKNOWN_CWD and is_install_root_path(running_cwd):
                named.append(running_cwd)
        named += [hit for word in words if (hit := _install_root_spelling(word, running_cwd))]
        running_cwd = _fold_cwd(component, running_cwd)
    if _has_interpreter(command):
        code_cwd = _proven_cwd(command, cwd)
        named += [
            hit for word in _code_words(command)
            if word not in programs and (hit := _install_root_spelling(word, code_cwd))
        ]
    if named and not read_only:
        return named[0] + _NOT_READ_ONLY
    return None


def _component_writes_protected_path(component: str, cwd: Optional[str] = None) -> Optional[str]:
    """Return the offending protected path if `component` writes to one.

    The component is unwrapped before tokenization: the write capability is
    decided by ``tokens[0]``, so ``(cp x .claude/hooks/y.py)`` put ``(cp``
    there and walked through this categorical boundary. Unwrapping is applied
    to the component STRING, never per token -- a dangerous command quoted
    inside another command's argument stays one opaque token and must keep
    reading as a mention rather than a use.
    """
    tokens = _tokenize(strip_grouping_wrappers(component))
    if not tokens:
        return None

    base = command_name(tokens[0])
    redirects, operands = _write_targets(tokens, base)
    subcommand = operands.pop(0) if base == "git" and operands else ""
    writer = subcommand in _GIT_WRITE_SUBCOMMANDS if base == "git" else base in _FILESYSTEM_WRITE_COMMANDS

    for target in redirects + (operands if writer else []) + downloader_paths(tokens):
        hit = _protected_spelling(target, cwd)
        if hit:
            return hit

    protected_tokens = [p for p in map(_protected_spelling, tokens) if p]
    if not protected_tokens:
        return None
    for tok in tokens:
        stripped = tok.lstrip("<>&|")
        if tok != stripped:
            hit = _protected_spelling(stripped)
            if hit:
                return hit
    if writer or any(t in (">", ">>") for t in tokens):
        return protected_tokens[0]
    return None


def targets_protected_path(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """Return the offending path if `command` writes into the protected tree.

    Args:
        command: The full Bash command line (may contain operator-linked
            components, quotes, and redirects).
        cwd: The directory relative targets anchor at; ``cd`` moves it.

    Returns:
        The protected path being written (absolute when spelled relative), or
        None if the command does not write into the protected tree.
    """
    if not command:
        return None

    hit = _interpreter_writes_protected_path(command, cwd)
    if hit is not None:
        return hit

    running_cwd = cwd
    seen_cwds = [cwd]
    for component in _OPERATOR_SPLIT.split(command):
        component = component.strip()
        if not component:
            continue
        hit = _component_writes_protected_path(component, running_cwd)
        if hit is not None:
            return hit
        running_cwd = _fold_cwd(component, running_cwd)
        seen_cwds.append(running_cwd)

    # A command substitution runs BEFORE the command that contains it, so
    # ``echo $(cp payload.py .claude/hooks/pre_tool_use.py)`` overwrites the
    # hook while presenting ``echo`` as tokens[0] -- the categorical boundary
    # never fired. Scan what would actually execute. The extraction runs on the
    # FULL command rather than per component, because it is quoting that tells
    # a use from a mention and the operator split above breaks quoting; and it
    # yields nothing at all for a single-quoted or escaped mention, so a
    # protected path merely NAMED inside an argument stays free.
    for inner in extract_substitutions(command):
        for inner_component in _OPERATOR_SPLIT.split(inner):
            inner_component = inner_component.strip()
            if not inner_component:
                continue
            for inner_cwd in seen_cwds:
                hit = _component_writes_protected_path(inner_component, inner_cwd)
                if hit is not None:
                    return hit

    return _computed_target_names_root(command, cwd) or _names_install_root_unread(command, cwd)


def rejection_message(path: str) -> str:
    """Return the canonical rejection message for a protected-path write."""
    if path.endswith(_UNKNOWN_DIR):
        return (
            f"[PROTECTED_PATH] Refusing a relative write the guard cannot place: {path}. "
            f"Name the file by its absolute path. Not approvable."
        )
    if path.endswith(_NOT_READ_ONLY):
        return (
            f"[PROTECTED_PATH] Refusing a command that names or runs inside an installed "
            f"Gaia root without being provably read-only: {path}. Only reads (cat, ls, "
            f"grep, find without -exec, git status/log/diff/show, ...) may touch it. Edit "
            f"the Gaia source checkout and let `gaia install` or `gaia dev` propagate the "
            f"change. Not approvable."
        )
    if is_read_spool_path(path):
        return (
            f"[PROTECTED_PATH] Refusing to write the harness read spool: {path}. Only "
            f"the PreToolUse recorder writes it; a line written by any tool would "
            f"forge a turn's files_checked. Not approvable."
        )
    return (
        f"[PROTECTED_PATH] Refusing to write Gaia's installed code or settings "
        f"via Bash: {path}. Gaia's installed code, the .claude hook trees and "
        f"the .claude settings files are a hard security boundary: no shell "
        f"command may modify them, and this block is not approvable. Edit the "
        f"Gaia source checkout and let `gaia install` or `gaia dev` propagate "
        f"the change."
    )


def check(command: str, cwd: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Main entrypoint for PreToolUse Bash hook integration.

    Args:
        command: The Bash command line.
        cwd: The directory the command runs in.

    Returns:
        (allowed, reason)
        - (True, None)  if command does not write the protected tree
        - (False, msg)  if command writes into the protected .claude/ tree
    """
    hit = targets_protected_path(command, cwd)
    if hit is not None:
        return False, rejection_message(hit)
    return True, None
