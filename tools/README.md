# Gaia-Ops Tools - Reorganized Structure

**Version:** 2.0.0
**Last Updated:** 2025-11-12

## Overview

This directory contains the core orchestration tools for the gaia-ops agent system, organized into atomic, purpose-driven modules.

## Directory Structure

```
tools/
├── 1-routing/              # Agent semantic routing and intent classification
├── 2-context/              # Context provisioning and enrichment
├── 3-clarification/        # Phase 0 ambiguity detection (clarify())
├── 4-validation/           # Approval gates and commit validation
├── 5-task-management/      # Large plan chunking and execution
├── 6-semantic/             # Embedding-based semantic matching
├── 7-utilities/            # Helper tools and audit logging
├── 8-shared/               # Common schemas and utilities (future)
├── fast-queries/           # Agent diagnostic scripts
├── __init__.py             # Package re-exports (backward compatible)
└── README.md               # This file
```

## Module Organization

### Core Modules (1-8)

Each numbered module follows these principles:
- **Single responsibility** - Each module has one clear purpose
- **Self-contained** - Minimal cross-module dependencies
- **Well-documented** - README.md in each module
- **Tested** - Comprehensive test coverage

#### 1-Routing
Routes user requests to appropriate agents using semantic matching.
- `AgentRouter` - Main router
- `IntentClassifier` - Intent detection
- `CapabilityValidator` - Agent capability matching

#### 2-Context
Manages SSOT context provisioning for agents.
- `load_project_context()` - Load project configuration
- `get_contract_context()` - Agent-specific context filtering
- `ContextSectionReader` - Token-optimized context extraction

#### 3-Clarification
Detects and resolves ambiguous user prompts (Phase 0).
- `clarify()` - Simple one-liner clarification
- `ClarificationEngine` - Complex ambiguity detection
- `execute_workflow()` - Complete clarification workflow

#### 4-Validation
Enforces governance and validation rules.
- `ApprovalGate` - T3 operation approval workflow
- `CommitMessageValidator` - Conventional Commits validation
- `validate_commit_message()` - Pre-commit validation

#### 5-Task-Management
Handles large plan chunking and state tracking.
- `TaskManager` - Plan splitting and execution tracking
- State persistence for resume capability

#### 6-Semantic
Improves routing accuracy through embeddings.
- `SemanticMatcher` - Semantic similarity matching
- `generate_embeddings()` - Offline embedding generation

#### 7-Utilities
Common utilities and audit logging.
- `AgentInvokerHelper` - Agent invocation patterns
- `TaskAuditLogger` - Comprehensive execution logging

#### 8-Shared
Common utilities (future extensions).
- Placeholder for shared schemas, exceptions, constants

### Fast-Queries Module

Quick diagnostic scripts for each agent domain:
```
fast-queries/
├── terraform/          # Terraform/Terragrunt validation
├── gitops/             # Kubernetes/Flux/Helm snapshots
├── cloud/
│   ├── gcp/            # GCP diagnostics
│   └── aws/            # AWS diagnostics
└── appservices/        # Application health checks
```

**Central runner:** `run_triage.sh`

## Usage

### Backward Compatible Imports

All old imports continue to work:
```python
from agent_router import AgentRouter
from context_provider import load_project_context
from approval_gate import ApprovalGate
```

### New Modular Imports

Preferred for new code:
```python
from tools.routing import AgentRouter
from tools.context import load_project_context
from tools.validation import ApprovalGate
from tools.clarification import clarify
```

### Using Fast-Queries

```bash
# Run all diagnostics
.claude/tools/fast-queries/run_triage.sh

# Run specific agent triage
.claude/tools/fast-queries/run_triage.sh terraform
.claude/tools/fast-queries/run_triage.sh gitops
```

## Migration from 1.x

**What Changed:**

1. **Files reorganized** into atomic modules (1-8, fast-queries)
2. **Backward compatibility** maintained via `__init__.py`
3. **Dead code removed** (demo_clarify.py, task_manager_example.py, agent_capabilities.json)
4. **Fast-queries** moved from root to organized subdirectories

**What Stayed the Same:**

- All function signatures
- All class interfaces
- Import paths (via re-exports)
- CLI command syntax

## Development

### Adding New Tools

1. Choose appropriate module or create new one
2. Follow module's architectural pattern
3. Add to module's `__init__.py` exports
4. Add to main `tools/__init__.py` if needed
5. Write tests in `tests/tools/test_<module>.py`
6. Update module's README.md

### Testing

```bash
# Run all tool tests
pytest tests/tools/ -v

# Run specific module tests
pytest tests/tools/test_agent_router.py -v
pytest tests/tools/test_clarify_engine.py -v
```

## Performance

| Module | Init Time | Typical Operation |
|--------|-----------|-------------------|
| 1-routing | ~80ms | 100ms per route |
| 2-context | ~60ms | 50ms per load |
| 3-clarification | ~100ms | 150ms detection |
| 4-validation | ~40ms | 30ms validation |
| 6-semantic | ~200ms | 80ms similarity |
| fast-queries | N/A | 2-8s per triage |

## Dependencies

**Python packages:**
- `numpy` - Semantic matching (6-semantic)
- `scikit-learn` - Embeddings (6-semantic)
- `pathlib`, `json` - Standard library

**External tools (for fast-queries):**
- `kubectl`, `flux`, `helm` - GitOps/Kubernetes
- `terraform`, `terragrunt` - Infrastructure
- `gcloud`, `aws` - Cloud providers

## See Also

- **Configuration:** `.claude/config/` - System configuration
- **Tests:** `tests/tools/` - Tool test suites
- **Hooks:** `hooks/` - Pre/post execution hooks
- **Agents:** `agents/` - Agent definitions
- **Documentation:** `.claude/config/*.md` - Complete specs

## Contributing

Follow gaia-ops contribution guidelines:
1. Test changes thoroughly
2. Maintain backward compatibility
3. Update relevant READMEs
4. Follow naming conventions
5. Add integration tests for new features

## Version History

- **2.0.0** (2025-11-12) - Major reorganization into atomic modules
- **1.1.0** (2025-11-08) - Added clarification module
- **1.0.0** (2025-10-15) - Initial tools structure
