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
- ``urlopen`` is a read when it sends no body and no ``Request`` the module
  builds carries a body or a non-GET/HEAD method, matching a ``curl`` GET.  A
  process call with a literal argv is judged by the command it runs only when
  the caller passes ``argv_is_mutative`` (the script-file and inline lanes do).
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
from typing import Callable, FrozenSet, Optional, Set, Tuple

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
    ("socket.create_connection", "socket-open", "NETWORK"),
)

# Calls that run code chosen at runtime: any argument but a literal is foreign code.
_DYNAMIC_CODE_CALLS: FrozenSet[str] = frozenset({
    "eval", "exec", "compile", "__import__", "builtins.eval", "builtins.exec",
    "importlib.import_module", "importlib.__import__", "runpy.run_path", "runpy.run_module",
})
_CODE_STRING_CALLS: FrozenSet[str] = frozenset({"eval", "exec", "compile", "builtins.eval", "builtins.exec"})
_IMPORT_CALLS: FrozenSet[str] = frozenset({
    "__import__", "builtins.__import__", "importlib.__import__", "importlib.import_module",
})


def _is_str_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _imported_module(call: ast.Call, dotted: Optional[str]) -> Optional[str]:
    """The module a literal import call evaluates to (``__import__`` yields the top package without a fromlist), else None."""
    if dotted not in _IMPORT_CALLS or not call.args or not _is_str_literal(call.args[0]):
        return None
    name = call.args[0].value
    if not all(part.isidentifier() for part in name.split(".")):
        return None
    if dotted == "importlib.import_module":
        return name
    fromlist = call.args[3] if len(call.args) > 3 else None
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
        if keyword.arg == "fromlist":
            fromlist = keyword.value
    if fromlist is None or isinstance(fromlist, ast.Constant) and fromlist.value is None:
        return name.split(".")[0]
    if isinstance(fromlist, (ast.List, ast.Tuple)):
        return name if fromlist.elts else name.split(".")[0]
    return None

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
# Provable reads among catalogued calls
# ============================================================================
# A process call whose whole argv is literal strings runs exactly that command,
# so it can be judged as the command it runs.  Every keyword outside this set is
# refused because it can change what runs or what it reads: ``shell`` turns the
# argv into a shell string, ``executable``/``env``/``cwd`` pick another binary or
# script, ``input``/``stdin`` feed it a program.
_ARGV_PROCESS_CALLS: FrozenSet[str] = frozenset({
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen",
})
_ARGV_PASSIVE_KEYWORDS: FrozenSet[str] = frozenset({
    "capture_output", "text", "check", "timeout", "encoding", "errors",
    "universal_newlines", "stdout", "stderr",
})


def _literal_argv(call: ast.Call) -> Optional[Tuple[str, ...]]:
    """Return the argv of a process call when it is provably literal, else None."""
    if len(call.args) != 1 or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        return None
    if any(kw.arg not in _ARGV_PASSIVE_KEYWORDS for kw in call.keywords):
        return None
    argv = call.args[0].elts
    if not argv or not all(
        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in argv
    ):
        return None
    return tuple(item.value for item in argv)


_URLOPEN = "urllib.request.urlopen"
_URL_REQUEST = "urllib.request.Request"
_READ_HTTP_METHODS: FrozenSet[Optional[str]] = frozenset({None, "GET", "HEAD"})


def _literal_or_absent(call: ast.Call, name: str, index: int) -> Tuple[bool, object]:
    """Return ``(known, value)`` for an argument given by keyword or position."""
    node = next((kw.value for kw in call.keywords if kw.arg == name), None)
    if node is None and len(call.args) > index:
        node = call.args[index]
    if node is None:
        return True, None
    if isinstance(node, ast.Constant):
        return True, node.value
    return False, None


def _request_is_read(call: ast.Call) -> bool:
    """True when a ``Request(...)`` provably carries no body and a read method."""
    if any(kw.arg is None for kw in call.keywords):
        return False
    data_known, data = _literal_or_absent(call, "data", 1)
    method_known, method = _literal_or_absent(call, "method", 5)
    return data_known and data is None and method_known and method in _READ_HTTP_METHODS


