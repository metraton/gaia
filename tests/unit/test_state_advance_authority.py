#!/usr/bin/env python3
"""No context other than a curator advances a brief or a plan to a new state.

Brief and plan status are curator-only (``DISPATCH_PERMISSIONS``). The planner is
a non-curator that is nevertheless allowed to author plan CONTENT, so every
writer that carries a plan status along with that content is a second door to the
same transition and must hold the same lock as ``set_plan_status``.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from gaia.briefs.store import (
    close_brief,
    close_brief_verified,
    get_brief,
    set_status_brief,
    upsert_brief,
)
from gaia.state.permissions import StateTransitionForbidden
from gaia.store.writer import (
    get_plan,
    pause_plan,
    resume_plan,
    set_plan_status,
    upsert_plan,
)

ENV = "GAIA_DISPATCH_AGENT"
NON_CURATORS = ["developer", "gaia-system", "gaia-planner", "planner", "gitops-operator"]
UNREACHABLE_DB = Path("/nonexistent/must-not-be-opened.db")

_BRIEF_FIELDS = {"title": "T", "objective": "O", "status": "draft"}


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch, bootstrapped_db_template):
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "gaia.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(ENV, raising=False)
    return db_path


def _seed(db_path, name, *, plan_status=None):
    upsert_brief("me", name, _BRIEF_FIELDS, db_path=db_path)
    if plan_status is not None:
        upsert_plan("me", name, content="body", status=plan_status, db_path=db_path)


# One entry per writer that moves a brief or plan to a new lifecycle state.
_TRANSITIONS = {
    "close_brief": lambda db: close_brief("me", "b", db_path=db),
    "close_brief_verified": lambda db: close_brief_verified("me", "b", db_path=db),
    "set_status_brief": lambda db: set_status_brief("me", "b", "open", db_path=db),
    "set_plan_status": lambda db: set_plan_status("me", "b", "active", db_path=db),
    "pause_plan": lambda db: pause_plan("me", "b", "why", db_path=db),
    "resume_plan": lambda db: resume_plan("me", "b", db_path=db),
}


@pytest.mark.parametrize("agent", NON_CURATORS)
@pytest.mark.parametrize("transition", sorted(_TRANSITIONS))
def test_non_curator_is_refused_before_the_database_is_opened(monkeypatch, transition, agent):
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(StateTransitionForbidden):
        _TRANSITIONS[transition](UNREACHABLE_DB)


@pytest.mark.parametrize("agent", NON_CURATORS)
def test_close_brief_leaves_the_brief_open(tmp_db, monkeypatch, agent):
    _seed(tmp_db, "b")
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(StateTransitionForbidden):
        close_brief("me", "b", db_path=tmp_db)
    assert get_brief("me", "b", db_path=tmp_db)["status"] == "draft"


@pytest.mark.parametrize("curator", ["gaia-orchestrator", "orchestrator", "gaia-operator", "operator"])
def test_curator_still_closes_a_brief(tmp_db, monkeypatch, curator):
    _seed(tmp_db, "b")
    monkeypatch.setenv(ENV, curator)
    assert close_brief("me", "b", db_path=tmp_db) is True
    assert get_brief("me", "b", db_path=tmp_db)["status"] == "closed"


@pytest.mark.parametrize("new_status", ["active", "closed"])
def test_planner_cannot_advance_an_existing_plan_through_a_save(tmp_db, monkeypatch, new_status):
    _seed(tmp_db, "b", plan_status="draft")
    monkeypatch.setenv(ENV, "gaia-planner")
    with pytest.raises(StateTransitionForbidden):
        upsert_plan("me", "b", content="rewritten", status=new_status, db_path=tmp_db)
    assert get_plan("me", "b", db_path=tmp_db)["status"] == "draft"


@pytest.mark.parametrize("new_status", ["active", "closed"])
def test_planner_cannot_create_a_plan_in_an_advanced_state(tmp_db, monkeypatch, new_status):
    _seed(tmp_db, "b")
    monkeypatch.setenv(ENV, "gaia-planner")
    with pytest.raises(StateTransitionForbidden):
        upsert_plan("me", "b", content="body", status=new_status, db_path=tmp_db)
    assert get_plan("me", "b", db_path=tmp_db) is None


def test_planner_still_authors_plan_content(tmp_db, monkeypatch):
    _seed(tmp_db, "b")
    monkeypatch.setenv(ENV, "gaia-planner")
    created = upsert_plan("me", "b", content="v1", status="draft", db_path=tmp_db)
    assert created["plan_status"] == "draft"
    rewritten = upsert_plan("me", "b", content="v2", status="draft", db_path=tmp_db)
    assert rewritten["action"] == "updated"
    assert get_plan("me", "b", db_path=tmp_db)["content"] == "v2"


def test_planner_save_preserves_the_status_a_curator_set(tmp_db, monkeypatch):
    _seed(tmp_db, "b", plan_status="active")
    monkeypatch.setenv(ENV, "gaia-planner")
    upsert_plan("me", "b", content="v2", status="active", db_path=tmp_db)
    assert get_plan("me", "b", db_path=tmp_db)["status"] == "active"


@pytest.mark.parametrize("who", ["gaia-orchestrator", None])
def test_curator_and_human_still_advance_a_plan_through_a_save(tmp_db, monkeypatch, who):
    _seed(tmp_db, "b", plan_status="draft")
    if who:
        monkeypatch.setenv(ENV, who)
    upsert_plan("me", "b", content="body", status="active", db_path=tmp_db)
    assert get_plan("me", "b", db_path=tmp_db)["status"] == "active"
