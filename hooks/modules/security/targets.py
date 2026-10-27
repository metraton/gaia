"""Where a command lands -- cluster context, project, account, workspace, stack or branch -- and whether that is production.

Everything here reads files or the database, so it runs outside the classifier's command-keyed cache.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .canonical_path import command_name

UNDETERMINED = "?"
DECLARATION_SECTION = "targets"

# Only a program no classifier table names (mutative_verbs._is_unmodelled_cli) is read with the generic row.
_GENERIC_TARGET_FLAGS = frozenset({
    "--context", "--kube-context", "--cluster", "--project", "--account", "--profile",
    "--workspace", "--stack", "--env", "--environment", "--subscription",
})
_KUBE_TARGET_FLAGS = frozenset({"--context", "--cluster"})
_PROGRAM_TARGET_FLAGS: Dict[str, frozenset] = {
    "kubectl": _KUBE_TARGET_FLAGS, "oc": _KUBE_TARGET_FLAGS, "k9s": _KUBE_TARGET_FLAGS,
    "helm": frozenset({"--kube-context"}), "flux": frozenset({"--context"}),
    "docker": frozenset({"--context", "-c"}), "podman": frozenset({"--connection", "-c"}),
    "gcloud": frozenset({"--project", "--account"}), "aws": frozenset({"--profile"}),
    "az": frozenset({"--subscription"}), "pulumi": frozenset({"--stack", "-s"}),
}
_PROGRAM_NAMING_VARIABLES: Dict[str, frozenset] = {
    "docker": frozenset({"DOCKER_CONTEXT"}), "podman": frozenset({"CONTAINER_CONNECTION"}),
    "gcloud": frozenset({"CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT"}), "aws": frozenset({"AWS_PROFILE"}),
    "terraform": frozenset({"TF_WORKSPACE"}),
}
_NAMING_VARIABLES = frozenset().union(*_PROGRAM_NAMING_VARIABLES.values())
_KUBE_PROGRAMS = frozenset({"kubectl", "oc", "helm", "flux", "kustomize", "k9s"})
_KUBE_OPTIONS = {
    **dict.fromkeys((
        "-n", "--namespace", "--kubeconfig", "--user", "-s", "--server", "--request-timeout", "-v", "--v",
        "-l", "--selector", "-o", "--output", "-f", "--filename", "-c", "--container", "--as", "--as-group",
        "--as-uid", "--token", "--username", "--password", "--certificate-authority", "--client-certificate",
        "--client-key", "--tls-server-name", "--cache-dir",
    ), 1),
    "--insecure-skip-tls-verify": 0, "--match-server-version": 0,
}
_DOCKER_OPTIONS = {
    **dict.fromkeys(("--context", "-H", "--host", "--config", "-l", "--log-level", "--tlscacert", "--tlscert", "--tlskey"), 1),
    "-D": 0, "--debug": 0, "--tls": 0, "--tlsverify": 0,
}
# Words each option before the operation word takes; an unlisted option keeps a production verdict from becoming a read.
# The aws, az and gcloud rows are the facts of mutative_verbs._CLOUD_GLOBAL_OPTIONS (task 1019) and must stay aligned.
_OPTION_ARITY: Dict[str, Dict[str, int]] = {
    "kubectl": _KUBE_OPTIONS, "oc": _KUBE_OPTIONS, "docker": _DOCKER_OPTIONS, "podman": _DOCKER_OPTIONS,
    "helm": {
        **dict.fromkeys(("-n", "--namespace", "--kubeconfig", "--kube-apiserver", "--kube-as-user", "--kube-as-group",
                         "--kube-ca-file", "--kube-token", "--kube-tls-server-name", "--registry-config",
                         "--repository-config", "--repository-cache", "--burst-limit", "--qps"), 1),
        "--debug": 0, "--kube-insecure-skip-tls-verify": 0,
    },
    "flux": {**dict.fromkeys(("-n", "--namespace", "--kubeconfig", "--timeout"), 1), "--verbose": 0},
    "aws": {
        **dict.fromkeys(("--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle", "--cli-read-timeout",
                         "--cli-connect-timeout", "--cli-binary-format"), 1),
        **dict.fromkeys(("--debug", "--no-verify-ssl", "--no-paginate", "--no-sign-request", "--no-cli-pager"), 0),
    },
    "az": {"--output": 1, "-o": 1, "--query": 1, "--debug": 0, "--verbose": 0, "--only-show-errors": 0},
    "gcloud": {
        **dict.fromkeys(("--configuration", "--verbosity", "--billing-project", "--zone", "--region", "--format",
                         "--impersonate-service-account", "--filter", "--limit", "--trace-token"), 1),
        "--quiet": 0, "-q": 0, "--log-http": 0,
    },
    "terraform": {"-chdir": 1, "-or-create": 0},
    "pulumi": {"-C": 1, "--cwd": 1, "--color": 1, "-v": 1, "--non-interactive": 0},
}
_KUBE_GROUPS = frozenset({"config", "rollout", "auth", "certificate"})
_DOCKER_GROUPS = frozenset({"container", "image", "volume", "network", "context", "compose", "system", "buildx"})
_GROUP_WORDS = {
    "kubectl": _KUBE_GROUPS, "oc": _KUBE_GROUPS, "docker": _DOCKER_GROUPS, "podman": _DOCKER_GROUPS,
    "terraform": frozenset({"workspace", "state"}), "gcloud": frozenset({"config", "alpha", "beta"}),
    "pulumi": frozenset({"stack"}),
}
_VERB_PATH_PROGRAMS = frozenset({"gcloud", "az"})
_SWITCHER_PROGRAMS = frozenset({"kubectx", "kubens"})
_KUBE_SWITCHES = frozenset({"use-context", "use"})
_CREDENTIAL_FETCHERS = {"gcloud": "get-credentials", "az": "get-credentials", "aws": "update-kubeconfig"}
_SWITCH_WORDS: Dict[str, frozenset] = {
    "kubectl": _KUBE_SWITCHES, "oc": _KUBE_SWITCHES, "docker": frozenset({"use"}), "podman": frozenset({"use"}),
    "terraform": frozenset({"select", "new"}), "gcloud": frozenset({"activate", "get-credentials"}),
    "pulumi": frozenset({"select"}), "az": frozenset({"get-credentials"}), "aws": frozenset({"update-kubeconfig"}),
    "kubectx": frozenset({"kubectx"}), "kubens": frozenset({"kubens"}),
}
_SWITCH_DESTINATION_FLAGS: Dict[str, frozenset] = {
    "pulumi": frozenset({"--stack", "-s"}), "az": frozenset({"-n", "--name"}), "aws": frozenset({"--name"}),
}
_EXTRA_READ_WORDS = frozenset({"access", "ps"})
_KUBE_CONTEXT_FLAGS = frozenset({"--context", "--kube-context"})
_PRODUCTION_WORDS = frozenset({"prod", "production", "prd"})
_NAME_PARTS = re.compile(r"[a-z0-9]+")
_DYNAMIC = re.compile(r"[$`]")
_ASSIGNMENT = re.compile(r"([A-Za-z_]\w*)=(.*)", re.S)
_CURRENT_CONTEXT = re.compile(r"""^current-context:[ \t]*["']?([^"'\s#]*)""", re.M)
_PULUMI_NAME = re.compile(r"""^name:[ \t]*["']?([^"'\s#]+)""", re.M)
_CONFIGURATION_NAME = re.compile(r"[A-Za-z0-9][\w.-]*")
_YAML_KEY = re.compile(r"^﻿?[A-Za-z][\w-]*:", re.M)
_SUBSCRIPTION_ID = re.compile(r"(?=[^0-9]*[0-9])[0-9A-Fa-f-]+")
_HELP_PROGRAMS = frozenset(_OPTION_ARITY) | frozenset(_PROGRAM_TARGET_FLAGS) | _KUBE_PROGRAMS | _SWITCHER_PROGRAMS


@dataclass(frozen=True)
class Target:
    """One place a command lands: *kind* says where the name came from, *name* is UNDETERMINED when nothing literal names it."""

    kind: str
    name: str


def _attached_short(word: str, options) -> Optional[str]:
    """The known short option *word* starts with when its value is written attached (``-napp``), else None."""
    if len(word) > 2 and word[0] == "-" and word[1] != "-" and word[:2] in options:
        return word[:2]
    return None


def _flag_values(args: Sequence[str], flags: frozenset) -> List[str]:
    values: List[str] = []
    for index, word in enumerate(args):
        if word == "--":
            break
        name, equals, attached = word.partition("=")
        if name not in flags:
            short = _attached_short(word, flags)
            if short is None:
                continue
            name, equals, attached = short, "=", word[2:]
        value = attached if equals else (args[index + 1] if index + 1 < len(args) else UNDETERMINED)
        if not (name in ("--env", "--environment") and "=" in value):
            values.append(value)
    return values


def _target_flags(program: str) -> frozenset:
    from .mutative_verbs import _is_unmodelled_cli

    if program in _PROGRAM_TARGET_FLAGS:
        return _PROGRAM_TARGET_FLAGS[program]
    return _GENERIC_TARGET_FLAGS if _is_unmodelled_cli(program) else frozenset()


def _naming_variables(program: str) -> frozenset:
    from .mutative_verbs import _is_unmodelled_cli

    if program in _PROGRAM_NAMING_VARIABLES:
        return _PROGRAM_NAMING_VARIABLES[program]
    return _NAMING_VARIABLES if program not in _PROGRAM_TARGET_FLAGS and _is_unmodelled_cli(program) else frozenset()


def _literal(value: str) -> str:
    return UNDETERMINED if not value or _DYNAMIC.search(value) else value


def _named(value: str) -> str:
    """*value* when literal, "" when nothing names a target, UNDETERMINED when the shell computes it."""
    return value and _literal(value)


def _read_text(path: Optional[Path]) -> Optional[str]:
    """The file's text, "" when it does not exist, None when it exists and cannot be read or *path* is unknown."""
    try:
        if path is None:
            return None
        if not path.exists() and not path.is_symlink():
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _json_value(text: str, kind: type, key: str):
    """``key`` of the JSON object *text*, which must be of *kind* or absent; UNDETERMINED for anything else."""
    try:
        document = json.loads(text.lstrip("﻿"))
    except (ValueError, RecursionError):
        return UNDETERMINED
    if not isinstance(document, dict):
        return UNDETERMINED
    value = document.get(key)
    return value if value is None or isinstance(value, kind) else UNDETERMINED


def _in_directory(path: str, cwd: Optional[str]) -> Optional[Path]:
    expanded = Path(path).expanduser()
    return expanded if expanded.is_absolute() else (Path(cwd) / expanded if cwd else None)


def _config_root(environment: Dict[str, str], variable: str, default: Tuple[str, ...], cwd: Optional[str]) -> Optional[Path]:
    """The directory *variable* names, read against the command's *cwd*; the home default when unset; None when unknown."""
    value = environment.get(variable)
    if not value:
        return Path.home().joinpath(*default)
    return None if _DYNAMIC.search(value) else _in_directory(value, cwd)


def _kube_context(environment: Dict[str, str], cwd: Optional[str]) -> str:
    paths = environment.get("KUBECONFIG")
    if paths and _DYNAMIC.search(paths):
        return UNDETERMINED
    files = [_in_directory(p, cwd) for p in paths.split(os.pathsep) if p] if paths else [Path.home() / ".kube" / "config"]
    for path in files:
        text = _read_text(path)
        if text is None:
            return UNDETERMINED
        if not text.lstrip("﻿").lstrip().startswith("{"):
            if text.strip() and not _YAML_KEY.search(text):
                return UNDETERMINED
            match = _CURRENT_CONTEXT.search(text)
            if match and match.group(1):
                return match.group(1)
            continue
        context = _json_value(text, str, "current-context")
        if context:
            return context
    return ""


def _gcloud_properties(environment: Dict[str, str], cwd: Optional[str]) -> Tuple[str, str]:
    root = _config_root(environment, "CLOUDSDK_CONFIG", (".config", "gcloud"), cwd)
    active = environment.get("CLOUDSDK_ACTIVE_CONFIG_NAME") or _read_text(None if root is None else root / "active_config")
    active = None if active is None else active.strip() or "default"
    if root is None or active is None or not _CONFIGURATION_NAME.fullmatch(active):
        return UNDETERMINED, UNDETERMINED
    text = _read_text(root / "configurations" / f"config_{active}")
    if text is None:
        return UNDETERMINED, UNDETERMINED
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return UNDETERMINED, UNDETERMINED
    return parser.get("core", "project", fallback=""), parser.get("core", "account", fallback="")


def _docker_context(environment: Dict[str, str], cwd: Optional[str]) -> str:
    if environment.get("DOCKER_CONTEXT"):
        return _literal(environment["DOCKER_CONTEXT"])
    root = _config_root(environment, "DOCKER_CONFIG", (".docker",), cwd)
    text = _read_text(None if root is None else root / "config.json")
    if not text:
        return "" if text == "" else UNDETERMINED
    return _json_value(text, str, "currentContext") or ""


def _azure_profile(environment: Dict[str, str], cwd: Optional[str]) -> Optional[List[Tuple[str, str, bool]]]:
    """(name, id, is default) of each subscription in azureProfile.json; [] when there is none, None when unreadable."""
    root = _config_root(environment, "AZURE_CONFIG_DIR", (".azure",), cwd)
    text = _read_text(None if root is None else root / "azureProfile.json")
    if text == "":
        return []
    subscriptions = UNDETERMINED if text is None else _json_value(text, list, "subscriptions")
    if not isinstance(subscriptions, list):
        return None
    profile = []
    for entry in subscriptions:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("id"), str):
            return None
        profile.append((entry["name"], entry["id"], bool(entry.get("isDefault"))))
    return profile


