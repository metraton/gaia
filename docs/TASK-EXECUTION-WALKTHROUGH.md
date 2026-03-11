# Task Execution Walkthrough: payment-gateway

A concrete end-to-end example showing how a multi-surface feature flows through
the gaia-ops system -- from messy conversation to completed tasks.

**Scenario:** "Deploy a new microservice called `payment-gateway` that needs to
be in the K8s cluster with a HelmRelease, Terraform for the Cloud SQL database,
and a CI/CD pipeline."

---

## Step 1: Spec (Orchestrator + User Conversation)

The orchestrator activates the `specification` skill when the user says "I want
to deploy a new microservice called payment-gateway..." The conversation goes
like this:

**Orchestrator (after reading governance.md):** "Before I draft the spec, a few
questions. What problem does the payment-gateway solve? Is it replacing an
existing payment flow or is this net-new functionality?"

**User:** "Net-new. We're integrating with Stripe for order payments. Right now
orders are created but there's no payment step."

**Orchestrator:** "Got it. Who initiates a payment -- the customer directly, or
does the order service trigger it automatically?"

**User:** "The order service calls payment-gateway after an order is confirmed.
Customers never talk to it directly."

**Orchestrator:** "One more -- should payment-gateway handle refunds, or is
that out of scope for now?"

**User:** "Out of scope. Just charge processing."

The orchestrator drafts and presents `spec.md`:

### specs/payment-gateway/spec.md

```markdown
# Feature Specification: Payment Gateway

**Created**: 2026-03-11
**Status**: Draft

## Problem Statement

Orders can be created in the system but there is no payment step. Confirmed
orders proceed without collecting payment, leaving revenue capture entirely
manual. The order service needs an internal microservice that processes
charges through Stripe so that payment happens automatically at order
confirmation time.

## User Stories

1. As the **order service**, I want to call a payment endpoint after order
   confirmation, so that the customer is charged without manual intervention.

2. As an **operations engineer**, I want payment processing to be observable
   (logs, metrics, health checks), so that I can detect and respond to
   payment failures.

3. As a **finance team member**, I want every charge attempt to be recorded
   with its outcome (success/failure/amount), so that reconciliation is
   possible without querying Stripe directly.

## Acceptance Criteria

1. Given a confirmed order with a valid payment method,
   when the order service calls payment-gateway,
   then a Stripe charge is created for the order amount and a success
   response is returned within 5 seconds.

2. Given a confirmed order with an invalid payment method,
   when the order service calls payment-gateway,
   then the charge fails gracefully, the failure is recorded, and the
   order service receives a structured error response.

3. Given payment-gateway is deployed,
   when an operations engineer checks its health,
   then a health endpoint confirms the service is running and can reach
   Stripe's API.

4. Given any charge attempt (success or failure),
   when the attempt completes,
   then a record is persisted with: order ID, amount, currency, Stripe
   charge ID (if successful), status, and timestamp.

## Scope Boundaries

| IN scope | OUT of scope |
|----------|-------------|
| Charge processing via Stripe | Refunds and disputes |
| Charge outcome recording | Customer-facing payment UI |
| Health and readiness endpoints | Multi-provider support (only Stripe) |
| Internal service (order service is the only caller) | Direct customer API access |
| GKE deployment with HelmRelease | Performance optimization beyond basic targets |

## Constraints (from governance)

- Must deploy via GitOps (Flux CD reconciliation, no manual kubectl apply)
- Must use Workload Identity for Stripe API key access (no JSON key files)
- Must define resource requests and limits (small tier: 256Mi/512Mi, 250m/500m)
- Infrastructure provisioned via Terraform + Terragrunt
- Conventional commits for all changes

## Key Entities

- **Charge Record** -- represents a single payment attempt. Related to an
  order (by order ID). Tracks amount, currency, status, Stripe reference,
  and timestamp.
- **Payment Method** -- reference to the customer's payment instrument.
  Passed by the order service, not stored by payment-gateway.

## Edge Cases

- What happens if Stripe is unreachable? Service returns a retriable error;
  the order service decides retry policy.
- What happens on duplicate charge requests for the same order? Payment-gateway
  is idempotent on order ID -- returns the existing charge record.
```

