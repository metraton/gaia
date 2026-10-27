"""Executors that run a command inside another process, container or host: the shape of what they hand over."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .canonical_path import command_name

ARGV, COMMAND_OPTION = "argv", "command-option"
_HELP_FLAGS = frozenset({"-h", "--help", "--dry-run"})
_GENERIC_VERBS = frozenset({"run", "exec"})


@dataclass(frozen=True)
class NestedRun:
    """What an executor runs: *inner* is None for a session; *unparsed* names the word that stopped the parse.

    *reads* are local files the executor loads (``--env-file``); *alternatives* are commands an ambiguous parse may run.
    """

    executor: str
    inner: Optional[str] = None
    unparsed: Optional[str] = None
    remote: bool = False
    generic: bool = False
    reads: Tuple[str, ...] = ()
    alternatives: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _Grammar:
    """``dashdash="legacy"`` also takes the command from the first word after the targets, when no ``--`` follows."""

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
    file_values: FrozenSet[str] = frozenset()


_KUBECTL_VALUES = frozenset({
    "-n", "--namespace", "--context", "--cluster", "--request-timeout", "-v", "--v",
})
_KUBECTL_EXEC = _Grammar(
    path=("exec",), global_values=_KUBECTL_VALUES, global_booleans=frozenset(),
    values=_KUBECTL_VALUES | {"-c", "--container", "-f", "--filename", "--pod-running-timeout"},
    booleans=frozenset({"-i", "-t", "-q", "--stdin", "--tty", "--quiet"}), bundled="itq",
    targets=1, payload=ARGV, dashdash="legacy", remote=True,
)
_DOCKER_GLOBAL_VALUES = frozenset({"--context", "-c", "-l", "--log-level"})
_DOCKER_GLOBAL_BOOLEANS = frozenset({"-D", "--debug", "--tls", "--tlsverify"})
_DOCKER_EXEC_VALUES = frozenset({"-e", "--env", "--env-file", "-u", "--user", "-w", "--workdir", "--detach-keys"})
_DOCKER_EXEC_BOOLEANS = frozenset({"-d", "-i", "-t", "--detach", "--interactive", "--tty"})


def _docker(path: Tuple[str, ...]) -> _Grammar:
    return _Grammar(
        path=path, global_values=_DOCKER_GLOBAL_VALUES, global_booleans=_DOCKER_GLOBAL_BOOLEANS,
        values=_DOCKER_EXEC_VALUES, booleans=_DOCKER_EXEC_BOOLEANS, bundled="dit",
        targets=1, payload=ARGV, dashdash="none", remote=True, file_values=frozenset({"--env-file"}),
    )


_GCLOUD_VALUES = frozenset({
    "--project", "--account", "--configuration", "--verbosity", "--billing-project", "--zone",
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
    targets=0, payload=ARGV, dashdash="required", remote=False, file_values=frozenset({"--env-file"}),
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

TARGET_VARIABLES = {
    "kubectl": {"KUBECONFIG": "--kubeconfig"},
    "oc": {"KUBECONFIG": "--kubeconfig"},
    "docker": {"DOCKER_HOST": "--host", "DOCKER_CONFIG": "--config"},
    "podman": {"CONTAINER_HOST": "--url", "CONTAINER_CONNECTION": "--connection", "DOCKER_HOST": "--url"},
    "gcloud": {
        "CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT": "--impersonate-service-account",
        "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE": "--access-token-file",
    },
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
_COPIERS_TO_HOSTS = frozenset({"scp", "sftp", "rsync"})
_SCRIPT_WORD = re.compile(r"(^|/)[\w.-]+\.(sh|bash|zsh|py|js|mjs|cjs|ts|rb|pl|pm|php|ps1)$")
_WORD = re.compile(r"[^\s'\"`;&|()<>]+")
_TARGET_NAMES = frozenset(name for variables in TARGET_VARIABLES.values() for name in variables)
_TARGET_MENTION = re.compile(
    rf"(?<![\w$])(?P<name>{'|'.join(sorted(_TARGET_NAMES))})(?!\w)(?:\[[^\]]*\])?(?:\+?=(?P<value>[^\s;&|()<>]*))?"
)
_OPAQUE_SETTER = re.compile(r"(?:^|[\s;&|(){}!`])(?:source|\.)(?=\s)|[<>]\(|<<<")
_NAME_SETTERS = frozenset({"export", "declare", "typeset", "readonly", "local"})
_NAME_READERS = {"read": "dinNptu", "mapfile": "dnOsuCc", "readarray": "dnOsuCc"}


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


def _skip_to_path(args: Sequence[str], grammar: _Grammar) -> Tuple[Optional[int], Optional[str]]:
    """Where the executor's own words start, or else the unknown global flag standing before its path."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        kind = _flag_kind(args[index], grammar.global_values, grammar.global_booleans, "")
        if not kind:
            width = len(grammar.path)
            reaches = any(tuple(args[at:at + width]) == grammar.path for at in range(index + 1, len(args)))
            return None, args[index] if reaches else None
        index += 2 if kind == "value" else 1
    if tuple(args[index:index + len(grammar.path)]) != grammar.path:
        return None, None
    return index + len(grammar.path), None


