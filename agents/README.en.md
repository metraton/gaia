# Gaia-Ops Specialist Agents

**[Version en espanol](README.md)**

Agents are AI specialists that handle specific tasks in your DevOps infrastructure.

## Purpose

Divide complex work into manageable specialties. Each agent focuses on what they do best - like having a team of experts instead of a generalist.

## How It Works

```
User sends question
        |
[Orchestrator] -> [Agent Router]
        |
   Selects agent
        |
  terraform | gitops | gcp | aws | devops | gaia
        |
[Context Provider] -> Agent executes
        |
   Result
```

## Available Agents

| Agent | Expert in | Tiers |
|-------|----------|-------|
| **terraform-architect** | Infrastructure as code | T0-T3 |
| **gitops-operator** | Kubernetes and deployments | T0-T3 |
| **gcp-troubleshooter** | GCP diagnostics | T0 |
| **aws-troubleshooter** | AWS diagnostics | T0 |
| **devops-developer** | Code and CI/CD | T0-T2 |
| **Gaia** | Agent system | T0-T2 |

## Security Tiers

| Tier | Description | Approval |
|------|-------------|----------|
| T0 | Read-only | No |
| T1 | Validation | No |
| T2 | Planning | No |
| T3 | Execution | **Yes** |

## Invocation

### Automatic (Recommended)

```bash
# Orchestrator selects automatically
"Deploy auth-service version 1.2.3"
# -> gitops-operator
```

### Manual

```python
Task(
  subagent_type="gitops-operator",
  description="Deploy auth service",
  prompt="Deploy auth-service version 1.2.3"
)
```

## Smart Routing

- Keywords: Domain-specific terms
- Semantic matching: Vector embeddings
- Context awareness: Project context

**Current accuracy:** ~92.7%

## References

- [config/orchestration-workflow.md](../config/orchestration-workflow.md)
- [config/agent-catalog.md](../config/agent-catalog.md)
- [config/context-contracts.md](../config/context-contracts.md)

---

**Updated:** 2025-12-06 | **Agents:** 6
