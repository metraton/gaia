"""Where a command lands -- cluster context, project, account, workspace, stack or branch -- and whether that is production.

Everything here reads files or the database, so it runs outside the classifier's command-keyed cache.
"""

from __future__ import annotations

import configparser
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

_TARGET_FLAGS = frozenset({
    "--context", "--kube-context", "--cluster", "--project", "--account", "--profile",
    "--workspace", "--stack", "--env", "--environment", "--subscription",
})
_PROGRAM_TARGET_FLAGS = {"docker": frozenset({"-c"}), "pulumi": frozenset({"-s"})}
_NAMING_VARIABLES = frozenset({
    "DOCKER_CONTEXT", "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_CORE_ACCOUNT", "AWS_PROFILE", "TF_WORKSPACE",
})
_KUBE_PROGRAMS = frozenset({"kubectl", "oc", "helm", "flux", "kustomize", "k9s"})
_KUBE_VALUE_FLAGS = frozenset({
    "-n", "--namespace", "--kubeconfig", "--user", "-s", "--server", "--request-timeout", "-v", "--v",
    "-l", "--selector", "-o", "--output", "-f", "--filename", "-c", "--container",
})
_DOCKER_VALUE_FLAGS = frozenset({"--context", "-H", "--host", "--config", "-l", "--log-level"})
_VALUE_FLAGS = {
    "kubectl": _KUBE_VALUE_FLAGS, "oc": _KUBE_VALUE_FLAGS, "docker": _DOCKER_VALUE_FLAGS, "podman": _DOCKER_VALUE_FLAGS,
    "aws": frozenset({"--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle"}),
    "gcloud": frozenset({"--configuration", "--verbosity", "--billing-project", "--zone", "--region", "--format",
                         "--impersonate-service-account", "--filter", "--limit"}),
}
_KUBE_GROUPS = frozenset({"config", "rollout", "auth", "certificate"})
_DOCKER_GROUPS = frozenset({"container", "image", "volume", "network", "context", "compose", "system", "buildx"})
_GROUP_WORDS = {
    "kubectl": _KUBE_GROUPS, "oc": _KUBE_GROUPS, "docker": _DOCKER_GROUPS, "podman": _DOCKER_GROUPS,
    "terraform": frozenset({"workspace", "state"}), "gcloud": frozenset({"config", "alpha", "beta"}),
}
_SWITCH_WORDS = frozenset({"use-context", "use", "select", "activate", "switch"})
_EXTRA_READ_WORDS = frozenset({"access", "ps"})
_KUBE_CONTEXT_FLAGS = frozenset({"--context", "--kube-context"})
_PRODUCTION_WORDS = frozenset({"prod", "production", "prd"})
_NAME_PARTS = re.compile(r"[a-z0-9]+")
_DYNAMIC = re.compile(r"[$`]")
_ASSIGNMENT = re.compile(r"([A-Za-z_]\w*)=(.*)", re.S)
_CURRENT_CONTEXT = re.compile(r"""^current-context:[ \t]*["']?([^"'\s#]*)""", re.M)


@dataclass(frozen=True)
class Target:
    """One place a command lands: *kind* says where the name came from, *name* is UNDETERMINED when nothing literal names it."""

    kind: str
    name: str


def _flag_values(args: Sequence[str], flags: frozenset) -> List[str]:
    values: List[str] = []
    for index, word in enumerate(args):
        if word == "--":
            break
        name, equals, attached = word.partition("=")
        if name not in flags:
            continue
        value = attached if equals else (args[index + 1] if index + 1 < len(args) else UNDETERMINED)
        if not (name in ("--env", "--environment") and "=" in value):
            values.append(value)
    return values


def _literal(value: str) -> str:
    return UNDETERMINED if not value or _DYNAMIC.search(value) else value


def _read_text(path: Path) -> Optional[str]:
    """The file's text, "" when it does not exist, None when it exists and cannot be read."""
    if not path.exists() and not path.is_symlink():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _kube_context(environment: Dict[str, str]) -> str:
    paths = environment.get("KUBECONFIG")
    files = [Path(p) for p in paths.split(os.pathsep) if p] if paths else [Path.home() / ".kube" / "config"]
    for path in files:
        text = _read_text(path.expanduser())
        if text is None:
            return UNDETERMINED
        if not text.lstrip().startswith("{"):
            match = _CURRENT_CONTEXT.search(text)
            if match and match.group(1):
                return match.group(1)
            continue
        try:
            context = json.loads(text).get("current-context")
        except (ValueError, AttributeError):
            return UNDETERMINED
        if context:
            return str(context)
    return ""


def _gcloud_properties(environment: Dict[str, str]) -> Tuple[str, str]:
    root = Path(environment.get("CLOUDSDK_CONFIG") or Path.home() / ".config" / "gcloud").expanduser()
    active = environment.get("CLOUDSDK_ACTIVE_CONFIG_NAME") or _read_text(root / "active_config")
    if active is None:
        return UNDETERMINED, UNDETERMINED
    text = _read_text(root / "configurations" / f"config_{active.strip() or 'default'}")
    if text is None:
        return UNDETERMINED, UNDETERMINED
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return UNDETERMINED, UNDETERMINED
    return parser.get("core", "project", fallback=""), parser.get("core", "account", fallback="")


