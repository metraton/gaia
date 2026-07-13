#!/usr/bin/env python3
"""AC-3: upsert_brief / upsert_plan consult authorization before mutating CONTENT.

Under the (now live) planner protocol, brief content is authored by the
orchestrator and plan content by the planner. An unauthorized dispatched agent
that tries to mutate brief/plan content is BLOCKED; the authorized flows
(planner for plans, orchestrator via brief-spec for briefs) and a human CLI call
(no dispatch identity) pass.

The guard function is exercised directly (all identity combinations) and
end-to-end through upsert_brief / upsert_plan against a bootstrapped DB.
"""

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from gaia.state.permissions import (
    _assert_dispatch_can_write_content,
    ContentWriteForbidden,
)
from gaia.briefs.store import upsert_brief
from gaia.store.writer import upsert_plan

ENV = "GAIA_DISPATCH_AGENT"


# ---------------------------------------------------------------------------
# Fixture: bootstrapped DB (mirrors tests/test_writer_plan_brief_invariants.py)
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    bootstrap = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
    db_path = tmp_path / "gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(bootstrap)],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, (
        f"bootstrap failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(ENV, raising=False)
    return db_path


# ---------------------------------------------------------------------------
# Guard function -- briefs
# ---------------------------------------------------------------------------

def test_brief_unset_allowed(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _assert_dispatch_can_write_content("briefs")  # no raise


@pytest.mark.parametrize("agent", ["orchestrator", "gaia-orchestrator", "operator", "gaia-operator"])
def test_brief_orchestrator_allowed(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    _assert_dispatch_can_write_content("briefs")  # no raise


@pytest.mark.parametrize("agent", ["developer", "gaia-planner", "planner", "gaia-system"])
def test_brief_unauthorized_blocked(monkeypatch, agent):
    """Even the planner may NOT author brief content -- briefs are the
    orchestrator's."""
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(ContentWriteForbidden):
        _assert_dispatch_can_write_content("briefs")


# ---------------------------------------------------------------------------
# Guard function -- plans
# ---------------------------------------------------------------------------

def test_plan_unset_allowed(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    _assert_dispatch_can_write_content("plans")  # no raise


@pytest.mark.parametrize("agent", ["planner", "gaia-planner", "orchestrator", "gaia-orchestrator"])
def test_plan_authorized_allowed(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    _assert_dispatch_can_write_content("plans")  # no raise


@pytest.mark.parametrize("agent", ["developer", "gitops-operator", "gaia-system", "platform-architect"])
def test_plan_unauthorized_blocked(monkeypatch, agent):
    monkeypatch.setenv(ENV, agent)
    with pytest.raises(ContentWriteForbidden):
        _assert_dispatch_can_write_content("plans")


# ---------------------------------------------------------------------------
# End-to-end through upsert_brief
# ---------------------------------------------------------------------------

_BRIEF_FIELDS = {"title": "T", "objective": "O", "status": "draft"}


def test_upsert_brief_blocked_before_db(monkeypatch):
    """A blocked subagent never reaches persistence (guard runs before _connect)."""
    monkeypatch.setenv(ENV, "developer")
    with pytest.raises(ContentWriteForbidden):
        upsert_brief("me", "gate-brief", _BRIEF_FIELDS,
                     db_path=Path("/nonexistent/should-not-be-touched.db"))


def test_upsert_brief_orchestrator_authored(tmp_db, monkeypatch):
    monkeypatch.setenv(ENV, "gaia-orchestrator")
    res = upsert_brief("me", "authored-brief", _BRIEF_FIELDS, db_path=tmp_db)
    assert res["status"] == "applied"


def test_upsert_brief_human_cli(tmp_db, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    res = upsert_brief("me", "human-brief", _BRIEF_FIELDS, db_path=tmp_db)
    assert res["status"] == "applied"


# ---------------------------------------------------------------------------
# End-to-end through upsert_plan
# ---------------------------------------------------------------------------

def _seed_brief(db_path, name, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    upsert_brief("me", name, _BRIEF_FIELDS, db_path=db_path)


def test_upsert_plan_planner_authored(tmp_db, monkeypatch):
    _seed_brief(tmp_db, "plan-brief", monkeypatch)
    monkeypatch.setenv(ENV, "gaia-planner")
    res = upsert_plan("me", "plan-brief", content="plan body", db_path=tmp_db)
    assert res["status"] == "applied"


def test_upsert_plan_blocked_for_developer(tmp_db, monkeypatch):
    _seed_brief(tmp_db, "plan-brief2", monkeypatch)
    monkeypatch.setenv(ENV, "developer")
    with pytest.raises(ContentWriteForbidden):
        upsert_plan("me", "plan-brief2", content="unauthorized", db_path=tmp_db)


def test_upsert_plan_human_cli(tmp_db, monkeypatch):
    _seed_brief(tmp_db, "plan-brief3", monkeypatch)
    monkeypatch.delenv(ENV, raising=False)
    res = upsert_plan("me", "plan-brief3", content="human plan", db_path=tmp_db)
    assert res["status"] == "applied"
