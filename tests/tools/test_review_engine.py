#!/usr/bin/env python3
"""
Tests for ReviewEngine.

Validates:
1. List action returns pending updates
2. Approve action transitions and applies
3. Reject action transitions
4. Stats action returns statistics
5. Error handling for invalid actions and missing IDs
"""

import sys
import json
import pytest
from pathlib import Path

# Add tools to path
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from review.review_engine import review_pending
from context.pending_updates import PendingUpdateStore, DiscoveryResult


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def store(tmp_path):
    """Create a PendingUpdateStore with temporary directory."""
    return PendingUpdateStore(base_path=tmp_path / "pending-updates")


@pytest.fixture
def sample_context(tmp_path):
    """Create a sample project-context.json."""
    context = {
        "metadata": {
            "version": "1.0.0",
            "last_updated": "2026-01-01T00:00:00Z",
        },
        "sections": {
            "project_details": {"id": "test"},
            "application_services": {},
        },
    }
    path = tmp_path / "project-context.json"
    path.write_text(json.dumps(context, indent=2))
    return path


@pytest.fixture
def populated_store(store):
    """Create a store with some pending updates."""
    d1 = DiscoveryResult(
        category="configuration_issue",
        target_section="project_details",
        proposed_change={"fix": "value1"},
        summary="Config issue 1",
        confidence=0.85,
        source_agent="cloud-troubleshooter",
    )
    d2 = DiscoveryResult(
        category="new_resource",
        target_section="application_services",
        proposed_change={"name": "new-svc"},
        summary="New service found",
        confidence=0.8,
        source_agent="gitops-operator",
    )
    store.create(d1)
    store.create(d2)
    return store


# ============================================================================
# Test List Action
# ============================================================================

class TestListAction:
    """Test the list action."""

    def test_list_empty(self, store):
        result = review_pending("list", store=store)
        assert result["action"] == "list"
        assert result["count"] == 0
        assert result["updates"] == []

    def test_list_with_updates(self, populated_store):
        result = review_pending("list", store=populated_store)
        assert result["count"] == 2
        assert len(result["updates"]) == 2


# ============================================================================
# Test Approve Action
# ============================================================================

class TestApproveAction:
    """Test the approve action."""

    def test_approve_with_context(self, populated_store, sample_context):
        updates = populated_store.list_pending()
        update_id = updates[0].update_id
        result = review_pending(
            "approve",
            update_id=update_id,
            context_path=sample_context,
            store=populated_store,
        )
        assert result["action"] == "approve"
        assert result["update_id"] == update_id
        assert result["applied"] is True

    def test_approve_without_id_raises(self, populated_store):
        with pytest.raises(ValueError, match="update_id"):
            review_pending("approve", store=populated_store)


# ============================================================================
# Test Reject Action
# ============================================================================

class TestRejectAction:
    """Test the reject action."""

    def test_reject(self, populated_store):
        updates = populated_store.list_pending()
        update_id = updates[0].update_id
        result = review_pending("reject", update_id=update_id, store=populated_store)
        assert result["action"] == "reject"
        assert result["success"] is True

    def test_reject_without_id_raises(self, populated_store):
        with pytest.raises(ValueError, match="update_id"):
            review_pending("reject", store=populated_store)


# ============================================================================
# Test Stats Action
# ============================================================================

class TestStatsAction:
    """Test the stats action."""

    def test_stats(self, populated_store):
        result = review_pending("stats", store=populated_store)
        assert result["action"] == "stats"
        assert "statistics" in result
        assert result["statistics"]["total_count"] == 2
        assert result["statistics"]["pending_count"] == 2


# ============================================================================
# Test Error Handling
# ============================================================================

class TestErrorHandling:
    """Test error handling."""

    def test_invalid_action(self, store):
        with pytest.raises(ValueError, match="Invalid action"):
            review_pending("invalid", store=store)


# ============================================================================
# Test E2E: Approve and Apply
# ============================================================================

class TestApproveApplyE2E:
    """Integration test: approve and apply E2E (quickstart scenario 4)."""

    def test_approve_updates_context_file(self, populated_store, sample_context):
        updates = populated_store.list_pending()
        # Find the config issue update (targets project_details)
        config_update = next(u for u in updates if u.target_section == "project_details")

        result = review_pending(
            "approve",
            update_id=config_update.update_id,
            context_path=sample_context,
            store=populated_store,
        )
        assert result["applied"] is True

        # Verify context was modified
        with open(sample_context) as f:
            ctx = json.load(f)
        assert "fix" in ctx["sections"]["project_details"]

    def test_reject_does_not_modify_context(self, populated_store, sample_context):
        # Read original context
        original = json.loads(sample_context.read_text())

        updates = populated_store.list_pending()
        update_id = updates[0].update_id
        review_pending("reject", update_id=update_id, store=populated_store)

        # Context should be unchanged
        current = json.loads(sample_context.read_text())
        assert current == original
