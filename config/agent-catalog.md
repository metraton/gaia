# Agent Catalog

**Version:** 2.0.0
**Last Updated:** 2025-11-07
**Parent:** CLAUDE.md

This document provides a comprehensive catalog of all specialist agents in the system, including their capabilities, security tiers, use cases, and invocation patterns.

---

## Agent Classification

### Project Agents (Use context_provider.py)

These agents work on **USER PROJECTS** (infrastructure, GitOps, applications). They MUST receive context via `context_provider.py`.

| Agent | Primary Role | Security Tier | Model |
|-------|--------------|---------------|-------|
| **terraform-architect** | Terraform/Terragrunt validation & realization | T0-T3 | inherit |
| **gitops-operator** | Kubernetes/Flux operations & realization | T0-T3 | inherit |
| **gcp-troubleshooter** | GCP diagnostics | T0-T2 | inherit |
| **aws-troubleshooter** | AWS diagnostics | T0-T2 | inherit |
| **devops-developer** | Application build/test/debug | T0-T2 | inherit |

---

### Meta-Agents (Direct invocation, NO context_provider.py)

These agents work on **CLAUDE CODE SYSTEM ITSELF** (agent orchestration, tooling, optimization). They receive MANUAL context in the prompt.

| Agent | Primary Role | Context Type | Model |
|-------|--------------|--------------|-------|
| **Gaia** | System analysis & optimization | Manual (gaia-ops package, .claude/, tests) | inherit |
| **Explore** | Codebase exploration for understanding | Automatic (file patterns) | haiku |
| **Plan** | Planning mode for implementation design | Automatic (user prompt) | inherit |

---

## Security Tiers

### Tier Definitions

| Tier | Operations | Approval Required | Examples |
|------|-----------|-------------------|----------|
| **T0** | Read-only queries | No | `kubectl get`, `git status`, `terraform show` |
| **T1** | Local changes only | No | File edits, local git commits |
| **T2** | Reversible remote operations | No | `git push` to feature branch, `terraform plan` |
| **T3** | Irreversible operations | **YES** | `git push` to main, `terraform apply`, `kubectl apply` |

**Critical Rule:** T3 operations REQUIRE Phase 4 Approval Gate (MANDATORY).

---

## Project Agents: Detailed Catalog

### terraform-architect

**Full Name:** Terraform Infrastructure Architect

**Purpose:** Manages the complete Terraform/Terragrunt lifecycle for cloud infrastructure.

**Capabilities:**
- **Validation:** Syntax check, `terraform validate`, `terraform fmt`, linting
- **Planning:** Generate `terraform plan`, analyze resource changes
- **Code Generation:** Create new terraform modules/resources from specifications
- **Realization:** Execute `terragrunt apply` with verification
- **State Management:** Analyze terraform state, detect drift
- **Rollback:** Revert failed applies using state backups

**Tools Available:**
- Read, Edit, Write, Glob, Grep, Bash
- `terraform` (validate, plan, apply, destroy, show, state)
- `terragrunt` (plan, apply, run-all)
- `tflint` (linting)
- `gcloud`, `aws` (for provider operations)
- `kubectl` (for GKE/EKS cluster validation)
- Task (for sub-agent delegation)

**Security Tiers:**
- T0: `terraform show`, `terraform state list`, `terraform output`
- T1: File edits, `terraform fmt`, `terraform validate`
- T2: `terraform plan`
- T3: `terragrunt apply` (requires approval)

**Context Contract:** See `.claude/config/context-contracts.md#terraform-architect`

**Use Cases:**
- "Create a CloudSQL instance with Terraform"
- "Update GKE node pool configuration"
- "Validate terraform code for syntax errors"
- "Apply terraform changes for VPC networking"

