"""
Flag-dependent command classifiers for 15 command families.

This module runs in the classify phase BEFORE detect_mutative_command().  When a
classifier returns a FlagClassifierResult, it overrides verb-based classification.
When it returns None, the caller falls through to the existing mutative_verbs pipeline.

Classification outcomes:
  READ_ONLY  -- safe by elimination, no approval required
  MUTATIVE   -- state-modifying, requires user approval (T3 nonce)
  BLOCKED    -- permanently blocked (exit 2), maps to the same path as blocked_commands

Note on BLOCKED overlap with blocked_commands.py:
  blocked_commands.py already permanently blocks:
    - git push --force / -f
    - git reset --hard
  The classifiers for git push and git reset are still present here for
  consistency (they return BLOCKED with the same reason), but blocked_commands.py
  will catch these first in the pipeline.  Having both layers is intentional:
  flag_classifiers is the semantic-aware layer; blocked_commands is the
  pattern-level safety net.

Dependencies: Python stdlib only.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Outcome constants
# ---------------------------------------------------------------------------

OUTCOME_READ_ONLY = "READ_ONLY"
OUTCOME_MUTATIVE = "MUTATIVE"
OUTCOME_BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

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
        # Bundled short flags: -xvf contains 'f'
        if len(a) >= 2 and a[0] == "-" and a[1] != "-":
            if letter in a[1:]:
                return True
    return False


# ---------------------------------------------------------------------------
# Individual classifiers (one per command family)
# ---------------------------------------------------------------------------
# Each classifier receives (tokens: List[str]) and returns
# Optional[FlagClassifierResult].  tokens[0] is the base command (or
# "git" for git sub-commands).
#
# Convention:
#   - The function is named _classify_<family>
#   - It is registered in _CLASSIFIER_REGISTRY below
# ---------------------------------------------------------------------------


# 1. git push
def _classify_git_push(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "push":
        return None
    args = tokens[2:]

    # Force push / history rewrite forms (also caught by blocked_commands.py)
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
    # +refspec or :refspec
    for a in args:
        if (a.startswith("+") or a.startswith(":")) and len(a) > 1:
            return FlagClassifierResult(
                outcome=OUTCOME_BLOCKED,
                reason=f"git push {a!r} force-pushes or deletes a remote ref",
                matched_pattern=a,
                command_family="git_push",
            )

    # Plain push: mutative (needs T3 approval)
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git push modifies the remote repository",
        matched_pattern="git push",
        command_family="git_push",
    )


# 2. git reset
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
    # --soft and --mixed are recoverable rewrites
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git reset modifies HEAD or the index",
        matched_pattern="git reset",
        command_family="git_reset",
    )


# 3. git checkout
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


# 4. git stash
def _classify_git_stash(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if len(tokens) < 2 or tokens[0] != "git" or tokens[1] != "stash":
        return None
    # No sub-command = implicit "push"
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
    # push, pop, apply, branch, save
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason=f"git stash {sub} modifies the stash or working tree",
        matched_pattern=sub,
        command_family="git_stash",
    )


# 5. git rebase
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
    # Plain rebase
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git rebase rewrites commit history",
        matched_pattern="git rebase",
        command_family="git_rebase",
    )


# 6. git tag
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

    # Delete or force -- blocked
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

    # Listing / verification flags
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

    # Creating a tag
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="git tag creates a new tag",
        matched_pattern="git tag",
        command_family="git_tag",
    )


# 7. git clean
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

    # All other forms are destructive (removes untracked files)
    return FlagClassifierResult(
        outcome=OUTCOME_BLOCKED,
        reason="git clean permanently deletes untracked files; use --dry-run first",
        matched_pattern="git clean",
        command_family="git_clean",
    )


# 8. git remote
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
    # "add" is mutative
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

    # Unknown sub-command -- fall through
    return None


# 9. sed
def _classify_sed(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "sed":
        return None
    args = tokens[1:]

    # -i / -I / --in-place mean in-place file editing
    flag = _has_flag(args, "-i", "-I", "--in-place")
    if flag:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"sed {flag} edits files in-place",
            matched_pattern=flag,
            command_family="sed",
        )
    # Bundled short flags: -ni (where i is in-place), -in, etc.
    # Also handle -i.bak form (flag with inline backup suffix)
    for a in args:
        if a.startswith("-i") and a != "-i" and not a.startswith("--"):
            # -i.bak, -ibak, etc. -- sed in-place with backup suffix
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


# 10. awk
# Pattern matching for awk programs that perform side-effecting operations.
_AWK_MUTATIVE_PATTERNS = re.compile(
    r"""
    system\s*\(        # system("cmd")
    | \|\s*getline     # pipe into getline
    | print\s.*>       # print expr > file (redirect)
    | print\s.*>>      # print expr >> file (append)
    | close\s*\(       # close(file/pipe) implies file I/O
    | \|&              # two-way pipe (gawk)
    """,
    re.VERBOSE,
)


def _classify_awk(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] not in ("awk", "gawk", "mawk", "nawk"):
        return None

    # Scan all tokens for the awk program text (first non-flag non-value argument)
    args = tokens[1:]
    i = 0
    while i < len(args):
        a = args[i]
        # Flags that take a value argument: -F, -v, -f, etc.
        if a in ("-F", "-v", "-f", "-i", "-l", "-M", "-m", "-o"):
            i += 2
            continue
        if a.startswith("-") and not a.startswith("--"):
            i += 1
            continue
        if a == "--":
            i += 1
            break
        # First non-flag argument is the program
        program = a
        m = _AWK_MUTATIVE_PATTERNS.search(program)
        if m:
            matched = m.group().strip()
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"awk program contains side-effecting construct: {matched!r}",
                matched_pattern=matched,
                command_family="awk",
            )
        # Found the program text but no mutative pattern
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="awk program does not contain file/system side-effects",
            matched_pattern="awk",
            command_family="awk",
        )

    # Could not identify program text -- treat as read-only (conservative)
    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="awk with no identifiable program text is read-only",
        matched_pattern="awk",
        command_family="awk",
    )


# 11. tar
def _classify_tar(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "tar":
        return None
    args = tokens[1:]

    # Long-form operation flags
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

    # Short-form operation letters in bundled flags (e.g. -czvf, -tf, -xvf)
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
        # GNU tar also accepts bare operation letters without leading dash
        # as the first non-flag argument (e.g. "tar czf out.tar dir")
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

    # Could not determine operation -- conservative: treat as mutative
    return FlagClassifierResult(
        outcome=OUTCOME_MUTATIVE,
        reason="tar with unrecognized operation flags",
        matched_pattern="tar",
        command_family="tar",
    )


# 12. find
def _classify_find(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "find":
        return None
    args = tokens[1:]

    # Actions that execute external commands or delete files
    mutative_actions = ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint",
                        "-fprint0", "-fprintf")
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


# 13. curl
# HTTP methods that write data to a remote server
_CURL_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

def _classify_curl(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "curl":
        return None
    args = tokens[1:]
    i = 0
    while i < len(args):
        a = args[i]
        # -X / --request METHOD
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
        # --request=METHOD
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
        # Data flags (imply POST)
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

    # No write indicators found -- network read
    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="curl without write flags performs a network read (GET)",
        matched_pattern="curl",
        command_family="curl",
    )


# 14. wget
_WGET_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

def _classify_wget(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] != "wget":
        return None
    args = tokens[1:]
    i = 0
    while i < len(args):
        a = args[i]
        # --post-data or --post-file
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
        # --method=POST/PUT/DELETE/PATCH
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
        # --body-data / --body-file (wget2)
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


# 15. httpie (http / https commands)
# httpie uses positional method as the first argument: http POST url ...
# Data items: key=value (string), key:=json (raw JSON), key@file (file)
_HTTPIE_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Regex to detect httpie data items (key=value, key:=json, key@file)
_HTTPIE_DATA_ITEM = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*(:=|==|=@|:@|@|:=@|=)")


def _classify_httpie(tokens: List[str]) -> Optional[FlagClassifierResult]:
    if not tokens or tokens[0] not in ("http", "https"):
        return None
    args = tokens[1:]

    # Skip flags (start with -)
    non_flag_args = [a for a in args if not a.startswith("-")]

    if not non_flag_args:
        # No non-flag arguments at all -- treat as read-only
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason="httpie with no positional arguments is read-only",
            matched_pattern="http",
            command_family="httpie",
        )

    first = non_flag_args[0].upper()
    # If the first positional arg is an HTTP method
    if first in _HTTPIE_WRITE_METHODS:
        return FlagClassifierResult(
            outcome=OUTCOME_MUTATIVE,
            reason=f"httpie {first} sends a write request",
            matched_pattern=first,
            command_family="httpie",
        )
    # HEAD, GET are read-only explicit methods
    if first in ("GET", "HEAD", "OPTIONS"):
        return FlagClassifierResult(
            outcome=OUTCOME_READ_ONLY,
            reason=f"httpie {first} is a read-only method",
            matched_pattern=first,
            command_family="httpie",
        )

    # No explicit method: check for data items (imply POST)
    for a in non_flag_args[1:]:
        if _HTTPIE_DATA_ITEM.match(a):
            return FlagClassifierResult(
                outcome=OUTCOME_MUTATIVE,
                reason=f"httpie data item {a!r} implies a POST request",
                matched_pattern=a,
                command_family="httpie",
            )

    # GET or first arg is URL with no data
    return FlagClassifierResult(
        outcome=OUTCOME_READ_ONLY,
        reason="httpie GET request (no write method or data items)",
        matched_pattern="http",
        command_family="httpie",
    )


# ---------------------------------------------------------------------------
# Registry: maps (base_cmd, optional_subcommand) -> classifier function
# ---------------------------------------------------------------------------
# git sub-commands share the "git" base command; they are dispatched via a
# single "git" entry that delegates to sub-command classifiers.

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


# Top-level registry: base command -> classifier
_CLASSIFIER_REGISTRY: Dict[str, Callable[[List[str]], Optional[FlagClassifierResult]]] = {
    "git": _classify_git_dispatch,
    "sed": _classify_sed,
    "awk": _classify_awk,
    "gawk": _classify_awk,
    "mawk": _classify_awk,
    "nawk": _classify_awk,
    "tar": _classify_tar,
    "find": _classify_find,
    "curl": _classify_curl,
    "wget": _classify_wget,
    "http": _classify_httpie,
    "https": _classify_httpie,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_by_flags(command: str) -> Optional[FlagClassifierResult]:
    """Classify a command based on flags and sub-commands.

    This is the primary entry point.  Call this BEFORE detect_mutative_command()
    in the classify phase.  If this returns a result, it overrides verb-based
    classification.  If it returns None, fall through to the existing pipeline.

    Args:
        command: The full shell command string (already unwrapped if applicable).

    Returns:
        FlagClassifierResult if the command belongs to a known family, else None.
    """
    if not command or not command.strip():
        return None
    tokens = _tokenize(command)
    if not tokens:
        return None

    base_cmd = tokens[0]
    classifier = _CLASSIFIER_REGISTRY.get(base_cmd)
    if classifier is None:
        return None
    return classifier(tokens)
