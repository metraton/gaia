# Context Module

**Purpose:** Context provisioning and enrichment for agents

## Overview

This module manages the project knowledge agents receive at dispatch time. It reads agent contracts from the `project_context_contracts` and `agent_contract_permissions` tables in `~/.gaia/gaia.db`, filters sections by contract, and builds the injection payload. It also classifies the task into Gaia surfaces, emits an `investigation_brief`, and injects `write_permissions` so agents receive deterministic cross-surface guidance and writable-section ownership, not just raw project data.

## Core Functions

### `load_project_context(workspace, db_path=None)`
Loads project context for a workspace from the `project_context_contracts` table in `~/.gaia/gaia.db`. The DB is the canonical source of truth; the legacy `project-context.json` file on disk is no longer written or read by `gaia scan`.

```python
from tools.context.context_provider import load_project_context
context = load_project_context("me")
```

### `get_contract_context(project_context, agent_name, provider_contracts)`
Gets the specific context needed for an agent based on its contract. Contracts are sourced from the DB; `provider_contracts` carries the cloud extension overlay.

```python
from tools.context.context_provider import get_contract_context
contract_context = get_contract_context(
    project_context,
    "platform-architect",
    provider_contracts
)
```

### `get_context_update_contract(agent_name, provider_contracts)`
Gets the readable/writable section contract that governs `update_contracts` clauses. The source of truth is `agent_contract_permissions` in `~/.gaia/gaia.db`.

```python
from tools.context.context_provider import get_context_update_contract
update_contract = get_context_update_contract("platform-architect", provider_contracts)
```

### `load_provider_contracts(cloud_provider)`
Loads cloud provider-specific agent contracts (GCP, AWS) from `config/cloud/`.

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
brief = build_investigation_brief("Review hook/skill drift", "gaia-system", contract_context={})
```

## Agent Contracts

Agent contracts live in `~/.gaia/gaia.db` (`project_context_contracts` + `agent_contract_permissions` tables). `config/context-contracts.json` is the seeding source applied by `gaia install`; the DB is the runtime SSOT. Each agent receives specific sections:

**platform-architect:**
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
- gitops_configuration, application_services, architecture_overview

The same contracts are exposed under `write_permissions`:
- `readable_sections`
- `writable_sections`

Agents should use the injected `write_permissions`, not a hardcoded table in a skill,
when deciding whether an `update_contracts` clause is allowed.

## Command Line Usage

```bash
python3 tools/context/context_provider.py platform-architect "Create a VPC" \
  --workspace me
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
├── benchmark_context.py       # Performance benchmarking
└── README.md
```

## See Also

- `~/.gaia/gaia.db` — `project_context_contracts` + `agent_contract_permissions` tables (runtime SSOT)
- `config/context-contracts.json` — seeding source for agent contracts; applied on install
- `config/cloud/gcp.json` — GCP-specific contract extensions
- `config/cloud/aws.json` — AWS-specific contract extensions
- `hooks/modules/context/context_writer.py` — Context write operations
- `tests/tools/test_context_provider.py` — Test suite
