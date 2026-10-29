"""`gaia open REF [--line|--json]` is a T0 read for the orchestrator lane and for a subagent."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

_CONTRACT_ID = "a173f63dee5740a6f.9301a33c2ddb"
_BRIEF = "information-lives-once-at-its-coordinate"
_APPROVAL_ID = "P-39e3fa64e45d4198bd1028f369c81ce4"
_MOVED_SLUG = "decision_gaia_vocabulario_workspace_grupo_proyecto_v3_20261028"

DESIGN_EXAMPLES = [
    "memory:ws/thread_gaia_coordenadas_en_vez_de_recuentos",
    "memory:user/user_pref_formato_estandar_descripcion_pr_20261022",
    "context:ws/branchkinect-factory/infrastructure#evidence_convention",
    f"contract:{_CONTRACT_ID}#conclusion",
    "contract:34522",
    "task:1036",
    "plan:94",
    "evidence:42",
    f"approval:{_APPROVAL_ID}",
    "code:gaia@9ed3911c/hooks/modules/context/kernel_builder.py::build_kernel_context",
]

RESOLVER_EXAMPLES = [
    "contract:a173f63dee5740a6f",
    "contract:a96b3329d5915478a",
    f"contract:{_CONTRACT_ID}",
    "contract:34522#conclusion",
    f"memory:me/{_MOVED_SLUG}",
    f"memory:ws/{_MOVED_SLUG}",
    "memory:ws/decision_gaia_vocabulario_workspace",
    "memory:ws/decision_gaia_referencias_por_coordenadas_20261028",
    f"plan:{_BRIEF}",
    "task:1044",
    f"task:{_BRIEF}/16",
    f"brief:{_BRIEF}",
    "approval:P-39e3fa64",
    "notification:81",
    "context:ws/infrastructure",
    "context:ws/infrastructure#evidence_convention",
    "context:ws/gaia/stack",
]

MUTATIVE_WORD_SLUGS = [
    "memory:ws/thread_delete_stale_rows",
    "memory:ws/feedback_push_before_merge",
    "memory:ws/feedback-deploy-before-push",
    "brief:me/remove-legacy-install",
    "memory:ws/decision-apply-migrations#body",
    "brief:deploy-pipeline-rollout",
    "memory:ws/push",
]

REFERENCES = DESIGN_EXAMPLES + RESOLVER_EXAMPLES + MUTATIVE_WORD_SLUGS
SHAPES = [(), ("--line",), ("--json",)]

_ORCHESTRATOR = {"agent_type": "", "session_id": "ses-open-lane", "cwd": str(_REPO_ROOT)}
_SUBAGENT = {
    "agent_id": "a0123456789abcdef",
    "agent_type": "developer",
    "session_id": "ses-open-lane",
    "cwd": str(_REPO_ROOT),
}


@pytest.fixture(autouse=True)
def _repo_gaia_first_on_path(tmp_path, monkeypatch):
    (tmp_path / "gaia").symlink_to(_GAIA_BIN)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")


def _orchestrator(argline: str):
    return BashValidator().validate(
        f"gaia {argline}", is_subagent=False, session_id="ses-open-lane",
        hook_payload=dict(_ORCHESTRATOR),
    )


def _subagent(argline: str):
    return BashValidator().validate(
        f"gaia {argline}", is_subagent=True, session_id="ses-open-lane",
        agent_type="developer", hook_payload=dict(_SUBAGENT),
    )


def _argline(ref: str, shape: tuple[str, ...]) -> str:
    return " ".join(("open", ref, *shape))


def _quoted_if_fragment(ref: str) -> str:
    return f"'{ref}'" if "#" in ref else ref


@pytest.mark.parametrize("ref", [r for r in REFERENCES if "#" in r])
def test_orchestrator_quotes_a_fragment_because_bare_hash_is_outside_its_allowlist(ref):
    result = _orchestrator(_argline(ref, ()))
    assert not result.allowed
    assert "outside quotes" in result.reason


def test_real_binary_is_trusted_so_orchestrator_checks_are_live():
    assert guard.is_trusted_gaia_binary(str(_GAIA_BIN))


def test_open_is_an_orchestrator_read_with_only_line_and_json():
    assert ("open",) in guard.ALLOWED_READ_PHRASES
    assert ("open",) not in guard.ALLOWED_WRITE_PHRASES
    assert guard._KNOWN_SAFE_FLAGS[("open",)] == frozenset({"--line", "--json"})


@pytest.mark.parametrize("shape", SHAPES, ids=lambda s: " ".join(s) or "bare")
@pytest.mark.parametrize("ref", REFERENCES)
def test_reference_is_t0_for_the_orchestrator(ref, shape):
    result = _orchestrator(_argline(_quoted_if_fragment(ref), shape))
    assert result.allowed, result.reason
    assert result.tier is SecurityTier.T0_READ_ONLY, (result.tier, result.reason)


@pytest.mark.parametrize("shape", SHAPES, ids=lambda s: " ".join(s) or "bare")
@pytest.mark.parametrize("ref", REFERENCES)
def test_reference_is_t0_for_a_subagent(ref, shape):
    result = _subagent(_argline(ref, shape))
    assert result.allowed, result.reason
    assert result.tier is SecurityTier.T0_READ_ONLY, (result.tier, result.reason)


@pytest.mark.parametrize("argline", [
    "open plan:94 --field conclusion",
    "open plan:94 --lin",
    "open plan:94 --jso",
    "open plan:94 --line --workspace=ws",
    "open -- plan:94",
    "--json open plan:94 --verbose",
])
def test_orchestrator_open_admits_no_other_option(argline):
    result = _orchestrator(argline)
    assert not result.allowed
    assert "GAIA CLI ONLY" in result.reason


@pytest.mark.parametrize("argline", [
    "open plan:94 ; rm -rf build",
    "open memory:ws/`id`",
    "open contract:$(id)",
])
def test_orchestrator_open_does_not_carry_a_second_program(argline):
    assert not _orchestrator(argline).allowed
