# Context Module

**Purpose:** Context provisioning and enrichment for agents

## Overview

This module manages the SSOT (Single Source of Truth) context that agents receive. It loads project configuration, filters by agent contract (defined in `config/context-contracts.json` + cloud extensions), and provides context to agents.
It also classifies the task into generic Gaia surfaces, emits an `investigation_brief`, and injects `write_permissions` so agents receive deterministic cross-surface guidance and writable-section ownership, not just raw project data.

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

### `get_context_update_contract(agent_name, provider_contracts)`
Gets the readable/writable section contract that governs `CONTEXT_UPDATE`.

```python
from tools.context.context_provider import get_context_update_contract
update_contract = get_context_update_contract("terraform-architect", provider_contracts)
```

### `load_provider_contracts(cloud_provider)`
Loads cloud provider-specific agent contracts (GCP, AWS).

```python
from tools.context.context_provider import load_provider_contracts
contracts = load_provider_contracts("gcp")
```

### `classify_surfaces(task, current_agent=...)`
Classifies a task into one or more active Gaia surfaces using generic signals.

```python
from tools.context.surface_router import classify_surfaces
routing = classify_surfaces("Investigate rollout failure after CI image change", current_agent="gitops-operator")
```

### `build_investigation_brief(task, agent_name, contract_context)`
Builds the deterministic investigation brief injected into project context.

```python
from tools.context.surface_router import build_investigation_brief
brief = build_investigation_brief("Review hook/skill drift", "gaia", contract_context={})
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

Each agent receives specific v2 context sections (defined in `config/context-contracts.json` v3):

**terraform-architect:**
- project_identity, stack, git, environment, infrastructure, orchestration
- terraform_infrastructure, infrastructure_topology
- operational_guidelines, cluster_details, application_services, architecture_overview

**gitops-operator:**
- project_identity, stack, git, environment, infrastructure, orchestration
- gitops_configuration, cluster_details
- operational_guidelines, application_services, architecture_overview

**cloud-troubleshooter:**
- project_identity, stack, git, environment, infrastructure, orchestration
- cluster_details, infrastructure_topology, terraform_infrastructure
- gitops_configuration, application_services, monitoring_observability, architecture_overview

The same contracts are also exposed under `write_permissions`:
- `readable_sections`
- `writable_sections`

Agents should use the injected `write_permissions`, not a hardcoded table in a skill,
when deciding whether a `CONTEXT_UPDATE` is allowed.

## Command Line Usage

```bash
python3 tools/context/context_provider.py terraform-architect "Create a VPC" \
  --context-file .claude/project-context/project-context.json
```

## Files

```
context/
├── __init__.py                # Public exports (re-exports from context_provider + surface_router)
├── _paths.py                  # Shared config directory resolution (resolve_config_dir)
├── context_provider.py        # Main context provisioning logic
├── surface_router.py          # Surface classification + investigation brief
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
