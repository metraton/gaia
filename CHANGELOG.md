# Changelog: CLAUDE.md

All notable changes to the CLAUDE.md orchestrator instructions are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

### Documentation
- Added comprehensive Phase 0 implementation guide
- Added troubleshooting guide for clarification system
- Updated speckit.specify.md with Phase 0 workflow integration
- Added Protocol G diagnostic steps in gaia.md

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

## [2.2.3] - 2025-11-11

### Fixed - Deterministic Project Context Location
- **context_provider.py**
  - Always reads `.claude/project-context/project-context.json` (no fallback to legacy paths)
  - Removed legacy auto-detection logic and unused imports
  - Prevents “Context file not found” errors when projects only use the new structure
- **templates/CLAUDE.template.md**
  - Rule 1 clarifies when to delegate vs. self-execute
  - Rule 2 explicitly documents the `context_provider.py --context-file .claude/project-context/project-context.json …` invocation
  - Workflow summary now references orchestration docs after the table (cleaner render)

### Changed - CLI Documentation & Version Alignment
- **README.md / README.en.md**
  - Documented the exact `npx` commands (`npx gaia-init` / `npx @jaguilar87/gaia-ops`) and clarified installation steps
  - Updated “Current version” badges to **2.2.3**
- **package.json**
  - Bumped package version to `2.2.3`

### Benefits
- ✅ No manual tweaks needed to point `context_provider.py` at the correct project context
- ✅ CLAUDE template now tells the orchestrator exactly how to invoke the context provider
- ✅ README instructions reflect the real CLI entry points, reducing confusion for new installs

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
- ✅ **Zero configuration:** Semantic routing works immediately after installation
- ✅ **Better routing:** Handles ambiguous queries with 6x higher confidence
- ✅ **Consistent structure:** All config files in one place (`config/`)
- ✅ **Smaller package:** Embeddings optimized for size (74KB vs 5MB unoptimized)
- ✅ **Regeneration optional:** Users can regenerate with `python3 .claude/tools/generate_embeddings.py` if needed
- ✅ **Test coverage:** 254 tests passing (0 failures)

### Technical Details
```
config/ directory now contains:
├── Documentation (markdown)
│   ├── AGENTS.md, agent-catalog.md, context-contracts.md
│   ├── git-standards.md, orchestration-workflow.md
├── Provider Contracts (JSON)
│   ├── context-contracts.gcp.json, context-contracts.aws.json
│   └── git_standards.json
└── Semantic Embeddings (JSON + binary) ← NEW
    ├── intent_embeddings.json
    ├── intent_embeddings.npy
    └── embeddings_info.json
```

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
- ✅ **Accurate documentation:** All paths and structures match actual package contents
- ✅ **No broken links:** References point to existing files
- ✅ **Clear API:** Deprecated functions clearly marked
- ✅ **User trust:** Documentation matches reality
- ✅ **npm test passes:** No false failures

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
- ✅ **Unified configuration:** Everything in one settings.json file
- ✅ **Automatic setup:** No manual settings configuration needed
- ✅ **Smaller package:** 57% reduction in template code
- ✅ **Flexibility maintained:** Users can still create `settings.local.json` for overrides
- ✅ **Package consistency:** All references use correct package name

### Final Template Structure
```
templates/
├── CLAUDE.template.md         (164 lines) - Orchestrator instructions
└── settings.template.json     (214 lines) - Complete Claude Code settings
```

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
- ✅ **Clarity:** Field names match cloud provider terminology
- ✅ **Simplicity:** No complex conditional validation logic in agents
- ✅ **Extensibility:** Adding Azure = create one JSON file (15 minutes)
- ✅ **Agents Stay Agnostic:** Agents use pattern discovery, don't need provider logic
- ✅ **Single Source of Truth:** Orchestrator selects the right contract

### Backward Compatibility
- **Legacy support maintained:** If provider-specific contracts don't exist, falls back to hardcoded contracts
- **Existing projects:** Continue to work without changes
- **Migration:** Optional, but recommended for clarity

### Technical Details
```python
# Before (v2.0.0):
contract_keys = AGENT_CONTRACTS[agent_name]  # Hardcoded

# After (v2.1.0):
cloud_provider = detect_cloud_provider(project_context)  # Auto-detect
contracts = load_provider_contracts(cloud_provider)      # Load from JSON
contract_keys = contracts["agents"][agent_name]["required"]  # Provider-specific
```

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

### Benefits
- Makes sense for new projects (no context yet)
- Makes sense for existing projects (have context)
- Directories are always the starting point (local to project)
- Context comes second (can be shared across projects)

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

### Improved
- Eliminates ALL redundant questions when context exists
- Better UX: "Here's what we loaded, just confirm the paths"
- Faster setup for teams with complete project contexts
- No interruptions for directory creation confirmations

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

### Improved
- Eliminates typos and configuration errors by pre-filling from existing context
- Saves time for users with existing project-context repos
- Better guidance when git operations fail

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

