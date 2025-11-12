# Git Commit Standards

**Version:** 2.0.0
**Last Updated:** 2025-11-07
**Parent:** CLAUDE.md
**Applies To:** Orchestrator AND all specialist agents

This document defines the universal commit message standards for this repository. ALL git commit operations MUST comply with these standards, regardless of who performs them (orchestrator or agent).

---

## Universal Validation Requirement

**CRITICAL:** Before EVERY `git commit`, the entity performing the commit (orchestrator or agent) MUST validate the commit message.

### Validation Code (MANDATORY)

```python
import sys
sys.path.insert(0, '$PROJECT_ROOT/.claude/tools')
from commit_validator import safe_validate_before_commit

# Validate commit message
if not safe_validate_before_commit(commit_message):
    # Validation failed - errors already printed to stderr
    return {
        "status": "failed",
        "reason": "commit_message_validation_failed"
    }

# Only if validation passes:
# Proceed with git commit
```

**Why this is mandatory:**
- Ensures consistency across all commit sources (orchestrator, agents)
- Enforces repository standards programmatically
- Logs violations for audit trail
- Prevents non-compliant commits from entering history

---

## Commit Message Format

### Standard: Conventional Commits

We use the [Conventional Commits](https://www.conventionalcommits.org/) specification.

**Format:**
```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Example:**
```
feat(helmrelease): add Phase 3.3 services

Deploys tcm-api and pg-query-api to non-prod-rnd cluster.
Includes HelmRelease configurations and health checks.

Refs: #234
```

---

## Type (Required)

The commit type MUST be one of the following:

| Type | Description | Example |
|------|-------------|---------|
| `feat` | New feature | `feat(api): add user authentication` |
| `fix` | Bug fix | `fix(pg-api): correct database connection timeout` |
| `refactor` | Code refactoring (no functional change) | `refactor(context): simplify enrichment logic` |
| `docs` | Documentation changes | `docs(readme): update installation instructions` |
| `test` | Test additions/modifications | `test(router): add agent selection tests` |
| `chore` | Maintenance tasks | `chore(deps): update kubectl to v1.28` |
| `ci` | CI/CD changes | `ci(github): add terraform validation workflow` |
| `perf` | Performance improvements | `perf(query): optimize database index usage` |
| `style` | Code style changes (formatting, semicolons, etc.) | `style(hooks): format with black` |
| `build` | Build system changes | `build(docker): update base image to alpine 3.19` |

**Invalid types will be REJECTED by commit_validator.py**

---

## Scope (Optional but Recommended)

The scope provides additional context about what part of the codebase is affected.

**Common scopes in this repository:**

### Infrastructure/GitOps
- `helmrelease`: HelmRelease manifests
- `deployment`: Kubernetes Deployment resources
- `service`: Kubernetes Service resources
- `ingress`: Kubernetes Ingress resources
- `configmap`: ConfigMaps
- `secret`: Secrets
- `namespace`: Namespace configurations

### Terraform
- `terraform`: General terraform changes
- `gke`: GKE cluster configuration
- `cloudsql`: CloudSQL instances
- `vpc`: VPC networking
- `iam`: IAM policies

### Agent System
- `orchestrator`: Orchestrator logic (CLAUDE.md)
- `agent`: Agent definitions
- `router`: Agent routing logic
- `context`: Context provider
- `approval`: Approval gate
- `clarify`: Clarification engine

### Services (use service name)
- `tcm-api`: TCM API service
- `pg-api`: PG API service
- `pg-query-api`: PG Query API service
- `tcm-admin-ui`: TCM Admin UI service

**Scope format:**
- Lowercase
- Hyphenated (not camelCase or snake_case)
- Singular (not plural): `deployment` not `deployments`

---

## Description (Required)

The description is a short summary of the change.

**Rules:**
- Maximum 72 characters (commit_validator.py enforces this)
- Start with lowercase letter
- Use imperative mood ("add feature" NOT "added feature" or "adds feature")
- No period at the end
- Be concise but descriptive

**Good Examples:**
```
add Phase 3.3 services to non-prod cluster
correct API key environment variable mappings
simplify context provider enrichment logic
update kubectl version to 1.28.4
```

**Bad Examples:**
```
Added new feature.           # ❌ Not imperative, has period
Update                       # ❌ Too vague
Adds the new user authentication feature to the API with JWT tokens and refresh logic  # ❌ Too long (>72 chars)
```

---

## Body (Optional)

The body provides additional context about the change.

**When to include a body:**
- Change is non-obvious or requires explanation
- Breaking changes need documentation
- Multiple related changes in one commit
- References to issues, tickets, or external docs

**Format:**
- Separate from description with blank line
- Wrap at 72 characters per line
- Use bullet points for multiple items
- Explain "why" rather than "what" (code shows "what")

**Example:**
```
feat(pg-api): add connection pooling

Implements HikariCP connection pooling to reduce database
connection overhead. Configuration:
- Max pool size: 10
- Connection timeout: 30s
- Idle timeout: 600s

This resolves intermittent "too many connections" errors
observed in non-prod environment.
```

---

## Footer (Optional)

Footers provide metadata about the commit.

### Allowed Footers

| Footer | Purpose | Example |
|--------|---------|---------|
| `BREAKING CHANGE:` | Indicates incompatible API change | `BREAKING CHANGE: remove deprecated /v1/users endpoint` |
| `Refs:` | References issue/ticket | `Refs: #234, #235` |
| `Closes:` | Closes issue/ticket | `Closes: #123` |
| `Fixes:` | Fixes issue/ticket | `Fixes: #456` |
| `Implements:` | Implements feature spec | `Implements: SPEC-789` |
| `See:` | Links to external resource | `See: https://docs.example.com/api-v2` |

**Format:**
- Each footer on separate line
- Footer key followed by colon and space
- Multiple values separated by commas

**Example:**
```
feat(api): add v2 endpoints

Refs: #234, #235
Implements: SPEC-789
BREAKING CHANGE: /v1/users endpoint removed, use /v2/users
```

---

### Forbidden Footers

**CRITICAL:** The following footers are FORBIDDEN and will cause validation to FAIL:

1. **Claude Code Attribution:**
   ```
   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

2. **Claude Co-Author:**
   ```
   Co-Authored-By: Claude <noreply@anthropic.com>
   ```

3. **Any variant:**
   - `Generated with Claude Code`
   - `Co-Authored-By: Claude`
   - Any line containing "Claude Code" or "Co-Authored-By: Claude"

**Reason:** This repository's policy is to NOT attribute AI-generated code in commit messages. Attribution happens at repository level (README, CLAUDE.md), not per-commit.

**Enforcement:** `commit_validator.py` scans commit message and REJECTS any commit with forbidden footers.

---

## Subject Line Rules (Enforced)

The subject line (type + scope + description) MUST comply with:

1. **Maximum length:** 72 characters
2. **No period:** Subject must not end with `.`
3. **Imperative mood:** "add" not "added" or "adds"
4. **Lowercase description:** First word of description is lowercase (unless proper noun)

**Validation:**
```python
# commit_validator.py checks:
if len(subject) > 72:
    return False, "Subject line exceeds 72 characters"

if subject.endswith('.'):
    return False, "Subject line must not end with period"

if not is_imperative_mood(description):
    return False, "Use imperative mood (add, fix, update)"
```

---

## Examples

### Valid Commits

**Simple feature:**
```
feat(helmrelease): add Phase 3.3 services
```

**Bug fix with body:**
```
fix(pg-non-prod): correct API key environment variable mappings

The API_KEY variable was pointing to wrong secret key.
Updated to use pg-api-credentials.api-key instead of
tcm-api-credentials.api-key.

Fixes: #345
```

**Refactoring with explanation:**
```
refactor(context): simplify enrichment logic

Replaces nested loops with dictionary comprehension for
service correlation. Improves performance by ~40% on
large project-context.json files.

No functional changes.
```

**Breaking change:**
```
feat(api): migrate to v2 endpoints

Updates all API routes to /v2 prefix with new request/response
formats. Deprecates v1 endpoints (to be removed in 3 months).

BREAKING CHANGE: /v1/users endpoint removed, use /v2/users
Refs: #567
See: https://docs.example.com/api-v2-migration
```

**Documentation:**
```
docs(claude): refactor CLAUDE.md to modular structure

Splits 380-line monolith into:
- CLAUDE.md (core instructions, 150 lines)
- orchestration-workflow.md (phases 0-5)
- git-standards.md (this file)
- context-contracts.md
- agent-catalog.md

Refs: #890
```

---

### Invalid Commits (Will Be Rejected)

**Bad type:**
```
added(helmrelease): add Phase 3.3 services
# ❌ Invalid type: "added" (should be "feat")
```

**Too long:**
```
feat(helmrelease): add Phase 3.3 services including tcm-api, pg-query-api, and pg-admin-ui to non-prod-rnd cluster
# ❌ Subject exceeds 72 characters
```

**Period at end:**
```
feat(helmrelease): add Phase 3.3 services.
# ❌ Subject ends with period
```

**Not imperative:**
```
feat(helmrelease): added Phase 3.3 services
# ❌ Not imperative mood ("added" should be "add")
```

**Forbidden footer:**
```
feat(helmrelease): add Phase 3.3 services

🤖 Generated with [Claude Code](https://claude.com/claude-code)
# ❌ Forbidden footer
```

**Wrong mood:**
```
fix(pg-api): fixes the database timeout issue
# ❌ Not imperative ("fixes" should be "fix")
```

---

## Configuration

Standards are defined in JSON schema: `.claude/config/git_standards.json`

**Schema:**
```json
{
  "allowed_types": ["feat", "fix", "refactor", "docs", "test", "chore", "ci", "perf", "style", "build"],
  "max_subject_length": 72,
  "forbidden_footers": [
    "Generated with [Claude Code]",
    "Co-Authored-By: Claude"
  ],
  "imperative_mood_required": true,
  "scope_format": "lowercase-hyphenated"
}
```

**Modifying standards:**
1. Edit `.claude/config/git_standards.json`
2. Run validation tests: `pytest .claude/tests/test_commit_validator.py`
3. Update this document to reflect changes
4. Increment version number in frontmatter

---

## Violation Logging

All commit validation violations are logged to: `.claude/logs/commit-violations.jsonl`

**Log Entry Format:**
```json
{
  "timestamp": "2025-11-07T15:42:33Z",
  "commit_message": "added new feature.",
  "violations": [
    {"type": "invalid_type", "message": "Type 'added' is not allowed"},
    {"type": "subject_format", "message": "Subject ends with period"}
  ],
  "source": "orchestrator",
  "rejected": true
}
```

**Uses:**
- Audit trail of rejected commits
- Analysis of common mistakes
- Training data for improving commit message generation
- Compliance reporting

---

## Git Operations: Who Does What

### Orchestrator-Level Commits

The orchestrator performs git commits for:
- **Ad-hoc commits** (user: "commitea los cambios")
- **Standalone operations** (user: "crea un PR")
- **Simple changes** (not part of infrastructure workflow)

**Process:**
1. Generate commit message (following standards)
2. Validate with `commit_validator.py`
3. If valid: Execute `git add`, `git commit`
4. If invalid: Report violations to user, regenerate

---

### Agent-Level Commits

Specialist agents perform git commits for:
- **Infrastructure workflows** (Phase 5 realization)
- **Deployment operations** (gitops-operator)
- **Terraform changes** (terraform-architect)
- **Complex multi-step workflows** (part of larger operation)

**Process:**
1. Receive realization package with commit message
2. Validate commit message with `commit_validator.py`
3. If valid: Execute `git add`, `git commit`, `git push`
4. If invalid: Fail realization, report to orchestrator

**Same validation applies:** Agents use identical validation logic.

---

## Integration with Tools

### commit_validator.py

Located at: `$PROJECT_ROOT/.claude/tools/commit_validator.py`

**Functions:**

```python
def safe_validate_before_commit(commit_message: str) -> bool:
    """
    Validates commit message against standards.

    Returns:
        True if valid, False if invalid

    Side effects:
        - Prints errors to stderr
        - Logs violations to commit-violations.jsonl
    """

def validate_commit_message(commit_message: str) -> tuple[bool, list[str]]:
    """
    Validates commit message, returns detailed errors.

    Returns:
        (is_valid, list_of_error_messages)
    """

def is_imperative_mood(text: str) -> bool:
    """
    Checks if text is in imperative mood.

    Returns:
        True if imperative, False otherwise
    """
```

**Usage in orchestrator:**
```python
from commit_validator import safe_validate_before_commit

msg = generate_commit_message(changes)
if not safe_validate_before_commit(msg):
    # Regenerate or ask user
    msg = regenerate_commit_message(changes, feedback)
    if not safe_validate_before_commit(msg):
        report_to_user("Cannot generate valid commit message")
        return

# Proceed with commit
```

**Usage in agents:**
```python
from commit_validator import safe_validate_before_commit

# Realization package includes commit message
commit_msg = realization_package["git_operations"]["commit_message"]

if not safe_validate_before_commit(commit_msg):
    return {
        "status": "failed",
        "reason": "commit_message_validation_failed",
        "message": "Orchestrator provided invalid commit message"
    }

# Proceed with git operations
```

---

### Pre-commit Hook Integration (Future)

**Future enhancement:** Integrate validation into git pre-commit hook.

**Benefit:** Catches violations even for manual git commits (outside Claude Code).

**Implementation:**
```bash
# .git/hooks/pre-commit

#!/bin/bash
COMMIT_MSG=$(git log -1 --pretty=%B)
python3 .claude/tools/commit_validator.py "$COMMIT_MSG"
if [ $? -ne 0 ]; then
    echo "Commit message validation failed"
    exit 1
fi
```

---

## Common Pitfalls

### Pitfall 1: Skipping Validation

**Symptom:** Non-compliant commits in git history

**Cause:** Entity (orchestrator or agent) executed `git commit` without calling `safe_validate_before_commit()`

**Fix:** Enforce validation in code (make commit_validator.py a hard dependency)

---

### Pitfall 2: Generating Non-Imperative Mood

**Symptom:** Commit messages like "added feature", "fixes bug"

**Cause:** LLM defaults to past tense or present tense

**Fix:** Explicitly instruct LLM: "Use imperative mood: add, fix, update (NOT added, fixed, updated)"

---

### Pitfall 3: Exceeding 72 Characters

**Symptom:** Commit messages truncated in git log, GitHub UI

**Cause:** LLM generates verbose descriptions

**Fix:** Count characters before validation, trim if necessary

---

### Pitfall 4: Including Forbidden Footers

**Symptom:** Commits rejected with "forbidden footer" error

**Cause:** Global Claude Code instructions add attribution footers

**Fix:** Override global instructions in CLAUDE.md with explicit "NO attribution footers"

---

### Pitfall 5: Vague Descriptions

**Symptom:** Commit messages like "update config", "fix bug"

**Cause:** Insufficient context provided to commit message generator

**Fix:** Analyze git diff, extract specific changes (file names, function names), include in description

---

## Testing Standards Compliance

### Manual Testing

```bash
# Test with sample commit messages
python3 .claude/tools/commit_validator.py "feat(helmrelease): add Phase 3.3 services"
# Expected: ✅ Valid

python3 .claude/tools/commit_validator.py "added new feature."
# Expected: ❌ Invalid (type, period)
```

### Automated Testing

```bash
# Run test suite
pytest .claude/tests/test_commit_validator.py -v

# Expected: 55+ tests, all passing
```

**Test categories:**
- Valid commit formats (10+ cases)
- Invalid types (5+ cases)
- Length violations (3+ cases)
- Forbidden footers (5+ cases)
- Imperative mood detection (10+ cases)
- Edge cases (special characters, unicode, etc.)

---

## Metrics

Track compliance metrics in `.claude/logs/commit-compliance.jsonl`:

- **Validation Pass Rate:** % of commit messages that pass validation on first attempt
- **Common Violations:** Top 5 validation errors
- **Source Distribution:** % of commits from orchestrator vs agents
- **Regeneration Rate:** % of commits that required regeneration after initial failure

**Target Thresholds:**
- Validation Pass Rate: >90%
- Regeneration Rate: <5%

---

## References

- **Conventional Commits Spec:** https://www.conventionalcommits.org/
- **Imperative Mood Guide:** https://chris.beams.io/posts/git-commit/#imperative
- **Git Commit Best Practices:** https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project

---

## Version History

### 2.0.0 (2025-11-07)
- Extracted from CLAUDE.md monolith
- Added comprehensive examples (valid and invalid)
- Clarified orchestrator vs agent responsibilities
- Added forbidden footers enforcement
- Added testing, metrics, pitfalls sections

### 1.x (Historical)
- Embedded in CLAUDE.md
- Basic Conventional Commits spec
- No automated validation