def _azure_subscriptions(value: str, profile: Optional[List[Tuple[str, str, bool]]], fetching: bool) -> List[str]:
    value = _literal(value)
    match = next((s for s in profile or () if value.lower() in (s[0].lower(), s[1].lower())), None)
    if match is not None:
        return [match[0], match[1]]
    return [UNDETERMINED if fetching and _SUBSCRIPTION_ID.fullmatch(value) else value]


def _pulumi_stack(args: Sequence[str], environment: Dict[str, str], cwd: Optional[str]) -> str:
    """The stack selected for the nearest Pulumi.yaml, read from the workspace file pulumi names after project and path SHA-1."""
    chosen = _flag_values(args, frozenset({"-C", "--cwd"}))
    start = _in_directory(chosen[-1] if chosen else ".", cwd)
    if start is None or (chosen and _DYNAMIC.search(chosen[-1])):
        return UNDETERMINED
    start = start.absolute()
    project = next((d / "Pulumi.yaml" for d in (start, *start.parents) if (d / "Pulumi.yaml").exists()), None)
    if project is None:
        return ""
    text = _read_text(project)
    name = _PULUMI_NAME.search(text or "")
    if name is None:
        return UNDETERMINED
    home = _config_root(environment, "PULUMI_HOME", (".pulumi",), cwd)
    digest = hashlib.sha1(str(project).encode()).hexdigest()
    settings = _read_text(None if home is None else home / "workspaces" / f"{name.group(1)}-{digest}-workspace.json")
    if not settings:
        return "" if settings == "" else UNDETERMINED
    return _json_value(settings, str, "stack") or ""


