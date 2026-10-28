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
import stat
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .canonical_path import command_name

UNDETERMINED = "?"
UNREADABLE = "??"
UNSET = "\x00"
DECLARATION_SECTION = "targets"

# Only a program no classifier table names (mutative_verbs._is_unmodelled_cli) is read with the generic row.
_GENERIC_TARGET_FLAGS = frozenset({
    "--context", "--kube-context", "--cluster", "--project", "--account", "--profile",
    "--workspace", "--stack", "--env", "--environment", "--subscription",
})
_KUBE_TARGET_FLAGS = frozenset({"--context", "--cluster"})
_PROGRAM_TARGET_FLAGS: Dict[str, frozenset] = {
    "kubectl": _KUBE_TARGET_FLAGS, "oc": _KUBE_TARGET_FLAGS, "k9s": _KUBE_TARGET_FLAGS,
    "helm": frozenset({"--kube-context", "--kube-apiserver", "-n", "--namespace"}), "flux": frozenset({"--context"}),
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
_PROGRAM_ENV_PREFIXES: Dict[str, Tuple[str, ...]] = {
    **dict.fromkeys(_KUBE_PROGRAMS | {"kubectx", "kubens"}, ("KUBE",)), "helm": ("HELM_", "KUBECONFIG"),
    "terraform": ("TF_",), "pulumi": ("PULUMI_",), "gcloud": ("CLOUDSDK_",), "az": ("AZURE_",), "aws": ("AWS_",),
    "docker": ("DOCKER_",), "podman": ("CONTAINER_", "CONTAINERS_"),
}
_TARGET_PREFIXES = tuple(sorted(set().union(*_PROGRAM_ENV_PREFIXES.values())))
_HELM_SELECTORS = {
    "HELM_KUBECONTEXT": ("kube-context", frozenset({"--kube-context"})),
    "HELM_KUBEAPISERVER": ("kube-apiserver", frozenset({"--kube-apiserver"})),
    "HELM_NAMESPACE": ("namespace", frozenset({"-n", "--namespace"})),
}
_AWS_SELECTORS = {
    "AWS_PROFILE": ("profile", "--profile"), "AWS_DEFAULT_PROFILE": ("profile", "--profile"),
    "AWS_REGION": ("region", "--region"), "AWS_DEFAULT_REGION": ("region", "--region"),
    "AWS_CONFIG_FILE": ("aws-config", None), "AWS_SHARED_CREDENTIALS_FILE": ("aws-credentials", None),
}
_PROGRAM_SELECTORS: Dict[str, frozenset] = {
    **dict.fromkeys(_KUBE_PROGRAMS | {"kubectx", "kubens"}, frozenset({"KUBECONFIG"})),
    "helm": frozenset({"KUBECONFIG", *_HELM_SELECTORS}), "terraform": frozenset({"TF_WORKSPACE", "TF_DATA_DIR"}),
    "pulumi": frozenset({"PULUMI_BACKEND_URL", "PULUMI_HOME"}), "aws": frozenset(_AWS_SELECTORS),
    "gcloud": frozenset({"CLOUDSDK_ACTIVE_CONFIG_NAME", "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "CLOUDSDK_CONFIG"}),
    "az": frozenset({"AZURE_CONFIG_DIR", "AZURE_SUBSCRIPTION_ID"}),
    "docker": frozenset({"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"}),
    "podman": frozenset({"CONTAINER_HOST", "CONTAINER_CONNECTION"}),
}
_HARMLESS_VARIABLES = frozenset({
    "HELM_DEBUG", "HELM_CACHE_HOME", "HELM_NO_PLUGINS", "TF_LOG", "TF_LOG_CORE", "TF_LOG_PROVIDER",
    "TF_LOG_PATH", "TF_IN_AUTOMATION", "TF_INPUT", "TF_PLUGIN_CACHE_DIR", "PULUMI_SKIP_UPDATE_CHECK",
    "CLOUDSDK_CORE_VERBOSITY", "CLOUDSDK_CORE_DISABLE_PROMPTS", "CLOUDSDK_CORE_DISABLE_USAGE_REPORTING",
    "AZURE_CORE_OUTPUT", "AZURE_CORE_ONLY_SHOW_ERRORS", "AZURE_CORE_COLLECT_TELEMETRY", "AZURE_CORE_NO_COLOR",
    "AWS_PAGER", "AWS_DEFAULT_OUTPUT", "DOCKER_CLI_HINTS", "DOCKER_BUILDKIT",
})
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
# The hook runtime ships no YAML parser (pyyaml is a dev extra): a YAML file is read only when every line is in the
# block subset kubectl and pulumi write, including a plain value folded onto deeper lines (kubectl wraps installHint).
_YAML_FORBIDDEN = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
_YAML_LINE = re.compile(r"(?P<item>-(?:[ \t]+|$))?(?:(?P<key>[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)*):(?:[ \t]+|$))?(?P<value>.*)")
_YAML_ROOT_KEY = re.compile(r"[A-Za-z0-9_.-]+")
_YAML_QUOTED = re.compile(r"""(?:"(?P<double>[^"\\]*)"|'(?P<single>[^']*)'|\{\}|\[\])(?:[ \t]+#.*)?""")
_YAML_PLAIN = re.compile(r"""(?![-?:](?:[ \t]|$))[^\s#&*!|>'"%@`{}\[\],](?:(?!:[ \t]).)*(?<!:)""")
_YAML_COMMENT = re.compile(r"[ \t]+#")
_YAML_TYPED = re.compile(r"(?i:null|true|false|yes|no|on|off|y|n|\.inf|\.nan)|\.?\d[\d_.eE+:-]*|0[xXoO][\w]+")
_READ_LIMIT = 1024 * 1024
_PULUMI_PROJECT_FILES = ("Pulumi.yaml", "Pulumi.yml")
_CONFIGURATION_NAME = re.compile(r"[A-Za-z0-9][\w.-]*")
_SUBSCRIPTION_ID = re.compile(r"(?=[^0-9]*[0-9])[0-9A-Fa-f-]+")
_HELP_PROGRAMS = frozenset(_OPTION_ARITY) | frozenset(_PROGRAM_TARGET_FLAGS) | _KUBE_PROGRAMS | _SWITCHER_PROGRAMS
_SELECTOR_VARIABLES = tuple(sorted(_NAMING_VARIABLES | {
    "HOME", "KUBECONFIG", "CLOUDSDK_CONFIG", "CLOUDSDK_ACTIVE_CONFIG_NAME", "DOCKER_CONFIG", "DOCKER_CONTEXT",
    "CONTAINER_CONNECTION", "AZURE_CONFIG_DIR", "AZURE_SUBSCRIPTION_ID", "PULUMI_HOME", "DOCKER_HOST", "CONTAINER_HOST",
}))
_ENGINE_HOST_VARIABLES = {"docker": "DOCKER_HOST", "podman": "CONTAINER_HOST"}
_TREE_LIMIT = 512
_ENV_SIGNAL_OPTIONS = frozenset({"--ignore-signal", "--default-signal", "--block-signal", "--list-signal-handling"})
_ENV_RESETTING_WRAPPERS = frozenset({"sudo", "doas", "su", "runuser", "pkexec"})
_CONFIG_READS: ContextVar[Optional[list]] = ContextVar("target_config_reads", default=None)


@dataclass(frozen=True)
class Target:
    """One place a command lands: *kind* says where the name came from, *name* is UNDETERMINED when nothing literal names
    it and UNREADABLE when the configuration file that names it cannot be read with certainty."""

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


def _read_text(path: Path) -> Optional[str]:
    """The text of the regular file *path* resolves to, "" when nothing is there, None when it is anything else (FIFO,
    device, directory, over _READ_LIMIT bytes, not UTF-8); recorded with its content hash while grant_target listens."""
    real, text, data = _read_file(path)
    reads = _CONFIG_READS.get()
    if reads is not None:
        digest = None if text is None else "absent" if data is None else hashlib.sha256(data).hexdigest()
        reads.append([str(path), real, digest])
    return text


def _read_file(path: Path) -> Tuple[Optional[str], Optional[str], Optional[bytes]]:
    """(resolved path, text as _read_text returns it, bytes read): checked before opening, opened without blocking,
    read capped."""
    real = None
    try:
        real = os.path.realpath(path)
        if not stat.S_ISREG(os.stat(real).st_mode):
            return real, None, None
        descriptor = os.open(real, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except (FileNotFoundError, NotADirectoryError):
        return real, None if os.path.lexists(path) else "", None
    except (OSError, ValueError):
        return real, None, None
    try:
        status, data = os.fstat(descriptor), b""
        if not stat.S_ISREG(status.st_mode) or status.st_size > _READ_LIMIT:
            return real, None, None
        while len(data) <= _READ_LIMIT and (chunk := os.read(descriptor, _READ_LIMIT + 1 - len(data))):
            data += chunk
        return (real, None, None) if len(data) > _READ_LIMIT else (real, data.decode("utf-8"), data)
    except (OSError, ValueError):
        return real, None, None
    finally:
        os.close(descriptor)


def _unique_keys(pairs):
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


class _JsonObject(list):
    """The (key, value) pairs of one JSON object in document order, duplicates kept."""


def _go_fold(name: str) -> str:
    """*name* folded as Go's encoding/json folds a key before matching it to a field."""
    folded = (c.upper() if c.isascii() else c.lower().upper() for c in name)
    return "".join(f if len(f) == 1 else c for c, f in zip(name, folded))


def _json_value(text: str, kind: type, key: str, *, fold: bool = False):
    """``key`` of the JSON object *text*: None when absent, "" when null, UNREADABLE when not of *kind* or not an object.
    *fold* matches keys as Go's encoding/json does, case-insensitively with the last non-null one winning; otherwise a
    duplicated key is UNREADABLE."""
    try:
        document = json.loads(text.lstrip("﻿"), object_pairs_hook=_JsonObject if fold else _unique_keys)
    except (ValueError, RecursionError):
        return UNREADABLE
    if not isinstance(document, _JsonObject if fold else dict):
        return UNREADABLE
    if fold:
        values = [value for name, value in document if _go_fold(name) == _go_fold(key)]
    else:
        values = [document[key]] if key in document else []
    if not values:
        return None
    present = [value for value in values if value is not None]
    if any(not isinstance(value, kind) for value in present):
        return UNREADABLE
    return present[-1] if present else ""


def _certified(value, text: str, key: str):
    """*value* when the strict reader found *key* set; otherwise UNSET (the file names no target), or UNREADABLE when
    *text* still mentions *key* loosely: case-insensitive, every Unicode line break read as one, leading whitespace
    ignored. A value spelling UNSET itself is UNREADABLE."""
    if value is not None:
        return UNREADABLE if value == UNSET else value or UNSET
    loose = "\n".join(line.lstrip() for line in text.splitlines()).casefold()
    return UNREADABLE if key.casefold() in loose else UNSET


def _yaml_scalar(value: str) -> Optional[Tuple[str, bool]]:
    """The string one-line YAML *value* holds and whether it is plain; ("", False) for nothing, a comment or an empty
    collection; None when it is outside the subset: escapes, anchors, aliases, tags, block scalars, flow content."""
    quoted = _YAML_QUOTED.fullmatch(value)
    if quoted:
        return next((part for part in quoted.group("double", "single") if part is not None), ""), False
    plain = "" if value.startswith("#") else _YAML_COMMENT.split(value, maxsplit=1)[0].rstrip()
    if not plain:
        return "", False
    return (plain, True) if _YAML_PLAIN.fullmatch(plain) else None


def _yaml_value(text: str, key: str) -> Optional[str]:
    """Root *key* of YAML *text*, None when absent; UNREADABLE unless every line is in the block subset (root mapping at
    column 0, plain keys, ``- `` items, one-line values per ``_yaml_scalar``, deeper lines folding a plain value) and
    *key* holds one plain or unescaped quoted string on its own line."""
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    if _YAML_FORBIDDEN.search(text):
        return UNREADABLE
    found, folding, owner, seen = set(), None, None, False
    for line in text.split("\n"):
        body = line.lstrip(" ")
        indent = len(line) - len(body)
        if not body.strip() or body.startswith("#"):
            folding = folding if not body.strip() else None
            continue
        parts = _YAML_LINE.fullmatch(body)
        at_root = indent == 0 and not parts.group("item")
        if body[0] == "\t" or not (seen or (at_root and parts.group("key"))):
            return UNREADABLE
        seen = True
        if not parts.group("item") and not parts.group("key"):
            continued = _YAML_COMMENT.split(body, maxsplit=1)[0].rstrip()
            if folding is None or indent <= folding or not _YAML_PLAIN.fullmatch(continued) or owner == key:
                return UNREADABLE
            continue
        scalar = _yaml_scalar(parts.group("value"))
        if scalar is None:
            return UNREADABLE
        if at_root and not _YAML_ROOT_KEY.fullmatch(parts.group("key")):
            return UNREADABLE
        if owner == key and not at_root:
            return UNREADABLE
        owner = parts.group("key") if at_root else (owner if indent else None)
        folding = indent if scalar[1] else None
        if at_root and owner == key:
            if scalar[1] and _YAML_TYPED.fullmatch(scalar[0]):
                return UNREADABLE
            found.add(scalar[0])
    return UNREADABLE if len(found) > 1 else next(iter(found), None)


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
    unset = ""
    for path in files:
        if path is None:
            return UNDETERMINED
        text = _read_text(path)
        if text is None:
            return UNREADABLE
        if not text:
            continue
        if text.lstrip("﻿").lstrip().startswith("{"):
            context = _json_value(text, str, "current-context")
        else:
            context = _yaml_value(text, "current-context")
        context = _certified(context, text, "current-context")
        if context != UNSET:
            return context
        unset = UNSET
    return unset


def _gcloud_properties(environment: Dict[str, str], cwd: Optional[str]) -> Tuple[str, str]:
    root = _config_root(environment, "CLOUDSDK_CONFIG", (".config", "gcloud"), cwd)
    if root is None:
        return UNDETERMINED, UNDETERMINED
    named = environment.get("CLOUDSDK_ACTIVE_CONFIG_NAME")
    active = named or _read_text(root / "active_config")
    active = None if active is None else active.strip() or "default"
    if active is None or not _CONFIGURATION_NAME.fullmatch(active):
        return (UNDETERMINED, UNDETERMINED) if named else (UNREADABLE, UNREADABLE)
    text = _read_text(root / "configurations" / f"config_{active}")
    if text is None:
        return UNREADABLE, UNREADABLE
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return UNREADABLE, UNREADABLE
    return parser.get("core", "project", fallback=""), parser.get("core", "account", fallback="")


def _docker_context(environment: Dict[str, str], cwd: Optional[str]) -> str:
    if environment.get("DOCKER_CONTEXT"):
        return _literal(environment["DOCKER_CONTEXT"])
    root = _config_root(environment, "DOCKER_CONFIG", (".docker",), cwd)
    if root is None:
        return UNDETERMINED
    text = _read_text(root / "config.json")
    if not text:
        return "" if text == "" else UNREADABLE
    return _certified(_json_value(text, str, "currentContext", fold=True), text, "currentContext")


def _azure_profile(environment: Dict[str, str], cwd: Optional[str]):
    """(name, id, is default) of each subscription in azureProfile.json; [] when there is none, UNDETERMINED when its
    directory is computed, UNREADABLE when the file cannot be read."""
    root = _config_root(environment, "AZURE_CONFIG_DIR", (".azure",), cwd)
    if root is None:
        return UNDETERMINED
    text = _read_text(root / "azureProfile.json")
    if text == "":
        return []
    subscriptions = UNREADABLE if text is None else _json_value(text, list, "subscriptions")
    if not isinstance(subscriptions, list):
        return UNREADABLE
    profile = []
    for entry in subscriptions:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("id"), str):
            return UNREADABLE
        profile.append((entry["name"], entry["id"], bool(entry.get("isDefault"))))
    return profile


def _azure_subscriptions(value: str, profile, fetching: bool) -> List[str]:
    value = _literal(value)
    match = next((s for s in (profile if isinstance(profile, list) else ()) if value.lower() in (s[0].lower(), s[1].lower())), None)
    if match is not None:
        return [match[0], match[1]]
    return [UNDETERMINED if fetching and _SUBSCRIPTION_ID.fullmatch(value) else value]


def _pulumi_projects(args: Sequence[str], cwd: Optional[str]) -> Optional[List[Path]]:
    """The pulumi project files of the nearest directory holding one; None when the shell computes the directory."""
    chosen = _flag_values(args, frozenset({"-C", "--cwd"}))
    start = _in_directory(chosen[-1] if chosen else ".", cwd)
    if start is None or (chosen and _DYNAMIC.search(chosen[-1])):
        return None
    start = start.absolute()
    for directory in (start, *start.parents):
        projects = [directory / name for name in _PULUMI_PROJECT_FILES if (directory / name).exists()]
        if projects:
            return projects
    return []


def _pulumi_stack(projects: List[Path], environment: Dict[str, str], cwd: Optional[str]) -> str:
    """The stack selected for *projects*; UNREADABLE when there are two and their stacks differ."""
    stacks = {_pulumi_project_stack(project, environment, cwd) for project in projects}
    return "" if not stacks else stacks.pop() if len(stacks) == 1 else UNREADABLE


def _pulumi_backend(projects: List[Path], environment: Dict[str, str], cwd: Optional[str]) -> str:
    """The backend PULUMI_BACKEND_URL, a project's ``backend`` or the credentials file's ``current`` names, in pulumi's order."""
    if environment.get("PULUMI_BACKEND_URL"):
        return _literal(environment["PULUMI_BACKEND_URL"])
    for project in projects:
        text = _read_text(project)
        if text is None or _yaml_value(text, "backend") is not None:
            return UNREADABLE
    home = _config_root(environment, "PULUMI_HOME", (".pulumi",), cwd)
    if home is None:
        return UNDETERMINED
    text = _read_text(home / "credentials.json")
    if not text:
        return "" if text == "" else UNREADABLE
    return _certified(_json_value(text, str, "current", fold=True), text, "current")


def _pulumi_project_stack(project: Path, environment: Dict[str, str], cwd: Optional[str]) -> str:
    """The stack selected for *project*, read from the workspace file pulumi names after project and path SHA-1."""
    text = _read_text(project)
    name = UNREADABLE if text is None else _yaml_value(text, "name")
    if name in (None, "", UNREADABLE):
        return UNREADABLE
    home = _config_root(environment, "PULUMI_HOME", (".pulumi",), cwd)
    if home is None:
        return UNDETERMINED
    settings = _read_text(home / "workspaces" / f"{name}-{hashlib.sha1(str(project).encode()).hexdigest()}-workspace.json")
    if not settings:
        return "" if settings == "" else UNREADABLE
    return _certified(_json_value(settings, str, "stack", fold=True), settings, "stack")


def _leading_option_values(program: str, args: Sequence[str], option: str) -> List[str]:
    """Values of *option* among the options written before *program*'s first positional, where its global options go."""
    arity, values, index = _OPTION_ARITY.get(program, {}), [], 0
    while index < len(args) and args[index].startswith("-") and args[index] not in ("-", "--"):
        name, equals, value = args[index].partition("=")
        if name == option:
            values.append(value if equals else (args[index + 1] if index + 1 < len(args) else ""))
        index += 1 + (0 if equals else arity.get(name, 0))
    return values


def _ambient(
    program: str, args: Sequence[str], environment: Dict[str, str], cwd: Optional[str], fetching: bool,
) -> List[Target]:
    if program in _KUBE_PROGRAMS:
        found = [Target(kind, _literal(environment[name])) for name, (kind, flags) in _HELM_SELECTORS.items()
                 if program == "helm" and environment.get(name) and not _flag_values(args, flags)]
        if _flag_values(args, _KUBE_CONTEXT_FLAGS) or any(t.kind == "kube-context" for t in found):
            return found
        for value in _flag_values(args, frozenset({"--kubeconfig"})):
            path = _in_directory(value, cwd)
            if path is None or _DYNAMIC.search(value):
                return [Target("kube-context", UNDETERMINED)]
            environment["KUBECONFIG"] = str(path)
        return found + [Target("kube-context", _kube_context(environment, cwd))]
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
        host = environment.get(_ENGINE_HOST_VARIABLES[program])
        found = [Target(f"{program}-host", _literal(host))] if host else []
        if program == "podman" and environment.get("CONTAINER_CONNECTION"):
            return found + [Target("podman-connection", _literal(environment["CONTAINER_CONNECTION"]))]
        for value in _leading_option_values(program, args, "--config") if program == "docker" else ():
            environment["DOCKER_CONFIG"] = value
        return found + [Target("docker-context", _docker_context(environment, cwd))]
    if program == "az" and not _flag_values(args, _PROGRAM_TARGET_FLAGS["az"]):
        profile = _azure_profile(environment, cwd)
        if isinstance(profile, str):
            found = [Target("subscription", profile)]
        else:
            found = [Target("subscription", part) for name, sid, default in profile if default for part in (name, sid)]
        if environment.get("AZURE_SUBSCRIPTION_ID"):
            found += [Target("subscription", name)
                      for name in _azure_subscriptions(environment["AZURE_SUBSCRIPTION_ID"], profile, fetching)]
        return found
    if program == "pulumi":
        projects = _pulumi_projects(args, cwd)
        if projects is None:
            return [Target("stack", UNDETERMINED)]
        found = [] if _flag_values(args, _PROGRAM_TARGET_FLAGS["pulumi"]) else [
            Target("stack", _pulumi_stack(projects, environment, cwd))]
        return found + [Target("backend", _pulumi_backend(projects, environment, cwd))]
    if program == "aws":
        return [Target(kind, _literal(environment[name])) for name, (kind, flag) in _AWS_SELECTORS.items()
                if environment.get(name) and not (flag and _flag_values(args, frozenset({flag})))]
    if program == "terraform":
        if environment.get("TF_WORKSPACE"):
            return [Target("workspace", _literal(environment["TF_WORKSPACE"]))]
        located = _terraform_environment(args, environment, cwd)
        text = None if located is None else _read_text(located)
        return [Target("workspace", UNDETERMINED if located is None else UNREADABLE if text is None else text.strip())]
    return []


def _terraform_environment(args: Sequence[str], environment: Dict[str, str], cwd: Optional[str]) -> Optional[Path]:
    directory = _in_directory(next((a.partition("=")[2] for a in args if a.startswith("-chdir=")), "."), cwd)
    data = environment.get("TF_DATA_DIR") or ".terraform"
    if directory is None or _DYNAMIC.search(data):
        return None
    return _in_directory(data, str(directory)) / "environment"


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


def _env_options(words: Sequence[str], environment: Dict[str, str]) -> Optional[int]:
    """Apply the options ``env`` reads from *words* to *environment*; how many words they take, None for one not modelled."""
    index = 0
    while index < len(words) and words[index].startswith("-"):
        word, index = words[index], index + 1
        name, equals, value = word.partition("=")
        if word == "--":
            return index
        if word in ("-", "-i", "--ignore-environment"):
            environment.clear()
        elif word in ("-0", "--null", "-v", "--debug") or name in _ENV_SIGNAL_OPTIONS:
            continue
        elif name in ("--unset", "--chdir", "--split-string"):
            value = value if equals else (words[index] if index < len(words) else None)
            index += 0 if equals else 1
            if value is None or not _env_valued(name[2], value, environment):
                return None
        elif word[1] != "-":
            flags = word[1:]
            for position, flag in enumerate(flags):
                if flag in "uCS":
                    value = flags[position + 1:] or (words[index] if index < len(words) else None)
                    index += 0 if flags[position + 1:] else 1
                    if value is None or not _env_valued(flag.lower(), value, environment):
                        return None
                    break
                if flag == "i":
                    environment.clear()
                elif flag not in "0v":
                    return None
        else:
            return None
    return index


def _env_valued(flag: str, value: str, environment: Dict[str, str]) -> bool:
    from ..tools.shell_parser import shell_split

    if flag == "u":
        environment.pop(value, None)
        return not _DYNAMIC.search(value)
    if flag == "s":
        try:
            split = shell_split(value)
        except ValueError:
            return False
        return _env_options(split, environment) is not None
    return True


def _edited_environment(prefix: Sequence[str], environment: Dict[str, str]) -> Optional[Dict[str, str]]:
    """*environment* after the assignments and ``env`` options written before the program, in order; None when a word
    there edits it in a way not modelled."""
    environment, index = dict(environment), 0
    while index < len(prefix):
        word, index = prefix[index], index + 1
        assignment = _ASSIGNMENT.fullmatch(word)
        if assignment:
            environment[assignment.group(1)] = assignment.group(2)
        elif command_name(word) == "env":
            taken = _env_options(prefix[index:], environment)
            if taken is None:
                return None
            index += taken
        elif command_name(word) in _ENV_RESETTING_WRAPPERS or (command_name(word) == "exec" and "-c" in prefix[index:]):
            return None
    return environment


def _command_context(command: str, cwd: Optional[str], inherited: Optional[Dict[str, str]]):
    """(program, args, environment, cwd, assignments, words, modelled) *command* runs with; None when it runs no
    program. *modelled* is False when a word before the program edits the environment in a way not modelled."""
    from .mutative_verbs import _peel_leading_cd

    cwd, command, _ = _peel_leading_cd(command, cwd)
    split = _split(command)
    if split is None or not split[1]:
        return None
    assignments, words, prefix = split
    base = {**os.environ, **(inherited or {})}
    edited = _edited_environment(prefix, base)
    environment = base if edited is None else edited
    if edited is None:
        environment.update(_ASSIGNMENT.fullmatch(word).groups() for word in assignments)
    return (command_name(words[0]), words[1:], environment, _wrapper_cwd(prefix, cwd), assignments, words,
            edited is not None)


def _file_state(path: Path) -> list:
    """[path, resolved path, sha256 of its bytes, "absent", or None when it cannot be hashed]."""
    real, text, data = _read_file(path)
    return [str(path), real, None if text is None else "absent" if data is None else hashlib.sha256(data).hexdigest()]


def _tree_state(root: Path) -> Optional[list]:
    """The _file_state of every file under *root*, [] when it is absent; None for a symlinked directory, an entry that
    is neither file nor directory, or more than _TREE_LIMIT entries."""
    if not os.path.lexists(root):
        return [[str(root), None, "absent"]]
    if os.path.islink(root) or not os.path.isdir(root):
        return None
    states, pending = [], [root]
    while pending:
        try:
            entries = sorted(os.scandir(pending.pop()), key=lambda e: e.name)
        except OSError:
            return None
        for entry in entries:
            if len(states) >= _TREE_LIMIT or entry.is_symlink() and os.path.isdir(entry.path):
                return None
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
            else:
                states.append(_file_state(Path(entry.path)))
    return states


def _consultable(program: str, args: Sequence[str], environment: Dict[str, str], cwd: Optional[str]) -> Optional[list]:
    """Every file *program* could consult to pick its target, each with its state; None when that set is not known."""
    if program in _KUBE_PROGRAMS or program == "kubens":
        chosen = _flag_values(args, frozenset({"--kubeconfig"}))
        listed = chosen[-1:] or (environment.get("KUBECONFIG") or "").split(os.pathsep)
        paths = [_in_directory(p, cwd) for p in listed if p] or [Path.home() / ".kube" / "config"]
        return None if None in paths else [_file_state(p) for p in paths]
    roots = {"gcloud": ("CLOUDSDK_CONFIG", (".config", "gcloud")), "az": ("AZURE_CONFIG_DIR", (".azure",)),
             "docker": ("DOCKER_CONFIG", (".docker",)), "podman": ("DOCKER_CONFIG", (".docker",)),
             "pulumi": ("PULUMI_HOME", (".pulumi",))}
    if program in roots:
        if program == "docker":
            environment = {**environment, **{"DOCKER_CONFIG": v for v in _leading_option_values(program, args, "--config")}}
        root = _config_root(environment, *roots[program], cwd)
        if root is None:
            return None
        if program == "gcloud":
            return _states([_file_state(root / "active_config")], _tree_state(root / "configurations"))
        if program == "az":
            return [_file_state(root / "azureProfile.json"), _file_state(root / "config")]
        if program in ("docker", "podman"):
            return _states([_file_state(root / "config.json")], _tree_state(root / "contexts"))
        return _states(_pulumi_project_files(args, cwd), [_file_state(root / "credentials.json")],
                       _tree_state(root / "workspaces"))
    if program == "terraform":
        located = _terraform_environment(args, environment, cwd)
        return None if located is None else [_file_state(located)]
    return [] if program in _PROGRAM_ENV_PREFIXES else None


def _states(*groups: Optional[list]) -> Optional[list]:
    return None if any(group is None for group in groups) else [state for group in groups for state in group]


def _pulumi_project_files(args: Sequence[str], cwd: Optional[str]) -> Optional[list]:
    """The state of each pulumi project file from the selected directory up to the first directory holding one."""
    chosen = _flag_values(args, frozenset({"-C", "--cwd"}))
    start = _in_directory(chosen[-1] if chosen else ".", cwd)
    if start is None:
        return None
    states = []
    for directory in (start.absolute(), *start.absolute().parents):
        found = [_file_state(directory / name) for name in _PULUMI_PROJECT_FILES]
        states += found
        if any(state[2] != "absent" for state in found):
            break
    return states


def command_targets(
    command: str, *, cwd: Optional[str] = None, inherited: Optional[Dict[str, str]] = None, ambient: bool = True,
) -> List[Target]:
    """Every target *command* lands on: its target flags, the naming variables set before it, a switch's destination,
    then what its configuration picks, read in *cwd* after any leading ``cd`` of the line."""
    context = _command_context(command, cwd, inherited)
    if context is None:
        return []
    program, args, environment, cwd, assignments, words, modelled = context
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
    inline = {*(inherited or {}), *(_ASSIGNMENT.fullmatch(w).group(1) for w in assignments)}
    found += [Target("setting", UNREADABLE) for name in sorted(inline) if name.startswith(_PROGRAM_ENV_PREFIXES.get(
        program, ())) and name not in _PROGRAM_SELECTORS.get(program, frozenset()) | _HARMLESS_VARIABLES]
    if not modelled:
        found.append(Target("environment", UNREADABLE))
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
    """The target variables (``DOCKER_CONTEXT``, ``TF_DATA_DIR``...) a literal assignment on *line* sets, with their values."""
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
            if match and (match.group(1) in _NAMING_VARIABLES or match.group(1).startswith(_TARGET_PREFIXES)):
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
    """The targets and the hashed prefixed variables a grant for *command* binds to; UNDETERMINED when the shell computes one.

    A target a config file names without certainty binds to UNREADABLE plus a fingerprint of what would decide it: the
    command, *cwd*, the selector and prefixed variables, the targets, and every file the tool could consult (_consultable) with its
    content hash. UNDETERMINED when that set is not known or a file in it cannot be hashed.
    """
    reads: list = []
    listening = _CONFIG_READS.set(reads)
    try:
        targets = command_targets(command, cwd=cwd)
    except Exception:
        return UNDETERMINED
    finally:
        _CONFIG_READS.reset(listening)
    if any(t.name == UNDETERMINED or t.kind == "environment" for t in targets):
        return UNDETERMINED
    found = sorted([t.kind, t.name] for t in targets)
    try:
        context = _command_context(command, cwd, None)
    except Exception:
        return UNDETERMINED
    prefixes = _PROGRAM_ENV_PREFIXES.get(context[0], ()) if context else ()
    settings = sorted([name, value] for name, value in context[2].items() if name.startswith(prefixes)) if prefixes else []
    if not any(t.name == UNREADABLE for t in targets):
        bound = json.dumps(found) if found else ""
        return f"{bound}#{hashlib.sha256(json.dumps(settings).encode('utf-8')).hexdigest()}" if settings else bound
    try:
        consulted = None if context is None else _consultable(*context[:4])
    except Exception:
        return UNDETERMINED
    if consulted is None or any(digest is None for _, _, digest in reads + consulted):
        return UNDETERMINED
    selectors = {name: context[2].get(name) for name in _SELECTOR_VARIABLES}
    inputs = json.dumps([command, cwd, selectors, settings, found, reads, consulted], sort_keys=True)
    return f"{UNREADABLE}:{hashlib.sha256(inputs.encode('utf-8')).hexdigest()}"


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
    A configuration file that cannot be read with certainty, or a failure while reading targets, asks instead.
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
    verb = detected.verb if detected.verb and "/" not in detected.verb else _operation(words)[0] if words else None
    verb = verb or (command_name(words[0]) if words else "")
    return MutativeResult(
        is_mutative=True, category=CATEGORY_MUTATIVE, verb=verb,
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
    named = next((t for t in targets if t.name not in (UNDETERMINED, UNREADABLE) and is_production(t.name, declaration)), None)
    if named is not None:
        landing = f"the production {named.kind} {named.name!r}"
    elif any(t.name == UNREADABLE for t in targets) or (switching and any(t.name == UNDETERMINED for t in targets)):
        landing = "a target that could not be determined"
    return (landing, list(words)) if landing else None