The user reviews, requests one change ("add idempotency to the acceptance
criteria"), the orchestrator updates, the user approves, and the spec is saved
to `specs/payment-gateway/spec.md`.

---

## Step 2: Plan (speckit-planner)

The orchestrator delegates to speckit-planner: "Create a plan for
specs/payment-gateway/spec.md. The spec is approved."

speckit-planner reads the spec, reads governance.md, and produces `plan.md`.
This is where architecture decisions happen -- constrained by governance.

### specs/payment-gateway/plan.md (key sections)

```markdown
# Implementation Plan: Payment Gateway

**Spec**: specs/payment-gateway/spec.md

## Summary

Internal gRPC microservice deployed to GKE via Flux CD. Processes Stripe
charges on behalf of the order service. Persists charge records to a
dedicated Cloud SQL PostgreSQL instance. Uses Workload Identity for
Stripe secret access via GCP Secret Manager.

## Technical Context

**Language/Version**: Go 1.22 (matches existing microservices)
**Primary Dependencies**: Stripe Go SDK, gRPC, pgx (PostgreSQL driver)
**Storage**: Cloud SQL PostgreSQL (dedicated instance for payment data)
**Testing**: go test, grpcurl for contract testing
**Target Platform**: GKE (oci-pos-dev-cluster)

## Architecture Decisions

1. **gRPC over REST** -- internal service-to-service; order service already
   uses gRPC for inter-service calls. Aligns with existing patterns.

2. **Dedicated Cloud SQL instance** -- payment data has compliance
   sensitivity. Separate instance allows independent backup policy and
   access controls. Provisioned via Terraform.

3. **Stripe secret via Secret Manager + Workload Identity** -- the service's
   K8s ServiceAccount is annotated for Workload Identity. A GCP Service
   Account with `secretmanager.secretAccessor` role retrieves the Stripe
   API key at startup. No key files in containers.

4. **Idempotency via order ID** -- charge_records table has a unique
   constraint on order_id. Duplicate requests return the existing record.

## Constitution Check

| Check | Status |
|-------|--------|
| GitOps deployment (no manual kubectl) | PASS |
| Workload Identity (no JSON keys) | PASS |
| Resource limits defined | PASS (small tier) |
| Health checks present | PASS (gRPC health + HTTP readiness) |
| No :latest image tags | PASS (semver tags from CI) |
| Conventional commits | PASS |
```

speckit-planner also generates `data-model.md`, `contracts/`, and
`research.md`. The plan is presented for approval. User approves.

---

## Step 3: Tasks (speckit-planner)

speckit-planner generates tasks from the approved plan. Here are 5 example
tasks in the intent-based format -- note that they describe WHAT to achieve,
not HOW to achieve it:

### specs/payment-gateway/tasks.md (excerpt)

```markdown
## Milestone 1: Infrastructure Foundation

### T001: Provision Cloud SQL instance for payment data [ ]
- **Intent**: The payment-gateway needs a PostgreSQL database. Provision
  the Cloud SQL instance following existing Terraform patterns in the
  repository.
- **Context**: Plan specifies a dedicated Cloud SQL PostgreSQL instance.
  Existing infrastructure uses Terraform + Terragrunt. The database
  stores charge records with compliance sensitivity requiring a
  separate instance from the shared database.
- **Exit contract**:
  - Terraform plan shows the Cloud SQL instance will be created in us-east4
  - Instance name follows the project naming convention
  - Backup configuration is enabled
  - Private IP networking is configured (no public IP)
- **References**: plan.md Architecture Decision #2, governance.md Principle #1
- **Suggested-agent**: terraform-architect
- **Tier**: T3

---

### T002: Create GCP Service Account and Workload Identity binding [ ]
- **Intent**: The payment-gateway pod needs to access Secret Manager for
  the Stripe API key. Set up the GCP Service Account, IAM bindings, and
  Workload Identity annotation so that the K8s ServiceAccount can
  authenticate as the GCP SA.
- **Context**: Plan specifies Workload Identity (governance principle #5).
  The GCP SA needs `secretmanager.secretAccessor` on the Stripe key
  secret and `cloudsql.client` for database access. Existing services
  in the cluster use the same pattern.
- **Exit contract**:
  - GCP Service Account exists with the correct IAM roles
  - Workload Identity binding connects the K8s SA to the GCP SA
  - Terraform plan shows all resources
- **References**: plan.md Architecture Decision #3, governance.md Principle #5
- **Suggested-agent**: terraform-architect
- **Tier**: T3
- **Depends-on**: none

---

### T003: Create HelmRelease and Kubernetes manifests [ ]
- **Intent**: Define the payment-gateway deployment in the GitOps
  repository so that Flux CD can reconcile it to the cluster. Include
  the Helm chart, HelmRelease, namespace, ServiceAccount, and
  NetworkPolicy.
- **Context**: Plan specifies GKE deployment via Flux CD (governance
  principle #2). Resource limits: small tier (256Mi/512Mi memory,
  250m/500m CPU). The service exposes gRPC on port 50051 and HTTP
  health on port 8080. K8s ServiceAccount must have Workload Identity
  annotation from T002.
- **Exit contract**:
  - HelmRelease manifest passes `flux diff kustomization`
  - Resource limits match small tier
  - Health check probes are configured (gRPC liveness, HTTP readiness)
  - ServiceAccount annotation references the GCP SA from T002
  - No `:latest` image tags
- **References**: plan.md Architecture Decisions #1 and #3, governance.md Principles #2 and #6
- **Suggested-agent**: gitops-operator
- **Tier**: T3
- **Depends-on**: T002

---

### T004: Create CI/CD pipeline for payment-gateway [ ]
- **Intent**: The payment-gateway needs a build and deploy pipeline that
  compiles the Go binary, runs tests, builds a container image, pushes
  it to the registry, and triggers a Flux reconciliation.
- **Context**: Plan specifies Go 1.22, semver image tags (no :latest),
  conventional commits. Existing services in the repository have CI
  pipelines that can be used as a pattern. The pipeline must produce
  a container image tagged with the git semver tag.
- **Exit contract**:
  - Pipeline configuration file exists and passes linting/validation
  - Pipeline stages include: test, build, push, tag
  - Image tag strategy uses semver (not :latest)
  - Pipeline triggers on push to main and on tags
- **References**: plan.md Technical Context, governance.md Principle #4
- **Suggested-agent**: devops-developer
- **Tier**: T3
- **Depends-on**: none

---

### T005: Milestone 1 Quality Gate [ ]
- **Intent**: Validate that all infrastructure foundation components are
  in place and consistent with each other before proceeding to
  application implementation.
- **Context**: T001-T004 must all be complete. Cross-check that the
  Terraform outputs (database connection, SA email) match the values
  referenced in the HelmRelease and CI pipeline.
- **Exit contract**:
  - Terraform plan for T001 and T002 shows no pending changes
  - HelmRelease references the correct ServiceAccount
  - CI pipeline references a valid container registry path
  - All cross-references between infra and GitOps are consistent
- **Suggested-agent**: devops-developer
- **Tier**: T0
- **Depends-on**: T001, T002, T003, T004
```

Key differences from the old task format:
- **Intent** replaces prescriptive description -- says WHAT to achieve, not HOW
- **Exit contract** has testable criteria -- the orchestrator uses these to verify
- **No file names or class names** -- the agent discovers those from codebase patterns
- **References** point to the plan for context -- the agent reads them if needed
- **Suggested-agent** is a suggestion, not a binding assignment

---

## Step 4: Task Execution (Orchestrator -> Agent)

Walking through **T001: Provision Cloud SQL instance** end to end.

### 4.1 Orchestrator reads the task

The orchestrator reads `tasks.md` and identifies T001 as the next pending
task (unchecked `- [ ]`). It reads the task metadata.

### 4.2 Routes to the right agent

Routing decision:
- `suggested-agent: terraform-architect` -- noted as suggestion
- Surface signals: "Cloud SQL", "Terraform", "Terragrunt" -- matches
  `terraform_iac` surface in the routing table
- Routing table says `terraform_iac` -> `terraform-architect`
- Suggestion and routing table agree -> route to `terraform-architect`

The orchestrator spawns:

```
subagent_type: "terraform-architect"
description:   "Provision Cloud SQL for payment-gateway"
prompt:        "Execute task T001 from specs/payment-gateway/tasks.md.
                Intent: Provision a Cloud SQL PostgreSQL instance for the
                payment-gateway service. Read the task's full context and
                exit contract from the tasks file. Read plan.md for
                architecture decisions. Follow existing Terraform patterns
                in the repository."
```

### 4.3 Agent investigates first

terraform-architect follows the investigation skill:

1. **Reads plan.md** -- extracts Architecture Decision #2 (dedicated
   instance, compliance sensitivity, private IP)
