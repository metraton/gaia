#!/usr/bin/env python3
"""No Gaia CLI verb that can spawn a package-manager lifecycle script is free.

A lifecycle script is an arbitrary command the packager runs on the repository
itself: ``npm pack`` runs ``prepack``, which in this repo regenerates the plugin
root manifests -- including ``hooks/hooks.json``, a categorically protected
path. So a verb that reaches a lifecycle spawn reaches an unbounded write to the
working tree, and the classifier must gate it.

The invariant is keyed on the PROPERTY, never on a verb list. Both halves are
derived from the tree on every run:

* the verb chains come from the real argparse tree the dispatcher builds
  (``bin/gaia::_build_parser`` registers every ``bin/cli/*.py``), so a verb
  added tomorrow is enumerated the moment it exists; and
* reachability comes from a call graph over ``bin/``, ``scripts/`` and
  ``tools/``, so a lifecycle spawn added behind any number of helper hops is
  found without anything naming the helper.

That is what a fix at the write sink cannot do. Hardening the generator closes
the one route that was measured; it cannot see the next verb whose subprocess
reaches the same place. Here, a new free verb that spawns a lifecycle script
fails the moment it is written, because "T0 by elimination" -- a verb classified
read-only because nothing classified it at all -- is precisely the case this
asserts against.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BIN_DIR = REPO_ROOT / "bin"
CLI_DIR = BIN_DIR / "cli"
HOOKS_DIR = REPO_ROOT / "hooks"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(BIN_DIR))

from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.tiers import SecurityTier, classify_command_tier  # noqa: E402

# Basenames, so an absolute path or a Windows shim resolves the same way.
PACKAGE_MANAGERS = frozenset({
    "npm", "npm.cmd", "npm.exe",
    "pnpm", "pnpm.cmd", "pnpm.exe",
    "yarn", "yarn.cmd", "yarn.exe",
})

# Subcommands that hand control to a script declared in package.json. `config`,
# `init`, `ls`, `view` and friends are absent because they run no such script:
# the property is "arbitrary repository code executes", not "npm was invoked".
LIFECYCLE_SUBCOMMANDS = frozenset({
    "add", "ci", "dedupe", "exec", "i", "install", "link", "pack", "prune",
    "publish", "rebuild", "restart", "run", "run-script", "start", "stop",
    "test", "unlink", "up", "update", "version",
})

SOURCE_ROOT_NAMES = ("bin", "scripts", "tools")

_SHELL_COMMENT_RE = re.compile(r"(?m)(?:^|\s)#.*$")
_SHELL_SEGMENT_RE = re.compile(r"\|\||&&|[;|()]|\bthen\b|\bdo\b|\belse\b")
_SHELL_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*")
_SHELL_PREFIXES = frozenset({"!", "if", "elif", "while", "until", "time", "env", "exec", "nohup", "sudo"})


# ---------------------------------------------------------------------------
# Spawn detection
# ---------------------------------------------------------------------------

def _argv_lifecycle_spawn(node: ast.AST) -> str | None:
    """Return "npm pack" when *node* is an argv literal running a lifecycle.

    Keyed on the ARGV LITERAL rather than on the callee, because the caller is
    not reliably nameable: ``bin/cli/install.py`` spawns through a ``runner``
    parameter, and a helper can pass the list down any number of hops. The
    argv is the invariant part of a subprocess spawn.

    Reading only the literal is also what keeps prose out: a docstring or a
    remediation message that mentions ``npm install`` is a lone string, never a
    list whose first element is the packager. That distinction is the whole
    reason this does not scan raw text -- ``bin/cli/doctor.py`` prints
    ``npm install @jaguilar87/gaia@latest`` as advice and spawns nothing.
    """
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    constants = [
        element.value
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    if not constants or Path(constants[0]).name not in PACKAGE_MANAGERS:
        return None
    for token in constants[1:]:
        if token.startswith("-"):
            continue
        return f"{constants[0]} {token}" if token in LIFECYCLE_SUBCOMMANDS else None
    return None


def _shell_lifecycle_spawn(text: str) -> str | None:
    """Return the lifecycle command a shell script runs, or None.

    The packager has to sit at a COMMAND POSITION -- first token of a line or
    of a pipeline/list segment, past any leading assignment. Matching it
    anywhere in the text instead reads the script's own usage banner
    (``--version <spec>  npm version specifier``) as an invocation, which names
    the wrong command in a failure message that exists to be acted on.

    A heredoc body whose line genuinely starts with the packager still matches.
    That direction is the safe one: a false positive asks for consent nobody
    needed, a false negative hands out an ungated lifecycle spawn.
    """
    for line in _SHELL_COMMENT_RE.sub("", text).splitlines():
        for segment in _SHELL_SEGMENT_RE.split(line):
            tokens = segment.split()
            while tokens and (tokens[0] in _SHELL_PREFIXES or _SHELL_ASSIGNMENT_RE.fullmatch(tokens[0])):
                tokens.pop(0)
            if not tokens or Path(tokens[0]).name not in PACKAGE_MANAGERS:
                continue
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                if token in LIFECYCLE_SUBCOMMANDS:
                    return f"{tokens[0]} {token}"
                break
    return None


# ---------------------------------------------------------------------------
# Static index
# ---------------------------------------------------------------------------

class SourceIndex:
    """Functions, import aliases and shell scripts of the scanned source roots."""

    def __init__(self) -> None:
        self.functions: dict[Path, dict[str, ast.AST]] = {}
        self.module_aliases: dict[Path, dict[str, Path]] = {}
        self.function_aliases: dict[Path, dict[str, tuple[Path, str]]] = {}
        self.shell_scripts: dict[str, Path] = {}
        self._build()

    def _source_files(self, suffix: str):
        for root_name in SOURCE_ROOT_NAMES:
            root = REPO_ROOT / root_name
            if root.is_dir():
                yield from sorted(root.rglob(f"*{suffix}"))

    def _resolve_module(self, dotted: str) -> Path | None:
        parts = dotted.split(".")
        for root in (BIN_DIR, REPO_ROOT):
            candidate = root.joinpath(*parts).with_suffix(".py")
            if candidate.is_file():
                return candidate
            package = root.joinpath(*parts, "__init__.py")
            if package.is_file():
                return package
        return None

    def _build(self) -> None:
        for path in self._source_files(".sh"):
            self.shell_scripts[path.name] = path
        for path in self._source_files(".py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            self.functions[path] = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self._index_imports(path, tree)

    def _index_imports(self, path: Path, tree: ast.AST) -> None:
        modules: dict[str, Path] = {}
        functions: dict[str, tuple[Path, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = self._resolve_module(alias.name)
                    if target is not None:
                        modules[(alias.asname or alias.name).split(".")[0]] = target
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    # A relative import is resolved against the importer's own
                    # package directory, which the dotted resolver cannot see.
                    base = path.parents[node.level - 1]
                    for alias in node.names:
                        candidate = base / f"{alias.name}.py"
                        if candidate.is_file():
                            modules[alias.asname or alias.name] = candidate
                        elif (base / f"{prefix}.py").is_file():
                            functions[alias.asname or alias.name] = (
                                base / f"{prefix}.py", alias.name,
                            )
                    continue
                for alias in node.names:
                    as_module = self._resolve_module(f"{prefix}.{alias.name}" if prefix else alias.name)
                    if as_module is not None:
                        modules[alias.asname or alias.name] = as_module
                        continue
                    owner = self._resolve_module(prefix) if prefix else None
                    if owner is not None:
                        functions[alias.asname or alias.name] = (owner, alias.name)
        self.module_aliases[path] = modules
        self.function_aliases[path] = functions

    def spawn_in(self, path: Path, name: str) -> str | None:
        """The lifecycle spawn this one function performs itself, if any."""
        node = self.functions.get(path, {}).get(name)
        if node is None:
            return None
        for child in ast.walk(node):
            spawn = _argv_lifecycle_spawn(child)
            if spawn is not None:
                return spawn
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                script = self.shell_scripts.get(Path(child.value).name)
                if script is not None and child.value.endswith(".sh"):
                    shell_spawn = _shell_lifecycle_spawn(script.read_text(encoding="utf-8"))
                    if shell_spawn is not None:
                        return f"{script.relative_to(REPO_ROOT)}: {shell_spawn}"
        return None

    def callees(self, path: Path, name: str) -> list[tuple[Path, str]]:
        node = self.functions.get(path, {}).get(name)
        if node is None:
            return []
        local = self.functions.get(path, {})
        modules = self.module_aliases.get(path, {})
        functions = self.function_aliases.get(path, {})
        out: list[tuple[Path, str]] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            callee = child.func
            if isinstance(callee, ast.Name):
                if callee.id in local:
                    out.append((path, callee.id))
                elif callee.id in functions:
                    out.append(functions[callee.id])
            elif isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name):
                owner = modules.get(callee.value.id)
                if owner is not None:
                    out.append((owner, callee.attr))
        return out

    def lifecycle_trail(self, start: tuple[Path, str]) -> list[str] | None:
        """Shortest call trail from *start* to a lifecycle spawn, or None."""
        seen = {start}
        queue: list[tuple[tuple[Path, str], list[str]]] = [(start, [])]
        while queue:
            (path, name), trail = queue.pop(0)
            label = f"{path.relative_to(REPO_ROOT)}::{name}"
            spawn = self.spawn_in(path, name)
            if spawn is not None:
                return trail + [label, f"spawns `{spawn}`"]
            for callee in self.callees(path, name):
                if callee not in seen:
                    seen.add(callee)
                    queue.append((callee, trail + [label]))
        return None


# ---------------------------------------------------------------------------
# Verb chains
# ---------------------------------------------------------------------------

def _verb_chains() -> list[tuple[tuple[str, ...], object]]:
    """Every ``gaia`` verb chain with the handler that runs it.

    Mirrors ``bin/gaia::_build_parser``: every ``bin/cli/*.py`` registers into
    one shared subparsers action. A sub-verb binds its handler with
    ``set_defaults(func=...)`` and is dispatched inside the plugin through
    ``args.func``, so that binding is invisible to call syntax and must be read
    back off the parser. A chain with no binding of its own falls back to the
    dispatcher's own contract, ``mod.cmd_<stem>``.
    """
    parser = argparse.ArgumentParser(prog="gaia")
    subparsers = parser.add_subparsers(dest="subcommand")
    chains: list[tuple[tuple[str, ...], object]] = []

    for path in sorted(CLI_DIR.glob("*.py")):
        stem = path.stem
        if stem.startswith("_"):
            continue
        module = importlib.import_module(f"cli.{stem}")
        register = getattr(module, "register", None)
        if register is None:
            continue
        before = set(subparsers.choices)
        register(subparsers)
        fallback = getattr(module, f"cmd_{stem}", None)
        for name in set(subparsers.choices) - before:
            chains.extend(_walk_parser(subparsers.choices[name], (name,), fallback))
    return chains


def _walk_parser(parser, chain, fallback):
    yield chain, parser._defaults.get("func", fallback)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _walk_parser(sub, chain + (name,), fallback)


def _handler_node(handler) -> tuple[Path, str] | None:
    if handler is None:
        return None
    source = inspect.getsourcefile(handler)
    return (Path(source).resolve(), handler.__name__) if source else None


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------

def test_no_free_gaia_verb_can_spawn_a_package_lifecycle_script():
    """Asserted against ``detect_mutative_command``, which is what enforces.

    Not against ``classify_command_tier``: that function tests T1_PATTERNS
    (``check``, ``validate``, ``lint``) and returns before it ever consults the
    detector, so ``gaia release check`` reads T1 there no matter what the
    detector decides. Its own docstring says it produces tier metadata AFTER
    the bash validator has enforced, and the validator enforces on
    ``is_mutative`` -- that boolean is the consent decision, so that is the
    assertion. The tier is reported in the failure text for context only.
    """
    index = SourceIndex()
    violations = []
    unreadable = []

    for chain, handler in _verb_chains():
        node = _handler_node(handler)
        if node is None or node[1] not in index.functions.get(node[0], {}):
            unreadable.append(f"gaia {' '.join(chain)} -> {handler!r}")
            continue
        trail = index.lifecycle_trail(node)
        if trail is None:
            continue
        command = "gaia " + " ".join(chain)
        result = detect_mutative_command(command)
        if result.is_mutative:
            continue
        violations.append(
            f"{command!r} is NOT mutative to the live detector "
            f"(tier={classify_command_tier(command).value}, verb={result.verb!r}, "
            f"{result.reason}) yet reaches a package-manager lifecycle script:"
            "\n    " + "\n    -> ".join(trail)
        )

    assert not unreadable, (
        "A verb handler could not be located in the scanned source roots, so this "
        "invariant cannot see what it reaches. Extend SOURCE_ROOT_NAMES or the "
        "handler resolution -- do not leave the verb unchecked:\n  "
        + "\n  ".join(sorted(unreadable))
    )
    assert not violations, (
        "A Gaia CLI verb that spawns a package-manager lifecycle script is not "
        "gated. A lifecycle script runs arbitrary repository code -- in this repo "
        "prepack rewrites hooks/hooks.json, a protected path -- so the verb must "
        "classify T3 and ask for consent. Anchor it in "
        "COMMAND_PATH_MUTATIVE_UPGRADES (hooks/modules/security/mutative_verbs.py), "
        "or take the spawn out of its reach:\n\n" + "\n\n".join(sorted(violations))
    )


def test_the_scanned_sources_spawn_only_through_argv_literals():
    """The detector reads argv literals; a shell-string spawn would evade it.

    Not a style rule. ``shell=True`` or ``os.system`` passes the command as one
    opaque string, which ``_argv_lifecycle_spawn`` cannot read -- the same
    quotation-versus-use blindness that static classification has everywhere.
    Today no such call exists in bin/, scripts/ or tools/; if one is added, this
    fails to say the detector must grow a string lane before it is trusted.
    """
    index = SourceIndex()
    offenders = []
    for path in index.functions:
        text = path.read_text(encoding="utf-8")
        for marker in ("shell=True", "os.system(", "os.popen(", "os.execv", "os.spawn"):
            if marker in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert not offenders, (
        "A shell-string spawner appeared in the scanned sources. The lifecycle "
        "detector only reads argv list literals, so this call is invisible to it:"
        "\n  " + "\n  ".join(sorted(offenders))
    )
