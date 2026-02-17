# Changelog: CLAUDE.md

All notable changes to the CLAUDE.md orchestrator instructions are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.12.0] - 2026-02-17

### Refactor: Principle-First Skills & Agent Deduplication

Major redesign of skills and agents. Skills now teach principles instead of enumerating commands. Agents delegate process knowledge to skills, keeping only domain identity.

#### Removed
- **`skills/anti-patterns/`** - Merged into `command-execution` skill as defensive execution principles

#### Changed
- **`skills/command-execution/SKILL.md`** - Complete rewrite with defensive execution framework
  - Timeout hierarchy (tool-native → shell wrapper → abort)
  - Pre-flight checklist ("Can this hang?" / "Do I know the timeout?")
  - 7 numbered rules: no pipes, one command per step, Claude Code tools over bash, validate before mutate, absolute paths, files over inline data, quote variables
- **`skills/security-tiers/SKILL.md`** - Changed from command enumeration to decision framework
  - Classification by question: "Does it modify live state?" → T3
- **`skills/terraform-patterns/SKILL.md`** - Split into slim SKILL.md (86 lines) + reference.md
- **`skills/gitops-patterns/SKILL.md`** - Split into slim SKILL.md (94 lines) + reference.md
- **`skills/fast-queries/SKILL.md`** - Cut from 256 to 41 lines (essentials only)
- **`skills/investigation/SKILL.md`** - Fixed to use Glob/Grep/Read tools, removed duplicated content
- **`skills/output-format/SKILL.md`** - Removed dead escalation protocol
- **`skills/execution/SKILL.md`** - Consolidated commit format to git-conventions reference
- **`skills/approval/SKILL.md`** - Removed duplicated commit standards and AskUserQuestion section
- **All 6 agents** - Removed duplicated Before Acting, Investigation Protocol, Pre-loaded Standards, and command enumeration tier tables

#### Added
- **`skills/reference.md`** - Agent template and npm release checklist (moved from gaia agent)
- **`skills/terraform-patterns/reference.md`** - Full HCL examples
- **`skills/gitops-patterns/reference.md`** - Full YAML examples
- **`investigation` skill** assigned to cloud-troubleshooter, terraform-architect, gitops-operator, devops-developer, gaia
- **`git-conventions` skill** assigned to terraform-architect, gitops-operator, devops-developer
- **`agent-protocol` + `security-tiers` skills** assigned to speckit-planner

#### Metrics
- Skills: 1,865 → 725 lines (-61%)
- Agents: 1,914 → 1,007 lines (-47%)
- Total injected tokens significantly reduced
- All 882 tests pass

## [3.11.0] - 2026-02-16

### feat: 3-Layer E2E Testing System

Added Layer 1 prompt regression tests (86 tests) validating agent frontmatter, prompt content, skill cross-references, context contracts, security tier consistency, routing table, and skill content rules.

## [3.7.0] - 2026-01-20

### Refactor: Commit Validator Architecture

Moved commit validation to hooks system for better encapsulation and clearer separation of concerns.

#### Changed
- **commit_validator.py location**: Moved from `tools/validation/` to `hooks/modules/validation/`
- **bash_validator.py imports**: Updated to use relative import from sibling module
- **Module structure**: commit_validator.py now exclusively used by bash_validator.py (no direct imports)
- **Documentation**: Updated tools/validation/README.md to reflect new architecture

#### Technical Details
- bash_validator.py now uses relative import: `from ..validation.commit_validator import validate_commit_message`
- commit_validator.py path resolution updated for new location (4 dirname calls instead of 3)
- pre-publish-validate.js updated to validate new path
- tools/validation/__init__.py no longer exports commit_validator (internal use only)

#### Benefits
- Better encapsulation: commit validation only accessible through bash_validator
- Clearer architecture: validation logic properly contained within hooks system
- No breaking changes: commit validation continues to work identically

## [3.6.1] - 2026-01-20

### Fix: Include skills/ directory in npm package

#### Fixed
- **package.json files array**: Added `"skills/"` to ensure skills directory is published to npm
- This was preventing skills/standards/ from being available in v3.6.0

## [3.6.0] - 2026-01-20

### Standards Migration to Skills System

Major architectural change: migrated from dual context system (standards + skills) to unified skills-based architecture.

#### Added
- **New skills directory**: `skills/standards/` with 4 standards skills:
  - `security-tiers/` - T0-T3 operation classification (auto_load)
  - `output-format/` - Global output contract for all agents (auto_load)
  - `command-execution/` - Shell security rules and timeout guidelines (triggered)
  - `anti-patterns/` - Common mistakes by tool: kubectl, terraform, gcloud, helm, flux, npm, docker (triggered)
- **Standards loader in skill_loader.py**: New `_load_standards_skills()` method
- **Standards config in skill-triggers.json**: New `standards` section with auto_load and triggers

#### Changed
- **Unified loading system**: All context now loaded via `skill_loader.py` (skills only)
- **skill-triggers.json**: Added `standards` section with 4 skills configuration