### Package Quality Improvements
- Better npm package metadata for discoverability
- Professional badges in documentation
- Automated publishing workflow
- Cleaner package distribution (no cache files)
- Improved documentation consistency

---

## [2.1.0] - 2025-11-07

### Changed

- **Optimized CLAUDE.md verbosity:** Further reduced from 195 lines to 154 lines (21% additional reduction)
- **Converted narrative to rules format:** All sections now use Rule X.Y [Priority] format for clarity
- **Implemented numeric priority system:** `[P0]` (critical), `[P1]` (important) for better emphasis
- **Reduced Python code blocks:** Removed verbose code examples, replaced with concise function references
- **Optimized sections:**
  - Core Operating Principles: Narrative → Rules 1.0-4.0
  - Orchestrator Workflow: Narrative → Rule 5.0-5.1 with table format
  - Git Operations: Narrative → Rules 6.0-6.1 with table format
  - Common Anti-Patterns: Lists → Rule 7.0 with comparison table

### Added

- **Code examples in templates:** Created `.claude/templates/code-examples/` with:
  - `commit_validation.py` (86 lines) - Complete commit validation patterns
  - `clarification_workflow.py` (94 lines) - Phase 0 clarification examples
  - `approval_gate_workflow.py` (141 lines) - Phase 4 approval gate examples
- **Rule numbering system:** Rules 1.0-7.0 for easy reference and navigation
- **Priority indicators:** `[P0]` for critical rules, `[P1]` for important rules

### Improved

- **Token efficiency:** Additional 25% reduction (1,900 → ~1,450 tokens)
- **Scannability:** Table format for workflows, commit rules, anti-patterns
- **Conciseness:** Removed redundant explanations, kept essential info
- **Maintainability:** Code examples separate from core instructions

---

## [2.0.0] - 2025-11-07

### Changed (BREAKING)

- **Refactored to modular structure:** Split 380-line monolith into:
  - `CLAUDE.md` (core instructions, 196 lines)
  - `.claude/docs/orchestration-workflow.md` (phases 0-6, ~550 lines)
  - `.claude/docs/git-standards.md` (complete git specification, ~450 lines)
  - `.claude/docs/context-contracts.md` (agent contracts, ~400 lines)
  - `.claude/docs/agent-catalog.md` (agent capabilities, ~550 lines)

- **Updated Core Operating Principles:**
  - Changed Principle #1 from "ZERO Direct Execution" to "Selective Delegation (Context-Aware)"
  - Clarified that orchestrator CAN execute SIMPLE operations (commits, file edits, queries)
  - Clarified that orchestrator MUST delegate COMPLEX workflows (infrastructure, deployments)

### Added

- **Frontmatter with metadata:**
  - Version: 2.0.0
  - Last updated date
  - Maintainer info
  - Changelog reference

- **Git Operations section:**
  - Clear distinction between orchestrator-level commits (simple, ad-hoc) and agent-level commits (complex workflows)
  - Table of "Distinction Rules" showing which handler (orchestrator or agent) for each scenario
  - Universal validation requirement (`commit_validator.py`) for both orchestrator and agents

- **References section:**
  - Links to all modular docs (`.claude/docs/*.md`)
  - Links to code examples (`.claude/templates/code-examples/`)

- **System Paths section:**
  - Centralized list of all system paths (agent system, tools, logs, tests, project SSOT)

- **Common Anti-Patterns section:**
  - DON'T list (skip approval gate, use context_provider for meta-agents, etc.)
  - DO list (route tasks, use AskUserQuestion, update SSOT, etc.)

### Improved

- **Token efficiency:** Reduced from ~3,800 tokens to ~1,500 tokens (60% reduction)
- **Navigability:** Clear section structure with references to detailed docs
- **Mantenibility:** Changes to git standards, workflows, or contracts don't require editing CLAUDE.md
- **Clarity:** Explicit distinction between project agents and meta-agents

### Removed

- **Detailed Phase 0-6 workflows:** Moved to `.claude/docs/orchestration-workflow.md`
- **Complete git standards:** Moved to `.claude/docs/git-standards.md`
- **Full context contracts:** Moved to `.claude/docs/context-contracts.md`
- **Detailed agent capabilities:** Moved to `.claude/docs/agent-catalog.md`
- **Code examples:** Moved to `.claude/templates/code-examples/`

---

## [1.4.x] - 2025-11-01 to 2025-11-06

### Added

- **Phase 0: Intelligent Clarification** (NEW)
  - `clarify_engine.py` integration for ambiguity detection
  - Dynamic questions with options from `project-context.json`
  - Enriched prompt generation for better routing accuracy

- **Phase 6: System State Update**
  - Mandatory SSOT updates after realization
  - `TaskManager` integration for `tasks.md` updates (handles >25K tokens)
  - Infrastructure state updates (`project-context.json`)

### Changed

- **Phase 4: Approval Gate** made MANDATORY (enforced with validation logic)
- **Phase 5: Realization** requires `validation["approved"] == True` (cannot skip)

---

## [1.3.x] - 2025-10-15 to 2025-10-31

### Added