def _ambient(
    program: str, args: Sequence[str], environment: Dict[str, str], cwd: Optional[str], fetching: bool,
) -> List[Target]:
    if program in _KUBE_PROGRAMS and not _flag_values(args, _KUBE_CONTEXT_FLAGS):
        for value in _flag_values(args, frozenset({"--kubeconfig"})):
            path = _in_directory(value, cwd)
            if path is None or _DYNAMIC.search(value):
                return [Target("kube-context", UNDETERMINED)]
            environment["KUBECONFIG"] = str(path)
        return [Target("kube-context", _kube_context(environment, cwd))]
    if program == "gcloud":
        for value in _flag_values(args, frozenset({"--configuration"})):
            environment["CLOUDSDK_ACTIVE_CONFIG_NAME"] = value
        project, account = _gcloud_properties(environment, cwd)
        found = []
        if not _flag_values(args, frozenset({"--project"})):
            found.append(Target("project", _named(environment.get("CLOUDSDK_CORE_PROJECT") or project)))
        if not _flag_values(args, frozenset({"--account"})):
            found.append(Target("account", _named(environment.get("CLOUDSDK_CORE_ACCOUNT") or account)))
        return found
    if program in ("docker", "podman") and not _flag_values(args, _PROGRAM_TARGET_FLAGS[program]):
        if program == "podman" and environment.get("CONTAINER_CONNECTION"):
            return [Target("podman-connection", _literal(environment["CONTAINER_CONNECTION"]))]
        return [Target("docker-context", _docker_context(environment, cwd))]
    if program == "az" and not _flag_values(args, _PROGRAM_TARGET_FLAGS["az"]):
        profile = _azure_profile(environment, cwd)
        if profile is None:
            found = [Target("subscription", UNDETERMINED)]
        else:
            found = [Target("subscription", part) for name, sid, default in profile if default for part in (name, sid)]
        if environment.get("AZURE_SUBSCRIPTION_ID"):
            found += [Target("subscription", name)
                      for name in _azure_subscriptions(environment["AZURE_SUBSCRIPTION_ID"], profile, fetching)]
        return found
    if program == "pulumi" and not _flag_values(args, _PROGRAM_TARGET_FLAGS["pulumi"]):
        return [Target("stack", _pulumi_stack(args, environment, cwd))]
    if program == "aws" and not _flag_values(args, frozenset({"--profile"})):
        return [Target("profile", _literal(environment["AWS_PROFILE"]) if environment.get("AWS_PROFILE") else "")]
    if program == "terraform":
        if environment.get("TF_WORKSPACE"):
            return [Target("workspace", _literal(environment["TF_WORKSPACE"]))]
        directory = _in_directory(next((a.partition("=")[2] for a in args if a.startswith("-chdir=")), "."), cwd)
        text = None if directory is None else _read_text(directory / ".terraform" / "environment")
        return [Target("workspace", UNDETERMINED if text is None else text.strip())]
    return []


