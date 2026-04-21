"""Catalog loader for the eval framework.

Exposes :class:`CaseModel` (the per-case dataclass) and
:func:`load_catalog` (YAML -> ``list[CaseModel]``). The loader validates
the on-disk catalog structure without touching live project-context --
see the plan (section T5) for the full schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# Allowed values for CaseModel.backend -- kept here so the loader can
# validate without importing the runner.
VALID_BACKENDS = ("subprocess", "routing_sim", "hook_log_replay")

# Allowed scoring modes.
VALID_SCORING = ("binary", "semantic")

# Allowed grader names. ``routing_grader`` is used by the routing-sim
# backend (T3d); ``skill_injection_consumer`` is the S7 hook-log path
# (T4). Every grader listed in a case's ``grader`` field must be in
# this tuple.
VALID_GRADERS = (
    "code_grader",
    "contract_grader",
    "tool_trace_grader",
    "routing_grader",
    "skill_injection_consumer",
)

# Required top-level keys in the catalog YAML file.
REQUIRED_TOP_LEVEL = ("name", "description", "version", "cases")

# Required per-case keys. Optional expectation dicts
# (``expect_present``, ``contract_expect``, ...) default to empty when
# omitted.
REQUIRED_CASE_KEYS = ("id", "agent", "task", "grader", "backend", "scoring")


class CatalogError(ValueError):
    """Raised when the catalog YAML is malformed or fails validation."""


@dataclass(frozen=True)
class CaseModel:
    """A single eval case loaded from a catalog YAML.

    Field meanings match the catalog YAML schema documented in the plan
    (section T5). Optional per-grader expectation dicts default to empty
    mappings so downstream graders can consume them uniformly.

    Attributes:
        id: Unique case identifier (e.g. ``"S1"``, ``"repo_host_trap"``).
        agent: Target agent name (e.g. ``"developer"``).
        task: Natural-language prompt fed to the agent.
        grader: List of grader names applied to the response. Multiple
            graders may be combined for a single case.
        backend: Dispatch backend -- see :data:`VALID_BACKENDS`.
        scoring: Either ``"binary"`` (pass/fail) or ``"semantic"``
            (0..1 score with ``threshold``).
        threshold: Minimum score for a semantic case to pass. Defaults
            to ``0.8``; ignored for binary cases.
        expect_present: Keywords the response must contain.
        expect_absent: Keywords the response must NOT contain.
        contract_expect: Expectations for :func:`graders.contract_grader`.
        trace_expect: Expectations for :func:`graders.tool_trace_grader`.
        routing_expect: Expectations for the routing-sim grader (T3d).
        anomaly_expect: Expectations for the skill_injection consumer (T4).
    """

    id: str
    agent: str
    task: str
    grader: list[str]
    backend: str
    scoring: str
    threshold: float = 0.8
    expect_present: list[str] = field(default_factory=list)
    expect_absent: list[str] = field(default_factory=list)
    contract_expect: dict = field(default_factory=dict)
    trace_expect: dict = field(default_factory=dict)
    routing_expect: dict = field(default_factory=dict)
    anomaly_expect: dict = field(default_factory=dict)


def _validate_top_level(data: Any, path: Path) -> dict:
    """Return the parsed top-level mapping or raise :class:`CatalogError`."""
    if not isinstance(data, dict):
        raise CatalogError(
            f"{path}: catalog root must be a mapping, got {type(data).__name__}"
        )
    missing = [k for k in REQUIRED_TOP_LEVEL if k not in data]
    if missing:
        raise CatalogError(f"{path}: catalog missing required keys: {missing}")
    cases = data["cases"]
    if not isinstance(cases, list) or not cases:
        raise CatalogError(
            f"{path}: 'cases' must be a non-empty list, got {type(cases).__name__}"
        )
    return data


def _case_from_raw(raw: Any, path: Path, index: int) -> CaseModel:
    """Build a :class:`CaseModel` from one YAML case dict."""
    if not isinstance(raw, dict):
        raise CatalogError(
            f"{path}: case #{index} must be a mapping, got {type(raw).__name__}"
        )
    missing = [k for k in REQUIRED_CASE_KEYS if k not in raw]
    if missing:
        raise CatalogError(
            f"{path}: case #{index} ({raw.get('id', '?')}) missing keys: {missing}"
        )

    grader = raw["grader"]
    if not isinstance(grader, list) or not grader:
        raise CatalogError(
            f"{path}: case {raw['id']} 'grader' must be a non-empty list"
        )
    bad_graders = [g for g in grader if g not in VALID_GRADERS]
    if bad_graders:
        raise CatalogError(
            f"{path}: case {raw['id']} has unknown graders {bad_graders}; "
            f"valid set: {list(VALID_GRADERS)}"
        )

    backend = raw["backend"]
    if backend not in VALID_BACKENDS:
        raise CatalogError(
            f"{path}: case {raw['id']} has invalid backend {backend!r}; "
            f"valid set: {list(VALID_BACKENDS)}"
        )

    scoring = raw["scoring"]
    if scoring not in VALID_SCORING:
        raise CatalogError(
            f"{path}: case {raw['id']} has invalid scoring {scoring!r}; "
            f"valid set: {list(VALID_SCORING)}"
        )

    threshold = raw.get("threshold", 0.8)
    if not isinstance(threshold, (int, float)):
        raise CatalogError(
            f"{path}: case {raw['id']} threshold must be numeric, "
            f"got {type(threshold).__name__}"
        )

    def _list_field(key: str) -> list[str]:
        value = raw.get(key, []) or []
        if not isinstance(value, list):
            raise CatalogError(
                f"{path}: case {raw['id']} '{key}' must be a list, "
                f"got {type(value).__name__}"
            )
        return list(value)

    def _dict_field(key: str) -> dict:
        value = raw.get(key, {}) or {}
        if not isinstance(value, dict):
            raise CatalogError(
                f"{path}: case {raw['id']} '{key}' must be a mapping, "
                f"got {type(value).__name__}"
            )
        return dict(value)

    return CaseModel(
        id=str(raw["id"]),
        agent=str(raw["agent"]),
        task=str(raw["task"]),
        grader=list(grader),
        backend=backend,
        scoring=scoring,
        threshold=float(threshold),
        expect_present=_list_field("expect_present"),
        expect_absent=_list_field("expect_absent"),
        contract_expect=_dict_field("contract_expect"),
        trace_expect=_dict_field("trace_expect"),
        routing_expect=_dict_field("routing_expect"),
        anomaly_expect=_dict_field("anomaly_expect"),
    )


def load_catalog(path: Path) -> list[CaseModel]:
    """Load a catalog YAML from ``path`` and return its cases.

    Args:
        path: Absolute path to the catalog YAML file.

    Returns:
        List of :class:`CaseModel` objects in catalog order.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        CatalogError: If the YAML is malformed or any case fails
            validation (unknown grader / backend / scoring, missing
            required keys, duplicate case ids, etc.).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"catalog not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise CatalogError(f"{path}: invalid YAML: {exc}") from exc

    top = _validate_top_level(data, path)
    cases: list[CaseModel] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(top["cases"]):
        case = _case_from_raw(raw, path, i)
        if case.id in seen_ids:
            raise CatalogError(f"{path}: duplicate case id {case.id!r}")
        seen_ids.add(case.id)
        cases.append(case)

    return cases
