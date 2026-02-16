# Context Module

**Purpose:** Context provisioning and enrichment for agents

## Overview

This module manages the SSOT (Single Source of Truth) context that agents receive. It loads project configuration, filters by agent contract, and enriches context based on semantic analysis.

## Core Functions

### `load_project_context(path)`
Loads the project-context.json file.

```python
from tools.context import load_project_context
context = load_project_context(Path(".claude/project-context/project-context.json"))
```

### `get_contract_context(project_context, agent_name, provider_contracts)`
Gets the specific context needed for an agent based its contract.

```python
from tools.context import get_contract_context
contract_context = get_contract_context(
    project_context,
    "terraform-architect",
    provider_contracts
)
```

### `load_provider_contracts(cloud_provider)`
Loads cloud provider-specific agent contracts (GCP, AWS, Azure).

```python
from tools.context import load_provider_contracts
contracts = load_provider_contracts("gcp")
```

## Core Classes

### `ContextSectionReader`
Selective context loading for token optimization.

**Methods:**
```python
reader = ContextSectionReader(project_context)
sections = reader.get_sections_for_agent("gitops-operator")
```

## Agent Contracts

Each agent receives specific context sections:

**terraform-architect:**
- project_details
- terraform_infrastructure
- operational_guidelines

**gitops-operator:**
- project_details
- gitops_configuration
- infrastructure_topology
- cluster_details
- operational_guidelines

**troubleshooters (gcp/aws):**
- project_details
- infrastructure_topology
- terraform_infrastructure
- gitops_configuration
- cloud_provider_details

## Usage Example

```python
from tools.context import load_project_context, get_contract_context
from pathlib import Path

# Load project context
context_path = Path(".claude/project-context/project-context.json")
project_context = load_project_context(context_path)

# Get context for terraform-architect
contract_ctx = get_contract_context(
    project_context,
    "terraform-architect",
    provider_contracts={}
)

# Send to agent
print(contract_ctx["contract"])
```

## Command Line Usage

```bash
python3 context_provider.py terraform-architect "Create a VPC" \
  --context-file .claude/project-context/project-context.json
```

## Files

- `context_provider.py` - Main context provisioning logic
- `context_section_reader.py` - Token-optimized context extraction
- `README.md` - This file

## See Also

- `context_provider.py` (`load_provider_contracts`) - Contract loading logic
- `tools/__init__.py` - Package re-exports
- `tests/tools/test_context_provider.py` - Test suite