def _gcloud_configuration_targets(name: str, environment: Dict[str, str], cwd: Optional[str]) -> List[Target]:
    project, account = _gcloud_properties({**environment, "CLOUDSDK_ACTIVE_CONFIG_NAME": name}, cwd)
    return [Target("switch", project), Target("switch", account)]


def _split(command: str) -> Optional[Tuple[List[str], List[str], List[str]]]:
    """The assignments before the program, the program's words, and every word written before them."""
    from ..tools.shell_parser import shell_split
    from .mutative_verbs import _expand_split_strings, _peel_wrapper_readings

    readings, _ = _peel_wrapper_readings(command)
    dropped, expanded, _, _ = _expand_split_strings(command)
    try:
        written, words = shell_split(dropped) + shell_split(expanded), shell_split(readings[0])
    except ValueError:
        return None
    prefix = written[: max(len(written) - len(words), 0)]
    return [w for w in prefix if _ASSIGNMENT.fullmatch(w)], words, prefix


def _wrapper_cwd(prefix: Sequence[str], cwd: Optional[str]) -> Optional[str]:
    """*cwd* after the ``env -C``/``--chdir`` written before the program; None when the shell computes it."""
    under_env = False
    for index, word in enumerate(prefix):
        under_env = under_env or command_name(word) == "env"
        name, equals, value = word.partition("=")
        if under_env and word.startswith("-C") and word != "-C":
            name, equals, value = "-C", "=", word[2:]
        if not under_env or name not in ("-C", "--chdir"):
            continue
        value = value if equals else (prefix[index + 1] if index + 1 < len(prefix) else "")
        located = None if not value or _DYNAMIC.search(value) else _in_directory(value, cwd)
        cwd = str(located) if located else None
    return cwd


