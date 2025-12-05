# Agent Framework - Agnostic Agent Execution Protocol

Unified framework for executing agents with 5-layer workflow, payload validation, local discovery, finding classification, and execution profiling.

**Reference Documentation:** See `docs/Agent-Complete-Workflow.md`

---

## 📦 Components

### 1. `payload_validator.py` - Phase A (Validation)
Validates incoming payload structure without assuming project layout.

```python
from payload_validator import PayloadValidator

validator = PayloadValidator()
result = validator.validate_payload(payload)

print(validator.generate_report(result))
```

**What it validates:**
- ✓ JSON structure valid
- ✓ Contract fields present (project_details, infrastructure_paths, operational_guidelines)
- ✓ Paths exist and accessible
- ✓ Enrichment data coherent (optional)
- ✓ Metadata present

---

### 2. `local_discoverer.py` - Phase B (Discovery)
Explores repository locally within given paths to find SSOT files and extract configuration.

```python
from local_discoverer import LocalDiscoverer
from pathlib import Path

discoverer = LocalDiscoverer(Path("/path/to/repo"))
result = discoverer.discover()

print(discoverer.generate_report(result))
```

**What it discovers:**
- Terraform files (.tf, terraform.tfvars)
- Kustomization.yaml
- HelmRelease.yaml
- Dockerfile
- GitHub workflows
- Configuration extraction from each

---

### 3. `finding_classifier.py` - Phase C (Classification)
Classifies findings into 4 tiers to avoid overwhelming reports.

```python
from finding_classifier import FindingClassifier, FindingFactory

classifier = FindingClassifier()
classifier.add_finding(FindingFactory.secrets_in_wrong_location("/app/.env", "/secrets/"))
classifier.add_finding(FindingFactory.pattern_detected("Docker", "Multi-stage detected"))

result = classifier.classify()
report = classifier.generate_report(result)
```

**Tiers:**
- **CRITICAL (Tier 1):** Security risk, blocks operation
- **DEVIATION (Tier 2):** Doesn't follow standards but works
- **IMPROVEMENT (Tier 3):** Could be better (omitted in reports)
- **PATTERN (Tier 4):** Detected pattern, auto-apply

---

### 4. `execution_manager.py` - Phase D (Execution)
Executes commands with agent-specific profiles: timeouts, retries, fallbacks.

```python
from execution_manager import ExecutionManager

manager = ExecutionManager()
metrics = manager.execute(
    command="terraform plan",
    profile_name="terraform-plan",
    cwd=Path("/terraform")
)

print(manager.generate_report(metrics))
```

**Profiles defined for:**
- terraform (validate, plan, apply)
- flux (check, reconcile)
- helm (upgrade)
- kubectl (wait)
- docker (build, push)

---

### 5. `logging_manager.py` - Logging & Benchmarking
Structured JSON logging for metrics collection.

```python
from logging_manager import JSONLogger

logger = JSONLogger()
logger.log_validation_complete("terraform-architect", True, 1200, 5, 0)
logger.log_discovery_complete("terraform-architect", 15, 3, 0, 4500)

logger.print_metrics_summary()
logger.save_metrics_report("metrics.json")
```

**Metrics tracked:**
- Duration per phase (validation, discovery, execution)
- Retry attempts and effectiveness
- Success rates
- P95 latencies

---

### 6. `agent_orchestrator.py` - Complete Workflow
Integrates all 5 phases into single orchestrated execution.

```python
from agent_orchestrator import AgentOrchestrator

orchestrator = AgentOrchestrator("terraform-architect")
result = orchestrator.execute_full_workflow(payload)

print(orchestrator.generate_final_report(result))
```

---

## 🔄 Workflow (5 Capas)

```
┌─────────────────────────────────────────┐
│ CAPA 1: PAYLOAD VALIDATION              │
│ ├─ A1: JSON structure valid?            │
│ ├─ A2: Contract fields present?         │
│ ├─ A3: Paths exist & accessible?        │
│ ├─ A4: Enrichment valid (optional)?     │
│ └─ A5: Metadata coherent?               │
└─────────────────────────────────────────┘
              ↓ (if valid)
┌─────────────────────────────────────────┐
│ CAPA 2: LOCAL DISCOVERY                 │
│ ├─ B1: Explore structure (depth limit)  │
│ ├─ B2: Find SSOT files                  │
│ ├─ B3: Extract configuration            │
│ ├─ B4: Validate internal coherence      │
│ └─ B5: Report findings                  │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ CAPA 3: FINDING CLASSIFICATION          │
│ ├─ Classify by Tier (1-4)               │
│ ├─ Specify data origin                  │
│ ├─ Decide escalation path               │
│ └─ Generate concise report              │
└─────────────────────────────────────────┘
              ↓ (if discrepancies)
┌─────────────────────────────────────────┐
│ CAPA 4: REMOTE VALIDATION               │
│ ├─ Query live infrastructure            │
│ ├─ Detect drift                         │
│ └─ Report state differences             │
└─────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────┐
│ CAPA 5: EXECUTION WITH PROFILES         │
│ ├─ Apply execution profile              │
│ ├─ Handle timeouts & retries            │
│ ├─ Use fallbacks if needed              │
│ └─ Log metrics for benchmarking         │
└─────────────────────────────────────────┘
```

