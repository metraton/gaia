# Project Governance

This document defines the architectural principles and standards that guide all development decisions in this project.

## Stack Definition

### Infrastructure Layer
- **Cloud Provider**: Google Cloud Platform (GCP)
- **Region**: us-central1
- **Project ID**: aaxis-rnd-general-project
- **Infrastructure as Code**: Terraform + Terragrunt
- **Terraform Base Path**: `$PROJECT_ROOT/terraform`

### Kubernetes Layer
- **Orchestration**: Google Kubernetes Engine (GKE)
- **Cluster**: tcm-gke-non-prod
- **GitOps Tool**: Flux CD
- **GitOps Repository**: `$PROJECT_ROOT/gitops`
- **Package Manager**: Helm

### Application Layer
- **Primary Database**: PostgreSQL (Cloud SQL)
- **Instance**: tcm-postgres-non-prod
- **Container Registry**: Artifact Registry (us-central1-docker.pkg.dev)
- **Service Architecture**: Microservices with Helm charts

## Core Architectural Principles

### 1. Code-First Protocol (Mandatory)

**All agents MUST analyze existing patterns before creating new resources.**

When creating any new infrastructure or Kubernetes resource:
1. **Discover**: Search for similar existing resources in the codebase
2. **Read**: Examine 2-3 examples to identify patterns
3. **Extract**: Document directory structure, naming conventions, configuration patterns
4. **Replicate**: Follow discovered patterns, adapt only what's specific to the new resource
5. **Explain**: Document which pattern was followed and why

**Rationale**: Ensures consistency, reduces drift, and leverages proven configurations.

### 2. GitOps as Single Source of Truth

**All Kubernetes changes MUST go through Git, never direct cluster manipulation.**

- Infrastructure state lives in Terraform repository
- Application state lives in GitOps repository
- Flux CD synchronizes Git → Cluster
- Manual `kubectl apply` is forbidden in production workflows
- All changes require git commit → push → Flux reconciliation

**Rationale**: Auditability, rollback capability, and declarative state management.

### 3. Security Tier Enforcement

**All operations are classified by security tier with explicit approval gates.**

| Tier | Operations | Approval |
|------|-----------|----------|
| T0 | Read-only (get, describe, logs, plan) | Auto-approved |
| T1 | Validation (validate, template, lint) | Auto-approved |
| T2 | Simulation (dry-run, plan) | Auto-approved |
| T3 | Realization (apply, push, reconcile) | User approval required |

**Rationale**: Prevents accidental infrastructure changes and provides approval visibility.

### 4. Conventional Commits Standard

**All commits MUST follow Conventional Commits specification.**

Format: `<type>(<scope>): <description>`

Allowed types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`, `perf`, `style`, `build`

Examples:
- `feat(helmrelease): add report-generator worker service`
- `fix(terraform): correct VPC network peering configuration`
- `refactor(gitops): consolidate duplicate service account definitions`

**Rationale**: Enables automated changelog generation, semantic versioning, and clear change tracking.

### 5. Workload Identity for All Services

**All GKE services MUST use Workload Identity, not service account keys.**

- Create Kubernetes ServiceAccount with GCP IAM binding annotation
- Bind GCP Service Account with `iam.workloadIdentityUser` role
- Never use downloaded JSON keys in containers

**Rationale**: Eliminates static credentials, follows GCP security best practices, automatic key rotation.

### 6. Resource Limits as Default

**All Helm releases MUST define resource requests and limits.**

Standard tiers:
- **Small**: 256Mi/512Mi memory, 250m/500m CPU
- **Medium**: 512Mi/1Gi memory, 500m/1000m CPU
- **Large**: 1Gi/2Gi memory, 1000m/2000m CPU

**Rationale**: Prevents resource contention, enables proper scheduling, cost predictability.

## Decision Making Process

### When to Create an ADR

Create an Architecture Decision Record (ADR) when:
- Choosing between multiple viable technical approaches
- Making a decision that affects multiple services or teams
- Establishing a new pattern or standard
- Deviating from an existing principle for valid reasons

**ADR Template**: `.claude-shared/speckit/templates/adr-template.md`

**ADR Location**: `.claude-shared/speckit/decisions/`

### ADR Lifecycle

1. **Draft**: Create ADR with status "Proposed"
2. **Review**: Share with team (if applicable)
3. **Decide**: Update status to "Accepted" or "Rejected"
4. **Implement**: Reference ADR number in commits
5. **Archive**: ADRs are immutable; superseding decisions get new ADRs

**Rationale**: Decisions are immutable historical records. Context matters more than current state.

## Standards Evolution

### How to Update This Document

1. Identify need for principle change
2. Create ADR documenting the proposed change and rationale
3. If ADR is accepted, update this governance.md
4. Commit both files together with message: `docs(governance): [description] (ADR-XXX)`

### Versioning Strategy

This document uses **Git history as version control**. No semantic versioning.

- To see why a principle exists: `git log -p governance.md`
- To see decision context: Read corresponding ADR
- To propose changes: Create ADR first, then update

**Rationale**: Git already provides perfect versioning. Additional versioning adds complexity without value.

## Compliance

### Enforcement

These principles are enforced through:
1. **Agent Protocols**: Specialized agents (terraform-architect, gitops-operator) follow these standards
2. **Validation Hooks**: Pre-commit and pre-tool-use hooks validate compliance
3. **Security Tiers**: Permission system blocks non-compliant operations
4. **Code Review**: Human review of T3 operations before realization

### Exceptions

Exceptions to these principles require:
1. ADR documenting the exception and time-bound context
2. Explicit approval in the ADR review process
3. Scheduled re-evaluation date in ADR

**No permanent exceptions.** All deviations must justify their continued existence or be removed.

---

**Last Updated**: 2025-11-05
**Related ADRs**: None yet (foundation document)
**Supersedes**: constitution.md (deprecated)