def command_targets(
    command: str, *, cwd: Optional[str] = None, inherited: Optional[Dict[str, str]] = None, ambient: bool = True,
) -> List[Target]:
    """Every target *command* lands on: its target flags, the naming variables set before it, a switch's destination,
    then what its configuration picks, read in *cwd* after any leading ``cd`` of the line."""
    from .mutative_verbs import _peel_leading_cd

    cwd, command, _ = _peel_leading_cd(command, cwd)
    split = _split(command)
    if split is None or not split[1]:
        return []
    assignments, words, prefix = split
    cwd = _wrapper_cwd(prefix, cwd)
    program, args = command_name(words[0]), words[1:]
    environment = {**os.environ, **(inherited or {})}
    for word in assignments:
        name, value = _ASSIGNMENT.fullmatch(word).groups()
        environment[name] = value
    operation, operands, _ = _operation(words)
    fetching = operation is not None and _CREDENTIAL_FETCHERS.get(program) == operation
    kind, flagged = "switch" if fetching else "flag", _flag_values(args, _target_flags(program))
    if program == "az" and flagged:
        profile = _azure_profile(environment, cwd)
        found = [Target(kind, name) for value in flagged for name in _azure_subscriptions(value, profile, fetching)]
    else:
        found = [Target(kind, _literal(value)) for value in flagged]
    found += [Target("variable", _literal(environment[name])) for name in sorted(_naming_variables(program))
              if name in environment and (name in (inherited or {}) or any(w.startswith(f"{name}=") for w in assignments))]
    if operation in _SWITCH_WORDS.get(program, ()):
        destinations = _flag_values(args, _SWITCH_DESTINATION_FLAGS.get(program, frozenset()))[-1:]
        if not destinations and operands:
            last, known, _ = _positionals(words)[-1]
            known = known or "--" in words[1:-1]
            destinations = [last if known and last != "-" else UNDETERMINED]
        for destination in map(_literal, destinations):
            found.append(Target("switch", destination))
            if program == "gcloud" and operation == "activate" and destination != UNDETERMINED:
                found += _gcloud_configuration_targets(destination, environment, cwd)
        if program == "kubens" and destinations:
            found.append(Target("switch", _kube_context(environment, cwd)))
    if ambient:
        found += _ambient(program, args, environment, cwd, fetching)
    return [target for target in found if target.name]