**Invocation Pattern:**
```python
# Phase 1: Routing
agent = agent_router.py("Update GKE node pool configuration")
# Returns: "terraform-architect"

# Phase 2: Context
context = context_provider.py("terraform-architect", "Update GKE node pool configuration")

# Phase 3: Invoke for Planning
Task(
    subagent_type="terraform-architect",
    description="Generate GKE node pool update",
    prompt=f"{context}\n\nTask: Update GKE node pool configuration"
)
# Returns: Realization package with terraform code

# Phase 4: Approval Gate (MANDATORY for apply)

# Phase 5: Invoke for Realization
Task(
    subagent_type="terraform-architect",
    description="Apply GKE node pool update",
    prompt=f"Execute realization: {realization_package}"
)
```

---

### gitops-operator

**Full Name:** GitOps Kubernetes Operator

**Purpose:** Manages Kubernetes applications using GitOps methodology (Flux reconciliation).

**Capabilities:**
- **Manifest Generation:** Create Kubernetes YAML (Deployments, Services, Ingresses, etc.)
- **HelmRelease Management:** Generate/update HelmRelease resources for Flux
- **Deployment:** Apply manifests to cluster, wait for readiness
- **Verification:** Check pod status, logs, events
- **Rollback:** Revert failed deployments using git revert
- **Troubleshooting:** Diagnose deployment issues (ImagePullBackOff, CrashLoopBackOff, etc.)

**Tools Available:**
- Read, Edit, Write, Glob, Grep, Bash
- `kubectl` (apply, get, describe, logs, exec, port-forward)
- `helm` (template, lint, list, status)
- `flux` (reconcile, get, logs, check)
- `kustomize` (build)
- `gcloud container clusters get-credentials` (for cluster access)
- Task (for sub-agent delegation)

**Security Tiers:**
- T0: `kubectl get`, `kubectl describe`, `kubectl logs`, `flux get`
- T1: File edits, `helm template`, `kustomize build`
- T2: `git push` to feature branch, `flux reconcile`
- T3: `kubectl apply`, `git push` to main (requires approval)

**Context Contract:** See `.claude/config/context-contracts.md#gitops-operator`

**Use Cases:**
- "Deploy tcm-api service to non-prod cluster"
- "Update pg-api to version v2.1.0"
- "Debug why tcm-api pods are crashing"
- "Create Ingress for new service"
- "Rollback failed deployment of pg-query-api"

**Invocation Pattern:**
```python
# Phase 1: Routing
agent = agent_router.py("Deploy tcm-api service to non-prod cluster")
# Returns: "gitops-operator"

# Phase 2: Context
context = context_provider.py("gitops-operator", "Deploy tcm-api service to non-prod cluster")

# Phase 3: Invoke for Planning
Task(
    subagent_type="gitops-operator",
    description="Generate tcm-api deployment",
    prompt=f"{context}\n\nTask: Deploy tcm-api service to non-prod cluster"
)
# Returns: Realization package with Kubernetes YAML

# Phase 4: Approval Gate (MANDATORY for kubectl apply)

# Phase 5: Invoke for Realization
Task(
    subagent_type="gitops-operator",
    description="Deploy tcm-api to cluster",
    prompt=f"Execute realization: {realization_package}"
)
```

---

### gcp-troubleshooter

**Full Name:** GCP Diagnostic Specialist

**Purpose:** Diagnoses issues in GCP environments by comparing intended state (IaC/GitOps) with actual state (live resources).

**Capabilities:**
- **State Comparison:** Compare terraform/kubectl desired state with live GCP/GKE state
- **Log Analysis:** Analyze Cloud Logging, GKE pod logs, Cloud SQL logs
- **Network Diagnostics:** Test connectivity, DNS resolution, firewall rules
- **IAM Debugging:** Verify service account permissions, IAM policy bindings
- **Performance Analysis:** Query Cloud Monitoring metrics, identify bottlenecks
- **Root Cause Analysis:** Correlate symptoms across multiple resources

**Tools Available:**
- Read, Glob, Grep, Bash
- `gcloud` (compute, container, sql, iam, logging, monitoring)
- `kubectl` (get, describe, logs, exec)
- `gsutil` (for GCS bucket inspection)
- `terraform` (state inspection)
- Task (for sub-agent delegation)

