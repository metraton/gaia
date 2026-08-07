"""M5 end-to-end integration test.

Exercises the full M5 workflow in a single test:
  1. Create a brief via upsert_brief
  2. Add an AC via add_ac
  3. Add a milestone via add_milestone
  4. Add a task to plan via add_task_to_plan
  5. Patch surface_type via update_brief_field
  6. Run verify_brief -- assert pass=True
  7. Remove AC via remove_ac
  8. Run verify_brief again -- assert pass=False (orphan task AC detected)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def bootstrapped_db(tmp_path, monkeypatch, bootstrapped_db_template):
    """Bootstrap a v5 DB in tmp_path (copied from the session template).

    Uses the session-scoped ``bootstrapped_db_template`` and copies it per
    test instead of re-running ``scripts/bootstrap_database.sh`` each time.
    Each test still gets its own independent, mutable DB file -- isolation is
    unchanged.
    """
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "gaia.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return db_path


def test_m5_end_to_end(bootstrapped_db):
    """Full M5 workflow in a single end-to-end test."""
    from gaia.briefs.store import (
        upsert_brief,
        add_ac,
        remove_ac,
        add_milestone,
        verify_brief,
    )
    from gaia.store.writer import (
        upsert_plan,
        add_task_to_plan,
        add_gate_to_task,
        update_brief_field,
    )

    # Step 1: Create a brief
    upsert_brief(
        "me",
        "m5-test-brief",
        {
            "status": "draft",
            "title": "M5 Integration Brief",
            "objective": "Test M5 end-to-end",
            "context": "Initial context",
            "approach": "Step by step",
            "out_of_scope": "Nothing",
            "surface_type": None,
            "topic_key": None,
            "acceptance_criteria": [],
            "milestones": [],
            "dependencies": [],
        },
        db_path=bootstrapped_db,
    )

    # Step 2: Add an AC via granular writer
    add_ac("me", "m5-test-brief", "AC-1",
           description="First AC", db_path=bootstrapped_db)

    # Step 3: Add a milestone via granular writer
    add_milestone("me", "m5-test-brief", "M1",
                  description="Phase 1", db_path=bootstrapped_db)

    # Step 4: Create the plan, then add a task that references AC-1
    upsert_plan("me", "m5-test-brief",
                content="Plan body", status="draft",
                db_path=bootstrapped_db)
    add_task_to_plan("me", "m5-test-brief", 1,
                     "Implement AC-1 logic", db_path=bootstrapped_db)
    # Author a well-formed gate on the task so verify_brief's gate
    # well-formedness invariant (R1-B-1) sees a >=1 valid gate and the
    # end-to-end verify stays clean.
    add_gate_to_task("me", "m5-test-brief", 1, "command",
                     evidence_shape="pytest tests/ -q",
                     db_path=bootstrapped_db)

    # Step 5: Patch the surface_type metadata field
    res = update_brief_field("me", "m5-test-brief", "surface_type",
                             "cli", db_path=bootstrapped_db)
    assert res["status"] == "applied"

    # Step 6: Verify -- should pass (no inconsistencies)
    report = verify_brief("me", "m5-test-brief", db_path=bootstrapped_db)
    assert report["pass"] is True, (
        f"expected clean verify, got inconsistencies: {report['inconsistencies']}"
    )

    # Step 7: Remove AC-1 (the task still references it)
    remove_ac("me", "m5-test-brief", "AC-1", db_path=bootstrapped_db)

    # Step 8: Verify again -- orphan task AC ref should be detected
    report = verify_brief("me", "m5-test-brief", db_path=bootstrapped_db)
    assert report["pass"] is False
    kinds = {i["kind"] for i in report["inconsistencies"]}
    assert "orphan_task_ac_ref" in kinds, (
        f"expected orphan_task_ac_ref, got kinds: {kinds}"
    )