def _positionals(words: Sequence[str]) -> List[Tuple[str, bool, bool]]:
    """Each positional of *words*, whether every option before it is known, and whether it is in the leading run."""
    program = command_name(words[0])
    arity = {**dict.fromkeys(_target_flags(program), 1), **_OPTION_ARITY.get(program, {})}
    found: List[Tuple[str, bool, bool]] = []
    known, ended, leading, index = True, False, True, 1
    while index < len(words):
        word, index = words[index], index + 1
        if ended or word == "-" or not word.startswith("-"):
            found.append((word, known, leading))
        elif word == "--":
            ended = True
        else:
            leading = leading and not found
            short = _attached_short(word, arity)
            name, equals, _ = (short, "=", "") if short and arity[short] == 1 else word.partition("=")
            known = known and name in arity
            index += 0 if equals else arity.get(name, 0)
    return found


def _is_verb_word(word: str, program: str) -> bool:
    from .mutative_verbs import MUTATIVE_VERBS, READ_ONLY_VERBS, SIMULATION_VERBS

    verbs = MUTATIVE_VERBS | READ_ONLY_VERBS | SIMULATION_VERBS
    return word in verbs | _EXTRA_READ_WORDS | _SWITCH_WORDS.get(program, frozenset()) or word.split("-")[0] in verbs


def _operation(words: Sequence[str]) -> Tuple[Optional[str], List[str], bool]:
    """The word naming what *words* does, the operands after it, and whether every option before it is known.

    aws: second word; kubectx/kubens: the program; gcloud/az: ``_path_operation``; others: first non-group positional.
    """
    if not words:
        return None, [], False
    program, positionals = command_name(words[0]), [(w.lower(), known, lead) for w, known, lead in _positionals(words)]
    groups = _GROUP_WORDS.get(program, frozenset())
    operands = [w for w, _, _ in positionals]
    if program in _SWITCHER_PROGRAMS:
        return program, operands, True
    if program == "aws":
        return (operands[1], operands[2:], positionals[1][1]) if len(positionals) > 1 else (None, [], False)
    if program in _VERB_PATH_PROGRAMS:
        index = _path_operation([w for w, _, lead in positionals if lead], program)
        return (operands[index], operands[index + 1:], positionals[index][1]) if index is not None else (None, [], False)
    for index, (word, known, _) in enumerate(positionals):
        if word not in groups:
            return word, operands[index + 1:], known
    return None, [], False


