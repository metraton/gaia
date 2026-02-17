#!/usr/bin/env python3
"""
Tests for PendingUpdateStore and related data classes.

Validates:
1. Data class creation and validation
2. PendingUpdateStore CRUD operations
3. Deduplication logic
4. Status transitions (approve, reject, apply)
5. Statistics and counting
6. Edge cases and error handling
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

# Add tools to path
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from context.pending_updates import (
    PendingUpdate,
    PendingUpdateStore,
    DiscoveryResult,
    DiscoveryCategory,
    UpdateStatus,
    CATEGORY_TO_SECTIONS,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def store(tmp_path):
    """Create a PendingUpdateStore with temporary directory."""
    return PendingUpdateStore(base_path=tmp_path / "pending-updates")


@pytest.fixture
def sample_discovery():
    """Create a sample DiscoveryResult."""
    return DiscoveryResult(
        category="configuration_issue",
        target_section="project_details",
        proposed_change={"wi_binding": {"project": "oci-pos-dev-471216"}},
        summary="WI binding references wrong project ID",
        confidence=0.85,
        source_agent="cloud-troubleshooter",
        source_task="investigate WI binding issues",
        source_episode_id="ep_test_001",
    )


@pytest.fixture
def sample_context(tmp_path):
    """Create a sample project-context.json file."""
    context = {
        "metadata": {
            "version": "1.0.0",
            "last_updated": "2026-01-01T00:00:00Z",
            "project_name": "Test Project",
        },
        "sections": {
            "project_details": {
                "id": "test-project",
                "region": "us-central1",
            },
            "application_services": [],
            "cluster_details": {},
            "infrastructure_topology": {},
        },
    }
    context_path = tmp_path / "project-context.json"
    context_path.write_text(json.dumps(context, indent=2))
    return context_path


# ============================================================================
# Test Data Classes
# ============================================================================

class TestDiscoveryCategory:
    """Test DiscoveryCategory enum."""

    def test_all_categories_exist(self):
        assert DiscoveryCategory.NEW_RESOURCE == "new_resource"
        assert DiscoveryCategory.CONFIGURATION_ISSUE == "configuration_issue"
        assert DiscoveryCategory.DRIFT_DETECTED == "drift_detected"
        assert DiscoveryCategory.DEPENDENCY_DISCOVERED == "dependency_discovered"
        assert DiscoveryCategory.TOPOLOGY_CHANGE == "topology_change"

    def test_category_count(self):
        assert len(DiscoveryCategory) == 5


class TestUpdateStatus:
    """Test UpdateStatus enum."""

    def test_all_statuses_exist(self):
        assert UpdateStatus.PENDING == "pending"
        assert UpdateStatus.APPROVED == "approved"
        assert UpdateStatus.REJECTED == "rejected"
        assert UpdateStatus.APPLIED == "applied"

    def test_status_count(self):
        assert len(UpdateStatus) == 4


class TestDiscoveryResult:
    """Test DiscoveryResult dataclass."""

    def test_creation_with_required_fields(self):
        result = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "new-service"},
            summary="Found new service",
            confidence=0.8,
            source_agent="cloud-troubleshooter",
        )
        assert result.category == "new_resource"
        assert result.source_task == ""
        assert result.source_episode_id == ""

    def test_creation_with_all_fields(self, sample_discovery):
        assert sample_discovery.source_task == "investigate WI binding issues"
        assert sample_discovery.source_episode_id == "ep_test_001"


class TestCategoryToSections:
    """Test CATEGORY_TO_SECTIONS mapping."""

    def test_all_categories_mapped(self):
        for cat in DiscoveryCategory:
            assert cat.value in CATEGORY_TO_SECTIONS

    def test_sections_are_lists(self):
        for sections in CATEGORY_TO_SECTIONS.values():
            assert isinstance(sections, list)
            assert len(sections) > 0


# ============================================================================
# Test PendingUpdateStore - Directory Setup
# ============================================================================

class TestStoreSetup:
    """Test store initialization and directory creation."""

    def test_creates_base_directory(self, tmp_path):
        base = tmp_path / "new-store"
        PendingUpdateStore(base_path=base)
        assert base.exists()

    def test_creates_applied_directory(self, tmp_path):
        base = tmp_path / "new-store"
        PendingUpdateStore(base_path=base)
        assert (base / "applied").exists()

    def test_creates_empty_index(self, store):
        index_path = store.base_path / "pending-index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert index["total_count"] == 0
        assert index["pending_count"] == 0
        assert index["updates"] == {}
        assert index["hash_index"] == {}


# ============================================================================
# Test PendingUpdateStore.create
# ============================================================================

class TestStoreCreate:
    """Test creating pending updates."""

    def test_create_returns_update_id(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        assert update_id.startswith("pu_")
        assert len(update_id) > 10

    def test_create_writes_to_jsonl(self, store, sample_discovery):
        store.create(sample_discovery)
        jsonl_path = store.base_path / "pending-updates.jsonl"
        assert jsonl_path.exists()
        with open(jsonl_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "created"

    def test_create_updates_index(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        index = json.loads((store.base_path / "pending-index.json").read_text())
        assert index["total_count"] == 1
        assert index["pending_count"] == 1
        assert update_id in index["updates"]
        assert index["updates"][update_id]["status"] == "pending"

    def test_create_validates_low_confidence(self, store):
        discovery = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "test"},
            summary="Low confidence",
            confidence=0.5,
            source_agent="cloud-troubleshooter",
        )
        with pytest.raises(ValueError, match="Invalid discovery"):
            store.create(discovery)

    def test_create_validates_invalid_category(self, store):
        discovery = DiscoveryResult(
            category="invalid_category",
            target_section="application_services",
            proposed_change={"name": "test"},
            summary="Invalid category",
            confidence=0.8,
            source_agent="cloud-troubleshooter",
        )
        with pytest.raises(ValueError, match="Invalid discovery"):
            store.create(discovery)

    def test_create_validates_invalid_target_section(self, store):
        discovery = DiscoveryResult(
            category="new_resource",
            target_section="nonexistent_section",
            proposed_change={"name": "test"},
            summary="Invalid section",
            confidence=0.8,
            source_agent="cloud-troubleshooter",
        )
        with pytest.raises(ValueError, match="Invalid discovery"):
            store.create(discovery)


# ============================================================================
# Test PendingUpdateStore - Deduplication
# ============================================================================

class TestStoreDedup:
    """Test deduplication logic."""

    def test_same_content_returns_same_id(self, store, sample_discovery):
        id1 = store.create(sample_discovery)
        id2 = store.create(sample_discovery)
        assert id1 == id2

    def test_dedup_increments_seen_count(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        store.create(sample_discovery)
        update = store.get(update_id)
        assert update.seen_count == 2

    def test_dedup_tracks_different_agents(self, store):
        d1 = DiscoveryResult(
            category="configuration_issue",
            target_section="project_details",
            proposed_change={"key": "value"},
            summary="Same discovery",
            confidence=0.85,
            source_agent="cloud-troubleshooter",
        )
        d2 = DiscoveryResult(
            category="configuration_issue",
            target_section="project_details",
            proposed_change={"key": "value"},
            summary="Same discovery",
            confidence=0.9,
            source_agent="terraform-architect",
        )
        update_id = store.create(d1)
        store.create(d2)
        update = store.get(update_id)
        assert "cloud-troubleshooter" in update.seen_by_agents
        assert "terraform-architect" in update.seen_by_agents

    def test_dedup_writes_increment_event(self, store, sample_discovery):
        store.create(sample_discovery)
        store.create(sample_discovery)
        jsonl_path = store.base_path / "pending-updates.jsonl"
        with open(jsonl_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        event = json.loads(lines[1])
        assert event["event"] == "dedup_increment"

    def test_different_section_not_deduped(self, store):
        d1 = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "svc"},
            summary="Service in apps",
            confidence=0.8,
            source_agent="cloud-troubleshooter",
        )
        d2 = DiscoveryResult(
            category="new_resource",
            target_section="cluster_details",
            proposed_change={"name": "svc"},
            summary="Service in cluster",
            confidence=0.8,
            source_agent="cloud-troubleshooter",
        )
        id1 = store.create(d1)
        id2 = store.create(d2)
        assert id1 != id2


# ============================================================================
# Test PendingUpdateStore.get
# ============================================================================

class TestStoreGet:
    """Test retrieving updates."""

    def test_get_existing(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        update = store.get(update_id)
        assert update is not None
        assert update.update_id == update_id
        assert update.summary == "WI binding references wrong project ID"
        assert update.status == "pending"

    def test_get_nonexistent(self, store):
        result = store.get("pu_nonexistent")
        assert result is None


# ============================================================================
# Test PendingUpdateStore.list
# ============================================================================

class TestStoreList:
    """Test listing updates."""

    def test_list_pending_empty(self, store):
        result = store.list_pending()
        assert result == []

    def test_list_pending_returns_only_pending(self, store, sample_discovery):
        store.create(sample_discovery)
        d2 = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "new-svc"},
            summary="New service found",
            confidence=0.8,
            source_agent="gitops-operator",
        )
        id2 = store.create(d2)
        store.approve(id2)
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].category == "configuration_issue"

    def test_list_all_no_filter(self, store, sample_discovery):
        store.create(sample_discovery)
        d2 = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "svc"},
            summary="Service found",
            confidence=0.8,
            source_agent="gitops-operator",
        )
        store.create(d2)
        result = store.list_all()
        assert len(result) == 2

    def test_list_all_with_status_filter(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        approved = store.list_all(status="approved")
        assert len(approved) == 1
        assert approved[0].status == "approved"


# ============================================================================
# Test PendingUpdateStore.approve / reject
# ============================================================================

class TestStoreStatusTransitions:
    """Test status transitions."""

    def test_approve_pending(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        result = store.approve(update_id)
        assert result.status == "approved"

    def test_approve_non_pending_raises(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        with pytest.raises(ValueError, match="pending"):
            store.approve(update_id)

    def test_reject_pending(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        result = store.reject(update_id)
        assert result.status == "rejected"

    def test_reject_non_pending_raises(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        store.reject(update_id)
        with pytest.raises(ValueError, match="pending"):
            store.reject(update_id)

    def test_status_change_logged_to_jsonl(self, store, sample_discovery):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        jsonl_path = store.base_path / "pending-updates.jsonl"
        with open(jsonl_path) as f:
            lines = f.readlines()
        # Should have: created + status_change
        assert len(lines) == 2
        event = json.loads(lines[1])
        assert event["event"] == "status_change"
        assert event["old_status"] == "pending"
        assert event["new_status"] == "approved"


# ============================================================================
# Test PendingUpdateStore.apply
# ============================================================================

class TestStoreApply:
    """Test applying approved updates to project-context.json."""

    def test_apply_updates_context(self, store, sample_discovery, sample_context):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        result = store.apply(update_id, context_path=sample_context)
        assert result["success"] is True
        # Verify project-context.json was updated
        with open(sample_context) as f:
            ctx = json.load(f)
        assert ctx["sections"]["project_details"]["wi_binding"] == {"project": "oci-pos-dev-471216"}

    def test_apply_creates_backup(self, store, sample_discovery, sample_context):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        result = store.apply(update_id, context_path=sample_context)
        assert result["backup_path"] is not None
        assert Path(result["backup_path"]).exists()

    def test_apply_updates_metadata_timestamp(self, store, sample_discovery, sample_context):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        store.apply(update_id, context_path=sample_context)
        with open(sample_context) as f:
            ctx = json.load(f)
        assert ctx["metadata"]["last_updated"] != "2026-01-01T00:00:00Z"

    def test_apply_non_approved_raises(self, store, sample_discovery, sample_context):
        update_id = store.create(sample_discovery)
        with pytest.raises(ValueError, match="approved"):
            store.apply(update_id, context_path=sample_context)

    def test_apply_marks_as_applied(self, store, sample_discovery, sample_context):
        update_id = store.create(sample_discovery)
        store.approve(update_id)
        store.apply(update_id, context_path=sample_context)
        update = store.get(update_id)
        assert update.status == "applied"


# ============================================================================
# Test PendingUpdateStore.get_statistics
# ============================================================================

class TestStoreStatistics:
    """Test statistics computation."""

    def test_empty_statistics(self, store):
        stats = store.get_statistics()
        assert stats["total_count"] == 0
        assert stats["pending_count"] == 0

    def test_statistics_counts(self, store, sample_discovery):
        store.create(sample_discovery)
        d2 = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "svc"},
            summary="New service",
            confidence=0.8,
            source_agent="gitops-operator",
        )
        id2 = store.create(d2)
        store.reject(id2)
        stats = store.get_statistics()
        assert stats["total_count"] == 2
        assert stats["pending_count"] == 1
        assert stats["by_status"].get("rejected", 0) == 1
        assert "configuration_issue" in stats["by_category"]
        assert "cloud-troubleshooter" in stats["by_agent"]

    def test_get_pending_count(self, store, sample_discovery):
        store.create(sample_discovery)
        assert store.get_pending_count() == 1
        d2 = DiscoveryResult(
            category="new_resource",
            target_section="application_services",
            proposed_change={"name": "svc"},
            summary="Service",
            confidence=0.8,
            source_agent="gitops-operator",
        )
        store.create(d2)
        assert store.get_pending_count() == 2


# ============================================================================
# Test Integration: Dedup E2E Scenario
# ============================================================================

class TestDedupScenario:
    """Integration test: deduplication across agents (quickstart scenario 2)."""

    def test_dedup_across_agents(self, store):
        d1 = DiscoveryResult(
            category="configuration_issue",
            target_section="project_details",
            proposed_change={"wi_binding": {"project": "oci-pos-dev-471216"}},
            summary="WI binding references wrong project ID",
            confidence=0.85,
            source_agent="cloud-troubleshooter",
        )
        id1 = store.create(d1)

        d2 = DiscoveryResult(
            category="configuration_issue",
            target_section="project_details",
            proposed_change={"wi_binding": {"project": "oci-pos-dev-471216"}},
            summary="WI binding references wrong project ID",
            confidence=0.9,
            source_agent="terraform-architect",
        )
        id2 = store.create(d2)

        assert id1 == id2
        update = store.get(id1)
        assert update.seen_count == 2
        assert set(update.seen_by_agents) == {"cloud-troubleshooter", "terraform-architect"}
