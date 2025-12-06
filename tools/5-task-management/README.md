# 5-Task-Management Module

**Purpose:** Large plan chunking and execution tracking

## Overview

This module manages breaking down large implementation plans into manageable chunks, tracking task execution, and maintaining state across long-running operations.

## Core Classes

### `TaskManager`
Manages large Terraform and infrastructure plans.

**Methods:**
```python
from tools.task_management import TaskManager

manager = TaskManager(tasks_file_path)

# Mark a task as complete
manager.mark_task_complete("T045")

# Get pending tasks
pending = manager.get_pending_tasks(limit=10)

# Get full details for a specific task
details = manager.get_task_details("T045")

# Get statistics
stats = manager.get_task_statistics()
```

## Purpose

Large plans (>3000 lines) can exceed context limits. TaskManager:
- Handles task files efficiently without loading entire content
- Uses Grep for searching and Edit for targeted updates
- Tracks completion progress
- Extracts task metadata from HTML comments

## Usage Example

```python
from tools.task_management import TaskManager

# Initialize with path to tasks.md
tm = TaskManager("/path/to/tasks.md")

# Get task statistics
stats = tm.get_task_statistics()
print(f"Total: {stats['total_tasks']}, Pending: {stats['pending_tasks']}")

# Get pending tasks
for task in tm.get_pending_tasks(limit=5):
    print(f"{task['task_id']}: {task['title']}")

# Mark task complete
tm.mark_task_complete("T045")
```

## Features

- **Task status management:** Mark tasks complete/pending
- **Efficient file operations:** Uses grep/sed, no full-file rewrites
- **Metadata extraction:** Parses agent, tier, tags from HTML comments
- **Statistics:** Track completion progress

## Files

- `task_manager.py` - Main TaskManager implementation
- `task_manager_README.md` - Additional documentation
- `README.md` - This file

## See Also

- `tools/4-memory/episodic.py` - Episodic memory for context
- `hooks/subagent_stop.py` - Workflow metrics capture