def _path_operation(run: Sequence[str], program: str) -> Optional[int]:
    """Index in the leading *run* of its verb: the last word, or the verb before it when the last is an operand."""
    if not run:
        return None
    last = len(run) - 1
    if last and not _is_verb_word(run[last], program) and _is_verb_word(run[last - 1], program):
        return last - 1
    return last


def _is_read(operation: Optional[str]) -> bool:
    from .mutative_verbs import MUTATIVE_VERBS, READ_ONLY_VERBS, SIMULATION_VERBS

    if operation is None or set(operation.split("-")) & MUTATIVE_VERBS:
        return False
    reads = READ_ONLY_VERBS | SIMULATION_VERBS
    return operation in reads | _EXTRA_READ_WORDS or operation.split("-")[0] in reads


def naming_variables_on_line(line: str) -> Dict[str, str]:
    """The naming variables (``DOCKER_CONTEXT``, ``CLOUDSDK_CORE_PROJECT``...) a literal assignment on *line* sets, with their values."""
    from ..tools.shell_parser import parse_command, shell_split

    assigned: Dict[str, str] = {}
    try:
        components = parse_command(line)
    except Exception:
        return assigned
    for component in components:
        try:
            words = shell_split(component)
        except ValueError:
            continue
        for word in words:
            match = _ASSIGNMENT.fullmatch(word)
            if match and match.group(1) in _NAMING_VARIABLES:
                assigned[match.group(1)] = match.group(2)
    return assigned


def is_production(name: str, declaration: Dict) -> bool:
    """A declared name decides; otherwise a name is production when one of its words is prod, production or prd."""
    lowered = name.lower()
    if lowered in {str(n).lower() for n in declaration.get("development", ())}:
        return False
    if lowered in {str(n).lower() for n in declaration.get("production", ())}:
        return True
    return bool(_PRODUCTION_WORDS & set(_NAME_PARTS.findall(lowered)))


def declaration_for(cwd: Optional[str]) -> Dict:
    """The ``targets`` project-context section of the active project holding *cwd*; {} when there is none, no *cwd*,
    or it cannot be read."""
    if not cwd:
        return {}
    try:
        from gaia.store.project_context import project_sections
        from gaia.store.reader import _ro_connect

        here = Path(cwd).resolve()
        con = _ro_connect()
    except Exception:
        return {}
    try:
        rows = con.execute(
            "SELECT workspace, name, path, project_identity FROM projects WHERE status = 'active' AND path IS NOT NULL",
        ).fetchall()
        stored = [(Path(r[2]).resolve(), r) for r in rows]
        holding = [(root, r) for root, r in stored if here == root or root in here.parents]
        if not holding:
            return {}
        workspace, name, path, identity = max(holding, key=lambda pair: len(pair[0].parts))[1]
        section = project_sections(con, {"identity": identity or path, "workspace": workspace, "name": name})
        payload = section.get(DECLARATION_SECTION, {}).get("payload")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    finally:
        con.close()


def grant_target(command: str, *, cwd: Optional[str] = None) -> str:
    """The resolved targets a grant for *command* binds to, as one string; UNDETERMINED when any of them cannot be read."""
    try:
        targets = command_targets(command, cwd=cwd)
    except Exception:
        return UNDETERMINED
    if any(t.name == UNDETERMINED for t in targets):
        return UNDETERMINED
    return ";".join(sorted(f"{t.kind}={t.name}" for t in targets))


