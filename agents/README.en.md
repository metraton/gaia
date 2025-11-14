# Gaia-Ops Specialist Agents

**[🇪🇸 Versión en español](README.md)**

Agents are AI specialists that handle specific tasks in your DevOps infrastructure. Each agent is an expert in a particular domain and works in coordination with the main orchestrator.

## 🎯 Purpose

Agents allow complex work to be divided into manageable specialties. Instead of having a single system that tries to do everything, each agent focuses on what they do best - like having a team of experts instead of a generalist.

**Problem it solves:** DevOps tasks are diverse and complex. A single agent cannot be an expert in everything (Terraform, Kubernetes, GCP, AWS, applications). Specialist agents enable depth of knowledge in each area.

## 🔄 How It Works

### Architecture Flow

```
User sends question
        ↓
[Orchestrator (CLAUDE.md)]
        ↓
[Agent Router] ← analyzes question
        ↓
   Selects appropriate agent
        ↓
    ┌───┴───┬───────┬────────┬─────────┬────────┐
    ↓       ↓       ↓        ↓         ↓        ↓
[terraform] [gitops] [gcp]  [aws]  [devops]  [gaia]
 architect  operator troubl. troubl. developer meta-agent
    ↓       ↓       ↓        ↓         ↓        ↓
    └───┬───┴───────┴────────┴─────────┴────────┘
        ↓
[Context Provider] ← provides relevant information
        ↓
Agent executes task
        ↓
Result to user
```

### Real Example Flow

```
Example: "Deploy auth service to production cluster"

1. User asks question
   ↓
2. [Orchestrator] receives request
   ↓
3. [Agent Router] analyzes keywords:
   - "deploy" → deployment operation
   - "service" → Kubernetes application
   - "cluster" → GitOps
   ↓
4. Router selects → **gitops-operator**
   ↓
5. [Context Provider] prepares information:
   - Current namespace
   - Existing releases
   - Cluster configuration
   ↓
6. [gitops-operator] receives context and question
   ↓
7. Agent generates plan:
   - Update deployment.yaml
   - Increment image version
   - Apply with kubectl
   ↓
8. [Approval Gate] requests confirmation (T3 operation)
   - Shows proposed changes
   - User approves ✅
   ↓
9. [gitops-operator] executes:
   - kubectl apply -f deployment.yaml
   - kubectl rollout status deployment/auth
   ↓
10. Verifies success:
    - Pods running: 3/3
    - Health checks: OK
    ↓
11. Reports result: "✅ auth deployed successfully to production"
```

## 📋 Available Agents

### 1. terraform-architect 🏗️
**Expert in:** Infrastructure as code

Handles everything related to Terraform and Terragrunt. Like the architect who designs and builds the foundations of your cloud infrastructure.

**When to use:**
- Create GKE clusters
- Configure VPCs and networks
- Manage storage buckets
- Configure IAM permissions

**Example questions:**
- "Create a new GKE cluster for staging environment"
- "Add an additional subnet in us-east1"

**Tiers:** T0 (read), T1 (validate), T2 (plan), T3 (apply)

---

### 2. gitops-operator ⚙️
**Expert in:** Kubernetes and deployments

Handles applications in Kubernetes, deployments, services and everything related to GitOps. Like the operator who keeps applications running in clusters.

**When to use:**
- Deploy services
- Update deployments
- Configure ingress
- Scale applications

**Example questions:**
- "Deploy version 1.2.3 of the backend"
- "Scale auth service to 5 replicas"

**Tiers:** T0 (read), T1 (validate), T2 (plan), T3 (apply)

---

### 3. gcp-troubleshooter 🔍
**Expert in:** Google Cloud Platform diagnostics

Identifies problems and gathers information about GCP resources. Like the detective who investigates what's happening in the cloud.

**When to use:**
- Diagnose GCP errors
- Review Cloud Logging logs
- Check resource status
- Analyze IAM permissions

**Example questions:**
- "Why is the cluster failing?"
- "Show auth service logs from the last 2 hours"

