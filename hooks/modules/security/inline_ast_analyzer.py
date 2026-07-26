"""
AST-based analyzer for inline interpreter code.

When a runtime interpreter is invoked with an inline code flag (e.g.,
``python3 -c "..."``), the payload is opaque to shell-token scanners.  Pure
regex matching false-positives on *mentions* of dangerous APIs:

    python3 -c "import subprocess; print('hi')"

Here ``subprocess`` is *imported* but never *invoked* — there is no real
mutative behavior.  This module classifies inline code by **invocation**
rather than mention by parsing the payload with the language's native AST
(``ast.parse`` for Python).

Design:

- ``analyze_python_inline(code)`` returns an :class:`InlineAstResult` with
  the matched dangerous call (or ``None`` if the code is safe).
- The analyzer walks the AST and inspects ``Call`` nodes only.  Bare
  ``Import``/``ImportFrom`` statements, attribute references that are not
  inside a call, and string literals that happen to contain dangerous
  identifiers do **not** trigger.
- Mode-dependent openers (``open``, ``io.open``, ``codecs.open``,
  ``pathlib.Path.open``) are gated on the mode argument, and ``os.open`` on
  its integer flags, so a read-only open is classified safe while a
  write/append/truncate open is FILE_WRITE.  ``_MODE_ARG_INDEX`` and
  ``_OS_OPEN_WRITE_FLAGS`` hold the per-API argument positions.
- Call CHAINS are resolved through intermediate calls, because the idiomatic
  form of most mutating APIs is a chain, not a flat ``module.function(...)``:
  ``Path("/x").unlink()`` resolves to ``pathlib.Path.unlink`` and
  ``boto3.client("s3").delete_object(...)`` to
  ``boto3.client.delete_object``.  Local bindings of a resolvable call
  (``c = boto3.client("s3")``) are tracked so the two-statement form resolves
  identically.  Without this the whole ``pathlib.Path.*`` block below would be
  unreachable in practice and no cloud-SDK entry could ever match.
- Cloud-SDK mutations are matched by module PREFIX plus an invoked-method
  VERB (``_CLOUD_SDK_PREFIXES`` / ``_CLOUD_SDK_MUTATIVE_VERBS``) rather than
  by enumerating method names: the surface is thousands of generated methods
  per SDK, and they are syntactically indistinguishable from reads except by
  their verb.
- If the payload fails to parse (``SyntaxError``), the function returns a
  result with ``parse_failed=True`` so the caller can fall back to the
  legacy regex layer rather than allowing the command unconditionally.

CEILING (read this before trusting a clean result)
--------------------------------------------------
This is static classification over source text; it is a gate, not
containment.  A clean result means "no catalogued invocation was found by
name", never "this payload cannot mutate".  It is defeated, by design and
without any effort, by: dynamic dispatch (``getattr(shutil, "copy" + "file")``,
``importlib.import_module``, ``eval``/``exec``), an indirection through a
helper defined in another module or fetched at runtime, a bound handle whose
write mode was decided elsewhere (``f.write(...)``, ``cur.execute(...)``), and
by the next library nobody catalogued.  Every entry below narrows a known
path; none of them bounds the reachable syscalls.  Treat additions here as
raising the cost of an accident, not as coverage.

Bash inline code (``bash -c "..."``, ``sh -c "..."``) is **not** parsed
here — bash AST libraries require a runtime dependency (``bashlex``) that
is not yet vendored.  The caller continues to handle bash payloads through
``shell_unwrapper`` + ``blocked_commands`` (Layer 1 in
``_check_inline_code``).
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from typing import FrozenSet, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Result type
# ============================================================================

@dataclass(frozen=True)
class InlineAstResult:
    """Outcome of inspecting a Python payload via AST.

    Attributes:
        is_dangerous: True when an actual invocation of a dangerous API is
            detected.  Mentions, imports, and references-only do not count.
        label: Short human-readable identifier for the matched call
            (e.g. ``"subprocess-run"``, ``"os-system"``, ``"open-write"``).
            Empty string when ``is_dangerous`` is False.
        category: Category constant — ``"PROCESS_EXECUTION"``,
            ``"FILE_DELETION"``, ``"FILE_WRITE"``, ``"FILE_MUTATION"``,
            ``"PERMISSION_MOD"``, ``"NETWORK"``, ``"CLOUD_SDK"``, or ``""``
            when safe.
        parse_failed: True when ``ast.parse`` raised ``SyntaxError`` and the
            caller should fall back to its regex layer.  ``is_dangerous`` is
            ``False`` in that case.
        detail: Source-level detail (the dotted call name) for diagnostics.
    """

    is_dangerous: bool = False
    label: str = ""
    category: str = ""
    parse_failed: bool = False
    detail: str = ""


# ============================================================================
# Call signature catalog
# ============================================================================
# Keys are dotted names as they appear *in source* (not aliased imports).
# We accept both the bare attribute (``subprocess.run``) and a leaf-only
# match (``run`` after ``from subprocess import run``) by tracking aliases
# during AST walking.

# (dotted-name, label, category)
_DANGEROUS_CALLS: Tuple[Tuple[str, str, str], ...] = (
    # --- Process execution ---
    ("subprocess.run", "subprocess-run", "PROCESS_EXECUTION"),
    ("subprocess.call", "subprocess-call", "PROCESS_EXECUTION"),
    ("subprocess.check_call", "subprocess-check-call", "PROCESS_EXECUTION"),
    ("subprocess.check_output", "subprocess-check-output", "PROCESS_EXECUTION"),
    ("subprocess.Popen", "subprocess-popen", "PROCESS_EXECUTION"),
    ("subprocess.getoutput", "subprocess-getoutput", "PROCESS_EXECUTION"),
    ("subprocess.getstatusoutput", "subprocess-getstatusoutput", "PROCESS_EXECUTION"),
    ("os.system", "os-system", "PROCESS_EXECUTION"),
    ("os.popen", "os-popen", "PROCESS_EXECUTION"),
    ("os.execv", "os-exec", "PROCESS_EXECUTION"),
    ("os.execve", "os-exec", "PROCESS_EXECUTION"),
    ("os.execvp", "os-exec", "PROCESS_EXECUTION"),
    ("os.execvpe", "os-exec", "PROCESS_EXECUTION"),
    ("os.execl", "os-exec", "PROCESS_EXECUTION"),
    ("os.execle", "os-exec", "PROCESS_EXECUTION"),
    ("os.execlp", "os-exec", "PROCESS_EXECUTION"),
    ("os.execlpe", "os-exec", "PROCESS_EXECUTION"),
    ("os.spawnv", "os-spawn", "PROCESS_EXECUTION"),
    ("os.spawnve", "os-spawn", "PROCESS_EXECUTION"),
    ("os.spawnvp", "os-spawn", "PROCESS_EXECUTION"),
    ("os.spawnl", "os-spawn", "PROCESS_EXECUTION"),
    ("pty.spawn", "pty-spawn", "PROCESS_EXECUTION"),

    # --- File deletion ---
    ("os.remove", "os-remove", "FILE_DELETION"),
    ("os.unlink", "os-unlink", "FILE_DELETION"),
    ("os.rmdir", "os-rmdir", "FILE_DELETION"),
    ("os.removedirs", "os-removedirs", "FILE_DELETION"),
    ("shutil.rmtree", "shutil-rmtree", "FILE_DELETION"),
    ("pathlib.Path.unlink", "pathlib-unlink", "FILE_DELETION"),
    ("pathlib.Path.rmdir", "pathlib-rmdir", "FILE_DELETION"),

    # --- File mutation ---
    # ``shutil.copyfile`` sits one character from ``shutil.copy`` and reaches
    # the same syscalls; it was absent while its four siblings were present,
    # which is exactly how a copy gets performed after ``cp`` is refused.  The
    # rule for this block is the EFFECT (bytes or metadata on disk change),
    # never the spelling.
    ("os.rename", "os-rename", "FILE_MUTATION"),
    ("os.renames", "os-renames", "FILE_MUTATION"),
    ("os.replace", "os-replace", "FILE_MUTATION"),
    ("os.makedirs", "os-makedirs", "FILE_MUTATION"),
    ("os.mkdir", "os-mkdir", "FILE_MUTATION"),
    ("os.mknod", "os-mknod", "FILE_MUTATION"),
    ("os.mkfifo", "os-mkfifo", "FILE_MUTATION"),
    ("os.symlink", "os-symlink", "FILE_MUTATION"),
    ("os.link", "os-link", "FILE_MUTATION"),
    ("os.truncate", "os-truncate", "FILE_WRITE"),
    ("os.ftruncate", "os-ftruncate", "FILE_WRITE"),
    ("os.write", "os-write", "FILE_WRITE"),
    ("os.writev", "os-writev", "FILE_WRITE"),
    ("os.pwrite", "os-pwrite", "FILE_WRITE"),
    ("os.utime", "os-utime", "FILE_MUTATION"),
    ("shutil.copy", "shutil-copy", "FILE_MUTATION"),
    ("shutil.copy2", "shutil-copy2", "FILE_MUTATION"),
    ("shutil.copyfile", "shutil-copyfile", "FILE_MUTATION"),
    ("shutil.copyfileobj", "shutil-copyfileobj", "FILE_WRITE"),
    ("shutil.copytree", "shutil-copytree", "FILE_MUTATION"),
    ("shutil.move", "shutil-move", "FILE_MUTATION"),
    ("shutil.make_archive", "shutil-make-archive", "FILE_WRITE"),
    ("shutil.unpack_archive", "shutil-unpack-archive", "FILE_WRITE"),
    ("pathlib.Path.rename", "pathlib-rename", "FILE_MUTATION"),
    ("pathlib.Path.replace", "pathlib-replace", "FILE_MUTATION"),
    ("pathlib.Path.write_text", "pathlib-write-text", "FILE_WRITE"),
    ("pathlib.Path.write_bytes", "pathlib-write-bytes", "FILE_WRITE"),
    ("pathlib.Path.touch", "pathlib-touch", "FILE_MUTATION"),
    ("pathlib.Path.mkdir", "pathlib-mkdir", "FILE_MUTATION"),
    ("pathlib.Path.symlink_to", "pathlib-symlink-to", "FILE_MUTATION"),
    ("pathlib.Path.hardlink_to", "pathlib-hardlink-to", "FILE_MUTATION"),
    # ``Path.link_to`` is the 3.8-3.11 spelling of ``hardlink_to`` (removed in
    # 3.12); a payload targeting an older interpreter still reaches link(2).
    ("pathlib.Path.link_to", "pathlib-link-to", "FILE_MUTATION"),

    # --- Mode / flag dependent openers (gated in _build_open_aware_result) ---
    # Listed here so one lookup covers them, but they only escalate when the
    # mode or flag argument permits writing; a read-only open stays safe.
    ("open", "open-write", "FILE_WRITE"),
    ("io.open", "io-open-write", "FILE_WRITE"),
    ("codecs.open", "codecs-open-write", "FILE_WRITE"),
    ("pathlib.Path.open", "pathlib-open-write", "FILE_WRITE"),
    ("os.open", "os-open-write", "FILE_WRITE"),

    # --- Permission modification ---
    ("os.chmod", "os-chmod", "PERMISSION_MOD"),
    ("os.chown", "os-chown", "PERMISSION_MOD"),
    ("os.lchmod", "os-lchmod", "PERMISSION_MOD"),
    ("os.lchown", "os-lchown", "PERMISSION_MOD"),
    ("os.fchmod", "os-fchmod", "PERMISSION_MOD"),
    ("os.fchown", "os-fchown", "PERMISSION_MOD"),
    ("os.chflags", "os-chflags", "PERMISSION_MOD"),
    ("os.lchflags", "os-lchflags", "PERMISSION_MOD"),
    ("os.setxattr", "os-setxattr", "PERMISSION_MOD"),
    ("os.removexattr", "os-removexattr", "PERMISSION_MOD"),
    ("shutil.chown", "shutil-chown", "PERMISSION_MOD"),
    ("shutil.copymode", "shutil-copymode", "PERMISSION_MOD"),
    ("shutil.copystat", "shutil-copystat", "PERMISSION_MOD"),
    ("pathlib.Path.chmod", "pathlib-chmod", "PERMISSION_MOD"),
    ("pathlib.Path.lchmod", "pathlib-lchmod", "PERMISSION_MOD"),

    # --- Network egress (broad: any HTTP write or socket open) ---
    ("urllib.request.urlopen", "urlopen", "NETWORK"),
    ("requests.get", "requests-get", "NETWORK"),
    ("requests.post", "requests-post", "NETWORK"),
    ("requests.put", "requests-put", "NETWORK"),
    ("requests.delete", "requests-delete", "NETWORK"),
    ("requests.patch", "requests-patch", "NETWORK"),
    ("requests.request", "requests-request", "NETWORK"),
    ("httpx.get", "httpx-get", "NETWORK"),
    ("httpx.post", "httpx-post", "NETWORK"),
    ("socket.socket", "socket-open", "NETWORK"),
)

_DANGEROUS_BY_DOTTED = {entry[0]: entry for entry in _DANGEROUS_CALLS}

# Modules whose entire surface we treat as dangerous-when-called.  Used as a
# safety net for aliased imports (``from subprocess import run as r``) when
# we cannot statically resolve the underlying attribute.
_DANGEROUS_LEAVES_BY_MODULE: dict = {}
for _dotted, _label, _category in _DANGEROUS_CALLS:
    if "." in _dotted:
        _mod, _leaf = _dotted.rsplit(".", 1)
        _DANGEROUS_LEAVES_BY_MODULE.setdefault(_mod, {})[_leaf] = (_label, _category)

# Mode characters that escalate ``open(...)`` to FILE_WRITE.  Lower-cased
# during inspection.  ``r+``, ``w``, ``a``, ``x`` all permit writing.
_OPEN_WRITE_MODE_CHARS: FrozenSet[str] = frozenset({"w", "a", "x", "+"})

# Openers whose danger depends on an argument, and WHERE that argument sits.
# ``open(path, mode)`` and its two aliases take the mode second; the bound
# ``Path.open(mode)`` takes it first, because the path is the receiver.  An
# entry here means the catalog hit is gated, not accepted outright.
_MODE_ARG_INDEX: dict = {
    "open": 1,
    "io.open": 1,
    "codecs.open": 1,
    "pathlib.Path.open": 0,
}

# ``os.open`` takes integer flags rather than a mode string, so it is gated on
# the flag NAMES appearing in the expression (``os.O_WRONLY | os.O_CREAT``).
# Any of these permits creating, truncating, or writing; ``os.O_RDONLY`` alone
# does not.  Flags computed from a variable are unresolvable and left ungated
# (same posture as an unresolvable mode string) -- see the module CEILING note.
_OS_OPEN_FLAG_ARG_INDEX = 1
_OS_OPEN_WRITE_FLAGS: FrozenSet[str] = frozenset({
    "O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_EXCL",
    "O_TMPFILE",
})


# ============================================================================
# Cloud SDK mutation catalog
# ============================================================================
# The NETWORK category above matches what egress LOOKS like syntactically
# (``requests.post``, ``socket.socket``).  A cloud SDK looks like nothing of
# the sort: the transport is hidden inside a generated client, so
# ``boto3.client("s3").delete_object(...)`` is, to a name-matching catalog,
# indistinguishable from any other attribute chain -- and it deletes a real
# object in a real account.
#
# Enumerating method names is not viable (thousands per SDK, generated from
# discovery documents), so the rule is PREFIX + VERB: the chain must root in a
# known SDK module AND the invoked method must carry a mutative verb token.
# The prefix gate is what keeps the verb list from firing on ordinary code --
# ``s.replace(...)`` on a string is untouched because ``str`` is not an SDK.
_CLOUD_SDK_PREFIXES: Tuple[str, ...] = (
    "boto3",
    "google.cloud",
    "googleapiclient",
    "kubernetes.client",
    "python_terraform",
)

# Verb tokens that mean "this call changes the account/cluster/state".
# ``destroy`` and ``terminate`` are here because they are the most destructive
# operations in the terraform and EC2 surfaces respectively; ``put``,
# ``remove`` and ``replace`` because S3 and the Kubernetes API spell their
# writes that way.  Deliberately EXCLUDED: ``set`` -- ``boto3.set_stream_logger``
# is local logging configuration and would false-positive.  This list narrows
# a known set of verbs; it is not a closure over what an SDK can mutate.
_CLOUD_SDK_MUTATIVE_VERBS: FrozenSet[str] = frozenset({
    "delete", "create", "update", "apply", "patch", "insert",
    "destroy", "terminate", "put", "remove", "replace",
})

# Split an identifier into lowercase word tokens on both ``_`` and camelCase
# boundaries, so ``delete_object`` and ``deleteObject`` both yield ``delete``
# while ``get_creation_time`` yields ``creation`` (never ``create``).  Token
# equality, not substring matching, is what keeps that distinction.
_IDENT_WORD_RE = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _match_cloud_sdk(dotted: str) -> Optional[Tuple[str, str]]:
    """Return ``(label, category)`` when ``dotted`` is a cloud-SDK mutation.

    Args:
        dotted: Fully resolved chain name, e.g.
            ``"boto3.client.delete_object"`` or
            ``"google.cloud.storage.Client.bucket.delete"``.

    Returns:
        ``(label, category)`` when the chain roots in a known SDK prefix and
        the INVOKED method (the last segment) carries a mutative verb token;
        ``None`` otherwise.  Only the invoked segment is inspected -- an
        intermediate ``.delete(...)`` in a longer chain is itself a ``Call``
        node the walker visits separately, so mid-chain verbs are still caught
        without widening the match here.
    """
    prefix = next(
        (
            p for p in _CLOUD_SDK_PREFIXES
            if dotted == p or dotted.startswith(p + ".")
        ),
        None,
    )
    if prefix is None:
        return None
    leaf = dotted.rsplit(".", 1)[-1]
    tokens = {t.lower() for t in _IDENT_WORD_RE.findall(leaf)}
    verb = tokens & _CLOUD_SDK_MUTATIVE_VERBS
    if not verb:
        return None
    return (f"cloud-sdk-{sorted(verb)[0]}", "CLOUD_SDK")


# ============================================================================
# Public entry point
# ============================================================================

def analyze_python_inline(code: str) -> InlineAstResult:
    """Classify a Python inline-code payload by invocation, not mention.

    Args:
        code: The Python source string extracted from ``python3 -c "..."``.
            Already unquoted; the caller is responsible for stripping the
            surrounding shell quotes before passing it in.

    Returns:
        :class:`InlineAstResult` with ``is_dangerous`` set when at least one
        ``Call`` node matches a known dangerous signature, ``parse_failed``
        when the payload is not valid Python, otherwise a "safe" result.
    """
    if not code or not code.strip():
        return InlineAstResult()

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        # Fall through with parse_failed=True so the caller can decide.
        return InlineAstResult(parse_failed=True)

    # Build alias maps from imports so ``from subprocess import run as r`` and
    # ``import subprocess as sp`` both resolve correctly during call walk.
    alias_map = _AliasResolver()
    alias_map.collect(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        dotted = alias_map.resolve_call(node.func)
        if dotted is None:
            continue

        # Direct hit on full dotted name.  A mode/flag-gated opener can come
        # back SAFE from the builder, so the walk continues instead of
        # returning -- otherwise a read-only ``open(p)`` early in the payload
        # would mask a real mutation later in it.
        match = _DANGEROUS_BY_DOTTED.get(dotted)
        if match is not None:
            _, label, category = match
            result = _build_open_aware_result(node, dotted, label, category)
            if result.is_dangerous:
                return result
            continue

        # Module-level fallback: ``mod.leaf(...)`` where leaf is in the
        # dangerous catalog for that module (handles cases the explicit
        # _DANGEROUS_BY_DOTTED entries cover, plus any aliased re-exports).
        if "." in dotted:
            mod, leaf = dotted.rsplit(".", 1)
            mod_leaves = _DANGEROUS_LEAVES_BY_MODULE.get(mod)
            if mod_leaves and leaf in mod_leaves:
                label, category = mod_leaves[leaf]
                result = _build_open_aware_result(node, dotted, label, category)
                if result.is_dangerous:
                    return result
                continue

        # Cloud SDK mutation: matched by module prefix + invoked verb, since
        # the method surface is generated and cannot be enumerated.
        cloud = _match_cloud_sdk(dotted)
        if cloud is not None:
            label, category = cloud
            return InlineAstResult(
                is_dangerous=True,
                label=label,
                category=category,
                detail=dotted,
            )

    return InlineAstResult()


# ============================================================================
# Provable read-only classification (positive allowlist)
# ============================================================================
# Rationale: ``analyze_python_inline`` uses a *blocklist* — a clean result
# means "no KNOWN dangerous call was found", which is NOT the same as
# "read-only".  Bound-method mutations the catalog cannot see statically —
# ``cur.execute("INSERT ...")``, ``con.commit()``, ``f.write(...)`` on a
# handle whose write-mode was set elsewhere — parse cleanly yet mutate.
#
# This second classifier exists ONLY to safely exempt long-but-harmless
# inline code from the length heuristic (``heuristic-long-code``).  It is the
# inverse discipline: it returns True ONLY when EVERY statement and EVERY call
# in the payload is on a positive read-only allowlist.  Anything unrecognized
# — any node type, call target, assignment target, or SQL verb it cannot
# prove safe — makes it return False, leaving the length heuristic in force.
# No-false-negative is the contract: a mutation must never be classified
# read-only, even at the cost of leaving some genuinely-read-only payloads
# subject to the length flag (those remain T3-approvable, never silently run).

# Builtins that never mutate external state.  Deliberately conservative:
# ``open`` is excluded (write modes), ``exec``/``eval``/``compile``/
# ``__import__``/``input``/``getattr``/``setattr``/``delattr`` are excluded
# (dynamic dispatch defeats static analysis), ``print`` is allowed (stdout
# only).
_READ_ONLY_BUILTINS: FrozenSet[str] = frozenset({
    "print", "len", "str", "repr", "int", "float", "bool", "list", "tuple",
    "dict", "set", "frozenset", "sorted", "reversed", "enumerate", "zip",
    "map", "filter", "range", "sum", "min", "max", "abs", "round", "any",
    "all", "format", "ascii", "bin", "hex", "oct", "ord", "chr", "type",
    "isinstance", "issubclass", "hasattr", "iter", "next", "bytes",
    "bytearray", "id", "hash", "divmod", "pow", "vars", "dir",
})

# Read-only methods, matched by leaf attribute name regardless of receiver.
# These are common DB-cursor / mapping / sequence / string read accessors.
# ``execute``/``executemany``/``executescript`` are handled SEPARATELY: they
# are allowed ONLY when the SQL argument is a literal read-only statement.
_READ_ONLY_METHODS: FrozenSet[str] = frozenset({
    # DB cursor/connection read surface
    "fetchone", "fetchall", "fetchmany", "cursor", "close",
    # mapping / sequence reads
    "keys", "values", "items", "get", "copy", "index", "count",
    # string reads
    "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines", "join",
    "lower", "upper", "title", "capitalize", "startswith", "endswith",
    "find", "rfind", "format", "encode", "decode", "replace", "zfill",
    "ljust", "rjust", "center",
    # iteration / misc pure reads
    "isoformat", "total_seconds", "group", "groups", "groupdict", "read",
    "readline", "readlines",
})

# SQL statement prefixes that are read-only.  Matched case-insensitively
# against the leading token of a literal SQL string.  ``WITH`` (CTE) is
# allowed only when it ultimately SELECTs — but a CTE can wrap an
# INSERT/UPDATE/DELETE (``WITH x AS (...) DELETE ...``), so to stay airtight
# we require the literal to ALSO contain no mutating keyword.  Simpler and
# safer: allow the prefix, then reject if any mutating keyword appears
# anywhere in the literal.
_READ_ONLY_SQL_PREFIXES: Tuple[str, ...] = (
    "select", "pragma", "explain", "with", "values", "show",
)
_MUTATING_SQL_KEYWORDS: Tuple[str, ...] = (
    "insert", "update", "delete", "drop", "create", "alter", "replace",
    "truncate", "attach", "detach", "vacuum", "reindex", "commit",
    "rollback", "savepoint", "grant", "revoke", "merge", "upsert", "begin",
)
_SQL_EXEC_METHODS: FrozenSet[str] = frozenset({
    "execute", "executemany", "executescript",
})


def is_provably_read_only_python(code: str) -> bool:
    """Return True ONLY if every construct in ``code`` is provably read-only.

    Positive allowlist over the AST.  Any node type, call, assignment target,
    or SQL verb that cannot be proven safe returns False.  Used to exempt
    long-but-harmless inline code from the length heuristic; never used to
    grant execution by itself.

    Args:
        code: Python source extracted from ``python3 -c "..."`` (unquoted).

    Returns:
        True when the payload contains exclusively read-only constructs;
        False on any uncertainty (including parse failure).
    """
    if not code or not code.strip():
        # Empty payload: nothing to exempt; let caller's default path handle.
        return False

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return False

    checker = _ReadOnlyChecker()
    return checker.is_read_only(tree)


class _ReadOnlyChecker:
    """Walks an AST and proves it contains only read-only constructs."""

    # Statement node types that are structurally inert (control flow,
    # definitions, expression evaluation).  Mutation can only happen via a
    # Call, an attribute/subscript assignment, del, or import side effects —
    # all handled explicitly below.
    _ALLOWED_STMT_TYPES = (
        ast.Import, ast.ImportFrom, ast.Expr, ast.Assign, ast.AnnAssign,
        ast.AugAssign, ast.For, ast.While, ast.If, ast.With, ast.FunctionDef,
        ast.Return, ast.Pass, ast.Break, ast.Continue, ast.Assert,
        ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith,
    )

    def is_read_only(self, tree: ast.AST) -> bool:
        for node in ast.walk(tree):
            # Reject statements we do not explicitly allow.
            if isinstance(node, ast.stmt):
                if not isinstance(node, self._ALLOWED_STMT_TYPES):
                    return False
                # ``del`` removes bindings / can call __delitem__/__delattr__.
                if isinstance(node, ast.Delete):
                    return False
            # Assignment targets must be plain names or name-tuples.  A
            # Subscript or Attribute target (``os.environ[k]=v``,
            # ``obj.attr=v``) can mutate external state via __setitem__ /
            # __setattr__.
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if not self._targets_are_local(node):
                    return False
            # Every call must be on the allowlist.
            if isinstance(node, ast.Call):
                if not self._call_is_read_only(node):
                    return False
            # ``with`` items: the context manager is itself a Call/expr and is
            # validated by the Call check above; nothing extra needed.
        return True

    def _targets_are_local(self, node: ast.AST) -> bool:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for tgt in targets:
            if not self._is_local_target(tgt):
                return False
        return True

    def _is_local_target(self, tgt: ast.AST) -> bool:
        if isinstance(tgt, ast.Name):
            return True
        if isinstance(tgt, (ast.Tuple, ast.List)):
            return all(self._is_local_target(e) for e in tgt.elts)
        if isinstance(tgt, ast.Starred):
            return self._is_local_target(tgt.value)
        # Subscript / Attribute targets can mutate external state.
        return False

    def _call_is_read_only(self, node: ast.Call) -> bool:
        func = node.func
        # Bare name call: must be a read-only builtin.  (Unresolved local
        # function calls are rejected — we cannot prove their body is safe.)
        if isinstance(func, ast.Name):
            return func.id in _READ_ONLY_BUILTINS
        # Attribute call: ``x.method(...)``.
        if isinstance(func, ast.Attribute):
            method = func.attr
            if method in _SQL_EXEC_METHODS:
                return self._sql_arg_is_read_only(node)
            if method in _READ_ONLY_METHODS:
                return True
            # Allow ``sqlite3.connect(...)`` and module-qualified pure reads
            # we can name explicitly; everything else is rejected.
            return self._dotted_call_is_read_only(func)
        # Any other callable form (subscript result, lambda, call chain head)
        # cannot be proven safe.
        return False

    def _dotted_call_is_read_only(self, func: ast.Attribute) -> bool:
        # Build dotted source name (best-effort).  Only a tiny set of
        # module-level read-only constructors are permitted.
        parts = []
        cur: ast.AST = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            parts.reverse()
            dotted = ".".join(parts)
            return dotted in _READ_ONLY_DOTTED_CALLS
        return False

    def _sql_arg_is_read_only(self, node: ast.Call) -> bool:
        # ``execute``/``executemany`` are read-only ONLY when the first
        # positional argument is a string LITERAL whose leading token is a
        # read-only SQL verb AND which contains no mutating keyword.  A
        # non-literal SQL argument (variable, f-string, concatenation) cannot
        # be proven safe and is rejected.
        if not node.args:
            return False
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return False
        sql = first.value.strip().lower()
        if not sql:
            return False
        # Strip a leading comment / whitespace already done; take first word.
        leading = sql.split(None, 1)[0] if sql.split() else ""
        if leading not in _READ_ONLY_SQL_PREFIXES:
            return False
        # Reject if ANY mutating keyword appears anywhere (defeats CTE-wrapped
        # writes like ``WITH x AS (...) DELETE ...`` and stacked statements).
        for kw in _MUTATING_SQL_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", sql):
                return False
        return True


# Module-level read-only constructors permitted in a provable-read-only
# payload (dotted source names).  ``sqlite3.connect`` opens a handle;
# mutation would require a subsequent write call, which is independently
# checked.  Kept deliberately small.
_READ_ONLY_DOTTED_CALLS: FrozenSet[str] = frozenset({
    "sqlite3.connect",
    "json.dumps", "json.loads", "json.load",
    "os.getcwd", "os.getenv", "os.listdir", "os.path.join", "os.path.exists",
    "os.path.basename", "os.path.dirname", "os.path.abspath",
    "os.path.isfile", "os.path.isdir", "os.path.getsize",
    "sys.exit",
    "datetime.now", "datetime.utcnow", "time.time",
    "pathlib.Path", "Path",
})


# ============================================================================
# Internal helpers
# ============================================================================

def _build_open_aware_result(
    node: ast.Call, dotted: str, label: str, category: str,
) -> InlineAstResult:
    """Construct an InlineAstResult, gating the mode/flag-dependent openers.

    Every catalog hit passes through here.  For the four mode-string openers
    (``_MODE_ARG_INDEX``) and for ``os.open`` (integer flags) the entry is a
    CANDIDATE, not a verdict: the call is dangerous only when the argument
    permits writing, so a read-only open comes back with
    ``is_dangerous=False`` and the caller keeps walking.  Every other entry is
    dangerous by the fact of being invoked.

    An UNRESOLVABLE mode/flag (a variable, an f-string, a computed int) is
    treated as not-dangerous, matching the long-standing behavior of the
    builtin-``open`` arm.  That is a known hole, not a judgment that such a
    call is safe -- see the module CEILING note.
    """
    if dotted in _MODE_ARG_INDEX:
        mode = _extract_open_mode(node, _MODE_ARG_INDEX[dotted])
        if not mode or not any(
            ch in _OPEN_WRITE_MODE_CHARS for ch in mode.lower()
        ):
            return InlineAstResult()
        return InlineAstResult(
            is_dangerous=True,
            label=label,
            category=category,
            detail=f"{dotted}(..., mode={mode!r})",
        )

    if dotted == "os.open":
        flags = _extract_os_open_write_flags(node)
        if not flags:
            return InlineAstResult()
        return InlineAstResult(
            is_dangerous=True,
            label=label,
            category=category,
            detail=f"{dotted}(..., flags={'|'.join(sorted(flags))})",
        )

    return InlineAstResult(
        is_dangerous=True,
        label=label,
        category=category,
        detail=dotted,
    )


def _extract_open_mode(node: ast.Call, mode_index: int = 1) -> Optional[str]:
    """Return the mode string passed to an opener, or None if unknown.

    Args:
        node: The ``Call`` node of the opener.
        mode_index: Positional index of the mode argument -- 1 for
            ``open(path, mode)``, 0 for the bound ``Path.open(mode)`` whose
            path is the receiver.
    """
    if len(node.args) > mode_index and isinstance(node.args[mode_index], ast.Constant):
        val = node.args[mode_index].value
        if isinstance(val, str):
            return val
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            val = kw.value.value
            if isinstance(val, str):
                return val
    return None


def _extract_os_open_write_flags(node: ast.Call) -> Set[str]:
    """Return the write-permitting ``os.O_*`` flag names named in ``os.open``.

    The flags argument is an int expression, normally an ``|`` chain of
    ``os.O_*`` attributes, so the names are read off the expression's
    identifiers rather than evaluated.  A flags value that names none of them
    (``os.O_RDONLY``, or a variable) yields an empty set.
    """
    exprs = [
        node.args[_OS_OPEN_FLAG_ARG_INDEX]
    ] if len(node.args) > _OS_OPEN_FLAG_ARG_INDEX else []
    exprs += [kw.value for kw in node.keywords if kw.arg == "flags"]

    found: Set[str] = set()
    for expr in exprs:
        for sub in ast.walk(expr):
            name = None
            if isinstance(sub, ast.Attribute):
                name = sub.attr
            elif isinstance(sub, ast.Name):
                name = sub.id
            if name in _OS_OPEN_WRITE_FLAGS:
                found.add(name)
    return found


# ----------------------------------------------------------------------------
# Alias resolution
# ----------------------------------------------------------------------------

class _AliasResolver:
    """Track import aliases and local bindings to resolve call names statically.

    Examples handled::

        import subprocess as sp; sp.run(...)        -> subprocess.run
        from subprocess import run as r; r(...)     -> subprocess.run
        from os.path import join; join(...)         -> os.path.join
        Path("/x").unlink()                         -> pathlib.Path.unlink
        boto3.client("s3").delete_object(...)       -> boto3.client.delete_object
        c = boto3.client("s3"); c.delete_object()   -> boto3.client.delete_object

    The last three are the reason chain traversal exists.  A chain that passes
    through a Call is the IDIOMATIC form of every constructor-based API --
    ``pathlib`` and every cloud SDK -- so a resolver that stopped at the first
    Call reported ``None`` for all of them, silently making the entire
    ``pathlib.Path.*`` block unmatchable.

    Unresolved cases (``getattr(subprocess, "run")(...)``, a subscripted
    receiver, an element of a list) still return ``None`` from
    :meth:`resolve_call`.
    """

    def __init__(self) -> None:
        # name-as-bound -> canonical dotted prefix
        # e.g. {"sp": "subprocess", "r": "subprocess.run", "c": "boto3.client"}
        self._aliases: dict = {}

    def collect(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    self._aliases[name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    name = alias.asname or alias.name
                    full = f"{module}.{alias.name}" if module else alias.name
                    self._aliases[name] = full

        # Second pass: bind names assigned the result of a resolvable call, so
        # the two-statement form of a chain resolves like the one-liner.  Runs
        # after imports because resolving the call's own name needs them.
        # A rebinding to something UNRESOLVABLE deliberately does not clear the
        # entry: keeping the last resolvable binding can over-gate a shadowed
        # name (approval, recoverable), whereas clearing it would under-gate
        # ``p = Path(x); p = p.parent; p.unlink()`` (a miss, not recoverable).
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Call):
                continue
            resolved = self.resolve_call(node.value.func)
            if resolved is not None:
                self._aliases[target.id] = resolved

    def resolve_call(self, func: ast.AST) -> Optional[str]:
        """Return the dotted name of a Call's ``func`` expression, if known."""
        # Attribute chain, traversing intermediate calls: a.b(x).c
        parts = self._dotted_parts(func)
        if parts is None:
            return None
        head, *rest = parts
        canonical = self._aliases.get(head, head)
        if rest:
            return f"{canonical}.{'.'.join(rest)}"
        return canonical

    def _dotted_parts(self, node: ast.AST) -> Optional[list]:
        """Walk an Attribute/Name/Call chain into a list of identifier parts.

        Intermediate calls are transparent: ``Path("/x").open`` yields
        ``["Path", "open"]``, because the receiver's IDENTITY is what selects
        the API and its arguments are irrelevant to that choice.

        Returns None if the expression is not a static chain (a subscript, a
        literal receiver, a lambda).
        """
        parts: list = []
        cur = node
        while True:
            if isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            elif isinstance(cur, ast.Call):
                cur = cur.func
            else:
                break
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            parts.reverse()
            return parts
        return None