def _pushed_branch(words: Sequence[str], cwd: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The branch a ``git push`` names and the repository ``-C``/``--git-dir``/``--work-tree`` make it act on."""
    repo, tree, args, index = cwd, None, list(words[1:]), 0
    while index < len(args) and args[index].startswith("-"):
        name, equals, value = args[index].partition("=")
        valued = name in ("-C", "--git-dir", "--work-tree", "-c")
        if valued and not equals:
            value, index = (args[index + 1] if index + 1 < len(args) else ""), index + 1
        index += 1
        if name == "-C":
            repo = _in_directory(value, repo) if value else None
            repo = str(repo) if repo else None
        elif name in ("--git-dir", "--work-tree"):
            located = _in_directory(value, repo) if value else None
            tree = str(located) if located else None
            if tree is None:
                return None, None
    if index >= len(args) or args[index] != "push":
        return None, None
    operands = [w for w in args[index + 1:] if not w.startswith("-")]
    branch = _branch_name(operands[-1]) if len(operands) == 2 else None
    return branch, tree or repo


def _branch_name(ref: str) -> str:
    """The branch a push refspec or a declared name lands on: the destination of ``src:dst``, without ``+`` or ``refs/heads/``."""
    ref = ref.removeprefix("+").rpartition(":")[2]
    for prefix in ("refs/heads/", "heads/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def production_target_verdict(
    command: str, detected, *, cwd: Optional[str] = None, inherited: Optional[Dict[str, str]] = None,
):
    """Escalate a verdict that does not sign when *command* lands on a production target or a declared protected branch.

    The operation word decides a read (``_operation``), never an operand; an executor keeps the verdict of what it runs.
    A target that is not literal keeps the verdict (D178 pins ``docker --context $CTX ps`` free); only a grant refuses it.
    A failure while reading targets is a target that could not be determined, so it asks rather than reach the validator.
    """
    from .mutative_verbs import CATEGORY_MUTATIVE, MutativeResult

    if detected.is_mutative:
        return None
    try:
        landing = _landing(command, detected, cwd, inherited)
    except Exception:
        landing = ("a target that could not be determined", [])
    if landing is None:
        return None
    place, words = landing
    return MutativeResult(
        is_mutative=True, category=CATEGORY_MUTATIVE, verb=detected.verb or (command_name(words[0]) if words else ""),
        confidence="high", reason=f"{shlex.join(words) if words else command!r} lands on {place}", rule="sign.lands",
    )


def _performs_no_operation(words: Sequence[str], operation: Optional[str]) -> bool:
    """No operation word, a ``help`` first word, or ``--help`` on a modelled CLI: it prints and touches no target."""
    program = command_name(words[0])
    first = next((w.lower() for w, _, _ in _positionals(words)), None)
    if program not in _SWITCHER_PROGRAMS and (operation is None or first == "help"):
        return True
    shown = words[1:words.index("--")] if "--" in words else words[1:]
    return program in _HELP_PROGRAMS and any(w in ("--help", "-help") for w in shown)


def _landing(
    command: str, detected, cwd: Optional[str], inherited: Optional[Dict[str, str]],
) -> Optional[Tuple[str, List[str]]]:
    """What production target or protected branch *command* lands on, with the words that land there; None when none."""
    from .mutative_verbs import _READING_CATEGORIES
    from .nested_executors import nested_run

    split = _split(command)
    words = split[1] if split else []
    branch, repo = _pushed_branch(words, cwd) if words and command_name(words[0]) == "git" else (None, None)
    if detected.category in _READING_CATEGORIES and words and nested_run(words) is not None:
        return None
    operation, _, known = _operation(words)
    if not branch and words and _performs_no_operation(words, operation):
        return None
    switching = known and operation in _SWITCH_WORDS.get(command_name(words[0]) if words else "", ())
    if not branch and known and not switching and _is_read(operation):
        return None
    targets = [] if branch else command_targets(command, cwd=cwd, inherited=inherited)
    if not branch and not targets:
        return None
    declaration = declaration_for(repo if branch else cwd)
    landing = None
    if branch and branch in {_branch_name(str(b)) for b in declaration.get("protected_branches", ())}:
        landing = f"the declared protected branch {branch!r}"
    if switching and _CREDENTIAL_FETCHERS.get(command_name(words[0])) != operation:
        targets = [t for t in targets if t.kind == "switch"]
    named = next((t for t in targets if t.name != UNDETERMINED and is_production(t.name, declaration)), None)
    if named is not None:
        landing = f"the production {named.kind} {named.name!r}"
    elif switching and any(t.name == UNDETERMINED for t in targets):
        landing = "a target that could not be determined"
    return (landing, list(words)) if landing else None
