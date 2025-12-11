#!/usr/bin/env python3
"""
Tests for task_manager.py.

Validates:
1. Task file operations (mark complete, get pending)
2. Task details extraction
3. Metadata parsing
4. Statistics calculation
5. Edge cases and error handling
"""

import os
import sys
import tempfile
import pytest
from pathlib import Path

# Add task management to path
TASK_MGMT_DIR = Path(__file__).parent.parent.parent / "tools" / "5-task-management"
sys.path.insert(0, str(TASK_MGMT_DIR))

from task_manager import TaskManager


class TestTaskManagerInitialization:
    """Tests for TaskManager initialization."""

    def test_init_with_valid_file(self, tmp_path):
        """Test initialization with valid file path."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n")
        
        tm = TaskManager(str(tasks_file))
        
        assert tm.tasks_file_path == str(tasks_file)

    def test_init_with_nonexistent_file(self, tmp_path):
        """Test initialization fails with nonexistent file."""
        with pytest.raises(FileNotFoundError):
            TaskManager(str(tmp_path / "nonexistent.md"))

    def test_init_stores_absolute_path(self, tmp_path):
        """Test that path is stored as absolute."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n")
        
        # Pass relative-ish path
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            tm = TaskManager("tasks.md")
            assert os.path.isabs(tm.tasks_file_path)
        finally:
            os.chdir(original_cwd)


class TestMarkTaskComplete:
    """Tests for mark_task_complete method."""

    @pytest.fixture
    def sample_tasks_file(self, tmp_path):
        """Create a sample tasks file."""
        tasks_file = tmp_path / "tasks.md"
        content = """# Tasks

## Feature A

- [ ] T001 Create initial configuration
  <!-- 🤖 Agent: terraform-architect | ✅ T2 | ⚡ 0.95 -->
  <!-- 🏷️ Tags: #terraform -->

- [ ] T002 Deploy service
  <!-- 🤖 Agent: gitops-operator | ✅ T3 | ⚡ 0.90 -->
  <!-- 🏷️ Tags: #kubernetes #helm -->

- [x] T003 Already completed task
  <!-- 🤖 Agent: devops-developer | ✅ T1 | ⚡ 0.85 -->

- [ ] T004 Another pending task
  <!-- 🤖 Agent: terraform-architect | ✅ T2 | ⚡ 0.88 -->
"""
        tasks_file.write_text(content)
        return tasks_file

    def test_marks_pending_task_complete(self, sample_tasks_file):
        """Test marking a pending task as complete."""
        tm = TaskManager(str(sample_tasks_file))
        
        result = tm.mark_task_complete("T001")
        
        assert result is True
        
        # Verify file was updated
        content = sample_tasks_file.read_text()
        assert "- [x] T001" in content

    def test_returns_false_for_already_complete(self, sample_tasks_file):
        """Test returns False for already completed task."""
        tm = TaskManager(str(sample_tasks_file))
        
        result = tm.mark_task_complete("T003")
        
        assert result is False

    def test_raises_for_nonexistent_task(self, sample_tasks_file):
        """Test raises ValueError for nonexistent task."""
        tm = TaskManager(str(sample_tasks_file))
        
        with pytest.raises(ValueError) as exc_info:
            tm.mark_task_complete("T999")
        
        assert "not found" in str(exc_info.value).lower()

    def test_validates_task_id_format(self, sample_tasks_file):
        """Test validates task ID format."""
        tm = TaskManager(str(sample_tasks_file))
        
        with pytest.raises(ValueError) as exc_info:
            tm.mark_task_complete("invalid")
        
        assert "invalid" in str(exc_info.value).lower()

    def test_handles_lowercase_task_id(self, sample_tasks_file):
        """Test handles lowercase task ID (converts to uppercase)."""
        tm = TaskManager(str(sample_tasks_file))
        
        result = tm.mark_task_complete("t001")
        
        assert result is True


