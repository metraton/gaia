---
name: investigation
description: How to investigate before acting - local-first analysis and pattern discovery
phase: start
auto_load: true
---

# Investigation Skill

## Core Principle: Local First, Cloud Second

**ALWAYS start with local analysis before querying cloud resources.**

```
Priority Order:
1. LOCAL FIRST   - Read files, grep patterns, analyze code
2. VALIDATION    - Run read-only commands (terraform validate, kubectl get --dry-run)
3. LIVE STATE    - Only if local analysis insufficient
```

## Investigation Checklist (MANDATORY)

Before proposing ANY solution, complete these steps:

### 1. Freshen Repository
```bash
git fetch && git status
```
- If behind remote → `git pull --ff-only`
- If diverged → STOP, report to user

### 2. Discover Existing Patterns

**For Infrastructure (Terraform/Terragrunt):**
```bash
# Find similar resources
find terraform/ -name "*.hcl" -o -name "*.tf" | head -20

# Read 2-3 examples
# Extract patterns: directory structure, naming, module usage
```

**For Kubernetes (GitOps):**
```bash
# Find similar manifests
find gitops/ -name "*.yaml" | grep -E "(deployment|helmrelease)" | head -20

# Read 2-3 examples
# Extract patterns: namespace conventions, labels, resource limits
```

**For Application Code:**
```bash
# Find similar components/modules
find src/ -name "*.ts" -o -name "*.py" | head -20

# Read existing tests
# Extract patterns: testing conventions, naming, structure
```

### 3. Pattern Extraction Template

After reading examples, document:

```markdown
## Patterns Found

**Directory Structure:**
- [Where similar resources live]

**Naming Convention:**
- [Pattern: e.g., {env}-{service}-{resource}]

**Configuration Pattern:**
- [How configs are structured]

**Dependencies:**
- [What other resources/modules are referenced]
```

### 4. Validation (Before Changes)

Run validation commands:

| Technology | Validation Command | Expected |
|------------|-------------------|----------|
| Terraform | `terraform validate` | Success |
| Terragrunt | `terragrunt hclfmt --check` | No changes |
| Kubernetes | `kubectl apply --dry-run=client -f file.yaml` | Created (dry run) |
| Node.js | `npm run lint` or `tsc --noEmit` | No errors |
| Python | `flake8` or `mypy` | No errors |

### 5. Comparison: Intended vs Actual

For troubleshooting tasks, compare:

```markdown
## State Comparison

**Intended State** (from code):
- [What code/IaC says should exist]

**Actual State** (from cloud):
- [What actually exists]

**Discrepancies:**
- [Differences found]
```

## Finding Classification

Classify findings by severity:

| Tier | Severity | Action |
|------|----------|--------|
| **Tier 1 (CRITICAL)** | Blocks operation, security risk | STOP - Report immediately |
| **Tier 2 (DEVIATION)** | Works but non-standard | Note for remediation |
| **Tier 3 (OPTIMIZATION)** | Inefficient but functional | Suggest improvement |
| **Tier 4 (PATTERN)** | Pattern to replicate | Document for consistency |

## Output Format for Investigation

Present findings organized:

```markdown
## Investigation Results

### Summary
- [1-2 sentence summary]

### Findings by Tier
**Tier 1 (CRITICAL):**
- [Issue 1]: [Description]

**Tier 2 (DEVIATION):**
- [Issue 2]: [Description]

**Tier 4 (PATTERN):**
- [Pattern found]: [Description]

### Patterns Discovered
[Document patterns found during investigation]

### Recommendation
[What should happen next]
```

## Decision Tree

```
Start Investigation
    │
    ├─> git fetch, git status
    │   └─> Behind? → Pull before analyzing
    │
    ├─> Find similar resources (Glob)
    │   └─> Read 2-3 examples (Read)
    │       └─> Extract patterns
    │
    ├─> Run validation (T0)
    │   └─> Errors? → Report as Tier 1
    │
    ├─> Need live state?
    │   └─> YES → Query cloud (kubectl get, terraform show)
    │   └─> NO → Propose based on patterns
    │
    └─> Present findings with AGENT_STATUS: PENDING_APPROVAL or COMPLETE
```

## Anti-Patterns to Avoid

❌ **Generating code without reading examples first**
- Always read 2-3 similar resources before creating new ones

❌ **Going to cloud before checking local files**
- Local analysis is faster and respects quota limits

❌ **Proposing solutions before understanding current state**
- Investigation MUST complete before presenting solutions

❌ **Skipping validation**
- Always run `terraform validate`, `kubectl --dry-run`, etc.

## Integration with AGENT_STATUS

During investigation, emit:

```html
<!-- AGENT_STATUS -->
PLAN_STATUS: INVESTIGATING
CURRENT_PHASE: Investigation
PENDING_STEPS: ["Complete pattern analysis", "Run validation", "Present findings"]
NEXT_ACTION: Analyzing existing [resource type] patterns
AGENT_ID: [agentId]
<!-- /AGENT_STATUS -->
```

When investigation complete:
- If T3 operation needed → `PLAN_STATUS: PENDING_APPROVAL`
- If read-only task → `PLAN_STATUS: COMPLETE`
- If blocked → `PLAN_STATUS: BLOCKED`
- If need info → `PLAN_STATUS: NEEDS_INPUT`

## Example: Investigating Terraform Issue

```markdown
1. git fetch && git status
   ✓ Up to date with origin/main

2. Find similar Terragrunt files:
   terraform/vpc/terragrunt.hcl
   terraform/eks/terragrunt.hcl
   terraform/rds/terragrunt.hcl

3. Read terraform/vpc/terragrunt.hcl:
   - Pattern: include root terragrunt.hcl
   - Pattern: dependencies via dependency blocks
   - Pattern: naming: {env}-{resource}

4. Run validation:
   terragrunt validate
   ✓ Success

5. Check terraform state:
   terragrunt show
   [Output shows current VPC config]

6. Compare:
   Intended: CIDR 10.0.0.0/16
   Actual: CIDR 10.1.0.0/16
   → DISCREPANCY FOUND (Tier 2)

7. Present findings:
   "Found CIDR mismatch. Code says 10.0, cloud has 10.1.
    Recommend: Update code to match actual, or plan migration."
```