class _RequestProvenance:
    """Decide whether the target handed to ``urlopen`` is provably a body-less read.

    A target is provable only as a string literal, an f-string, a direct
    ``Request(...)`` call that is a read, or a name whose every binding in the
    module is one of those.  The whole module is disqualified when ``Request``
    is ever referenced other than as a direct call (``partial``, an alias, a
    subclass), when ``setattr`` is called, or when a ``data``/``method``
    attribute is assigned, since each can give a request a body unseen.
    """

    _MAX_NAME_HOPS = 4

    def __init__(self, tree: ast.AST, alias_map: "_AliasResolver") -> None:
        self._alias_map = alias_map
        self._assigned: dict = {}
        self._stores: dict = {}
        self._unprovable_names: Set[str] = set()
        call_funcs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_funcs.add(id(node.func))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if len(targets) == 1 and isinstance(targets[0], ast.Name) and node.value is not None:
                    self._assigned[id(targets[0])] = node.value
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self._stores.setdefault(node.id, []).append(node)
            elif isinstance(node, ast.arg):
                self._unprovable_names.add(node.arg)
            elif isinstance(node, ast.alias):
                self._unprovable_names.add(node.asname or node.name.split(".")[0])
        self.tampered = any(self._tampers(node, call_funcs) for node in ast.walk(tree))

    def _tampers(self, node: ast.AST, call_funcs: set) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            return True
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            return node.attr in ("data", "method")
        if isinstance(node, ast.Call):
            return self._alias_map.resolve_call(node.func) in ("setattr", "vars")
        # A locally assigned name resolves to Request when it holds an
        # instance (the resolver binds call results); only the class itself is
        # a reference that can be wrapped or subclassed.
        if isinstance(node, ast.Name) and node.id in self._stores:
            return False
        if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in call_funcs:
            return self._alias_map.resolve_call(node) == _URL_REQUEST
        return False

    def is_read(self, node: ast.AST, hops: int = 0) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str)
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.Call):
            return self._alias_map.resolve_call(node.func) == _URL_REQUEST and _request_is_read(node)
        if isinstance(node, ast.Name) and hops < self._MAX_NAME_HOPS:
            stores = self._stores.get(node.id, [])
            if not stores or node.id in self._unprovable_names:
                return False
            return all(
                id(store) in self._assigned and self.is_read(self._assigned[id(store)], hops + 1)
                for store in stores
            )
        return False


def _urlopen_is_read(call: ast.Call, provenance: _RequestProvenance) -> bool:
    """True when ``urlopen`` sends no body and its target is provably a read."""
    if provenance.tampered or any(kw.arg is None for kw in call.keywords):
        return False
    if len(call.args) != 1:
        return False
    data_known, data = _literal_or_absent(call, "data", 1)
    return data_known and data is None and provenance.is_read(call.args[0])


# ============================================================================
# Public entry point
# ============================================================================