def _parse(executor: str, args: Sequence[str], grammar: _Grammar) -> Optional[NestedRun]:
    after_dashdash = list(args[args.index("--") + 1:]) if "--" in args else None
    literal = shlex.join(after_dashdash) if after_dashdash and grammar.dashdash != "none" else None
    targets, command, reads, index = [], None, [], 0

    def found(inner: Optional[str], unparsed: Optional[str] = None, alternatives: Tuple[str, ...] = ()) -> NestedRun:
        return NestedRun(
            executor, inner, unparsed=unparsed, remote=grammar.remote, reads=tuple(reads), alternatives=alternatives,
        )

    while index < len(args):
        token = args[index]
        if token == "--":
            break
        if token.startswith("-") and len(token) > 1 and not (targets and grammar.dashdash == "none"):
            if token in _HELP_FLAGS:
                return None
            kind = _flag_kind(token, grammar.values, grammar.booleans, grammar.bundled)
            if not kind or (kind == "value" and index + 1 >= len(args)):
                return found(literal, token)
            name, _, attached = token.partition("=")
            value = attached if kind == "value-attached" else args[index + 1] if kind == "value" else None
            if name == "--command":
                command = value
            if name in grammar.file_values:
                reads.append(value)
            index += 2 if kind == "value" else 1
            continue
        if len(targets) < grammar.targets:
            targets.append(token)
            index += 1
            continue
        if grammar.dashdash == "legacy" and "--" in args:
            return found(literal, token, (shlex.join(args[index:]),))
        if grammar.dashdash in ("none", "optional", "legacy"):
            after_dashdash = list(args[index:])
            break
        return found(literal, token)
    if len(targets) < grammar.targets:
        return found(literal, "the target it runs in")
    if grammar.payload == COMMAND_OPTION:
        if after_dashdash is not None:
            return found(command, "--")
        inner = command.strip() if command is not None else ""
        return found(inner or None)
    if grammar.dashdash == "required" and after_dashdash is None:
        return None
    if after_dashdash and after_dashdash[0].startswith("-"):
        return found(shlex.join(after_dashdash), after_dashdash[0])
    return found(shlex.join(after_dashdash) if after_dashdash else None)


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
        executor = f"{program} {' '.join(grammar.path)}"
        start, stopper = _skip_to_path(args, grammar)
        if stopper is not None:
            return NestedRun(executor, unparsed=stopper, remote=grammar.remote)
        if start is not None:
            return _parse(executor, args[start:], grammar)
    return _parse_generic(program, args)


def with_target_flags(prefix: Sequence[str], words: Sequence[str]) -> Optional[List[str]]:
    """*words* with each variable *prefix* assigns to choose the program's target or identity spelled as its flag, else None."""
    names = TARGET_VARIABLES.get(command_name(words[0]), {}) if words else {}
    assigned = (word.partition("=") for word in prefix)
    flags = [f"{names[name]}={value}" for name, equals, value in assigned if equals and name in names]
    return [words[0], *flags, *words[1:]] if flags else None


def _reader_names(args: Sequence[str], value_letters: str) -> List[str]:
    names: List[str] = []
    takes_value, options = False, True
    for word in args:
        if takes_value:
            takes_value = False
        elif options and word == "--":
            options = False
        elif options and word.startswith("-") and len(word) > 1:
            takes_value = word[-1] in value_letters
        else:
            names.append(word)
    return names


def _printf_names(args: Sequence[str]) -> List[str]:
    return [word[2:] or (args[index + 1] if index + 1 < len(args) else "")
            for index, word in enumerate(args) if word.startswith("-v")]


def _computes_a_name(words: Sequence[str]) -> bool:
    computed = (index for index, word in enumerate(words)
                if word in _NAME_SETTERS or word in _NAME_READERS or word in ("eval", "printf"))
    start = next(computed, None)
    if start is None:
        return False
    command, args = words[start], words[start + 1:]
    if command == "eval":
        return any(mark in word for word in args for mark in "=$`")
    if command == "printf":
        names = _printf_names(args)
    elif command in _NAME_READERS:
        names = _reader_names(args, _NAME_READERS[command])
    else:
        names = [word.partition("=")[0] for word in args if not word.startswith(("-", "+"))]
    return any("$" in name or "`" in name for name in names)


def targets_set_on_line(line: str) -> Dict[str, str]:
    """Each target variable *line* names outside a ``$`` read, as written or after quote removal, or every one when it sources a file, substitutes a process, feeds a here-string, evals an assignment or sets a computed name; no scope, unset or unexport on the line ends one."""
    from ..tools.shell_parser import parse_command, shell_split

    unquoted: List[str] = []
    for component in parse_command(line):
        try:
            words = shell_split(component)
        except ValueError:
            continue
        if _computes_a_name(words):
            return {name: f"${name}" for name in _TARGET_NAMES}
        unquoted.append(" ".join(words))
    if _OPAQUE_SETTER.search(line):
        return {name: f"${name}" for name in _TARGET_NAMES}
    found: Dict[str, str] = {}
    for text in (line, *unquoted):
        for match in _TARGET_MENTION.finditer(text):
            name, value = match.group("name"), match.group("value")
            if text[:match.start()].endswith(("${", "${!", "${#")) and not text[match.end("name"):].startswith(("=", ":=")):
                continue
            if value or name not in found:
                found[name] = value.strip("'\"") if value else f"${name}"
    return found


def opens_session(inner: str) -> bool:
    """True when *inner* starts a shell or interpreter that waits for input instead of running a command."""
    try:
        words = shlex.split(inner)
    except ValueError:
        return False
    if not words or command_name(words[0]) not in _SESSION_PROGRAMS:
        return False
    return all(word.startswith("-") and word not in _INLINE_FLAGS for word in words[1:])


def writes_or_sends(program: str) -> bool:
    """True when *program* is a file writer or a network sender, whose effect only its flags can show to be a read."""
    from .composition_rules import _NETWORK_EXECUTABLES
    from .protected_path_guard import _FILESYSTEM_WRITE_COMMANDS

    name = command_name(program)
    return name in _FILESYSTEM_WRITE_COMMANDS or name in _NETWORK_EXECUTABLES or name in _COPIERS_TO_HOSTS


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