def _docker_context(environment: Dict[str, str]) -> str:
    if environment.get("DOCKER_CONTEXT"):
        return environment["DOCKER_CONTEXT"]
    root = Path(environment.get("DOCKER_CONFIG") or Path.home() / ".docker").expanduser()
    text = _read_text(root / "config.json")
    if not text:
        return "" if text == "" else UNDETERMINED
    try:
        return str(json.loads(text).get("currentContext") or "")
    except (ValueError, AttributeError):
        return UNDETERMINED


def _in_directory(path: str, cwd: Optional[str]) -> Optional[Path]:
    expanded = Path(path).expanduser()
    return expanded if expanded.is_absolute() else (Path(cwd) / expanded if cwd else None)


def _ambient(program: str, args: Sequence[str], environment: Dict[str, str], cwd: Optional[str]) -> List[Target]:
    if program in _KUBE_PROGRAMS and not _flag_values(args, _KUBE_CONTEXT_FLAGS):
        for value in _flag_values(args, frozenset({"--kubeconfig"})):
            path = _in_directory(value, cwd)
            if path is None or _DYNAMIC.search(value):
                return [Target("kube-context", UNDETERMINED)]
            environment["KUBECONFIG"] = str(path)
        return [Target("kube-context", _kube_context(environment))]
    if program == "gcloud":
        for value in _flag_values(args, frozenset({"--configuration"})):
            environment["CLOUDSDK_ACTIVE_CONFIG_NAME"] = value
        project, account = _gcloud_properties(environment)
        found = []
        if not _flag_values(args, frozenset({"--project"})):
            found.append(Target("project", environment.get("CLOUDSDK_CORE_PROJECT") or project))
        if not _flag_values(args, frozenset({"--account"})):
            found.append(Target("account", environment.get("CLOUDSDK_CORE_ACCOUNT") or account))
        return found
    if program in ("docker", "podman") and not _flag_values(args, frozenset({"--context", "-c"})):
        return [Target("docker-context", _docker_context(environment))]
    if program == "aws" and not _flag_values(args, frozenset({"--profile"})):
        return [Target("profile", environment.get("AWS_PROFILE", ""))]
    if program == "terraform":
        if environment.get("TF_WORKSPACE"):
            return [Target("workspace", environment["TF_WORKSPACE"])]
        directory = _in_directory(next((a.partition("=")[2] for a in args if a.startswith("-chdir=")), "."), cwd or os.getcwd())
        text = None if directory is None else _read_text(directory / ".terraform" / "environment")
        return [Target("workspace", UNDETERMINED if text is None else text.strip())]
    return []


def _gcloud_configuration_targets(name: str, environment: Dict[str, str]) -> List[Target]:
    project, account = _gcloud_properties({**environment, "CLOUDSDK_ACTIVE_CONFIG_NAME": name})
    return [Target("switch", project), Target("switch", account)]


def _split(command: str) -> Optional[Tuple[List[str], List[str]]]:
    from ..tools.shell_parser import shell_split
    from .mutative_verbs import _expand_split_strings, _peel_wrapper_readings

    readings, _ = _peel_wrapper_readings(command)
    dropped, expanded, _, _ = _expand_split_strings(command)
    try:
        written, words = shell_split(dropped) + shell_split(expanded), shell_split(readings[0])
    except ValueError:
        return None
    prefix = written[: max(len(written) - len(words), 0)]
    return [w for w in prefix if _ASSIGNMENT.fullmatch(w)], words


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
    assignments, words = split
    program, args = command_name(words[0]), words[1:]
    environment = {**os.environ, **(inherited or {})}
    for word in assignments:
        name, value = _ASSIGNMENT.fullmatch(word).groups()
        environment[name] = value
    flags = _TARGET_FLAGS | _PROGRAM_TARGET_FLAGS.get(program, frozenset())
    found = [Target("flag", _literal(value)) for value in _flag_values(args, flags)]
    found += [Target("variable", _literal(environment[name])) for name in sorted(_NAMING_VARIABLES)
              if name in environment and (name in (inherited or {}) or any(w.startswith(f"{name}=") for w in assignments))]
    operation, operands = _operation(words)
    if operation in _SWITCH_WORDS and operands:
        destination = _literal(operands[-1])
        found.append(Target("switch", destination))
        if program == "gcloud" and destination != UNDETERMINED:
            found += _gcloud_configuration_targets(destination, environment)
    if ambient:
        found += _ambient(program, args, environment, cwd)
    return [target for target in found if target.name]


