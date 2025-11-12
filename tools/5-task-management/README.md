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

manager = TaskManager()
chunks = manager.split_plan(terraform_plan_output, chunk_size=500)
# Returns: List of plan chunks

manager.get_current_chunk()
manager.mark_chunk_complete()
manager.save_state()
```

## Purpose

Large plans (>3000 lines) can exceed context limits. TaskManager:
- Splits plans into logical chunks
- Maintains execution state
- Enables resume on interruption
- Tracks completion progress

## Usage Example

```python
from tools.task_management import TaskManager

# Initialize with large plan
manager = TaskManager()
terraform_plan = read_large_terraform_plan()

# Split into chunks (each ~500 lines max)
chunks = manager.split_plan(terraform_plan)

# Process each chunk
for i, chunk in enumerate(chunks):
    print(f"Processing chunk {i+1}/{len(chunks)}")
    execute_chunk(chunk)
    manager.mark_chunk_complete(i)

# Persist state for potential resume
manager.save_state()
```

## Features

- **Plan splitting:** Logical resource grouping
- **State persistence:** Save/restore execution state
- **Progress tracking:** Know which chunks are done
- **Resume capability:** Continue after interruption
- **Line count tracking:** Manage context windows

## Configuration

**Default chunk size:** 500 lines per chunk

Adjustable:
```python
manager = TaskManager()
chunks = manager.split_plan(plan, chunk_size=1000)  # Custom size
```

## Files

- `task_manager.py` - Main TaskManager implementation
- `task_manager_README.md` - Additional documentation
- `README.md` - This file

## See Also

- `tools/__init__.py` - Package re-exports
- Terraform/Terragrunt agents - Primary consumers
- GitOps operators - Secondary consumers
