"""
The dispatch project travels FROM the dispatch, not the cwd.

Live finding: dispatch_project landed NULL on every real dispatch because the
orchestrator dispatches from the workspace root, so cwd-based resolution never
matched a project. The fix: a ``project=<name>`` token in the dispatch prompt
(same channel as task_id=/plan_id=) is extracted at birth and resolved by NAME
against project_identity; cwd resolution survives only as fallback.

Two seams under test:
  1. ``extract_dispatch_binding`` -- the token is extracted, and its presence
     alone never reclassifies a free dispatch as task_execution.
  2. ``resolve_project_by_name`` -- a known name resolves to "name (path)",
     an unknown name passes through verbatim (the orchestrator's assertion is
     dispatch data), and an absent name resolves to None.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
_TOOLS_DIR = str(_REPO_ROOT / "tools")
for _p in (_HOOKS_DIR, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.agents.dispatch_binding import extract_dispatch_binding  # noqa: E402
from tools.context import context_provider  # noqa: E402


# ---------------------------------------------------------------------------
# (1) token extraction
# ---------------------------------------------------------------------------

def test_project_token_extracted():
    binding = extract_dispatch_binding({
        "prompt": "Investigate the failing build. project=branchkinect",
        "subagent_type": "developer",
    })
    assert binding["project"] == "branchkinect"


def test_project_token_absent_is_none():
    binding = extract_dispatch_binding({
        "prompt": "Investigate the failing build.",
        "subagent_type": "developer",
    })
    assert binding["project"] is None


def test_project_token_alone_keeps_free_kind():
    """project= is dispatch data, never a binding coordinate: its presence
    must not flip a free dispatch into task_execution semantics."""
    binding = extract_dispatch_binding({
        "prompt": "Look around. project=gaia",
        "subagent_type": "developer",
    })
    assert binding["kind"] == "investigation"
    assert binding["plan_task_id"] is None


def test_project_token_coexists_with_task_binding():
    binding = extract_dispatch_binding({
        "prompt": "Execute task_id=43 plan_id=34 project=gaia",
        "subagent_type": "developer",
    })
    assert binding["project"] == "gaia"
    assert binding["plan_task_id"] == 43
    assert binding["kind"] == "task_execution"


# ---------------------------------------------------------------------------
# (2) name resolution against project_identity
# ---------------------------------------------------------------------------

_IDENTITY = {
    "sections": {
        "project_identity": {
            "gaia": {"name": "gaia", "local_path": "/home/u/ws/me/gaia"},
            "branchkinect": {
                "name": "BranchKinect",
                "local_path": "/home/u/ws/me/branchkinect",
            },
            "pathless": {"name": "pathless"},
        }
    }
}


def _patch_identity(monkeypatch):
    monkeypatch.setattr(
        context_provider, "load_project_context",
        lambda workspace, db_path=None: _IDENTITY,
    )


def test_known_name_resolves_to_name_and_path(monkeypatch):
    _patch_identity(monkeypatch)
    assert context_provider.resolve_project_by_name("me", "gaia") == (
        "gaia (/home/u/ws/me/gaia)"
    )


def test_name_match_is_case_insensitive_and_keeps_entry_casing(monkeypatch):
    _patch_identity(monkeypatch)
    assert context_provider.resolve_project_by_name("me", "branchkinect") == (
        "BranchKinect (/home/u/ws/me/branchkinect)"
    )


def test_known_name_without_path_resolves_bare(monkeypatch):
    _patch_identity(monkeypatch)
    assert context_provider.resolve_project_by_name("me", "pathless") == "pathless"


def test_unknown_name_passes_through_verbatim(monkeypatch):
    _patch_identity(monkeypatch)
    assert context_provider.resolve_project_by_name("me", "brand-new") == "brand-new"


def test_absent_name_is_none(monkeypatch):
    _patch_identity(monkeypatch)
    assert context_provider.resolve_project_by_name("me", None) is None
    assert context_provider.resolve_project_by_name("me", "  ") is None
    assert context_provider.resolve_project_by_name("", "gaia") is None