---

## 🧪 Tests

Tests in `/tests/agent-framework/`:

```bash
# Run all tests
pytest tests/agent-framework/ -v

# Run specific test suite
pytest tests/agent-framework/test_payload_validator.py -v

# Run with coverage
pytest tests/agent-framework/ --cov=tools/9-agent-framework
```

**Test Coverage:**
- Phase A (Validation): A1-A5 checkpoints
- Phase B (Discovery): Pattern matching, coherence
- Phase C (Classification): Tier assignment, reporting
- Phase D (Execution): Timeout, retry, fallback
- Phase E (Orchestration): Full workflow integration

---

## 📊 Payload Schema

See `agent-payload-schema.json` for complete schema.

**Minimal payload:**
```json
{
  "contract": {
    "project_details": {
      "name": "my-project",
      "root": "/path/to/project"
    },
    "infrastructure_paths": {
      "terraform": "/path/to/terraform",
      "gitops": "/path/to/gitops"
    },
    "operational_guidelines": {
      "action": "plan",
      "deployment_tool": "flux"
    }
  },
  "enrichment": {}
}
```

---

## 🚀 Quick Start

```python
from agent_orchestrator import AgentOrchestrator
from pathlib import Path

# Create payload
payload = {
    "contract": {
        "project_details": {
            "name": "terraform-test",
            "root": "/home/user/project"
        },
        "infrastructure_paths": {
            "terraform_root": "/home/user/project/terraform"
        },
        "operational_guidelines": {
            "action": "plan"
        }
    },
    "enrichment": {}
}

# Execute complete workflow
orchestrator = AgentOrchestrator("terraform-architect")
result = orchestrator.execute_full_workflow(payload)

# Print report
print(orchestrator.generate_final_report(result))
```

---

## 📈 Execution Profiles

Each agent type has predefined profiles for commands:

```
terraform-validate:   timeout=30s,  retries=1
terraform-plan:       timeout=300s, retries=2
terraform-apply:      timeout=600s, retries=1
flux-check:           timeout=30s,  retries=2
flux-reconcile:       timeout=300s, retries=2
helm-upgrade:         timeout=600s, retries=1
docker-build:         timeout=900s, retries=1
```

See `Agent-Execution-Profiles.md` for all profiles.

---

## 🔍 Observability

All execution logged to `.claude/logs/agent-execution.jsonl`:

```json
{
  "timestamp": "2025-11-12T10:30:45Z",
  "event_type": "execution_complete",
  "agent": "terraform-architect",
  "phase": "D",
  "status": "success",
  "duration_ms": 32000,
  "details": {
    "command": "terraform plan",
    "exit_code": 2,
    "retry_attempts": 0,
    "output_lines": 145
  }
}
```

Generate metrics report:
```python
logger.print_metrics_summary()
logger.save_metrics_report("metrics.json")
```

---

## 📋 Checklist: Implementation

- [x] Phase A: Payload validation (A1-A5)
- [x] Phase B: Local discovery
- [x] Phase C: Finding classification
- [x] Phase D: Remote validation
- [x] Phase E: Execution manager
- [x] Logging & metrics
- [x] Tests for Phase A
- [x] Tests for Phase B-E
- [x] Integration with terraform-architect agent
- [x] Integration with gitops-operator agent

---

## 📚 Documentation

- **Specification:** `docs/Agent-Complete-Workflow.md`
- **Execution Profiles:** `docs/Agent-Execution-Profiles.md`
- **Discovery Rules:** `docs/agent-discovery-rules.md`
- **Payload Schema:** `docs/agent-payload-schema.json`
- **Validation Lifecycle:** `docs/agent-validation-lifecycle.md`

---

## 🎯 Success Metrics

**Target Performance:**
- Validation latency: < 100ms
- Discovery latency: < 5s
- Classification latency: < 1s
- Execution success rate: > 98%
- Retry effectiveness: > 80%

---

## 📞 Support

For questions about:
- **Workflow:** See `Agent-Complete-Workflow.md`
- **Execution profiles:** See `Agent-Execution-Profiles.md`
- **Discovery patterns:** See `agent-discovery-rules.md`
- **Tests:** See `/tests/agent-framework/`