2. **Reads governance.md** -- confirms Terraform + Terragrunt, us-east4 region
3. **Searches for existing Cloud SQL patterns** -- finds 2 existing instances
   in `terraform/modules/cloudsql/` and `terraform/environments/dev/cloudsql/`
4. **Extracts patterns** -- naming convention: `{project}-{service}-db`,
   backup enabled, private IP via VPC peering, Terragrunt wrapper

### 4.4 Agent starts implementing

terraform-architect creates a plan:
- New Terragrunt configuration at `terraform/environments/dev/payment-gateway-db/`
- Reuses existing `cloudsql` module
- Sets instance name: `oci-pos-dev-payment-gateway-db`
- Enables backups, private IP, no public IP

### 4.5 Agent hits a blocker -- needs user input

While configuring the instance, the agent needs to decide the Cloud SQL tier.
The plan says "dedicated instance" but does not specify the machine type.

The agent returns:

```
<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- Existing Cloud SQL instances in terraform/environments/dev/
FILES_CHECKED:
- terraform/modules/cloudsql/variables.tf
- terraform/environments/dev/shared-db/terragrunt.hcl
COMMANDS_RUN:
- `not run` (local investigation only)
KEY_OUTPUTS:
- Existing instances use db-f1-micro (shared) and db-custom-2-4096 (dedicated)
- No standard tier defined in governance for Cloud SQL instances
VERBATIM_OUTPUTS:
- none
CROSS_LAYER_IMPACTS:
- Cloud SQL tier affects cost and performance targets from spec
OPEN_GAPS:
- Machine type not specified in plan.md or governance.md
<!-- /EVIDENCE_REPORT -->

The plan specifies a dedicated instance but does not define the machine type.
Existing instances in the repository use two different tiers:

**Option A**: `db-f1-micro` -- shared CPU, 614MB RAM. Used by the shared dev
database. Cost: ~$10/month. Sufficient for dev/testing but not for production
payment workloads.

**Option B**: `db-custom-2-4096` -- 2 vCPUs, 4GB RAM. Used by the dedicated
reporting database. Cost: ~$100/month. Appropriate for a service handling
financial transactions.

Which tier should the payment-gateway database use?

<!-- AGENT_STATUS -->
PLAN_STATUS: NEEDS_INPUT
PENDING_STEPS: [Create Terragrunt config, Run terraform plan, Present for approval]
NEXT_ACTION: Awaiting Cloud SQL tier selection
AGENT_ID: tf-arch-abc123
<!-- /AGENT_STATUS -->
```