**Security Tiers:**
- T0: `gcloud describe`, `kubectl get`, `gcloud logging read`
- T1: `gcloud sql connect` (read-only queries)
- T2: `gcloud compute ssh` (for VM diagnostics)
- T3: None (diagnostic agent, no destructive operations)

**Context Contract:** See `.claude/config/context-contracts.md#gcp-troubleshooter`

**Use Cases:**
- "Why is tcm-api pod crashing with database connection error?"
- "Diagnose why CloudSQL instance is unreachable from GKE"
- "Investigate high latency on pg-api service"
- "Why is IAM permission denied for service account?"

**Invocation Pattern:**
```python
# Phase 1: Routing
agent = agent_router.py("Why is tcm-api pod crashing with database connection error?")
# Returns: "gcp-troubleshooter"

# Phase 2: Context
context = context_provider.py("gcp-troubleshooter", "Diagnose tcm-api pod crash")

# Phase 3: Invoke for Diagnosis
Task(
    subagent_type="gcp-troubleshooter",
    description="Diagnose tcm-api database error",
    prompt=f"{context}\n\nTask: Why is tcm-api pod crashing with database connection error?"
)
# Returns: Diagnostic report with root cause and recommendations
```

---

### aws-troubleshooter

**Full Name:** AWS Diagnostic Specialist

**Purpose:** Diagnoses issues in AWS environments (EKS, RDS, EC2, etc.) by comparing intended state with actual state.

**Capabilities:**
- **State Comparison:** Compare terraform/kubectl desired state with live AWS/EKS state
- **Log Analysis:** Analyze CloudWatch Logs, EKS pod logs, RDS logs
- **Network Diagnostics:** Test connectivity, Route 53 DNS, security groups
- **IAM Debugging:** Verify IAM role permissions, trust policies
- **Performance Analysis:** Query CloudWatch metrics, identify bottlenecks
- **Root Cause Analysis:** Correlate symptoms across multiple resources

**Tools Available:**
- Read, Glob, Grep, Bash
- `aws` (ec2, eks, rds, iam, logs, cloudwatch)
- `kubectl` (get, describe, logs, exec)
- `eksctl` (for EKS cluster inspection)
- `terraform` (state inspection)
- Task (for sub-agent delegation)

**Security Tiers:**
- T0: `aws describe-*`, `kubectl get`, `aws logs tail`
- T1: `aws rds connect` (read-only queries)
- T2: `aws ec2 ssh` (for EC2 diagnostics)
- T3: None (diagnostic agent, no destructive operations)

**Context Contract:** See `.claude/config/context-contracts.md#aws-troubleshooter`

**Use Cases:**
- "Why is EKS pod failing with IAM permission error?"
- "Diagnose why RDS instance has high CPU usage"
- "Investigate network timeout on ALB"
- "Why is security group blocking traffic?"

---

### devops-developer

**Full Name:** DevOps Application Developer

**Purpose:** Application-level operations including build, test, debug, and ad-hoc git operations.

**Capabilities:**
- **Code Analysis:** Understand application code, dependencies, configuration
- **Build & Test:** Run build commands, execute test suites, analyze failures
- **Debugging:** Add logging, reproduce bugs, analyze stack traces
- **Dependency Management:** Update packages, resolve version conflicts
- **Git Operations:** Create commits, branches, pull requests (ad-hoc, NOT part of infrastructure workflow)
- **CI/CD:** Analyze pipeline failures, update GitHub Actions/GitLab CI configs

**Tools Available:**
- Read, Edit, Write, Glob, Grep, Bash
- `node`, `npm`, `npx` (for Node.js/TypeScript apps)
- `python3`, `pip`, `pytest` (for Python apps)
- `jest`, `eslint`, `prettier` (for JavaScript/TypeScript)
- `git` (all operations)
- Task (for sub-agent delegation)

**Security Tiers:**
- T0: `npm test`, `pytest`, `git log`, `git diff`
- T1: File edits, `npm install`, `pip install`, local git commits
- T2: `git push` to feature branch, `npm publish` (to test registry)
- T3: `git push` to main (requires approval)

