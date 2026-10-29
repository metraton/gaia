"""Per context section, the key paths that name what a request is about (WHAT); the rest of a section is HOW.

Path grammar: dot-separated steps; `*` any key; `name[]` each item of a list; a trailing `{}` keeps the selected keys without their values.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

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
        "live_demo.workloads",
        "scaled_to_zero_namespaces.*{}",
        "cluster_name",
        "provider",
        "project_id",
        "region",
    ),
    "application_services": (
        "*{}",
        "*.image_repository",
        "*.deployments.*{}",
        "*.marketplace_name",
        "*.plugins[].name",
    ),
    "gitops_configuration": (
        "git_remote",
        "cluster_entrypoint",
        "environments",
        "namespaces",
        "*.repo",
        "*.cluster_entrypoint",
        "*.namespaces",
    ),
    "infrastructure": ("*.project_id", "*.project"),
    "infrastructure_topology": (
        "gcp_projects.*{}",
        "*.environments.*{}",
        "*.environments.*.project_id",
        "*.seed_project",
        "*.factory_project.project_id",
    ),
    "project_identity": ("*.name", "*.remote_url", "*.local_path"),
    "git": ("repos[].name", "repos[].remote_url"),
    "stack": (),
    "environment": (),
    "architecture_overview": (),
    "workspace_repos": (),
    "operational_guidelines": (),
    "releases": (),
}

_STEP = re.compile(r"(?P<name>[^\[\]{}]+)(?P<each>\[\])?(?P<keys_only>\{\})?")
_NOTHING = object()


def what_keys(section: str) -> tuple:
    """The WHAT key paths of a section; empty for a HOW section and for one never classified."""
    return WHAT_KEYS.get(section, ())


def _steps(path: str) -> list:
    parsed = [_STEP.fullmatch(step) for step in path.split(".")]
    return [(m["name"], bool(m["each"]), bool(m["keys_only"])) for m in parsed]


def _select(value: Any, steps: list) -> Any:
    if not steps:
        return value
    if not isinstance(value, dict):
        return _NOTHING
    (name, each, keys_only), rest = steps[0], steps[1:]
    picked = {}
    for key in value if name == "*" else [name] if name in value else []:
        if keys_only:
            picked[key] = {}
        elif each:
            if not isinstance(value[key], list):
                continue
            items = [_select(item, rest) for item in value[key]]
            if any(item is not _NOTHING for item in items):
                picked[key] = [{} if item is _NOTHING else item for item in items]
        else:
            chosen = _select(value[key], rest)
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