**Tiers:** T0 only (read-only, makes no changes)

---

### 4. aws-troubleshooter 🔍
**Expert in:** Amazon Web Services diagnostics

Similar to gcp-troubleshooter but for AWS. Diagnoses problems and gathers information about AWS resources.

**When to use:**
- Diagnose AWS errors
- Review CloudWatch logs
- Check EC2/EKS resource status
- Analyze IAM policies

**Example questions:**
- "Why is the EKS cluster failing?"
- "Show EC2 instance metrics"

**Tiers:** T0 only (read-only)

---

### 5. devops-developer 💻
**Expert in:** Application code and CI/CD

Works with application code, Dockerfiles, builds and tests. Like the developer who ensures code works correctly.

**When to use:**
- Create/modify Dockerfiles
- Configure npm/yarn builds
- Write automation scripts
- Configure CI pipelines

**Example questions:**
- "Optimize the backend Dockerfile"
- "Add unit tests to the service"

**Tiers:** T0 (read), T1 (validate), T2 (test builds)

---

### 6. Gaia 🧠
**Expert in:** The agent system itself

The meta-agent that understands how the entire orchestration system works. Like the systems architect who optimizes and documents the operation of the agents themselves.

**When to use:**
- Analyze system logs
- Optimize agent routing
- Improve documentation
- Diagnose orchestrator problems

**Example questions:**
- "Why did routing fail in this case?"
- "Analyze agent router accuracy"

**Tiers:** T0-T2 (analysis and proposals, doesn't execute changes)

## 🚀 How Agents Are Invoked

### Automatic Invocation (Recommended)

The orchestrator analyzes your question and automatically selects the appropriate agent:

```bash
# In Claude Code, simply ask:
"Deploy auth-service version 1.2.3"
# → Orchestrator automatically invokes gitops-operator
```

### Manual Invocation (Advanced)

For specific cases where you want to directly invoke an agent:

```bash
# Use the Task command
Task(
  subagent_type="gitops-operator",
  description="Deploy auth service",
  prompt="Deploy auth-service version 1.2.3 to production cluster"
)
```

## 🔧 Technical Details

### Agent Structure

Each agent is a Markdown file (`agent.md`) with these sections:

```markdown
---
name: agent-name
description: Brief description
tools: List of allowed tools
model: Model configuration
---

# Agent Name

[Comprehensive instructions for the agent]
```

### Security Tiers

Agents operate at different security levels:

| Tier | Description | Requires Approval |
|------|-------------|-------------------|
| **T0** | Read-only (get, describe, list) | No |
| **T1** | Validation (validate, dry-run, test) | No |
| **T2** | Planning (plan, simulate) | No |
| **T3** | Execution (apply, create, delete) | **Yes** ✅ |

**Important note:** T3 operations ALWAYS require explicit user approval through the Approval Gate.

### Smart Routing

The system uses multiple techniques to select the right agent:

1. **Keywords:** Domain-specific terms
2. **Semantic matching:** Semantic similarity using embeddings
3. **Context awareness:** Considers project context

**Current accuracy:** ~92.7% (based on tests)

## 📖 References

**Related documentation:**
- [Orchestration Workflow](../config/orchestration-workflow.md) - How a request flows
- [Agent Catalog](../config/agent-catalog.md) - Complete details of each agent
- [Context Contracts](../config/context-contracts.md) - What information each agent receives
- [Agent Router](../tools/1-routing/agent_router.py) - Routing code

**Agent files:**
```
agents/
├── terraform-architect.md    (~800 lines)
├── gitops-operator.md        (~750 lines)
├── gcp-troubleshooter.md     (~600 lines)
├── aws-troubleshooter.md     (~600 lines)
├── devops-developer.md       (~500 lines)
└── gaia.md                   (~1650 lines)
```

---

**Version:** 1.0.0  
**Last updated:** 2025-11-14  
**Total agents:** 6 specialists  
**Maintained by:** Gaia (meta-agent)