### 4.6 Orchestrator processes NEEDS_INPUT

The orchestrator sees `PLAN_STATUS: NEEDS_INPUT`, extracts the options, and
presents them to the user:

**Orchestrator to user:** "The terraform-architect needs a decision on the
Cloud SQL tier for payment-gateway's database:

- **Option A**: db-f1-micro (shared CPU, $10/month) -- sufficient for dev
- **Option B**: db-custom-2-4096 (2 vCPU, 4GB, $100/month) -- production-grade

Which tier should we use?"

### 4.7 User responds

**User:** "Option A for now, we're still in dev. We'll upgrade before prod."

### 4.8 Orchestrator resumes agent

```
resume:  "tf-arch-abc123"
prompt:  "Continue: User selected Option A (db-f1-micro). This is for dev
          environment. Proceed with the implementation."
```

### 4.9 Agent completes

terraform-architect finishes the Terragrunt configuration, runs
`terraform plan` (T2 -- no approval needed), and presents the T3
realization plan for approval. After user approval, it applies.

The agent returns:

```
<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- terraform/environments/dev/shared-db/terragrunt.hcl (naming, structure)
- terraform/modules/cloudsql/ (module interface)
FILES_CHECKED:
- terraform/environments/dev/payment-gateway-db/terragrunt.hcl (created)
- terraform/modules/cloudsql/main.tf
- terraform/modules/cloudsql/variables.tf
COMMANDS_RUN:
- `terragrunt plan -chdir=terraform/environments/dev/payment-gateway-db` -> 3 resources to add
- `terragrunt apply -chdir=terraform/environments/dev/payment-gateway-db` -> Apply complete, 3 added
KEY_OUTPUTS:
- Cloud SQL instance oci-pos-dev-payment-gateway-db created in us-east4
- Private IP assigned, backup enabled, db-f1-micro tier
VERBATIM_OUTPUTS:
- `terragrunt apply`:
  ```
  google_sql_database_instance.main: Creating...
  google_sql_database_instance.main: Creation complete after 4m32s [id=oci-pos-dev-payment-gateway-db]
  google_sql_database.main: Creating...
  google_sql_database.main: Creation complete after 1s [id=oci-pos-dev-payment-gateway-db/payment_gateway]
  google_sql_user.main: Creating...
  google_sql_user.main: Creation complete after 1s

  Apply complete! Resources: 3 added, 0 changed, 0 destroyed.
  ```
CROSS_LAYER_IMPACTS:
- GitOps manifests (T003) need the database connection name from Terraform output
- Workload Identity (T002) needs cloudsql.client IAM role
OPEN_GAPS:
- none
<!-- /EVIDENCE_REPORT -->

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: none
AGENT_ID: tf-arch-abc123
<!-- /AGENT_STATUS -->
```