def analyze_python_inline(
    code: str,
    argv_is_mutative: Optional[Callable[[Tuple[str, ...]], bool]] = None,
) -> InlineAstResult:
    """Classify a Python inline-code payload by invocation, not mention.

    Args:
        code: The Python source string extracted from ``python3 -c "..."``.
            Already unquoted; the caller is responsible for stripping the
            surrounding shell quotes before passing it in.
        argv_is_mutative: Classifier for the argv a literal process call
            runs.  When given, a process call whose argv is provably literal
            counts only if that argv is mutative; when omitted, every process
            call counts.

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
    provenance = _RequestProvenance(tree, alias_map)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        dotted = alias_map.resolve_call(node.func)
        if dotted is None:
            continue

        if dotted in _DYNAMIC_CODE_CALLS:
            result = _dynamic_code_result(node, dotted, argv_is_mutative)
            if result.is_dangerous:
                return result
            continue
        if dotted == _URLOPEN and _urlopen_is_read(node, provenance):
            continue
        if argv_is_mutative is not None and dotted in _ARGV_PROCESS_CALLS:
            argv = _literal_argv(node)
            if argv is not None and not argv_is_mutative(argv):
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


def _dynamic_code_result(
    node: ast.Call, dotted: str,
    argv_is_mutative: Optional[Callable[[Tuple[str, ...]], bool]],
) -> InlineAstResult:
    """Judge a call that runs code chosen at runtime by the code it is handed."""
    first = node.args[0] if node.args else None
    if dotted in _IMPORT_CALLS:
        if _imported_module(node, dotted) is not None:
            return InlineAstResult()
    elif _is_str_literal(first):
        if dotted not in _CODE_STRING_CALLS:
            return InlineAstResult()
        inner = analyze_python_inline(first.value, argv_is_mutative)
        if inner.is_dangerous or not inner.parse_failed:
            return inner
    return InlineAstResult(is_dangerous=True, label="dynamic-code", category="DYNAMIC_CODE", detail=dotted)


_UNCATALOGUED_EFFECT_MODULES: FrozenSet[str] = frozenset({
    "socket", "ssl", "http", "requests", "httpx", "urllib3", "aiohttp", "ftplib", "smtplib",
    "poplib", "imaplib", "telnetlib", "paramiko", "websocket", "websockets", "xmlrpc", "ctypes",
    "asyncio", "multiprocessing", "pty", "shelve", "dbm", "tempfile", "webbrowser",
})
# Effect-module members that touch nothing outside the process (gettempdir removes its probe file).
_EFFECT_MODULE_READS: Dict[str, FrozenSet[str]] = {
    "tempfile": frozenset({"gettempdir", "gettempdirb", "gettempprefix", "gettempprefixb"}),
    "socket": frozenset({"gethostname"}),
    "multiprocessing": frozenset({"cpu_count"}),
    "http": frozenset({"HTTPStatus"}),
}
_EFFECT_METHODS: FrozenSet[str] = frozenset({
    "write", "writelines", "write_text", "write_bytes", "touch", "mkdir", "makedirs", "rmdir",
    "unlink", "rename", "chmod", "chown", "lchmod", "symlink_to", "hardlink_to", "truncate",
    "send", "sendall", "sendto", "sendmsg", "sendfile", "commit", "extractall", "extract",
    "rmtree", "copyfile", "copytree", "removedirs", "kill", "terminate", "writestr", "addfile",
})
_STDIO_WRITERS: FrozenSet[str] = frozenset({"sys.stdout.write", "sys.stderr.write"})
_RUNTIME_NAME_CALLS: FrozenSet[str] = frozenset({"getattr", "setattr", "delattr", "breakpoint"})


def parse_finds_no_effect(
    code: str, argv_is_mutative: Optional[Callable[[Tuple[str, ...]], bool]] = None,
) -> bool:
    """True when ``code`` parses and neither the catalog nor the uncatalogued-effect screen finds an effect."""
    if not code or not code.strip():
        return False
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return False
    if analyze_python_inline(code, argv_is_mutative).is_dangerous:
        return False
    return not uncatalogued_effect(tree)


def uncatalogued_effect(tree: ast.AST) -> str:
    """The first uncatalogued effect in *tree* (``module``, ``call``, ``sql``, ``write``, ``env`` or ``path``), or ""."""
    alias_map = _AliasResolver()
    alias_map.collect(tree)
    if _environment_reaches_a_process(tree, alias_map):
        return "env"
    bindings = _single_bindings(tree)
    sql = _ReadOnlyChecker()
    receivers = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    receivers |= {
        id(node.args[0]) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and alias_map.getattr_name(node) is not None
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [f"{node.module or ''}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Call):
            imported = [_imported_module(node, alias_map.resolve_call(node.func)) or ""]
        else:
            imported = []
        for name in imported:
            effect_module = name.split(".")[0] in _UNCATALOGUED_EFFECT_MODULES
            if effect_module and name not in _EFFECT_MODULE_READS and not _effect_module_read(name):
                return "module"
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)) and id(node) not in receivers:
            used = alias_map.resolve_bound(node) or ""
            if used.split(".")[0] in _EFFECT_MODULE_READS and not _effect_module_read(used):
                return "module"
        if isinstance(node, ast.Subscript) and alias_map.resolve_call(node.value) == "sys.modules":
            if not _is_str_literal(node.slice):
                return "call"
        if _reaches_interpreter_internals(node):
            return "call"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _is_path_value(node.left, alias_map):
            if not _provable_path(node.right, alias_map, bindings):
                return "path"
        if not isinstance(node, ast.Call):
            continue
        dotted = alias_map.resolve_call(node.func) or ""
        if dotted in _ARCHIVE_OPENERS and _opens_archive_to_write(node, dotted):
            return "write"
        if not all(_provable_path(arg, alias_map, bindings) for arg in _path_arguments(node, dotted)):
            return "path"
        leaf = node.func.attr if isinstance(node.func, ast.Attribute) else dotted.rpartition(".")[2]
        if alias_map.getattr_name(node) is not None and alias_map.resolve_call(node.args[0]) is not None:
            continue
        if leaf in _RUNTIME_NAME_CALLS or (leaf in _EFFECT_METHODS and dotted not in _STDIO_WRITERS):
            return "call"
        if leaf in _SQL_EXEC_METHODS and not sql._sql_arg_is_read_only(node):
            return "sql"
    return ""


# Openers whose mode (positional index, keyword) decides read or write.
_ARCHIVE_OPENERS: Dict[str, Tuple[int, str]] = {
    **{name: (1, "mode") for name in (
        "zipfile.ZipFile", "zipfile.PyZipFile", "tarfile.open", "tarfile.TarFile", "gzip.open",
        "gzip.GzipFile", "bz2.open", "bz2.BZ2File", "lzma.open", "lzma.LZMAFile",
    )},
    "dbm.open": (1, "flag"), "shelve.open": (1, "flag"),
}


def _opens_archive_to_write(call: ast.Call, dotted: str) -> bool:
    """True unless the opener's mode is absent or a literal read mode."""
    index, keyword = _ARCHIVE_OPENERS[dotted]
    if any(k.arg is None for k in call.keywords) or any(isinstance(a, ast.Starred) for a in call.args):
        return True
    mode = next((k.value for k in call.keywords if k.arg == keyword), None)
    if mode is None and len(call.args) > index:
        mode = call.args[index]
    if mode is None:
        return dotted == "shelve.open"
    return not (_is_str_literal(mode) and mode.value.startswith("r") and "+" not in mode.value)


