---
name: gaia-plan
description: Plan a feature -- create a brief and decompose into verifiable tasks
allowed-tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Task
  - Skill
  - AskUserQuestion
  - WebSearch
  - WebFetch
---

Plan a feature using the Gaia Planner. Single entry point that adapts to
feature size -- from quick bug fixes to multi-agent projects.

## Usage

- `/gaia-plan` -- interactive mode, asks what you want to build
- `/gaia-plan <description>` -- starts Phase 1 with the description as input
- `/gaia-plan --execute <path/to/brief.md>` -- skips to Phase 2 (task dispatch)

## How it works

$ARGUMENTS

1. **Parse arguments:**
   - If `$ARGUMENTS` is empty -> ask "What do you want to build?" via AskUserQuestion
   - If `$ARGUMENTS` starts with `--execute` -> extract brief path, skip to step 5
   - Otherwise -> use `$ARGUMENTS` as the feature description

2. **Load context:**
   - Read `project-context.json` from the project's `.claude/project-context/` directory
   - Extract constraints relevant to the feature (stack, cloud, git, services)
   - Missing context -> suggest `/scan-project`

3. **Load the gaia-planner skill:**
   - `Skill('gaia-planner')` and follow `reference.md` for Phase 1

4. **Run Phase 1 (Brief Creation):**
   - Size the work (S/M/L)
   - For S: skip brief, go directly to step 5 with the user's description
   - For M/L: ask focused questions, write `brief.md`
   - Present brief, iterate until confirmed

5. **Run Phase 2 (Task Dispatch):**
   - Read the brief (or user description for S)
   - Decompose into native Tasks via TaskCreate
   - Each task carries its own context slice, ACs, and verify commands
   - Report task count and suggest execution order
