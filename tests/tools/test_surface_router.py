"""Tests for the DB-backed surface router (surface_router.py).

Routing moved from config/surface-routing.json to the surface_routing table in
gaia.db, seeded from each agent's `routing:` frontmatter block. These tests
build a temp DB seeded from the real agents/ frontmatters (via
seed_surface_routing_from_agents) and point the loader at it through
GAIA_DATA_DIR, so they exercise the exact production path
(frontmatter -> seeder -> DB -> matcher).

Also covers the word-boundary matching fix: signals must match whole tokens,
never as substrings of a larger word ("pod" in "podria", "build" in "rebuild").
The matcher scores commands and artifacts only -- keywords were retired as a
signal source and no agent frontmatter declares them -- so the word-boundary
regression case below exercises "pod" as a live_runtime *artifact*.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))

from surface_router import (  # noqa: E402
    build_investigation_brief,
    classify_surfaces,
    load_surface_routing_config,
    _signal_matches,
)
from tests.fixtures.db_helpers import (  # noqa: E402
    bootstrap_gaia_schema,
    seed_surface_routing_from_agents,
)


@pytest.fixture()
def routing_config(tmp_path, monkeypatch):
    """Seed a temp gaia.db from the real agent frontmatters and load it.

    Points GAIA_DATA_DIR at the temp dir so load_surface_routing_config()
    resolves the seeded DB via gaia.paths, exactly as production does.
    """
    db = tmp_path / "gaia.db"
    bootstrap_gaia_schema(db)
    summary = seed_surface_routing_from_agents(db)
    assert summary["surfaces_seeded"] >= 6, summary
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return load_surface_routing_config()


# ---------------------------------------------------------------------------
# DB-backed config loading
# ---------------------------------------------------------------------------

def test_load_config_from_db(routing_config):
    assert routing_config["version"] == "db"
    assert routing_config["reconnaissance_agent"] == "developer"
    assert "iac" in routing_config["surfaces"]
    assert routing_config["surfaces"]["iac"]["primary_agent"] == "platform-architect"


def test_all_seven_surfaces_present(routing_config):
    surfaces = set(routing_config["surfaces"].keys())
    assert surfaces == {
        "live_runtime",
        "iac",
        "gitops_desired_state",
        "app_ci_tooling",
        "planning_specs",
        "gaia_system",
        "workspace",
    }


def test_contract_sections_mirror_agent_read_contracts(routing_config):
    """contract_sections is the single source of truth = the agent's read list."""
    dev = routing_config["surfaces"]["app_ci_tooling"]["contract_sections"]
    assert "application_services" in dev
    assert "project_identity" in dev


def test_signals_shape_has_no_keywords_key(routing_config):
    """The matcher's signal shape carries commands/artifacts only.

    Keywords were retired as a signal source (no agent frontmatter declares
    them); load_surface_routing_config must not surface a 'keywords' key in
    signals, so downstream consumers cannot accidentally depend on it.
    """
    for surface_name, surface_cfg in routing_config["surfaces"].items():
        signals = surface_cfg["signals"]
        assert set(signals.keys()) == {"commands", "artifacts"}, surface_name


def test_keywords_in_config_are_ignored_by_scoring():
    """Even if a caller hands classify_surfaces a legacy 'keywords' signal,
    scoring must ignore it -- only commands/artifacts contribute score."""
    routing_config = {
        "surfaces": {
            "legacy_surface": {
                "primary_agent": "developer",
                "signals": {"keywords": ["podria"], "commands": [], "artifacts": []},
            },
        },
        "reconnaissance_agent": "developer",
    }
    routing = classify_surfaces(
        "podria ayudarme con esto",
        current_agent="",
        routing_config=routing_config,
    )
    assert "legacy_surface" not in routing["active_surfaces"]


def test_planning_specs_sub_surfaces_edge_case(routing_config):
    """planning_specs carries the brief/plan sub-surface split.

    brief is owned by the orchestrator (via brief-spec skill); plan by
    gaia-planner. The surface primary_agent stays gaia-planner (matcher
    behavior unchanged); the sub_surfaces metadata records the owners.
    """
    ps = routing_config["surfaces"]["planning_specs"]
    assert ps["primary_agent"] == "gaia-planner"
    subs = {s["name"]: s for s in ps["sub_surfaces"]}
    assert subs["brief"]["owner"] == "gaia-orchestrator"
    assert subs["brief"]["owner_skill"] == "brief-spec"
    assert subs["plan"]["owner"] == "gaia-planner"