**Context Contract:** See `.claude/config/context-contracts.md#devops-developer`

**Use Cases:**
- "Run tests and fix failures"
- "Update package.json dependencies"
- "Debug why API returns 500 error"
- "Create a commit with these changes"
- "Generate pull request for feature branch"

**Invocation Pattern:**
```python
# Orchestrator delegates simple operations to devops-developer
Task(
    subagent_type="devops-developer",
    description="Fix test failures",
    prompt=f"{context}\n\nTask: Run tests and fix any failures found"
)
```

---

## Meta-Agents: Detailed Catalog

### Gaia

**Full Name:** Claude Code System Architect

**Purpose:** Analyzes, diagnoses, and optimizes the agent orchestration system itself.

**Capabilities:**
- **System Analysis:** Understand agent system architecture, workflows, data flows
- **Log Analysis:** Parse and analyze logs (routing, approvals, clarifications, violations)
- **Performance Optimization:** Identify bottlenecks, propose improvements
- **Best Practices Research:** Use WebSearch to find industry standards, patterns
- **Test Suite Analysis:** Review test results, coverage, identify gaps
- **Documentation Generation:** Create/update system documentation

**Tools Available:**
- Read, Glob, Grep, Bash
- WebSearch (for best practices research)
- Python (for data analysis, script generation)
- Task (for sub-agent delegation)

**Context Type:** Manual (NOT context_provider.py)

**Use Cases:**
- "Analyze routing accuracy and propose improvements"
- "Why is approval gate being bypassed?"
- "Optimize CLAUDE.md for token efficiency"
- "Research best practices for agent context provisioning"

**Invocation Pattern:**
```python
# Orchestrator provides manual context in prompt
Task(
    subagent_type="gaia",
    description="Analyze routing accuracy",
    prompt="""
## System Context
- Agent system: $PROJECT_ROOT/.claude/
- Logs: $PROJECT_ROOT/.claude/logs/
- Tools: $PROJECT_ROOT/.claude/tools/

## Task
Analyze routing accuracy over last 100 invocations. Propose improvements.
"""
)
```

---

### Explore

**Full Name:** Codebase Explorer

**Purpose:** Fast, thorough exploration of codebases to answer questions or find patterns.

**Capabilities:**
- **File Pattern Matching:** Find files by glob patterns (e.g., "src/components/**/*.tsx")
- **Keyword Search:** Search code for specific keywords or patterns
- **Architecture Understanding:** Analyze codebase structure, identify patterns
- **Dependency Mapping:** Trace imports, dependencies, data flows

**Tools Available:**
- Read, Glob, Grep, Bash (all tools)

**Thoroughness Levels:**
- `quick`: Basic searches, 1-2 file locations
- `medium`: Moderate exploration, 3-5 locations
- `very thorough`: Comprehensive analysis, multiple locations and naming conventions

**Context Type:** Automatic (file patterns, keywords)

**Use Cases:**
- "Find all API endpoints in the codebase"
- "Where is the user authentication logic?"
- "Show me all components that use the UserContext"

**Invocation Pattern:**
```python
Task(
    subagent_type="Explore",
    description="Find API endpoints",
    prompt="Find all API endpoints in the codebase (thoroughness: medium)"
)
```

---

### Plan

**Full Name:** Implementation Planner

**Purpose:** Breaks down complex implementation tasks into step-by-step plans.

**Capabilities:**
- **Task Decomposition:** Break large tasks into smaller, manageable subtasks
- **Dependency Analysis:** Identify task dependencies, optimal execution order
- **Risk Assessment:** Identify high-risk tasks, potential blockers
- **Resource Estimation:** Estimate time, complexity for each subtask

**Tools Available:**
- Read, Glob, Grep, Bash (all tools)

**Context Type:** Automatic (user prompt)

**Use Cases:**
- "Plan implementation of user authentication feature"
- "How should I refactor this module?"
- "Break down the task of migrating to Kubernetes"

