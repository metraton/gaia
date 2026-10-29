"""Per context section, the key paths that name what a request is about (WHAT); the rest of a section is HOW.

Path grammar: dot-separated steps; `*` any key; `name[]` each item of a list; a trailing `{}` keeps the selected keys without their values, `{dict}` only those holding an object.
A step may end in `~lead` (keep the text up to the first space) or `~basename` (keep the last path segment), and in `!key` guards: the step is skipped when the entry holds that sibling key.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, NamedTuple, Optional

WHAT_KEYS: Mapping[str, tuple] = {
    "cluster_details": (
        "gke_cluster_name",
        "gke_location",
        "gke_project",
        "namespaces",
        "gitops_controller.git_repository",
        "active_cluster.name",
        "active_cluster.location",
        "active_cluster.project",
        "live_demo.namespace",
        "live_demo.host",
        "live_demo.workloads~lead",
        "scaled_to_zero_namespaces.*{}",
        "cluster_name",
        "provider",
        "project_id",
        "region",
    ),
    "application_services": (
        "*{dict}",
        "services[].name",
        "*.image_repository",
        "*.deployments.*{}",
        "*.marketplace_name",
        "*.plugins[].name",
    ),
    "gitops_configuration": (
        "git_remote",
        "cluster_entrypoint~lead",
        "environments",
        "namespaces",
        "*.repo",
        "*.cluster_entrypoint~lead",
        "*.namespaces",
    ),
    "infrastructure": ("*.project_id", "*.project"),
    "infrastructure_topology": (
        "gcp_projects.*{}",
        "*.environments.*{}",
        "*.environments.*.project_id",
        "*.seed_project",
        "*.factory_project.project_id",
        "*.target_project~lead",
        "*.github_slug",
        "*.remote",
        "*.instance",
        "*.affected_services.*{}",
    ),
    "project_identity": (
        "*.remote_url!missing_since",
        "*.local_path~basename!remote_url!missing_since",
        "*.apps.*{}",
    ),
    "git": ("repos[].name", "repos[].remote_url"),
    "static_ips": ("*{}",),
    "stack": (),
    "environment": (),
    "architecture_overview": (),
    "workspace_repos": (),
    "operational_guidelines": (),
    "releases": (),
    "terraform_infrastructure": ("*{dict}",),
    "monitoring_observability": (),
    "orchestration": (),
}

_STEP = re.compile(
    r"(?P<name>[^\[\]{}~!]+)(?P<each>\[\])?(?P<keys_only>\{\}|\{dict\})?(?:~(?P<transform>\w+))?(?P<guards>(?:![^!]+)*)"
)
_PATH_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")
_NOTHING = object()
_TRANSFORMS = {
    "lead": lambda text: text.split()[0] if text.split() else "",
    "basename": lambda text: re.split(r"[\\/]+", text.rstrip("/\\"))[-1],
}


class _Step(NamedTuple):
    name: str
    each: bool
    keys_only: Optional[str]
    transform: Optional[str]
    guards: tuple


def what_keys(section: str) -> tuple:
    """The WHAT key paths of a section; empty for a HOW section and for one never classified."""
    return WHAT_KEYS.get(section, ())


def _steps(path: str) -> list:
    parsed = [_STEP.fullmatch(step) for step in path.split(".")]
    return [
        _Step(m["name"], bool(m["each"]), m["keys_only"], m["transform"], tuple(m["guards"].split("!")[1:]))
        for m in parsed
    ]


def _present(entry: dict, key: str) -> bool:
    return entry.get(key) not in (None, "")


def _apply(value: Any, transform: Optional[str]) -> Any:
    if transform is None:
        return value
    if isinstance(value, str):
        return _TRANSFORMS[transform](value) or _NOTHING
    if isinstance(value, list):
        shaped = [item for item in (_apply(item, transform) for item in value) if item is not _NOTHING]
        return shaped or _NOTHING
    return _NOTHING


def _select(value: Any, steps: list) -> Any:
    if not steps:
        return value
    if not isinstance(value, dict):
        return _NOTHING
    step, rest = steps[0], steps[1:]
    picked = {}
    if any(_present(value, guard) for guard in step.guards):
        return _NOTHING
    for key in value if step.name == "*" else [step.name] if step.name in value else []:
        if step.keys_only:
            if step.keys_only == "{}" or isinstance(value[key], dict):
                picked[key] = {}
        elif step.each:
            if not isinstance(value[key], list):
                continue
            items = [_select(item, rest) for item in value[key]]
            if any(item is not _NOTHING for item in items):
                picked[key] = [{} if item is _NOTHING else item for item in items]
        else:
            chosen = _select(value[key], rest)
            chosen = _apply(chosen, step.transform) if not rest else chosen
            if chosen is not _NOTHING:
                picked[key] = chosen
    return picked or _NOTHING


def _merge(first: Any, second: Any) -> Any:
    if isinstance(first, dict) and isinstance(second, dict):
        merged = dict(first)
        for key, value in second.items():
            merged[key] = _merge(first[key], value) if key in first else value
        return merged
    if isinstance(first, list) and isinstance(second, list) and len(first) == len(second):
        return [_merge(a, b) for a, b in zip(first, second)]
    return first if second == {} else second


def _path_tokens(path: str) -> list:
    return [int(index) if index else key for index, key in _PATH_TOKEN.findall(path)]


def _walk(value: Any, tokens: list) -> Any:
    for token in tokens:
        try:
            value = value[token]
        except (KeyError, IndexError, TypeError):
            return _NOTHING
    return value


def _path_matches(steps: list, tokens: list, payload: Any) -> bool:
    position = 0
    for step in steps:
        if position >= len(tokens) or not isinstance(tokens[position], str) or step.name not in ("*", tokens[position]):
            return False
        if payload is not None and step.guards:
            parent = _walk(payload, tokens[:position])
            if not isinstance(parent, dict) or any(_present(parent, guard) for guard in step.guards):
                return False
        position += 1
        if step.each:
            if position >= len(tokens) or not isinstance(tokens[position], int):
                return False
            position += 1
    keys_only = steps[-1].keys_only
    if not keys_only:
        return True
    if position != len(tokens):
        return False
    return keys_only == "{}" or payload is None or isinstance(_walk(payload, tokens), dict)


def is_what_path(section: str, path: str, payload: Any = None) -> bool:
    """Whether a concrete path like `live_demo.workloads[1]` is, or lies inside, a WHAT key of the section; given the payload, an object-only key also checks its value."""
    tokens = _path_tokens(path)
    return any(_path_matches(_steps(pattern), tokens, payload) for pattern in what_keys(section))


def project_what(section: str, payload: Any) -> dict:
    """The subset of a stored section payload that holds only its WHAT keys, as a copy."""
    if not isinstance(payload, dict):
        return {}
    projected: dict = {}
    for path in what_keys(section):
        picked = _select(payload, _steps(path))
        if picked is not _NOTHING:
            projected = _merge(projected, picked)
    return copy.deepcopy(projected)