def test_degraded_config_when_db_absent(tmp_path, monkeypatch):
    """A workspace whose DB has not been seeded degrades to reconnaissance."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "empty"))
    cfg = load_surface_routing_config()
    assert cfg["version"] == "missing"
    assert cfg["surfaces"] == {}
    assert cfg["reconnaissance_agent"] == "developer"


# ---------------------------------------------------------------------------
# Classification (DB-backed)
# ---------------------------------------------------------------------------

def test_classify_single_surface_task(routing_config):
    routing = classify_surfaces(
        "Review terraform state drift in the shared module and IAM policy.",
        current_agent="platform-architect",
        routing_config=routing_config,
    )
    assert routing["primary_surface"] == "iac"
    assert routing["active_surfaces"] == ["iac"]
    assert routing["dispatch_mode"] == "single_surface"
    assert routing["recommended_agents"] == ["platform-architect"]


def test_classify_multi_surface_task(routing_config):
    routing = classify_surfaces(
        "Investigate why the CI pipeline changed the image tag, the deployment "
        "rollout failed, and kubectl logs show runtime errors.",
        current_agent="developer",
        routing_config=routing_config,
    )
    assert routing["multi_surface"] is True
    assert "app_ci_tooling" in routing["active_surfaces"]
    assert "gitops_desired_state" in routing["active_surfaces"]
    assert "live_runtime" in routing["active_surfaces"]
    assert routing["dispatch_mode"] == "parallel"


def test_fallback_to_agent_surface_when_signals_weak(routing_config):
    routing = classify_surfaces(
        "Need a quick look at this task.",
        current_agent="gitops-operator",
        routing_config=routing_config,
    )
    assert routing["active_surfaces"] == ["gitops_desired_state"]
    assert routing["primary_surface"] == "gitops_desired_state"
    assert routing["confidence"] > 0.0


def test_ssh_remote_operations_route_to_live_runtime(routing_config):
    routing = classify_surfaces(
        "ssh into Metra Tower and rsync the project files",
        current_agent="cloud-troubleshooter",
        routing_config=routing_config,
    )
    assert "live_runtime" in routing["active_surfaces"]
    assert routing["primary_surface"] == "live_runtime"
    assert "cloud-troubleshooter" in routing["recommended_agents"]


# ---------------------------------------------------------------------------
# Word-boundary matching (substring-bug fix)
#
# _signal_matches is signal-source-agnostic: it guards commands and artifacts
# identically. These unit cases exercise it directly with representative
# command/artifact strings (kubectl diff, .tf, src/); the regression case at
# the end drives it end-to-end through classify_surfaces() using "pod", which
# is a live_runtime *artifact* in cloud-troubleshooter's routing frontmatter
# (not a keyword -- keywords are retired as a signal source).
# ---------------------------------------------------------------------------

class TestWordBoundaryMatching:
    def test_pod_does_not_match_inside_podria(self):
        assert _signal_matches("pod", "podria funcionar") is False

    def test_pod_matches_whole_word(self):
        assert _signal_matches("pod", "the pod is crashing") is True

    def test_build_does_not_match_inside_rebuild(self):
        assert _signal_matches("build", "we must rebuild it") is False

    def test_test_does_not_match_inside_latest(self):
        assert _signal_matches("test", "the latest release") is False

    def test_multiword_phrase_matches(self):
        assert _signal_matches("infrastructure as code", "define infrastructure as code here") is True

    def test_command_phrase_matches(self):
        assert _signal_matches("kubectl diff", "run kubectl diff now") is True

    def test_extension_artifact_matches_suffix(self):
        # ".tf" ends in an alnum char (guarded) but begins with punctuation
        # (unguarded), so it matches "main.tf" as a filename suffix.
        assert _signal_matches(".tf", "edit main.tf please") is True

    def test_path_artifact_matches(self):
        assert _signal_matches("src/", "look in src/ directory") is True

    def test_podria_task_does_not_route_to_live_runtime(self, routing_config):
        """Regression: 'podria' must not activate live_runtime via the 'pod'
        artifact declared in cloud-troubleshooter's routing frontmatter."""
        routing = classify_surfaces(
            "podria ayudarme con esto",
            current_agent="",
            routing_config=routing_config,
        )
        assert "live_runtime" not in routing["active_surfaces"]


# ---------------------------------------------------------------------------
# Investigation brief (unchanged shape, DB-backed source)
# ---------------------------------------------------------------------------

def test_build_investigation_brief_cross_surface(routing_config):
    contract_context = {
        "project_identity": {},
        "stack": {},
        "git": {},
        "environment": {},
        "application_services": {},
    }
    brief = build_investigation_brief(
        "Investigate why the CI pipeline changed the image tag, the deployment "
        "rollout failed, and kubectl logs show runtime errors.",
        "developer",
        contract_context,
        routing_config=routing_config,
    )
    assert brief["agent_role"] == "primary"
    assert brief["dispatch_mode"] == "parallel"
    assert brief["cross_check_required"] is True
    assert "gitops_desired_state" in brief["adjacent_surfaces"]
    assert "live_runtime" in brief["adjacent_surfaces"]
    assert "COMMANDS_RUN" in brief["evidence_required"]
    assert "OWNERSHIP_ASSESSMENT" in brief["consolidation_fields"]