### 4.10 Orchestrator validates exit contract

The orchestrator checks T001's exit contract against the agent's evidence:

| Exit criterion | Evidence | Met? |
|---|---|---|
| Terraform plan shows Cloud SQL instance in us-east4 | Apply output: `oci-pos-dev-payment-gateway-db` in us-east4 | Yes |
| Instance name follows naming convention | `oci-pos-dev-payment-gateway-db` matches `{project}-{service}-db` | Yes |
| Backup configuration enabled | Agent confirmed in KEY_OUTPUTS | Yes |
| Private IP, no public IP | Agent confirmed in KEY_OUTPUTS | Yes |

All criteria met.

### 4.11 Orchestrator marks completion

The orchestrator delegates a file edit to update `tasks.md`:

```markdown
### T001: Provision Cloud SQL instance for payment data [x]
```

The orchestrator reports to the user:

"T001 complete -- Cloud SQL instance `oci-pos-dev-payment-gateway-db`
provisioned in us-east4 with private IP and backups enabled. db-f1-micro
tier as selected. Evidence: 2 commands executed -- ask for details.

Next task: T002 (Create GCP Service Account and Workload Identity binding).
Proceed?"

---

## Step 5: What if the Agent Cannot Complete?

Walking through a failure scenario for **T003: Create HelmRelease**.

### The setup

The orchestrator routes T003 to `gitops-operator`. The agent investigates
existing HelmRelease patterns and discovers a problem.

### Agent reports BLOCKED