- **Git Commit Standards:**
  - `commit_validator.py` integration (mandatory validation)
  - Conventional Commits format enforcement
  - Forbidden footers (Claude Code attribution)
  - Violations logging to `.claude/logs/commit-violations.jsonl`

- **Context Contracts:**
  - Defined minimum context payload for each agent
  - `context_provider.py` as SSOT for context generation

### Changed

- **Core Principle #4:** Clarified two-phase workflow (Planning → Approval → Realization)

---

## [1.2.x] - 2025-09-20 to 2025-10-14

### Added

- **Agent System Overview:**
  - Distinction between Project Agents (use `context_provider.py`) and Meta-Agents (manual context)
  - Security tiers (T0-T3) for operations
  - Agent capabilities and tools

### Changed

- **Orchestrator Workflow:** Simplified to 5 phases (before Phase 0 was added later)

---

## [1.1.x] - 2025-08-15 to 2025-09-19

### Added

- **Execution Standards:**
  - Use native tools over bash redirections
  - Execute simple commands (not chained with `&&`)
  - Permission priority rules

### Changed

- **Language Policy:** Separated technical docs (English) from chat interactions (Spanish)

---

## [1.0.0] - 2025-08-01

### Added

- **Initial CLAUDE.md structure:**
  - Language Policy
  - Core Operating Principles (ZERO Direct Execution, Delegate to Specialists, Master of Context)
  - Basic workflow (Analysis → Context → Invocation → Verification)
  - Agent list with roles

---

## Future (Planned)

### Version 2.1.0 (Planned Q1 2026)

- **Improved routing:** Machine learning-based agent selection
- **Enhanced clarification:** Proactive clarification based on user history
- **Performance metrics:** Track token usage, latency, success rates per agent

### Version 2.2.0 (Planned Q2 2026)

- **Multi-agent coordination:** Support for workflows requiring multiple agents (e.g., terraform + gitops)
- **Rollback automation:** Automatic rollback on failed verifications
- **Enhanced observability:** Real-time workflow visualization

### Version 3.0.0 (Planned Q3 2026 - BREAKING)

- **Agent auto-discovery:** Agents register themselves via manifest files
- **Dynamic contract negotiation:** Agents specify required context dynamically
- **Plugin system:** Third-party agents can be added without modifying CLAUDE.md

---

## Migration Guide

### Migrating from 1.x to 2.0

**What changed:**

1. **CLAUDE.md is now modular:**
   - The file is 196 lines instead of 380 lines
   - Detailed docs moved to `.claude/docs/*.md`

2. **Core Principle #1 updated:**
   - OLD: "ZERO Direct Execution" (orchestrator never executes technical work)
   - NEW: "Selective Delegation" (orchestrator executes SIMPLE ops, delegates COMPLEX workflows)

3. **Git operations clarified:**
   - Orchestrator CAN do ad-hoc commits ("commitea los cambios")
   - Agents do commits as part of realization workflows
   - Both MUST use `commit_validator.py`

**Breaking changes:**

- None (backward compatible). Orchestrator behavior remains the same, only documentation structure changed.

**Action required:**

- None. System continues to work as before.

**Recommended:**

- Read `.claude/config/orchestration-workflow.md` to understand full Phase 0-6 workflow
- Review `.claude/config/git-standards.md` for complete commit standards
- Check `.claude/config/agent-catalog.md` for detailed agent capabilities

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

## Contributing

### How to Update CLAUDE.md

1. **For small changes (typos, clarifications):**
   - Edit `CLAUDE.md` directly
   - Increment PATCH version in frontmatter
   - Add entry to this CHANGELOG under "Unreleased"

2. **For new sections or features:**
   - Decide if belongs in `CLAUDE.md` (core instructions) or `.claude/config/*.md` (details)
   - If modular doc, create/update appropriate file in `.claude/config/`
   - If core instruction, update `CLAUDE.md` and add reference to modular doc
   - Increment MINOR version in frontmatter
   - Add entry to this CHANGELOG under "Unreleased"

3. **For breaking changes:**
   - Discuss with team first (impacts orchestrator behavior)
   - Update `CLAUDE.md` and related docs
   - Increment MAJOR version in frontmatter
   - Add entry to this CHANGELOG under "Unreleased" with **BREAKING** tag
   - Create migration guide if needed

### Testing Changes

Before committing changes to CLAUDE.md:

1. **Run validation script:**
   ```bash
   python3 .claude/scripts/validate-claude-md.py
   ```

2. **Check line count:**
   ```bash
   wc -l CLAUDE.md
   # Should be < 250 lines
   ```

3. **Test with orchestrator:**
   - Start Claude Code session
   - Verify CLAUDE.md is loaded correctly
   - Test sample workflows (routing, clarification, approval)

4. **Run test suite:**
   ```bash
   pytest .claude/tests/ -v
   ```

---

## Maintainers

- **Primary:** Jorge Aguilar (jaguilar@aaxis.com)
- **Contributors:** Claude Code Agent Swarm

---

## License

Internal documentation for Aaxis RnD team. Not for external distribution.
