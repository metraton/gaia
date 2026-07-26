"""Tests for what build_project_context puts in an agent's injected context.

Two independent properties are measured here:

* **Anchor telemetry** -- build_project_context() no longer saves anchors
  itself: at PreToolUse:Task dispatch time the host has not yet assigned this
  dispatch its agent_id (see anchor_tracker.py's module docstring), so the
  anchors it extracts here travel forward -- via the telemetry snapshot this
  function returns -- to whichever caller reaches SubagentStart, where
  agent_id becomes available and the caller can finally call save_anchors()
  with the full (session_id, agent_type, agent_id) key.
* **Workspace isolation** -- the context built for one workspace must carry
  that workspace's own identifiers verbatim and must not leak a sibling
  workspace's. See TestBuildProjectContextWorkspaceIsolation for why this
  lives here rather than in the LLM eval catalog.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

import modules.context.context_injector as context_injector

from tests.fixtures.db_helpers import (
    bootstrap_gaia_schema,
    seed_agent_perms,
    seed_workspace,
    seed_workspace_contracts,
)


@pytest.fixture
def stub_context_payload(monkeypatch):
    """Stub build_context_payload with a fixed payload carrying one anchor."""
    payload = {
        "project_knowledge": {
            "terraform_infrastructure": {
                "layout": {"base_path": "./qxo-monorepo/terraform"},
            },
        },
        "metadata": {},
        "write_permissions": {},
        "agent_contract_handoff": {},
        "surface_routing": {},
        "historical_context": {},
    }

    class _FakeModule:
        @staticmethod
        def build_context_payload(agent_name, user_task):
            return payload

    monkeypatch.setitem(sys.modules, "tools.context.context_provider", _FakeModule())
    return payload


@pytest.fixture
def stub_reminder_and_events(monkeypatch):
    """Avoid touching gaia.db from these unit tests -- neutral no-op stubs."""
    monkeypatch.setattr(
        context_injector, "build_context_update_reminder", lambda *a, **k: ""
    )


class TestBuildProjectContextAnchorTelemetry:
    """Anchors extracted during context build must surface in the returned
    telemetry snapshot -- the handoff to the caller's own save-at-SubagentStart
    step -- and build_project_context itself must never call save_anchors."""

    def test_anchors_surface_in_telemetry(
        self, stub_context_payload, stub_reminder_and_events,
    ):
        _context_text, telemetry = context_injector.build_project_context(
            {"subagent_type": "platform-architect", "prompt": "investigate terraform"},
            ["platform-architect"],
        )

        assert "qxo-monorepo/terraform" in telemetry.get("anchors", []), (
            "build_project_context must carry extracted anchors forward in "
            "its telemetry snapshot so the caller can save them once "
            "agent_id is known (at SubagentStart), instead of saving them "
            "itself here where agent_id does not yet exist."
        )

    def test_does_not_call_save_anchors_itself(
        self, stub_context_payload, stub_reminder_and_events, monkeypatch,
    ):
        """build_project_context must not import/call save_anchors directly
        -- that call now lives at SubagentStart, the only place agent_id is
        available. A regression here would resurrect the two-part key."""
        assert not hasattr(context_injector, "save_anchors"), (
            "context_injector must not import save_anchors: saving anchors "
            "here (before agent_id exists) is exactly the bug this rekey fixes."
        )

    def test_no_context_payload_yields_no_anchors(self, monkeypatch):
        """A non-project agent (context build skipped) yields no telemetry
        and therefore no anchors -- nothing to carry forward."""
        context_text, telemetry = context_injector.build_project_context(
            {"subagent_type": "not-a-project-agent", "prompt": "irrelevant"},
            ["platform-architect"],
        )
        assert context_text is None
        assert telemetry == {}


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------

# The personal workspace's own git remote, verbatim. The whole point of the
# assertion below is that the FULL literal survives the trip from
# project_context_contracts through build_context_payload's section filter to
# the rendered context string -- a substring like "metraton" would also match
# a context that named the wrong repository under the right org.
PERSONAL_REMOTE = "git@github.com:metraton/gaia.git"

# A token that appears only in the OTHER workspace's rows. Its absence is the
# leak assertion: nothing workspace-scoped to `work` may reach a `me` dispatch.
WORK_ONLY_TOKEN = "aaxisdigital"


@pytest.fixture
def two_workspace_db(tmp_path, monkeypatch):
    """A gaia.db carrying two workspaces with disjoint project identities.

    `me` holds the personal repo (PERSONAL_REMOTE); `work` holds a client repo
    whose remote carries WORK_ONLY_TOKEN. Both are readable rows in the SAME
    database file -- isolation must come from the workspace-scoped query, not
    from the two never coexisting.
    """
    db_path = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db_path)

    seed_workspace(db_path, "me")
    seed_workspace(db_path, "work")
    seed_workspace_contracts(db_path, "me", {
        "project_identity": {
            "gaia": {
                "name": "gaia",
                "local_path": "/home/jorge/ws/me/gaia",
                "remote_url": PERSONAL_REMOTE,
                "platform": "github",
            },
        },
    })
    seed_workspace_contracts(db_path, "work", {
        "project_identity": {
            "bildwiz": {
                "name": "bildwiz",
                "local_path": "/home/jorge/ws/work/bildwiz",
                "remote_url": f"https://bitbucket.org/{WORK_ONLY_TOKEN}/bildwiz.git",
                "platform": "bitbucket",
            },
        },
    })
    seed_agent_perms(db_path, "developer", reads=["project_identity"], writes=[])

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return db_path


@pytest.fixture
def current_workspace_me(monkeypatch):
    """Pin gaia.project.current() to `me`.

    Workspace identity is path-based (basename of the repo root), so under
    pytest it would otherwise resolve to whatever checkout the suite runs
    from. Pinning it makes the workspace under test an input rather than a
    property of the runner's cwd.
    """
    import gaia.project

    monkeypatch.setattr(gaia.project, "current", lambda: "me")


class TestBuildProjectContextWorkspaceIsolation:
    """The injected context carries this workspace's identifiers and no other's.

    This is the deterministic half of what the S1 eval case
    ("which remote do I push my personal repo to?") was reaching for. Asking a
    live agent measures two things at once: whether the context CONTAINED the
    right remote, and whether the agent then quoted it. Only the second needs
    an LLM. The first is a pure function of the DB and the section filter, so
    it belongs here -- it costs no tokens, it cannot be satisfied by a lucky
    paraphrase, and until now nothing measured it at all.
    """

    def test_personal_workspace_context_carries_its_own_remote(
        self, two_workspace_db, current_workspace_me, stub_reminder_and_events,
    ):
        context_text, _telemetry = context_injector.build_project_context(
            {"subagent_type": "developer", "prompt": "push to my personal repo"},
            ["developer"],
        )

        assert context_text is not None, (
            "no context was built for a project agent with a readable "
            "project_identity section -- the section filter or the workspace "
            "query dropped it"
        )
        assert PERSONAL_REMOTE in context_text, (
            f"the injected context must carry {PERSONAL_REMOTE!r} verbatim; "
            "an agent cannot report a remote the context never named"
        )

    def test_sibling_workspace_identifiers_never_leak(
        self, two_workspace_db, current_workspace_me, stub_reminder_and_events,
    ):
        """The `work` rows sit in the same DB file and must stay invisible.

        A regression that dropped the `WHERE workspace = ?` predicate, or that
        merged sections across workspaces, would surface the client remote in
        a personal-workspace dispatch. That is the failure this asserts
        against -- not a keyword the agent happened not to type.
        """
        context_text, _telemetry = context_injector.build_project_context(
            {"subagent_type": "developer", "prompt": "push to my personal repo"},
            ["developer"],
        )

        assert context_text is not None
        assert WORK_ONLY_TOKEN not in context_text, (
            f"{WORK_ONLY_TOKEN!r} belongs to the `work` workspace only; its "
            "presence in a `me` dispatch means workspace scoping leaked"
        )

    def test_the_other_workspace_really_is_in_the_same_db(self, two_workspace_db):
        """Guard the guard: the leak test above is only meaningful while the
        `work` rows actually exist. If the fixture ever stopped seeding them,
        the absence assertion would pass vacuously.
        """
        con = sqlite3.connect(str(two_workspace_db))
        rows = con.execute(
            "SELECT payload FROM project_context_contracts WHERE workspace = 'work'"
        ).fetchall()
        con.close()

        assert rows, "fixture rotted: no `work` rows to leak from"
        assert any(WORK_ONLY_TOKEN in json.loads(r[0]).get("bildwiz", {}).get("remote_url", "")
                   for r in rows)