class TestGetPendingTasks:
    """Tests for get_pending_tasks method."""

    @pytest.fixture
    def sample_tasks_file(self, tmp_path):
        """Create a sample tasks file with multiple tasks."""
        tasks_file = tmp_path / "tasks.md"
        content = """# Tasks

- [ ] T001 First pending task
- [ ] T002 Second pending task
- [x] T003 Completed task
- [ ] T004 Third pending task
- [ ] T005 Fourth pending task
- [ ] T006 Fifth pending task
"""
        tasks_file.write_text(content)
        return tasks_file

    def test_gets_pending_tasks(self, sample_tasks_file):
        """Test getting list of pending tasks."""
        tm = TaskManager(str(sample_tasks_file))
        
        pending = tm.get_pending_tasks()
        
        assert len(pending) == 5
        assert pending[0]["task_id"] == "T001"

    def test_respects_limit(self, sample_tasks_file):
        """Test limit parameter."""
        tm = TaskManager(str(sample_tasks_file))
        
        pending = tm.get_pending_tasks(limit=3)
        
        assert len(pending) == 3

    def test_returns_empty_when_no_pending(self, tmp_path):
        """Test returns empty list when no pending tasks."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [x] T001 Completed\n")
        
        tm = TaskManager(str(tasks_file))
        pending = tm.get_pending_tasks()
        
        assert len(pending) == 0

    def test_includes_line_numbers(self, sample_tasks_file):
        """Test that line numbers are included."""
        tm = TaskManager(str(sample_tasks_file))
        
        pending = tm.get_pending_tasks(limit=1)
        
        assert "line_number" in pending[0]
        assert isinstance(pending[0]["line_number"], int)

    def test_extracts_task_titles(self, sample_tasks_file):
        """Test that task titles are extracted."""
        tm = TaskManager(str(sample_tasks_file))
        
        pending = tm.get_pending_tasks(limit=1)
        
        assert pending[0]["title"] == "First pending task"


class TestGetTaskDetails:
    """Tests for get_task_details method."""

    @pytest.fixture
    def detailed_tasks_file(self, tmp_path):
        """Create tasks file with full metadata."""
        tasks_file = tmp_path / "tasks.md"
        content = """# Tasks

- [ ] T001 Create initial Terraform configuration
  <!-- 🤖 Agent: terraform-architect | ✅ T2 | ⚡ 0.95 -->
  <!-- 🏷️ Tags: #terraform #infrastructure -->
  <!-- 🎯 skill: terraform_validation (10.0) -->
  <!-- 🔄 Fallback: devops-developer -->

  **Description:** Create the initial Terraform configuration for the VPC and network setup.

  **Acceptance Criteria:**
  - VPC with proper CIDR ranges
  - Subnets for each availability zone
  - Security groups configured

- [x] T002 Completed task with result
  <!-- 🤖 Agent: gitops-operator | ✅ T3 | ⚡ 0.90 -->
  <!-- 📍 Result: HelmRelease deployed successfully -->

  **Description:** Deploy the service to Kubernetes.
"""
        tasks_file.write_text(content)
        return tasks_file

    def test_gets_task_details(self, detailed_tasks_file):
        """Test getting full task details."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert details["task_id"] == "T001"
        assert details["status"] == "pending"
        assert "Create initial Terraform" in details["title"]

    def test_extracts_metadata(self, detailed_tasks_file):
        """Test metadata extraction."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert details["metadata"]["agent"] == "terraform-architect"
        assert details["metadata"]["security_tier"] == "T2"
        assert details["metadata"]["confidence"] == 0.95

    def test_extracts_tags(self, detailed_tasks_file):
        """Test tag extraction."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert "terraform" in details["metadata"]["tags"]
        assert "infrastructure" in details["metadata"]["tags"]

    def test_extracts_skill(self, detailed_tasks_file):
        """Test skill extraction."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert details["metadata"]["skill"]["name"] == "terraform_validation"
        assert details["metadata"]["skill"]["score"] == 10.0

    def test_extracts_fallback(self, detailed_tasks_file):
        """Test fallback agent extraction."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert details["metadata"]["fallback"] == "devops-developer"

    def test_extracts_description(self, detailed_tasks_file):
        """Test description extraction."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert "VPC" in details["description"]

    def test_extracts_acceptance_criteria(self, detailed_tasks_file):
        """Test acceptance criteria extraction."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T001")
        
        assert len(details["acceptance_criteria"]) >= 3
        assert any("VPC" in c for c in details["acceptance_criteria"])

    def test_completed_task_status(self, detailed_tasks_file):
        """Test completed task shows correct status."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T002")
        
        assert details["status"] == "completed"

    def test_extracts_result(self, detailed_tasks_file):
        """Test result extraction for completed tasks."""
        tm = TaskManager(str(detailed_tasks_file))
        
        details = tm.get_task_details("T002")
        
        assert "HelmRelease" in details["metadata"]["result"]

    def test_raises_for_nonexistent_task(self, detailed_tasks_file):
        """Test raises for nonexistent task."""
        tm = TaskManager(str(detailed_tasks_file))
        
        with pytest.raises(ValueError):
            tm.get_task_details("T999")


class TestGetTaskStatistics:
    """Tests for get_task_statistics method."""

    @pytest.fixture
    def stats_tasks_file(self, tmp_path):
        """Create tasks file for statistics tests."""
        tasks_file = tmp_path / "tasks.md"
        content = """# Tasks