#### Removed
- **build_standards_context()**: Removed 91 lines from `context_provider.py`
- **Standards system**: Deleted `get_standards_dir()`, `read_standard_file()`, `should_preload_standard()`, `build_standards_context()`
- **--no-standards flag**: Removed from context_provider.py (no longer needed)
- **docs/ directory**: Eliminated symlink `.claude/docs` (standards now in skills/)
- **Obsolete tests**: Removed 66 lines of standards-specific tests from `test_context_provider.py`
- **Duplicate content**: Removed docs/standards reference from universal-protocol skill

#### Migration Notes
- **Breaking change**: Systems relying on `.claude/docs/standards/` must update to use skills system
- **Skills auto-load**: `security-tiers` and `output-format` now load for ALL agents (not just PROJECT_AGENTS)
- **No functional impact**: Same content, different delivery mechanism
- **Benefits**: Single loading system, better versioning, no duplication

## [3.3.2] - 2025-12-11

### Read-Only Auto-Approval & Code Optimization

Major improvements to the permission system with compound command support and code quality optimizations.

#### Added
- **Compound command auto-approval**: Safe compound commands (`cat file | grep foo`, `ls && pwd`, `tail file || echo error`) now execute WITHOUT ASK prompts
- **Extended safe command list**: Added `base64`, `md5sum`, `sha256sum`, `tar`, `gzip`, `time`, `timeout`, `sleep` to always-safe commands
- **Multi-word command support**: Added `kubectl get/describe/logs`, `helm list/status`, `flux check/get`, `docker ps/images`, `gcloud/aws describe/list` as always-safe

#### Changed
- **R1: Unified safe command configuration** (`SAFE_COMMANDS_CONFIG`) - Single source of truth for all safe commands, eliminating ~150 lines of duplicate patterns
- **R2: Unified validation flow** - `classify_command_tier()` now uses `is_read_only_command()` for T0 classification
- **R4: Singleton ShellCommandParser** - Single instance reused across all validations

#### Removed
- **R3: Dead code removal** - Removed unused `_contains_command_chaining()` method (~30 lines)
- **Removed tenacity dependency** - Simplified capabilities loading (retry logic was over-engineering)
- **Removed duplicate `allowed_read_operations`** - Now derived from `SAFE_COMMANDS_CONFIG`

#### Fixed
- Compound commands with safe components no longer trigger ASK prompts
- More consistent tier classification between auto-approval and security validation

#### Technical Details
- **Lines reduced**: ~200 lines removed through deduplication
- **Maintainability**: Single source of truth for safe commands
- **Performance**: Singleton parser avoids repeated instantiation

#### Test Results
All previous tests continue to pass:
- Simple read-only commands: NO ASK (auto-approved)
- Safe compound commands: NO ASK (NEW - auto-approved)
- Dangerous commands: BLOCKED correctly
- Compound with dangerous components: BLOCKED correctly

---

## [3.3.1] - 2025-12-11

### Granular AWS Permissions & Command Chaining Block

Refined AWS permission patterns to read-only operations and blocked command chaining to ensure predictable permission evaluation.

#### Changed
- **AWS permissions**: Replaced broad service wildcards with granular read-only patterns
  - `Bash(aws ec2:*)` → 40 specific `describe-*` and `get-*` commands
  - `Bash(aws s3:*)` → `s3 ls`, `s3api get-*`, `s3api list-*`, `s3api head-*`
  - `Bash(aws rds:*)` → `describe-*`, `list-tags-for-resource`
  - `Bash(aws iam:*)` → `get-*`, `list-*`, `generate-*`, `simulate-*`
  - Similar granular patterns for Lambda, Logs, CloudWatch, CloudFormation, ELB, Route53, SecretsManager, SSM, SNS, SQS, DynamoDB, ECR, EKS, ElastiCache

