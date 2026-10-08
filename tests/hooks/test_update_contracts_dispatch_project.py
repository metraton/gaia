"""An update_contracts write follows the project the dispatch names, not the session cwd.

Measured defect (plan 87 task 871): a dispatch opened from the workspace root
(``ws``) carried ``project=gaia``, whose project lives in ``me``. The close
wrote ``project_identity.gaia.workflow`` into ``ws``: ``gaia context project
gaia`` in ``me`` kept the old value and ``ws`` gained a stray partial entry.
"""

from __future__ import annotations

import json

import pytest

from tests.hooks.test_update_contracts_on_close import (
    DISPATCH_WORKSPACE,
    HOOK_CWD_WORKSPACE,
    PROJECT,
    WORKFLOW,
    _cli,
    _close_turn,
    _project_view,
    _REPO_ROOT,
    _seed_project,
    _isolated_substrate,  # noqa: F401  (autouse fixture)
    db_path,  # noqa: F401  (fixture)
)
from adapters.claude_code import ClaudeCodeAdapter

AGENT = "gaia-operator"


def _stored_in(workspace: str) -> str:
    """The workspace's project_identity as JSON; empty when it holds none (exit 1)."""
    shown = _cli([str(_REPO_ROOT / "bin" / "gaia"), "context", "get-contract",
                  "--section", "project_identity", "--workspace", workspace, "--json"])
    assert shown.returncode in (0, 1), shown.stderr
    return shown.stdout if shown.returncode == 0 else ""


@pytest.fixture()
def session_at_workspace_root(db_path, monkeypatch):
    """The dispatch is born in ``ws`` while the project is declared in ``me``."""
    monkeypatch.setenv("GAIA_WORKSPACE", HOOK_CWD_WORKSPACE)
    ClaudeCodeAdapter._maybe_birth_dispatched_row({"prompt": "bootstrap"}, AGENT, "sess-boot")
    _seed_project(db_path, AGENT)


def test_update_contracts_dispatch_project_lands_in_the_projects_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_turn(
        db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION",
        prompt=f"project={PROJECT} declare the project's workflow",
    )

    assert response.exit_code == 0, response.output
    assert WORKFLOW in json.dumps(_project_view(db_path)), (
        f"the workflow must reach `gaia context project {PROJECT}` of {DISPATCH_WORKSPACE}"
    )
    assert WORKFLOW not in _stored_in(HOOK_CWD_WORKSPACE), (
        f"no stray {PROJECT} entry may be left in the session's workspace"
    )


def test_update_contracts_dispatch_project_absent_keeps_the_born_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_turn(db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION")

    assert response.exit_code == 0, response.output
    assert WORKFLOW in _stored_in(HOOK_CWD_WORKSPACE)
    assert WORKFLOW not in json.dumps(_project_view(db_path))


def test_update_contracts_dispatch_project_unknown_keeps_the_born_workspace(
    db_path, monkeypatch, session_at_workspace_root,
):
    response = _close_turn(
        db_path, monkeypatch, AGENT, "NEEDS_VERIFICATION",
        prompt="project=not-a-known-project declare the project's workflow",
    )

    assert response.exit_code == 0, response.output
    assert WORKFLOW in _stored_in(HOOK_CWD_WORKSPACE)
    assert WORKFLOW not in json.dumps(_project_view(db_path))