- [ ] T001 Pending task 1
- [ ] T002 Pending task 2
- [x] T003 Completed task 1
- [x] T004 Completed task 2
- [x] T005 Completed task 3
- [ ] T006 Pending task 3
"""
        tasks_file.write_text(content)
        return tasks_file

    def test_calculates_total_tasks(self, stats_tasks_file):
        """Test total tasks calculation."""
        tm = TaskManager(str(stats_tasks_file))
        
        stats = tm.get_task_statistics()
        
        assert stats["total_tasks"] == 6

    def test_calculates_pending_tasks(self, stats_tasks_file):
        """Test pending tasks calculation."""
        tm = TaskManager(str(stats_tasks_file))
        
        stats = tm.get_task_statistics()
        
        assert stats["pending_tasks"] == 3

    def test_calculates_completed_tasks(self, stats_tasks_file):
        """Test completed tasks calculation."""
        tm = TaskManager(str(stats_tasks_file))
        
        stats = tm.get_task_statistics()
        
        assert stats["completed_tasks"] == 3

    def test_calculates_completion_rate(self, stats_tasks_file):
        """Test completion rate calculation."""
        tm = TaskManager(str(stats_tasks_file))
        
        stats = tm.get_task_statistics()
        
        assert stats["completion_rate"] == 50.0

    def test_handles_empty_file(self, tmp_path):
        """Test handles file with no tasks."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n\nNo tasks yet.\n")
        
        tm = TaskManager(str(tasks_file))
        stats = tm.get_task_statistics()
        
        assert stats["total_tasks"] == 0
        assert stats["completion_rate"] == 0.0

    def test_handles_all_completed(self, tmp_path):
        """Test handles all tasks completed."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [x] T001 Done\n- [x] T002 Done\n")
        
        tm = TaskManager(str(tasks_file))
        stats = tm.get_task_statistics()
        
        assert stats["completion_rate"] == 100.0


class TestTaskManagerEdgeCases:
    """Edge case tests for TaskManager."""

    def test_task_with_special_characters_in_title(self, tmp_path):
        """Test handling tasks with special characters."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [ ] T001 Deploy (v2.0) - critical!\n")
        
        tm = TaskManager(str(tasks_file))
        pending = tm.get_pending_tasks()
        
        assert len(pending) == 1
        assert "v2.0" in pending[0]["title"]

    def test_large_task_id_numbers(self, tmp_path):
        """Test handling large task ID numbers."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [ ] T9999 Large ID task\n")
        
        tm = TaskManager(str(tasks_file))
        
        details = tm.get_task_details("T9999")
        assert details["task_id"] == "T9999"

    def test_concurrent_modifications(self, tmp_path):
        """Test behavior with concurrent file modifications."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [ ] T001 Task\n")
        
        tm = TaskManager(str(tasks_file))
        
        # Simulate external modification
        tasks_file.write_text("# Tasks\n- [x] T001 Task\n")
        
        # Should see updated content
        pending = tm.get_pending_tasks()
        assert len(pending) == 0

    def test_unicode_in_tasks(self, tmp_path):
        """Test handling Unicode characters."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [ ] T001 Deploy 服务 (emoji: 🚀)\n")
        
        tm = TaskManager(str(tasks_file))
        pending = tm.get_pending_tasks()
        
        assert len(pending) == 1

    def test_task_without_metadata(self, tmp_path):
        """Test handling task without metadata comments."""
        tasks_file = tmp_path / "tasks.md"
        tasks_file.write_text("# Tasks\n- [ ] T001 Simple task without metadata\n")
        
        tm = TaskManager(str(tasks_file))
        details = tm.get_task_details("T001")
        
        assert details["metadata"]["agent"] is None
        assert details["metadata"]["tags"] == []