#### Added
- **Command chaining block** in `pre_tool_use.py`:
  - Blocks `&&`, `;`, `||` operators to prevent bypassing permission checks
  - Allows pipes `|` (don't affect permissions)
  - Smart detection avoids false positives in quoted strings
  - Clear error message: "Execute each command separately"

#### Fixed
- Moved `agents/README.md` files to `docs/` to resolve Claude Code parse errors

#### Security Impact
- Modification commands (create, start, stop) now properly require ASK confirmation
- Chained commands can no longer bypass individual permission evaluation
- Read-only operations execute without confirmation

---

## [3.2.3] - 2025-12-09

### Service-Level Permission Wildcards

Simplified permission patterns using service-level wildcards for better Claude Code compatibility.

#### Changed
- **AWS patterns**: Simplified from `Bash(aws rds describe-:*)` to `Bash(aws rds :*)`
  - Service-level wildcards: `aws ec2`, `aws rds`, `aws s3`, `aws iam`, etc.
  - Works around Claude Code pattern matching issues with hyphens
- **GCP patterns**: Simplified to `Bash(gcloud compute :*)`, `Bash(gcloud container :*)`, etc.
- **Format standardization**: Removed spaces before `:*` for commands without arguments

#### Fixed
- Agent README files renamed back to `README.md` (underscore prefix removed)
- Pattern matching now works for `aws rds describe-db-instances` and similar commands

#### Impact
- **Read-only commands**: Execute automatically ✓
- **Modification commands** (start/stop, upload, resize): Now execute automatically (Option A1)
- **Destructive commands** (delete, terminate): Still blocked ✓

#### Philosophy (Option A1 - Permissive with guardrails)
- Wide `allow[]` for entire services (e.g., `aws ec2 :*`)
- Strict `deny[]` for destructive operations
- Trade-off: Modification commands no longer require confirmation

---

## [3.2.2] - 2025-12-09

### Enhanced Permissions System

Complete overhaul of the permissions configuration to implement "permissive-with-guardrails" strategy.

#### Changed
- **Comprehensive allow[] rules**: 331 specific read-only patterns for shell, git, kubernetes, helm, flux, terraform, aws, gcp, docker commands
- **Granular ask[] rules**: 162 modification operations that require user confirmation
- **Strict deny[] rules**: 73 destructive operations that are completely blocked

#### Fixed
- Removed duplicate patterns (`uname:*`, `xargs:*`)
- Fixed `gsutil rm -r:*::*` → `gsutil rm -r:*` (incorrect double colon)
- Added missing `git branch:*` to allow[] for `git branch -a`

#### Added
- **New test suite**: `tests/permissions-validation/test_permissions_validation.py`
  - Emulates Claude Code's actual permission matching behavior
  - 114 test cases across 13 categories
  - Tests prefix matching with `:*` wildcard
  - Validates precedence: Deny → Allow → Ask

#### Philosophy
- **Allow**: Read-only commands execute automatically (no confirmation)
- **Ask**: Modification commands require user approval (can be approved)
- **Deny**: Destructive commands are blocked (cannot be approved)

---

## [3.2.1] - 2025-12-06

### Security Fix - Permission Bypass Bug

**Critical security fix** for permission enforcement in `settings.template.json`.

#### Fixed
- **Removed generic `"Bash"` from `allow[]`**: The generic `"Bash"` permission was bypassing all specific `ask[]` rules like `"Bash(git push:*)"`, allowing T3 operations (git push, git commit) to execute without user confirmation.
- **Changed hook matcher from `"BashTool"` to `"Bash"`**: The PreToolUse and PostToolUse hooks were configured with matcher `"BashTool"` but Claude Code invokes the tool as `"Bash"`, causing hooks to never execute.

#### Root Cause Analysis
- See post-mortem: Generic permission `allow: ["Bash"]` has higher precedence than specific `ask: ["Bash(git push:*)"]` in Claude Code's permission evaluation.
- Hook matchers must match the exact tool name used by Claude Code.

#### Impact
- All git operations (push, commit, add) now correctly trigger "ask" confirmation
- PreToolUse hooks now execute for bash commands
- Security tier enforcement restored

---

## [3.2.0] - 2025-12-06

### Added - Episodic Memory P0+P1 Enhancements

Inspired by [memory-graph](https://github.com/gregorydickson/memory-graph) analysis, selective feature adoption.

- **P0: Outcome Tracking** (`tools/4-memory/episodic.py`)
  - New fields: `outcome`, `success`, `duration_seconds`, `commands_executed`
  - Valid outcomes: "success", "partial", "failed", "abandoned"
  - New method: `update_outcome()` - Update episode results after execution
  - Search boost: 10% relevance increase for successful episodes

- **P1: Simple Relationships** (`tools/4-memory/episodic.py`)
  - New field: `related_episodes` - List of related episode IDs with types
  - Relationship types: SOLVES, CAUSES, DEPENDS_ON, VALIDATES, SUPERSEDES, RELATED_TO
  - New method: `add_relationship()` - Link episodes together
  - New method: `get_related_episodes()` - Query related episodes (outgoing/incoming/both)
  - Search enhancement: `include_relationships=True` parameter

- **Statistics Enhancements**
  - Outcome counts by type
  - Total relationships count
  - Relationship types breakdown

- **CLI Commands**
  - `store --outcome --duration` - Store with outcome tracking
  - `update-outcome <id> <outcome>` - Update episode outcome
  - `add-relationship <source> <target> <type>` - Create relationship
  - `get-related <id>` - Query related episodes
  - `search --include-relationships` - Search with relationship context

### Design Decisions

- Backward compatible: All new fields optional with None defaults
- Audit trail: Relationship and outcome events logged to JSONL
- Performance limits: 1000 episodes, 5000 relationships in index
- No external dependencies: Pure Python implementation

## [3.1.1] - 2025-12-06

### Fixed

- **package.json** - Added `docs/` to files array (was missing in 3.1.0)
  - `docs/standards/` now included in npm package
  - Required for hybrid pre-loading in `context_provider.py`

## [3.1.0] - 2025-12-06

### Added - Token Optimization & Consolidation

- **NEW:** `docs/standards/` - Shared execution standards
  - `security-tiers.md` - T0-T3 definitions
  - `output-format.md` - Report structure
  - `command-execution.md` - Execution pillars
  - `anti-patterns.md` - Common mistakes by tool

- **NEW:** Hybrid pre-loading in `context_provider.py`
  - Always loads: security-tiers, output-format
  - On-demand: command-execution
  - **78% token reduction** per agent invocation

- **NEW:** QuickTriage scripts
  - `tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh`
  - `tools/fast-queries/appservices/quicktriage_devops_developer.sh`

### Changed - Agent Optimization

- **agents/*.md** - All 6 agents reduced by 78%
  - terraform-architect: 916 → 183 lines
  - gitops-operator: 1,238 → 217 lines
  - gcp-troubleshooter: 600 → 156 lines
  - aws-troubleshooter: 565 → 142 lines
  - devops-developer: 641 → 173 lines

### Removed - Session System Consolidation

- **REMOVED:** Session management system (consolidated into Episodic Memory)
  - `commands/save-session.md`
  - `commands/restore-session.md`
  - `commands/session-status.md`
  - `hooks/session_start.py`
  - `tools/5-task-management/session-manager.py`
  - `tools/5-task-management/create_current_session_bundle.py`
  - `tools/5-task-management/restore_session.py`

### Changed - Episodic Memory Enhanced

- **tools/4-memory/episodic.py** - Added `capture_git_state()` migrated from session system

### Fixed - Test Suite

- **359 tests passing (100%)**
- Fixed import in `test_commit_validator.py`
- Fixed import in `test_episodic_memory.py`
- Updated `test_agent_definitions.py` for meta-agents
- Changed `test_hook_blocks_docker_ps` to `test_hook_default_permit_for_docker_ps`
- Fixed 11 warnings (return → assert)

### Changed - Documentation

- **README.md & README.en.md** - Updated to v3.1.0, reduced 41%
- **All subdirectory READMEs** - Reduced 63% total (~2,025 lines removed)
- Eliminated all references to session system

---

## [3.0.0] - 2025-12-05

### Added - Agent Intelligence System (MAJOR)

- **NEW:** `tools/10-agent-intelligence/` module for intelligent agent optimization
  - `agent_writing_assistant.py` (24KB) - Assists in writing and improving agent definitions
  - `workflow_optimizer.py` (29KB) - Applies the 7 LLM Engineering Principles to optimize workflows
    - Binary Decision Trees
    - Guards Over Advice
    - Tool Contracts
    - Failure Paths
    - TL;DR First
    - References Over Duplication
    - Metrics Over Subjective Goals

- **NEW:** `tools/4-memory/` Episodic Memory System
  - `episodic.py` (23KB) - Persistent storage and retrieval of historical context
  - `demo.py` - Demonstration script for episodic memory
  - Features:
    - Automatic episode storage with keywords and classifications
    - Smart search with time decay and relevance scoring
    - Auto-classification of episode types (deployment, troubleshooting, etc.)
    - Index management with automatic trimming (1000 episode limit)
    - Audit trail with append-only JSONL file

- **NEW:** `tools/conversation/` Enhanced Conversation Management
  - `enhanced_conversation_manager.py` (21KB) - Advanced conversation state management
  - `agent_contract_builder.py` (19KB) - Dynamic agent contract generation
  - `progressive_disclosure.py` (17KB) - Progressive context disclosure for token optimization

- **NEW:** `tests/workflow/` directory for workflow-specific tests
- **NEW:** `tests/test_agent_contract_integration.py` - Agent contract validation tests
- **NEW:** `tools/agent_capabilities.json` - Centralized agent capabilities definition

### Changed - Agent Enhancements

- **agents/gaia.md** - Major refactoring (1707 lines changed)
  - Streamlined agent definition
  - Improved protocol definitions
  - Better integration with new intelligence modules

- **agents/gitops-operator.md** - Enhanced with 234 new lines
  - Improved Kubernetes operation patterns
  - Better Flux CD integration guidance
  - Enhanced troubleshooting protocols

- **agents/terraform-architect.md** - Enhanced with 47 new lines
  - Improved Terragrunt support
  - Better module design guidance
  - Enhanced security scanning protocols

- **agents/gcp-troubleshooter.md** - Enhanced with 52 new lines
  - Improved GKE diagnostics
  - Better IAM analysis patterns
  - Enhanced networking troubleshooting

### Changed - Tools & Infrastructure

- **hooks/pre_tool_use.py** - Major enhancement (286+ lines)
  - Improved security validations
  - Better command blocking logic
  - Enhanced credential detection

- **hooks/subagent_stop.py** - Enhanced with 193 new lines
  - Better result packaging
  - Improved bundle generation
  - Enhanced session integration

- **tools/2-context/context_provider.py** - Enhanced (120+ lines changed)
  - Better provider detection
  - Improved contract validation
  - Enhanced error handling

- **tools/3-clarification/workflow.py** - Major enhancement (162+ lines)
  - Episodic memory integration
  - Improved ambiguity detection
  - Better context enrichment

- **tools/9-agent-framework/agent_orchestrator.py** - Enhanced (38+ lines)
  - Better phase management
  - Improved error recovery
  - Enhanced logging

### Changed - Fast Queries (Simplified)

- **tools/fast-queries/README.md** - Simplified documentation (185 lines changed)
- **tools/fast-queries/run_triage.sh** - Streamlined (152 lines changed)
- **tools/fast-queries/terraform/quicktriage_terraform_architect.sh** - Enhanced (90+ lines)
- **tools/fast-queries/gitops/quicktriage_gitops_operator.sh** - Enhanced (69+ lines)
- **tools/fast-queries/cloud/gcp/quicktriage_gcp_troubleshooter.sh** - Enhanced (99+ lines)

### Removed (BREAKING)

- **REMOVED:** `tools/fast-queries/USAGE_GUIDE.md` (369 lines) - Consolidated into README
- **REMOVED:** `tools/fast-queries/appservices/quicktriage_devops_developer.sh` (38 lines)
- **REMOVED:** `tools/fast-queries/cloud/aws/quicktriage_aws_troubleshooter.sh` (45 lines)

### Improved

- **Token Efficiency:** New progressive disclosure system reduces context by up to 70%
- **Agent Intelligence:** Workflows now validated against 7 engineering principles
- **Memory System:** Historical context improves routing accuracy over time
- **Conversation Management:** Multi-turn conversations with intelligent context carry-over
- **Test Coverage:** New workflow and integration tests

### Migration Guide for v3.0.0

**Breaking Changes:**
1. Removed `quicktriage_devops_developer.sh` - Use agent directly
2. Removed `quicktriage_aws_troubleshooter.sh` - Use agent directly
3. Removed `USAGE_GUIDE.md` - See README.md instead

**New Features to Adopt:**
```python
# Episodic Memory
from tools.4_memory.episodic import EpisodicMemory
memory = EpisodicMemory()
memory.store_episode(prompt="...", context={...})

# Workflow Optimizer
from tools.10_agent_intelligence.workflow_optimizer import WorkflowOptimizer
optimizer = WorkflowOptimizer()
result = optimizer.analyze(workflow_content)

# Enhanced Conversation
from tools.conversation.enhanced_conversation_manager import EnhancedConversationManager
manager = EnhancedConversationManager()
```

**Recommended Actions:**
- Review new agent definitions for improved patterns
- Enable episodic memory for better context over time
- Use workflow optimizer to validate custom workflows

---

## [2.6.2] - 2025-11-14

### Added - Absolute Paths Support

- **NEW:** `normalizePath()` function - Handles both absolute and relative paths transparently
- **NEW:** CLI option `--project-context-repo` - Specify git repository for project context in non-interactive mode
- **NEW:** Environment variable `CLAUDE_PROJECT_CONTEXT_REPO` - Alternative way to specify context repo

### Changed

- **`getConfiguration()`** - Now normalizes paths using `normalizePath()`
- **`validateAndSetupProjectPaths()`** - Enhanced to handle absolute paths correctly
- **CLI help and documentation** - Updated examples with absolute paths

### Improved

- Path handling is now more robust and user-friendly
- Better error messages for path-related issues
- Clearer documentation and examples

### Examples

```bash
# Absolute paths without context repo
npx gaia-init --non-interactive \
  --gitops /home/user/project/gitops \
  --terraform /home/user/project/terraform \
  --app-services /home/user/project/services

# Absolute paths with context repo
npx gaia-init --non-interactive \
  --gitops /path/to/gitops \
  --terraform /path/to/terraform \
  --project-context-repo git@bitbucket.org:org/repo.git
```

---

## [2.3.0] - 2025-11-11

### Added - Phase 0 Clarification Module

- **NEW:** `tools/clarification/` module for intelligent ambiguity detection before routing
  - `clarification/engine.py`: Core clarification engine (refactored from clarify_engine.py)
  - `clarification/patterns.py`: Ambiguity detection patterns (ServiceAmbiguityPattern, NamespaceAmbiguityPattern, etc.)
  - `clarification/workflow.py`: High-level helper functions for orchestrators (`execute_workflow()`)
  - `clarification/__init__.py`: Clean public API
- **Protocol G** in `agents/gaia.md`: Clarification system analysis and troubleshooting guide
- **Rule 5.0.1** in `templates/CLAUDE.template.md`: Phase 0 implementation guide with code examples
- **Phase 0 integration** in `/speckit.specify` command
- **Regression tests** in `tests/integration/test_phase_0_regression.py`
- **Clarification metrics** to Key System Metrics (target: 20-30% clarification rate)

### Changed - Module Restructuring (BREAKING)

- **BREAKING:** `clarify_engine.py` and `clarify_patterns.py` moved to `clarification/` module
  - **Old imports:** `from clarify_engine import request_clarification`
  - **New imports:** `from clarification import execute_workflow, request_clarification`
- Updated `application_services` structure in project-context.json:
  - Added `tech_stack` field (replaces `technology`)
  - Added `namespace` field for service location
  - **Removed** `status` field (dynamic state must be verified in real-time, not stored in SSOT)
- Service metadata now shows only static information: `tech_stack | namespace | port`

### Fixed

- Import paths in `tests/tools/test_clarify_engine.py` updated to new module structure
- Service metadata test updated to reflect removal of dynamic status field
- All 20 unit tests passing with new module structure

### Migration Guide for v2.3.0

```python
# Before (v2.2.x)
from clarify_engine import request_clarification, process_clarification

# After (v2.3.0)
from clarification import execute_workflow

# Simple usage
result = execute_workflow(user_prompt)
enriched_prompt = result["enriched_prompt"]
```

---

## [2.2.3] - 2025-11-11

### Fixed - Deterministic Project Context Location

- **context_provider.py**
  - Always reads `.claude/project-context/project-context.json` (no fallback to legacy paths)
  - Removed legacy auto-detection logic and unused imports
  - Prevents "Context file not found" errors when projects only use the new structure
- **templates/CLAUDE.template.md**
  - Rule 1 clarifies when to delegate vs. self-execute
  - Rule 2 explicitly documents the `context_provider.py --context-file .claude/project-context/project-context.json …` invocation
  - Workflow summary now references orchestration docs after the table (cleaner render)

### Changed - CLI Documentation & Version Alignment

- **README.md / README.en.md**
  - Documented the exact `npx` commands (`npx gaia-init` / `npx @jaguilar87/gaia-ops`) and clarified installation steps
  - Updated "Current version" badges to **2.2.3**
- **package.json**
  - Bumped package version to `2.2.3`

### Benefits

- No manual tweaks needed to point `context_provider.py` at the correct project context
- CLAUDE template now tells the orchestrator exactly how to invoke the context provider
- README instructions reflect the real CLI entry points, reducing confusion for new installs

---

## [2.2.2] - 2025-11-11

### Added - Pre-generated Semantic Embeddings

- **NEW:** Included pre-generated intent embeddings in package (74KB total)
  - `config/intent_embeddings.json` (55KB) - Semantic vectors for intent matching
  - `config/intent_embeddings.npy` (19KB) - Binary embeddings for fast loading
  - `config/embeddings_info.json` (371B) - Metadata about embeddings

### Changed - Semantic Routing Now Works Out-of-the-Box

- **Semantic matching enabled by default:** No manual setup required
- **Routing accuracy improved:** Ambiguous queries now route correctly using semantic similarity
- **Example improvement:**
  ```
  Query: "puede decirme el estado de los servicios de tcm?"
  Before: devops-developer (keyword "ci" - incorrect)
  After: gitops-operator (semantic matching - correct)
  ```

### Fixed - Directory Structure Consistency

- **Consolidated `configs/` into `config/`:** All configuration and data files now in single directory
- **Updated tool references:**
  - `tools/semantic_matcher.py`: Updated embeddings path (configs/ → config/)
  - `tools/generate_embeddings.py`: Updated output path (configs/ → config/)
  - All documentation updated to reference correct paths

### Fixed - Test Suite (254 tests, 100% passing)

- **tests/system/test_configuration_files.py:**
  - Updated to validate `templates/settings.template.json` (package contains template, not installed settings.json)
  - Tests now reflect npm package structure instead of installed project structure

- **tests/system/test_directory_structure.py:**
  - Completely rewritten for npm package validation
  - Tests now verify package directories (agents/, tools/, config/, templates/, bin/)
  - Removed tests for installed-project structure (session/, .claude/ name)
  - Added comprehensive tests for all package subdirectories (agents, tools, hooks, config, speckit)

- **tests/tools/test_clarify_engine.py:**
  - Fixed import paths (tests/tools → gaia-ops/tools)
  - Made emoji checks flexible (accepts any emoji, not just 📦)
  - All 32 clarify_engine tests now pass

- **tests/tools/test_context_provider.py:**
  - Updated troubleshooter contract test (application_services is optional, not required)
  - Fixed invalid_agent test expectation (now correctly exits with code 1)

- **tools/context_provider.py:**
  - Changed behavior for invalid agents: now exits with code 1 (was: warning + empty contract)
  - Better error messages: "ERROR: Invalid agent" instead of "Warning: No contract found"

### Benefits

- Zero configuration: Semantic routing works immediately after installation
- Better routing: Handles ambiguous queries with 6x higher confidence
- Consistent structure: All config files in one place (`config/`)
- Smaller package: Embeddings optimized for size (74KB vs 5MB unoptimized)
- Regeneration optional: Users can regenerate with `python3 .claude/tools/generate_embeddings.py` if needed
- Test coverage: 254 tests passing (0 failures)

---

## [2.2.1] - 2025-11-10

### Fixed - Documentation Consistency

- **README.md & README.en.md:**
  - Updated version numbers from 2.1.0 → 2.2.0
  - Corrected package structure (hooks/, templates/, commands/)
  - Fixed hooks/ listing: now shows actual Python files (pre_tool_use.py, post_tool_use.py, etc.) instead of non-existent pre-commit
  - Fixed templates/ listing: removed non-existent code-examples/, listed actual files (CLAUDE.template.md, settings.template.json)
  - Added context-contracts.gcp.json and context-contracts.aws.json to config/ section
  - Removed CLAUDE.md and AGENTS.md from package root (only templates exist)
  - Added speckit/ directory to structure

- **config/AGENTS.md:**
  - Updated all references: `.claude/docs/` → `.claude/config/`
  - Fixed quick links and support documentation paths

- **config/agent-catalog.md:**
  - Updated all 5 context contract references: `.claude/docs/` → `.claude/config/`

- **index.js:**
  - Deprecated `getDocPath()` function with console warning
  - Function now redirects to `config/` directory instead of non-existent `docs/`
  - Added JSDoc @deprecated annotation

- **README.en.md (Documentation section):**
  - Removed broken reference to `./CLAUDE.md` (file not in package)
  - Fixed all documentation links: `./docs/` → `./config/`
  - Updated to match actual config/ directory structure

- **speckit/README.en.md:**
  - Removed 3 non-existent commands: speckit.clarify, speckit.analyze-plan, speckit.constitution
  - Updated command count: 9 → 7 actual commands
  - Removed references to non-existent tasks-richer.py tool
  - Removed entire sections for non-existent templates (data-model-template.md, contracts-template.md)
  - Updated tool files list with actual tools (task_manager.py, clarify_engine.py, context_provider.py)
  - Fixed all code examples to use only existing commands

- **tools/context_provider.py:**
  - Added auto-detection for project-context.json location
  - Honors GAIA_CONTEXT_PATH environment variable
  - Falls back through common locations (.claude/project-context.json, .claude/project-context/project-context.json)
  - Fixes agent routing failures when project-context.json is in non-legacy location

- **package.json:**
  - Fixed `npm test` script (was calling non-existent pytest tests)
  - Now echoes informative message about fixture availability

- **Agent Branding Unification:**
  - Renamed `agents/claude-architect.md` → `agents/gaia.md` (aligns with gaia-ops package name)
  - Renamed `commands/gaina.md` → `commands/gaia.md` (unified as `/gaia` command)
  - Updated all references in README.md, README.en.md, and agents/gaia.md
  - Complete branding consistency: package name, agent name, and command name all use "gaia"

### Benefits

- Accurate documentation: All paths and structures match actual package contents
- No broken links: References point to existing files
- Clear API: Deprecated functions clearly marked
- User trust: Documentation matches reality
- npm test passes: No false failures

---

## [2.2.0] - 2025-11-10

### Added - Unified Settings Template & Auto-Installation

- **NEW:** Created unified `templates/settings.template.json` (214 lines)
  - Merged functionality from `settings.json` + `settings.local.json`
  - Includes all hooks (PreToolUse, PostToolUse, SubagentStop)
  - Complete permissions (75+ allow, 9 deny, 27 ask entries)
  - Full security tier definitions (T0-T3)
  - Environment configuration

- **Auto-Installation:** `gaia-init.js` now automatically generates `.claude/settings.json`
  - Added `generateSettingsJson()` function
  - Integrated into installation workflow (Step 6.5)
  - Projects get complete settings from day 1

### Removed - Dead Code Elimination

- **CLAUDE.md** from package root (only template exists now)
- **templates/code-examples/** (321 lines - never imported or executed)
  - `commit_validation.py`
  - `clarification_workflow.py`
  - `approval_gate_workflow.py`
- **templates/project-context.template.json** (126 lines - unused, installer generates programmatically)
- **templates/project-context.template.aws.json** (128 lines - never used)
- **package.json:** Removed `CLAUDE.md` from files array

### Changed - Package Consistency

- **templates/CLAUDE.template.md:**
  - Updated all references: `.claude/docs/` → `.claude/config/`
  - Updated package name: `@aaxis/claude-agents` → `@jaguilar87/gaia-ops`
  - Removed code-examples reference (no longer exists)

- **README.en.md:**
  - Updated API examples to use `@jaguilar87/gaia-ops`
  - Changed `getDocPath()` → `getConfigPath()` (correct function)

- **index.js:**
  - Updated header and JSDoc comments with new package name
  - Updated example usage

- **agents/gaia.md:**
  - Updated system paths to reflect gaia-ops package structure
  - Clarified symlink architecture and layout

### Improved - Package Quality

- **Reduced template bloat by 57%:** 882 lines → 378 lines (504 lines removed)
- **Single source of truth:** One settings template instead of scattered config
- **Cleaner architecture:** Only actual templates remain in `templates/`
- **Better defaults:** Projects start with complete, production-ready settings

### Benefits

- Unified configuration: Everything in one settings.json file
- Automatic setup: No manual settings configuration needed
- Smaller package: 57% reduction in template code
- Flexibility maintained: Users can still create `settings.local.json` for overrides
- Package consistency: All references use correct package name

---

## [2.1.0] - 2025-11-10

### Added - Provider-Specific Context Contracts

- **NEW:** Created separate contract files per cloud provider
  - `config/context-contracts.gcp.json` - GCP-specific contracts
  - `config/context-contracts.aws.json` - AWS-specific contracts
  - Ready for `context-contracts.azure.json` (future)

- **Auto-Detection:** `context_provider.py` now automatically:
  1. Detects cloud provider from `metadata.cloud_provider`
  2. Falls back to inferring from field presence (`project_id` → GCP, `account_id` → AWS)
  3. Loads the correct contract file
  4. Validates against provider-specific requirements

- **Test Fixtures:** Added sample contexts for testing
  - `tests/fixtures/project-context.gcp.json`
  - `tests/fixtures/project-context.aws.json`

### Changed

- **Context Provider:** Updated `tools/context_provider.py`
  - Added `detect_cloud_provider()` function
  - Added `load_provider_contracts()` function
  - Updated `get_contract_context()` to accept provider contracts
  - Legacy contracts remain for backward compatibility

- **Field Names:** Standardized provider-specific fields
  - GCP: `project_details.project_id` (no change)
  - AWS: `project_details.account_id` (was `aws_account`)
  - Installer updated to generate correct field names

- **Templates:** Created AWS-specific template
  - `templates/project-context.template.aws.json`
  - Matches AWS naming conventions (EKS, RDS, ECR, etc.)

- **Documentation:** Updated `config/context-contracts.md`
  - Added "Provider-Specific Contracts" section
  - Documented how provider detection works
  - Explained benefits of provider-specific approach
  - Version bumped to 2.1.0

### Benefits

- Clarity: Field names match cloud provider terminology
- Simplicity: No complex conditional validation logic in agents
- Extensibility: Adding Azure = create one JSON file (15 minutes)
- Agents Stay Agnostic: Agents use pattern discovery, don't need provider logic
- Single Source of Truth: Orchestrator selects the right contract

### Backward Compatibility

- Legacy support maintained: If provider-specific contracts don't exist, falls back to hardcoded contracts
- Existing projects: Continue to work without changes
- Migration: Optional, but recommended for clarity

---

## [1.4.0] - 2025-11-10

### Changed - BREAKING: Complete Installer Redesign

- **NEW FLOW:** Directories first, context second (much more logical!)
  1. Ask for directories (gitops, terraform, app-services) - ALWAYS
  2. Ask for project context repo - OPTIONAL
  3. If NO context: Ask basic questions to create project-context.json
  4. If YES context: Use that configuration and done!

### Improved

- **Clearer Purpose:** Context repo is now clearly optional
- **Better Fallback:** If no context exists, creates a basic one with minimal info
- **All Fields Optional:** Can leave everything empty if you don't know yet
- **Logical Order:** Ask for what you always need first (paths), then optional context

---

## [1.3.6] - 2025-11-10

### Fixed

- **Installer:** Skip questions when project context already has the answers
- **Smart Detection:** Only ask what's missing or needs confirmation (paths)
- **User Experience:** Show config summary when context is loaded
- **Directory Creation:** Auto-create missing directories without prompting

### Changed

- When project context loads successfully, only asks to confirm/adjust paths
- Cloud provider, credentials, region, and cluster name auto-applied from context
- Clearer feedback showing what was loaded from project context
- Missing directories (gitops, terraform, app-services) now created automatically

---

## [1.3.5] - 2025-11-10

### Added

- **Smart Installer Flow:** Project context repo now asked FIRST, with auto-population of all config
- **Input Sanitization:** Handles "git clone <url>" pastes automatically (extracts just URL)
- **Auto-Configuration:** Parses project-context.json and pre-fills all wizard questions
- **Better Error Messages:** Clear troubleshooting tips for git clone failures (SSH keys, access, URL)

### Changed

- **Wizard Question Order:** Project context moved from last to first question
- **User Experience:** Reduced manual input when project context exists
- **Clone Strategy:** Validates project context early, then sets up in final location
- **Error Handling:** Installation continues even if project context clone fails

---

## [1.3.4] - 2025-11-10

### Fixed

- **Installer:** Removed incorrect AGENTS.md symlink creation in project root during installation
- **Documentation:** AGENTS.md now only accessible via `.claude/config/AGENTS.md` as intended
- **Package Quality:** Excluded Python cache files (`__pycache__/`) from published package

### Changed

- **README.md:** Updated project structure documentation to reflect correct AGENTS.md location
- **README.en.md:** Updated project structure and corrected package references
- **Package Size:** Reduced from 911.7 kB (93 files) to 660.7 kB (77 files) - 27% reduction

### Added

- **Package Metadata:** Added `homepage` and `bugs` fields to package.json for better npm discovery
- **Badges:** Added npm version, license, and Node.js version badges to README files
- **CI/CD:** Created GitHub Actions workflow for automated npm publishing
- **.npmignore:** Added file to exclude development artifacts from package
- **Cleanup Script:** Added `npm run clean` to remove Python cache files automatically
- **Pre-publish Hook:** Added `prepublishOnly` script for automatic cleanup before publishing

---

## Versioning Policy

### Version Number Format: MAJOR.MINOR.PATCH

- **MAJOR:** Breaking changes to orchestrator behavior (requires agent updates, system changes)
- **MINOR:** New features, sections, or substantial improvements (backward compatible)
- **PATCH:** Bug fixes, clarifications, typos (backward compatible)

### Examples

- Adding new agent: MINOR (e.g., 2.0.0 → 2.1.0)
- Changing core principle: MAJOR (e.g., 2.1.0 → 3.0.0)
- Fixing typo in docs: PATCH (e.g., 2.1.0 → 2.1.1)
- Refactoring structure (like 2.0.0): MAJOR (changed from monolith to modular)

---

## Maintainers

- **Primary:** Jorge Aguilar (jaguilar@aaxis.com)
- **Contributors:** Claude Code Agent Swarm

---

## License

Internal documentation for Aaxis RnD team. Not for external distribution.