def _positionals(words: Sequence[str]) -> List[str]:
    program = command_name(words[0])
    value_flags = _TARGET_FLAGS | _PROGRAM_TARGET_FLAGS.get(program, frozenset()) | _VALUE_FLAGS.get(program, frozenset())
    found: List[str] = []
    for index, word in enumerate(words[1:], 1):
        if word == "--":
            break
        if not word.startswith("-") and words[index - 1] not in value_flags:
            found.append(word)
    return found


def _is_verb_word(word: str) -> bool:
    from .mutative_verbs import MUTATIVE_VERBS, READ_ONLY_VERBS, SIMULATION_VERBS

    verbs = MUTATIVE_VERBS | READ_ONLY_VERBS | SIMULATION_VERBS
    return word in verbs | _EXTRA_READ_WORDS | _SWITCH_WORDS or word.split("-")[0] in verbs


def _operation(words: Sequence[str]) -> Tuple[Optional[str], List[str]]:
    """The word naming what *words* does, at the program's command-word position, and the operands after it.

    aws names it second; gcloud names it after its groups, as the first word that is a verb; others name it first.
    """
    if not words:
        return None, []
    program, positionals = command_name(words[0]), [w.lower() for w in _positionals(words)]
    groups = _GROUP_WORDS.get(program, frozenset())
    if program == "aws":
        return (positionals[1], positionals[2:]) if len(positionals) > 1 else (None, [])
    for index, word in enumerate(positionals):
        if word in groups:
            continue
        if program != "gcloud" or _is_verb_word(word):
            return word, positionals[index + 1:]
    return None, []


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
    for component in parse_command(line):
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
    """The ``targets`` project-context section of the active project holding *cwd*; {} when there is none or it cannot be read."""
    try:
        from gaia.store.project_context import project_sections
        from gaia.store.reader import _ro_connect

        here = Path(cwd or os.getcwd()).resolve()
        con = _ro_connect()
    except Exception:
        return {}
    try:
        rows = con.execute(
            "SELECT workspace, name, path, project_identity FROM projects WHERE status = 'active' AND path IS NOT NULL",
        ).fetchall()
        holding = [r for r in rows if here == Path(r[2]) or Path(r[2]) in here.parents]
        if not holding:
            return {}
        workspace, name, path, identity = max(holding, key=lambda r: len(r[2]))
        section = project_sections(con, {"identity": identity or path, "workspace": workspace, "name": name})
        payload = section.get(DECLARATION_SECTION, {}).get("payload")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    finally:
        con.close()


def grant_target(command: str, *, cwd: Optional[str] = None) -> str:
    """The resolved targets a grant for *command* binds to, as one string; UNDETERMINED when any of them cannot be read."""
    targets = command_targets(command, cwd=cwd)
    if any(t.name == UNDETERMINED for t in targets):
        return UNDETERMINED
    return ";".join(sorted(f"{t.kind}={t.name}" for t in targets))


def _pushed_branch(words: Sequence[str]) -> Optional[str]:
    from .mutative_verbs import _git_subcommand_split

    split = _git_subcommand_split(tuple(words))
    if split is None or split[1] != "push":
        return None
    operands = [w for w in split[2] if not w.startswith("-")]
    return operands[-1].rpartition(":")[2] if len(operands) == 2 else None


def production_target_verdict(
    command: str, detected, *, cwd: Optional[str] = None, inherited: Optional[Dict[str, str]] = None,
):
    """Escalate a verdict that does not sign when *command* lands on a production target or a declared protected branch.

    The operation word decides a read (``_operation``), never an operand; an executor keeps the verdict of what it runs.
    A target that is not literal keeps the verdict (D178 pins ``docker --context $CTX ps`` free); only a grant refuses it.
    """
    from .mutative_verbs import _READING_CATEGORIES, CATEGORY_MUTATIVE, MutativeResult
    from .nested_executors import nested_run

    if detected.is_mutative:
        return None
    split = _split(command)
    words = split[1] if split else []
    branch = _pushed_branch(words) if words and command_name(words[0]) == "git" else None
    if detected.category in _READING_CATEGORIES and words and nested_run(words) is not None:
        return None
    operation = _operation(words)[0]
    if not branch and operation not in _SWITCH_WORDS and _is_read(operation):
        return None
    targets = [] if branch else command_targets(command, cwd=cwd, inherited=inherited)
    if not branch and not targets:
        return None
    declaration = declaration_for(cwd)
    landing = None
    if branch and branch in {str(b) for b in declaration.get("protected_branches", ())}:
        landing = f"the declared protected branch {branch!r}"
    if operation in _SWITCH_WORDS:
        targets = [t for t in targets if t.kind == "switch"]
    named = next((t for t in targets if t.name != UNDETERMINED and is_production(t.name, declaration)), None)
    if named is not None:
        landing = f"the production {named.kind} {named.name!r}"
    if not landing:
        return None
    return MutativeResult(
        is_mutative=True, category=CATEGORY_MUTATIVE, verb=detected.verb or command_name(words[0]),
        confidence="high", reason=f"{shlex.join(words)!r} lands on {landing}", rule="sign.lands",
    )
