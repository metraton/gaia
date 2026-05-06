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
- ``open(..., 'w')`` / ``open(..., 'a')`` / ``open(..., 'r+')`` patterns
  inspect the second positional argument (or ``mode=`` keyword) so a plain
  ``open(p)`` (read-only by default) is classified as safe.
- If the payload fails to parse (``SyntaxError``), the function returns a
  result with ``parse_failed=True`` so the caller can fall back to the
  legacy regex layer rather than allowing the command unconditionally.

Bash inline code (``bash -c "..."``, ``sh -c "..."``) is **not** parsed
here — bash AST libraries require a runtime dependency (``bashlex``) that
is not yet vendored.  The caller continues to handle bash payloads through
``shell_unwrapper`` + ``blocked_commands`` (Layer 1 in
``_check_inline_code``).
"""

from __future__ import annotations

import ast
import logging
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
            ``"PERMISSION_MOD"``, ``"NETWORK"``, or ``""`` when safe.
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
    ("os.rename", "os-rename", "FILE_MUTATION"),
    ("os.replace", "os-replace", "FILE_MUTATION"),
    ("os.makedirs", "os-makedirs", "FILE_MUTATION"),
    ("os.mkdir", "os-mkdir", "FILE_MUTATION"),
    ("os.symlink", "os-symlink", "FILE_MUTATION"),
    ("os.link", "os-link", "FILE_MUTATION"),
    ("shutil.copy", "shutil-copy", "FILE_MUTATION"),
    ("shutil.copy2", "shutil-copy2", "FILE_MUTATION"),
    ("shutil.copytree", "shutil-copytree", "FILE_MUTATION"),
    ("shutil.move", "shutil-move", "FILE_MUTATION"),
    ("pathlib.Path.rename", "pathlib-rename", "FILE_MUTATION"),
    ("pathlib.Path.write_text", "pathlib-write-text", "FILE_WRITE"),
    ("pathlib.Path.write_bytes", "pathlib-write-bytes", "FILE_WRITE"),
    ("pathlib.Path.touch", "pathlib-touch", "FILE_MUTATION"),
    ("pathlib.Path.mkdir", "pathlib-mkdir", "FILE_MUTATION"),

    # --- Permission modification ---
    ("os.chmod", "os-chmod", "PERMISSION_MOD"),
    ("os.chown", "os-chown", "PERMISSION_MOD"),
    ("os.lchmod", "os-lchmod", "PERMISSION_MOD"),
    ("os.lchown", "os-lchown", "PERMISSION_MOD"),
    ("pathlib.Path.chmod", "pathlib-chmod", "PERMISSION_MOD"),

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

        # Direct hit on full dotted name.
        match = _DANGEROUS_BY_DOTTED.get(dotted)
        if match is not None:
            _, label, category = match
            return _build_open_aware_result(node, dotted, label, category)

        # Module-level fallback: ``mod.leaf(...)`` where leaf is in the
        # dangerous catalog for that module (handles cases the explicit
        # _DANGEROUS_BY_DOTTED entries cover, plus any aliased re-exports).
        if "." in dotted:
            mod, leaf = dotted.rsplit(".", 1)
            mod_leaves = _DANGEROUS_LEAVES_BY_MODULE.get(mod)
            if mod_leaves and leaf in mod_leaves:
                label, category = mod_leaves[leaf]
                return _build_open_aware_result(node, dotted, label, category)

        # Builtin ``open`` with a write/append mode is dangerous.
        if dotted == "open":
            mode = _extract_open_mode(node)
            if mode and any(ch in _OPEN_WRITE_MODE_CHARS for ch in mode.lower()):
                return InlineAstResult(
                    is_dangerous=True,
                    label="open-write",
                    category="FILE_WRITE",
                    detail=f"open(..., mode={mode!r})",
                )

    return InlineAstResult()


# ============================================================================
# Internal helpers
# ============================================================================

def _build_open_aware_result(
    node: ast.Call, dotted: str, label: str, category: str,
) -> InlineAstResult:
    """Construct an InlineAstResult, refining FILE_WRITE for ``open``.

    Currently a thin wrapper, kept for symmetry with future refinements
    (e.g., distinguishing ``shutil.copy(src, dst)`` by destination prefix).
    """
    return InlineAstResult(
        is_dangerous=True,
        label=label,
        category=category,
        detail=dotted,
    )


def _extract_open_mode(node: ast.Call) -> Optional[str]:
    """Return the mode string passed to ``open(...)``, or None if unknown."""
    # Positional: open(path, mode=...)
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        val = node.args[1].value
        if isinstance(val, str):
            return val
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            val = kw.value.value
            if isinstance(val, str):
                return val
    return None


# ----------------------------------------------------------------------------
# Alias resolution
# ----------------------------------------------------------------------------

class _AliasResolver:
    """Track import aliases to resolve dotted call names statically.

    Examples handled::

        import subprocess as sp; sp.run(...)        -> subprocess.run
        from subprocess import run as r; r(...)     -> subprocess.run
        from os.path import join; join(...)         -> os.path.join

    Unresolved cases (e.g., ``getattr(subprocess, "run")(...)``) return
    ``None`` from :meth:`resolve_call`.
    """

    def __init__(self) -> None:
        # name-as-bound -> canonical dotted prefix
        # e.g. {"sp": "subprocess", "r": "subprocess.run"}
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

    def resolve_call(self, func: ast.AST) -> Optional[str]:
        """Return the dotted name of a Call's ``func`` expression, if known."""
        # Direct attribute chain: a.b.c
        parts = self._dotted_parts(func)
        if parts is None:
            return None
        head, *rest = parts
        canonical = self._aliases.get(head, head)
        if rest:
            return f"{canonical}.{'.'.join(rest)}"
        return canonical

    def _dotted_parts(self, node: ast.AST) -> Optional[list]:
        """Walk an Attribute/Name chain into a list of identifier parts.

        Returns None if the expression is not a static dotted name (e.g.
        a subscript, call result, or lambda).
        """
        parts: list = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            parts.reverse()
            return parts
        return None