```
<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- gitops/clusters/dev/helmreleases/ (existing HelmRelease manifests)
- gitops/clusters/dev/sources/ (HelmRepository sources)
FILES_CHECKED:
- gitops/clusters/dev/helmreleases/order-service.yaml
- gitops/clusters/dev/sources/internal-charts.yaml
COMMANDS_RUN:
- `flux get sources helm -n flux-system` -> 3 sources found
KEY_OUTPUTS:
- Existing HelmReleases reference charts from an internal Helm repository
- The internal Helm repository (internal-charts) does not yet contain
  a payment-gateway chart
- The CI pipeline (T004) is responsible for publishing the chart, but
  T004 is not yet complete and T003 depends on T004 for the chart to exist
VERBATIM_OUTPUTS:
- `flux get sources helm -n flux-system`:
  ```
  NAME              REVISION        SUSPENDED  READY  MESSAGE
  bitnami           sha256:abc...   False      True   stored artifact: revision 'sha256:abc...'
  internal-charts   sha256:def...   False      True   stored artifact: revision 'sha256:def...'
  grafana           sha256:ghi...   False      True   stored artifact: revision 'sha256:ghi...'
  ```
CROSS_LAYER_IMPACTS:
- T004 (CI pipeline) must publish the Helm chart before this HelmRelease
  can reference a valid chart version
- Dependency graph in tasks.md may have a gap: T003 does not list T004
  as a dependency, but functionally it needs the chart to exist
OPEN_GAPS:
- Cannot create a valid HelmRelease without a chart version to reference
<!-- /EVIDENCE_REPORT -->

I cannot create a fully valid HelmRelease because the payment-gateway Helm
chart does not exist yet in the internal chart repository. The CI pipeline
(T004) publishes charts, and T003 does not declare a dependency on T004.

**Options:**

1. **Create HelmRelease with a placeholder chart version** -- the manifest
   will be syntactically valid but Flux will fail to reconcile until the
   real chart is published. This is the pattern used by order-service
   during its initial setup.

2. **Block until T004 completes** -- reorder tasks so T004 runs first,
   publishes the chart, then T003 references the real version.

3. **Create the Helm chart structure locally without CI** -- add the chart
   directory to the GitOps repo so the HelmRelease can reference it, then
   T004 automates future updates.

<!-- AGENT_STATUS -->
PLAN_STATUS: BLOCKED
PENDING_STEPS: [Create HelmRelease, Create namespace manifest, Create ServiceAccount, Create NetworkPolicy]
NEXT_ACTION: Awaiting user decision on dependency resolution
AGENT_ID: gitops-op-def456
<!-- /AGENT_STATUS -->
```

### Orchestrator presents alternatives

The orchestrator sees `PLAN_STATUS: BLOCKED` and presents the situation:

**Orchestrator to user:** "T003 (HelmRelease creation) is blocked. The
gitops-operator found that the payment-gateway Helm chart does not exist
yet -- T004 (CI pipeline) publishes it, but T003 does not depend on T004
in the current task ordering.

Three options:
1. **Placeholder version** -- create the HelmRelease now with a placeholder chart version. Flux will not reconcile until the real chart is published. This matches the pattern used by order-service during its setup.
2. **Reorder tasks** -- run T004 first, then T003.
3. **Local chart** -- create the chart structure in the GitOps repo without CI.

Which approach?"

**User:** "Option 1 -- placeholder is fine, that's how we did order-service."

### Orchestrator resumes

```
resume:  "gitops-op-def456"
prompt:  "Continue: User selected Option 1 -- create HelmRelease with a
          placeholder chart version. This matches the order-service pattern.
          Proceed with the implementation."
```

The agent proceeds, creates the manifests with a placeholder version, and
completes successfully.

---

## Key Observations

**Separation of concerns is real.** The spec has no file names. The plan has
architecture decisions but no line-by-line instructions. Tasks have intent
and exit contracts. The agent figures out the implementation by reading
codebase patterns.

**The orchestrator never executes.** It reads tasks, routes to agents,
processes their status, presents decisions to the user, and marks
completion. It validates exit contracts from agent evidence, not by
running commands itself.

**Surface routing works.** T001 and T002 route to `terraform-architect`
(terraform_iac surface). T003 routes to `gitops-operator`
(gitops_desired_state surface). T004 routes to `devops-developer`
(app_ci_tooling surface). The routing table, not the task metadata, is
authoritative.

**Agents discover, not prescribe.** T001 does not say "create a file at
terraform/environments/dev/payment-gateway-db/terragrunt.hcl." The agent
investigates existing patterns and discovers that path itself. This means
the task survives repository reorganization.

**Failure paths are explicit.** When an agent is blocked, it surfaces
options with trade-offs. The orchestrator never guesses -- it presents
the options and waits for the user.
