---
name: investigation
description: How to investigate before acting - local-first analysis and pattern discovery
user-invocable: false
---

# Investigation Skill

## Core Principle: Local First, Cloud Second

**ALWAYS start with local analysis before querying cloud resources.**

```
1. LOCAL FIRST   - Glob, Grep, Read tools to analyze code
2. VALIDATION    - Read-only commands (terraform validate, kubectl get)
3. LIVE STATE    - Only if local analysis insufficient
```

## Investigation Checklist (MANDATORY)

### 1. Freshen Repository
```bash
git fetch && git status
```
- If behind remote → `git pull --ff-only`
- If diverged → STOP, report to user

### 2. Discover Existing Patterns

Use Claude Code tools (not shell commands):

```
# Find similar resources
Glob("terraform/**/*.hcl")     # Not: find terraform/ -name "*.hcl"
Glob("gitops/**/*.yaml")       # Not: find gitops/ -name "*.yaml"

# Search content
Grep("google_compute", path="terraform/")   # Not: grep -r "google_compute"

# Read examples
Read("terraform/vpc/terragrunt.hcl")        # Not: cat terraform/vpc/terragrunt.hcl
```

Read 2-3 similar resources. Extract: directory structure, naming, dependencies.

### 3. Pattern Extraction Template

```markdown
## Patterns Found
- **Structure:** [Where similar resources live]
- **Naming:** [Pattern: e.g., {env}-{service}-{resource}]
- **Config:** [How configs are structured]
- **Dependencies:** [What other resources are referenced]
```

### 4. Validation (Before Changes)

| Technology | Command | Expected |
|------------|---------|----------|
| Terraform | `terraform validate` | Success |
| Terragrunt | `terragrunt hclfmt --check` | No changes |
| Kubernetes | `kubectl apply --dry-run=client -f file.yaml` | Created (dry run) |
| Node.js | `npm run lint` or `tsc --noEmit` | No errors |

### 5. Comparison: Intended vs Actual

```markdown
## State Comparison
- **Intended** (from code): [What IaC says]
- **Actual** (from cloud): [What exists]
- **Discrepancies:** [Differences found]
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
    │   └─> Errors? → Report as Critical
    │
    ├─> Need live state?
    │   ├─> YES → Run fast-queries first, then targeted commands
    │   └─> NO → Propose based on patterns
    │
    └─> Present findings with AGENT_STATUS per agent-protocol skill
```

## Anti-Patterns

- Generating code without reading examples first
- Going to cloud before checking local files
- Proposing solutions before understanding current state
- Skipping validation
- Using `find`, `grep`, `cat` shell commands instead of Glob, Grep, Read tools