_ENVIRON_WRITERS: FrozenSet[str] = frozenset({
    "os.putenv", "os.unsetenv", "os.environ.update", "os.environ.setdefault", "os.environ.pop",
    "os.environ.popitem", "os.environ.clear", "os.environ.__setitem__", "os.environ.__delitem__",
})
_PROCESS_LAUNCH_PREFIXES: Tuple[str, ...] = (
    "subprocess.", "os.exec", "os.spawn", "os.posix_spawn", "os.system", "os.popen", "pty.spawn",
    "asyncio.create_subprocess",
)


def _environment_reaches_a_process(tree: ast.AST, alias_map: "_AliasResolver") -> bool:
    """True when *tree* changes ``os.environ`` and launches a process, which inherits the change."""
    writes = launches = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            targets = []
        for target in targets:
            receiver = target.value if isinstance(target, ast.Subscript) else target
            writes |= alias_map.resolve_call(receiver) == "os.environ"
        if isinstance(node, ast.Call):
            dotted = alias_map.resolve_call(node.func) or ""
            writes |= dotted in _ENVIRON_WRITERS
            launches |= dotted.startswith(_PROCESS_LAUNCH_PREFIXES)
    return writes and launches


# Dunders that only describe an object; any other reaches the interpreter's internals.
_INERT_DUNDERS: FrozenSet[str] = frozenset({
    "__name__", "__qualname__", "__doc__", "__version__", "__file__", "__module__", "__class__",
    "__init__", "__enter__", "__exit__", "__annotations__", "__all__", "__path__", "__len__",
    "__str__", "__repr__",
})
_INTERNAL_NAMES: FrozenSet[str] = frozenset({"__builtins__", "__loader__", "__spec__"})


