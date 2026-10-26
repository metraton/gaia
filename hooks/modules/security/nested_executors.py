"""Executors that run a command inside another process, container or host: the shape of what they hand over."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import FrozenSet, Optional, Sequence, Tuple

from .canonical_path import command_name

ARGV, COMMAND_OPTION = "argv", "command-option"
_HELP_FLAGS = frozenset({"-h", "--help", "--dry-run"})
_GENERIC_VERBS = frozenset({"run", "exec"})


@dataclass(frozen=True)
class NestedRun:
    """What an executor runs: *inner* is None for a session; *unparsed* names the word that stopped the parse."""

    executor: str
    inner: Optional[str] = None
    unparsed: Optional[str] = None
    remote: bool = False
    generic: bool = False


@dataclass(frozen=True)
class _Grammar:
    path: Tuple[str, ...]
    global_values: FrozenSet[str]
    global_booleans: FrozenSet[str]
    values: FrozenSet[str]
    booleans: FrozenSet[str]
    bundled: str
    targets: int
    payload: str
    dashdash: str
    remote: bool


_KUBECTL_VALUES = frozenset({
    "-n", "--namespace", "--context", "--cluster", "--user", "--kubeconfig", "-s", "--server",
    "--as", "--as-group", "--token", "--request-timeout", "-v", "--v",
})
_KUBECTL_EXEC = _Grammar(
    path=("exec",), global_values=_KUBECTL_VALUES, global_booleans=frozenset(),
    values=_KUBECTL_VALUES | {"-c", "--container", "-f", "--filename", "--pod-running-timeout"},
    booleans=frozenset({"-i", "-t", "-q", "--stdin", "--tty", "--quiet"}), bundled="itq",
    targets=1, payload=ARGV, dashdash="required", remote=True,
)
_DOCKER_GLOBAL_VALUES = frozenset({"-H", "--host", "--context", "-c", "--config", "-l", "--log-level"})
_DOCKER_GLOBAL_BOOLEANS = frozenset({"-D", "--debug", "--tls", "--tlsverify"})
_DOCKER_EXEC_VALUES = frozenset({"-e", "--env", "--env-file", "-u", "--user", "-w", "--workdir", "--detach-keys"})
_DOCKER_EXEC_BOOLEANS = frozenset({"-d", "-i", "-t", "--detach", "--interactive", "--tty", "--privileged"})


def _docker(path: Tuple[str, ...]) -> _Grammar:
    return _Grammar(
        path=path, global_values=_DOCKER_GLOBAL_VALUES, global_booleans=_DOCKER_GLOBAL_BOOLEANS,
        values=_DOCKER_EXEC_VALUES, booleans=_DOCKER_EXEC_BOOLEANS, bundled="dit",
        targets=1, payload=ARGV, dashdash="none", remote=True,
    )


_GCLOUD_VALUES = frozenset({
    "--project", "--account", "--configuration", "--verbosity", "--impersonate-service-account",
    "--billing-project", "--zone",
})
_GCLOUD_BOOLEANS = frozenset({"-q", "--quiet"})
_GCLOUD_SSH = _Grammar(
    path=("compute", "ssh"), global_values=_GCLOUD_VALUES, global_booleans=_GCLOUD_BOOLEANS,
    values=_GCLOUD_VALUES | {"--command", "--ssh-key-expire-after", "--strict-host-key-checking"},
    booleans=_GCLOUD_BOOLEANS | {"--tunnel-through-iap", "--internal-ip", "--plain"}, bundled="",
    targets=1, payload=COMMAND_OPTION, dashdash="forbidden", remote=True,
)
_OP_GLOBAL_VALUES = frozenset({"--account", "--config", "--session", "--encoding"})
_OP_GLOBAL_BOOLEANS = frozenset({"--cache", "--debug", "--iso-timestamps", "--no-color"})
_OP_RUN = _Grammar(
    path=("run",), global_values=_OP_GLOBAL_VALUES, global_booleans=_OP_GLOBAL_BOOLEANS,
    values=_OP_GLOBAL_VALUES | {"--env-file"}, booleans=_OP_GLOBAL_BOOLEANS, bundled="",
    targets=0, payload=ARGV, dashdash="required", remote=False,
)
_AWS_VAULT_GLOBAL_VALUES = frozenset({"--backend", "--prompt"})
_AWS_VAULT_EXEC = _Grammar(
    path=("exec",), global_values=_AWS_VAULT_GLOBAL_VALUES, global_booleans=frozenset({"--debug"}),
    values=_AWS_VAULT_GLOBAL_VALUES | {"-d", "--duration", "--region", "-t", "--mfa-token"},
    booleans=frozenset({"--debug", "-n", "--no-session", "--json", "--lazy"}), bundled="n",
    targets=1, payload=ARGV, dashdash="optional", remote=False,
)
_GRAMMARS = {
    "kubectl": (_KUBECTL_EXEC,),
    "oc": (_KUBECTL_EXEC,),
    "docker": (_docker(("exec",)), _docker(("container", "exec"))),
    "podman": (_docker(("exec",)), _docker(("container", "exec"))),
    "gcloud": (_GCLOUD_SSH,),
    "op": (_OP_RUN,),
    "aws-vault": (_AWS_VAULT_EXEC,),
}

_SHELL_PROGRAMS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "mksh", "ash", "fish", "csh", "tcsh", "busybox"})
_SESSION_PROGRAMS = _SHELL_PROGRAMS | {
    "python", "python3", "python2", "ipython", "node", "irb", "ruby", "perl", "php", "lua", "psql", "mysql", "redis-cli",
}
_INLINE_FLAGS = frozenset({"-c", "-e", "-m", "-E", "--eval", "--command", "-r"})
_LOCAL_STATE_PROGRAMS = frozenset({
    "python", "python3", "python2", "node", "deno", "ruby", "perl", "php", "pwsh", "powershell",
    "npm", "npx", "pnpm", "yarn", "bun", "bunx", "uv", "uvx", "poetry", "pipx",
    "git", "gaia", "rm", "rmdir", "cp", "tar",
})
_SCRIPT_WORD = re.compile(r"(^|/)[\w.-]+\.(sh|bash|zsh|py|js|mjs|cjs|ts|rb|pl|pm|php|ps1)$")
_WORD = re.compile(r"[^\s'\"`;&|()<>]+")


def _flag_kind(token: str, values: FrozenSet[str], booleans: FrozenSet[str], bundled: str) -> str:
    name, has_value, _ = token.partition("=")
    if name in values:
        return "value-attached" if has_value else "value"
    if not has_value and token in booleans:
        return "boolean"
    short = token[1:]
    if bundled and not token.startswith("--") and short and all(ch in bundled for ch in short):
        return "boolean"
    return ""


def _skip_to_path(args: Sequence[str], grammar: _Grammar) -> Optional[int]:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        kind = _flag_kind(args[index], grammar.global_values, grammar.global_booleans, "")
        if not kind:
            return None
        index += 2 if kind == "value" else 1
    if tuple(args[index:index + len(grammar.path)]) != grammar.path:
        return None
    return index + len(grammar.path)


def _parse(executor: str, args: Sequence[str], grammar: _Grammar) -> Optional[NestedRun]:
    after_dashdash = list(args[args.index("--") + 1:]) if "--" in args else None
    literal = shlex.join(after_dashdash) if after_dashdash and grammar.dashdash != "none" else None
    targets, command, index = [], None, 0
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        if token.startswith("-") and len(token) > 1 and not (targets and grammar.dashdash == "none"):
            if token in _HELP_FLAGS:
                return None
            kind = _flag_kind(token, grammar.values, grammar.booleans, grammar.bundled)
            if not kind or (kind == "value" and index + 1 >= len(args)):
                return NestedRun(executor, literal, unparsed=token, remote=grammar.remote)
            if token.partition("=")[0] == "--command":
                command = token.partition("=")[2] if kind == "value-attached" else args[index + 1]
            index += 2 if kind == "value" else 1
            continue
        if len(targets) < grammar.targets:
            targets.append(token)
            index += 1
            continue
        if grammar.dashdash in ("none", "optional"):
            after_dashdash = list(args[index:])
            break
        return NestedRun(executor, literal, unparsed=token, remote=grammar.remote)
    if len(targets) < grammar.targets:
        return NestedRun(executor, literal, unparsed="the target it runs in", remote=grammar.remote)
    if grammar.payload == COMMAND_OPTION:
        if after_dashdash is not None:
            return NestedRun(executor, command, unparsed="--", remote=grammar.remote)
        inner = command.strip() if command is not None else ""
        return NestedRun(executor, inner or None, remote=grammar.remote)
    if grammar.dashdash == "required" and after_dashdash is None:
        return None
    if after_dashdash and after_dashdash[0].startswith("-"):
        return NestedRun(executor, shlex.join(after_dashdash), unparsed=after_dashdash[0], remote=grammar.remote)
    return NestedRun(executor, shlex.join(after_dashdash) if after_dashdash else None, remote=grammar.remote)


def _parse_generic(program: str, args: Sequence[str]) -> Optional[NestedRun]:
    from .mutative_verbs import CATEGORY_MUTATIVE, _is_unmodelled_cli, _verb_word_effect

    if not args or args[0] not in _GENERIC_VERBS or "--" not in args or not _is_unmodelled_cli(program):
        return None
    prefix, after = args[1:args.index("--")], list(args[args.index("--") + 1:])
    if not after:
        return None
    stopper = next((word for word in prefix if word.startswith("-") or word[:1] in ("$", "`")
                    or _verb_word_effect(word) == CATEGORY_MUTATIVE), None)
    return NestedRun(f"{program} {args[0]}", shlex.join(after), unparsed=stopper, generic=True)


def nested_run(tokens: Sequence[str]) -> Optional[NestedRun]:
    """Return what the executor at the head of *tokens* (words after quote removal) runs, or None when it is not one."""
    if len(tokens) < 2:
        return None
    program, args = command_name(tokens[0]), list(tokens[1:])
    for grammar in _GRAMMARS.get(program, ()):
        start = _skip_to_path(args, grammar)
        if start is not None:
            return _parse(f"{program} {' '.join(grammar.path)}", args[start:], grammar)
    return _parse_generic(program, args)


def opens_session(inner: str) -> bool:
    """True when *inner* starts a shell or interpreter that waits for input instead of running a command."""
    try:
        words = shlex.split(inner)
    except ValueError:
        return False
    if not words or command_name(words[0]) not in _SESSION_PROGRAMS:
        return False
    return all(word.startswith("-") and word not in _INLINE_FLAGS for word in words[1:])


def reads_local_state(inner: str) -> bool:
    """True when a verdict on *inner* could rest on a local file the remote host does not share."""
    words = _WORD.findall(inner)
    for index, word in enumerate(words):
        name = command_name(word)
        if name in _LOCAL_STATE_PROGRAMS or word.startswith(("./", "../", "~")) or _SCRIPT_WORD.search(word):
            return True
        if name in _SHELL_PROGRAMS and any(not w.startswith("-") for w in words[index + 1:index + 2]):
            return True
    return False