**Invocation Pattern:**
```python
Task(
    subagent_type="Plan",
    description="Plan auth implementation",
    prompt="Plan implementation of user authentication with JWT tokens"
)
```

---

## Agent Selection Guide

### Decision Tree

```
User Request
│
├─ Infrastructure change (terraform, GCP, AWS)?
│  └─ YES → terraform-architect
│
├─ Kubernetes/deployment/service change?
│  └─ YES → gitops-operator
│
├─ Diagnostic/troubleshooting?
│  ├─ GCP? → gcp-troubleshooter
│  └─ AWS? → aws-troubleshooter
│
├─ Application code/build/test?
│  └─ YES → devops-developer
│
├─ System/agent analysis?
│  └─ YES → Gaia
│
├─ Codebase exploration/understanding?
│  └─ YES → Explore
│
└─ Implementation planning?
   └─ YES → Plan
```

---

## Invocation Anti-Patterns

### ❌ DON'T: Use context_provider.py for Meta-Agents

```python
# WRONG
context = context_provider.py("gaia", "analyze logs")
Task(subagent_type="gaia", prompt=context)
```

**Why:** Meta-agents work on the SYSTEM, not projects. They need system paths, not project context.

**Correct:**
```python
Task(
    subagent_type="gaia",
    prompt=f"System path: {system_path}\n\nTask: analyze logs"
)
```

---

### ❌ DON'T: Skip Approval Gate for T3 Operations

```python
# WRONG
Task(subagent_type="gitops-operator", prompt="Deploy to prod")
# Skips Phase 4 Approval Gate
```

**Why:** T3 operations are irreversible. Approval is MANDATORY.

**Correct:**
```python
# Phase 3: Planning
plan = Task(subagent_type="gitops-operator", prompt="Generate deployment plan")

# Phase 4: Approval Gate (MANDATORY)
approval = approval_gate.py(plan)
if not approval["approved"]:
    halt_workflow()

# Phase 5: Realization
Task(subagent_type="gitops-operator", prompt=f"Execute: {plan}")
```

---

### ❌ DON'T: Over-Prompt Agents

```python
# WRONG
Task(
    subagent_type="terraform-architect",
    prompt="""
    Context: {...}

    Task: Create CloudSQL instance

    Instructions:
    1. First, read the terraform code
    2. Then, generate a new module
    3. Then, run terraform validate
    4. Then, return a realization package
    ...
    """
)
```

**Why:** The agent knows its own protocol. Over-prompting causes the agent to ignore its internal workflow.

**Correct:**
```python
Task(
    subagent_type="terraform-architect",
    prompt=f"{context}\n\nTask: Create CloudSQL instance"
)
```

---

## Agent Performance Metrics

Track in `.claude/logs/agent-metrics.jsonl`:

### Per-Agent Metrics

| Agent | Invocations | Success Rate | Avg Duration | Approval Rate |
|-------|-------------|--------------|--------------|---------------|
| terraform-architect | 234 | 94% | 45s | 87% |
| gitops-operator | 567 | 96% | 32s | 91% |
| gcp-troubleshooter | 123 | 98% | 28s | N/A |
| aws-troubleshooter | 45 | 96% | 31s | N/A |
| devops-developer | 189 | 92% | 18s | 15% |
| Gaia | 34 | 100% | 120s | N/A |
| Explore | 456 | 99% | 8s | N/A |
| Plan | 78 | 95% | 22s | N/A |

### Target Thresholds

- **Success Rate:** >90% for all agents
- **Avg Duration:** <60s for project agents, <10s for meta-agents
- **Approval Rate:** 80-90% (for agents with T3 operations)

---

## Version History

### 2.0.0 (2025-11-07)
- Extracted from CLAUDE.md monolith
- Added comprehensive capabilities, tools, use cases for each agent
- Added security tier definitions and enforcement
- Added invocation patterns with examples
- Added anti-patterns section
- Added performance metrics
- Distinguished project agents from meta-agents

### 1.x (Historical)
- Embedded in CLAUDE.md
- Basic agent list with roles