def _reaches_interpreter_internals(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _INTERNAL_NAMES
    if not isinstance(node, ast.Attribute):
        return False
    attr = node.attr
    return attr.startswith("__") and attr.endswith("__") and attr not in _INERT_DUNDERS


# File calls by their count of leading path arguments; the secret-read floor reads
# only literals, so each must be one ``_provable_path`` proves.
_PATH_CALLS: Dict[str, int] = {name: 1 for name in (
    "open", "builtins.open", "io.open", "io.FileIO", "codecs.open", "os.open", "pathlib.Path",
    "pathlib.PurePath", "pathlib.PosixPath", "pathlib.WindowsPath", "os.listdir", "os.scandir",
    "os.walk", "glob.glob", "glob.iglob", "fileinput.input", "linecache.getline",
    "linecache.getlines", "sqlite3.connect", *_ARCHIVE_OPENERS,
)}
_PATH_KEYWORDS: FrozenSet[str] = frozenset({
    "file", "path", "name", "filename", "src", "dst", "top", "pathname", "database", "files",
})
_PATH_BUILDERS: FrozenSet[str] = frozenset({
    "os.path.join", "os.path.expanduser", "os.path.abspath", "os.path.normpath", "os.path.realpath",
    "os.path.dirname", "os.fspath", "pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath",
})
_PATH_ROOTS: FrozenSet[str] = frozenset({
    "pathlib.Path.home", "pathlib.Path.cwd", "os.getcwd", "tempfile.gettempdir",
})
_PATH_METHODS: FrozenSet[str] = frozenset({
    "joinpath", "expanduser", "resolve", "absolute", "with_name", "with_suffix",
})
_HOME_VARIABLES: FrozenSet[str] = frozenset({"HOME", "USERPROFILE"})


def _path_arguments(call: ast.Call, dotted: str) -> list:
    """The path arguments of a file call or of a path extended by a method, else []."""
    if isinstance(call.func, ast.Attribute) and call.func.attr in _PATH_METHODS:
        return list(call.args)
    count = 2 if dotted.startswith("shutil.") else _PATH_CALLS.get(dotted, 0)
    if not count:
        return []
    keywords = [k.value for k in call.keywords if k.arg in _PATH_KEYWORDS or k.arg is None]
    return call.args[:count] + keywords


def _single_bindings(tree: ast.AST) -> Dict[str, ast.AST]:
    """Names bound exactly once in *tree*, by a plain ``name = value``, mapped to that value."""
    stores: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            stores[node.id] = stores.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            stores[node.arg] = stores.get(node.arg, 0) + 2
    return {
        node.targets[0].id: node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and stores.get(node.targets[0].id) == 1
    }


def _provable_path(node: ast.AST, alias_map: "_AliasResolver", bindings: Dict[str, ast.AST], depth: int = 0) -> bool:
    """True when *node* is a path the parse proves: literals joined as path segments under a known root."""
    if depth > 8 or isinstance(node, ast.Starred):
        return False
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.JoinedStr):
        return all(isinstance(value, ast.Constant) for value in node.values)
    if isinstance(node, ast.Name):
        bound = bindings.get(node.id)
        return node.id == "__file__" or (bound is not None and _provable_path(bound, alias_map, bindings, depth + 1))
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _provable_path(node.value, alias_map, bindings, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return all(_provable_path(side, alias_map, bindings, depth + 1) for side in (node.left, node.right))
    if isinstance(node, ast.Subscript):
        return alias_map.resolve_call(node.value) == "os.environ" and _names_home(node.slice)
    if not isinstance(node, ast.Call) or node.keywords and any(k.arg is None for k in node.keywords):
        return False
    dotted = alias_map.resolve_call(node.func)
    if dotted in _PATH_ROOTS:
        return not node.args
    if dotted in ("os.getenv", "os.environ.get"):
        return bool(node.args) and _names_home(node.args[0])
    if dotted in _PATH_BUILDERS:
        receiver_proven = True
    elif isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_METHODS:
        receiver_proven = _provable_path(node.func.value, alias_map, bindings, depth + 1)
    else:
        return False
    return receiver_proven and all(_provable_path(arg, alias_map, bindings, depth + 1) for arg in node.args)


def _is_path_value(node: ast.AST, alias_map: "_AliasResolver") -> bool:
    """True when *node* is visibly a ``pathlib`` path, so ``/`` on it joins a path rather than divides."""
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        node = node.left
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return True
    if not isinstance(node, ast.Call):
        return False
    dotted = alias_map.resolve_call(node.func) or ""
    if dotted.startswith("pathlib.") or dotted in _PATH_ROOTS:
        return True
    return isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_METHODS


def _names_home(node: ast.AST) -> bool:
    return _is_str_literal(node) and node.value in _HOME_VARIABLES


def runs_code_string(code: str) -> bool:
    """True when *code* parses and hands a string to ``eval``/``exec``/``compile``."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return False
    alias_map = _AliasResolver()
    alias_map.collect(tree)
    return any(
        isinstance(node, ast.Call) and alias_map.resolve_call(node.func) in _CODE_STRING_CALLS
        for node in ast.walk(tree)
    )


def _effect_module_read(dotted: str) -> bool:
    """True when *dotted* names a read of an effect module, with no private attribute after it."""
    root, _, rest = dotted.partition(".")
    member, *tail = rest.split(".")
    return member in _EFFECT_MODULE_READS.get(root, ()) and not any(part.startswith("_") for part in tail)


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

    A computed name (``getattr(m, "r" + "un")``, ``sys.modules[n]``), any other
    subscript and an element of a list still return ``None`` from
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
            if not isinstance(target, ast.Name) or not isinstance(node.value, (ast.Call, ast.Subscript)):
                continue
            resolved = self.resolve_call(node.value)
            if resolved is not None:
                self._aliases[target.id] = resolved

    def resolve_call(self, func: ast.AST) -> Optional[str]:
        """Return the dotted name of a Call's ``func`` expression, if known."""
        chain = self._chain(func)
        return ".".join(chain[0]) if chain else None

    def resolve_bound(self, node: ast.AST) -> Optional[str]:
        """The dotted name of *node* when its head was bound by an import or a resolvable call, else None."""
        chain = self._chain(node)
        return ".".join(chain[0]) if chain and chain[1] else None

    def getattr_name(self, call: ast.Call) -> Optional[str]:
        """The attribute a ``getattr`` call names by a string literal, which makes it a plain attribute access."""
        if self.resolve_call(call.func) not in ("getattr", "builtins.getattr") or call.keywords:
            return None
        if len(call.args) not in (2, 3) or not _is_str_literal(call.args[1]):
            return None
        return call.args[1].value

    def _chain(self, node: ast.AST) -> Optional[Tuple[list, bool]]:
        """The canonical dotted parts of an expression, and whether its head is a known binding.

        Intermediate calls are transparent: ``Path("/x").open`` yields
        ``pathlib.Path.open``, because the receiver's IDENTITY is what selects
        the API.  A call or subscript that evaluates to a module or attribute
        named by a literal -- ``__import__('os')``, ``sys.modules['os']``,
        ``getattr(m, 'run')`` -- stands for that name, as ``import`` binds it.
        Returns None for anything else that is not a static chain.
        """
        tail: list = []
        cur = node
        while True:
            if isinstance(cur, ast.Attribute):
                tail.append(cur.attr)
                cur = cur.value
            elif isinstance(cur, ast.Call):
                attr = self.getattr_name(cur)
                if attr is not None:
                    tail.append(attr)
                    cur = cur.args[0]
                    continue
                module = _imported_module(cur, self.resolve_call(cur.func))
                if module is not None:
                    return module.split(".") + tail[::-1], True
                cur = cur.func
            elif isinstance(cur, ast.Subscript):
                if self.resolve_call(cur.value) != "sys.modules" or not _is_str_literal(cur.slice):
                    return None
                return cur.slice.value.split(".") + tail[::-1], True
            else:
                break
        if not isinstance(cur, ast.Name):
            return None
        return self._aliases.get(cur.id, cur.id).split(".") + tail[::-1], cur.id in self._aliases
