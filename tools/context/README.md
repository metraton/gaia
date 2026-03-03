# Context Module

**Purpose:** Context provisioning and enrichment for agents

## Overview

This module manages the SSOT (Single Source of Truth) context that agents receive. It loads project configuration, filters by agent contract (defined in `config/context-contracts.json` + cloud extensions), and provides context to agents.

## Core Functions

### `load_project_context(path)`
Loads the project-context.json file.

```python
from tools.context.context_provider import load_project_context
context = load_project_context(Path(".claude/project-context/project-context.json"))
```

### `get_contract_context(project_context, agent_name, provider_contracts)`
Gets the specific context needed for an agent based on its contract.

```python
from tools.context.context_provider import get_contract_context
contract_context = get_contract_context(
    project_context,
    "terraform-architect",
    provider_contracts
)
```

### `load_provider_contracts(cloud_provider)`
Loads cloud provider-specific agent contracts (GCP, AWS, Azure).

```python
from tools.context.context_provider import load_provider_contracts
contracts = load_provider_contracts("gcp")
```

## Core Classes

### `ContextSectionReader`
Selective context loading for token optimization.

```python
from tools.context.context_section_reader import ContextSectionReader
reader = ContextSectionReader(project_context)
sections = reader.get_sections_for_agent("gitops-operator")
```

## Agent Contracts

Each agent receives specific context sections (defined in `config/context-contracts.json`):

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

**cloud-troubleshooter:**
- project_details
- infrastructure_topology
- terraform_infrastructure
- gitops_configuration
- cloud_provider_details

## Command Line Usage

```bash
python3 tools/context/context_provider.py terraform-architect "Create a VPC" \
  --context-file .claude/project-context/project-context.json
```

## Files

```
context/
├── context_provider.py        # Main context provisioning logic
├── context_section_reader.py  # Token-optimized context extraction
├── context_selector.py        # Context selection logic
├── context_compressor.py      # Context compression for token optimization
├── context_lazy_loader.py     # Lazy loading for large contexts
├── deep_merge.py              # Deep merge utility for contract merging
├── pending_updates.py         # Pending context update management
├── benchmark_context.py       # Performance benchmarking
└── README.md
```

## See Also

- `config/context-contracts.json` - Agent contracts (SSOT)
- `config/cloud/gcp.json` - GCP-specific contract extensions
- `config/cloud/aws.json` - AWS-specific contract extensions
- `hooks/modules/context/context_writer.py` - Context write operations
- `tests/tools/test_context_provider.py` - Test suite
