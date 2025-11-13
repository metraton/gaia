# 🚀 Pre-Publish Validation Workflow

**Never publish to npm without validating changes locally first!**

This workflow ensures that changes work correctly in your local environment before publishing to npm.

## Overview

```
┌─────────────────────────────────────────────────────────┐
│ 1. Make changes to gaia-ops source code                 │
├─────────────────────────────────────────────────────────┤
│ 2. Commit changes (locally staged, not pushed)          │
├─────────────────────────────────────────────────────────┤
│ 3. Run: npm run pre-publish                             │
│    ├─ Bumps version (patch by default)                 │
│    ├─ Reinstalls node_modules locally                  │
│    ├─ Validates changes in node_modules                │
│    ├─ Runs validation tests                            │
│    └─ Reports readiness to publish                     │
├─────────────────────────────────────────────────────────┤
│ 4. If all checks pass ✓                                 │
│    ├─ npm publish                                       │
│    └─ git push (commit history)                         │
└─────────────────────────────────────────────────────────┘
```

## Commands

### 1. **Dry Run** (Recommended First Step)
```bash
cd /home/jaguilar/aaxis/rnd/repos/gaia-ops
npm run pre-publish:dry
```

**What it does:**
- Checks git status
- Shows what version would be bumped to
- Shows what would happen (but makes NO changes)
- Perfect for understanding the workflow

**Output:** ✓ No changes made, ready to proceed

---

### 2. **Validate Only** (Inspect Current Installation)
```bash
npm run pre-publish:validate
```

**What it does:**
- Validates current version in node_modules
- Checks all critical files
- Runs validation tests
- Does NOT bump version or reinstall

**Use when:** You've already made changes and want to verify they work

**Output:** ✓ Current installation is valid

---

### 3. **Full Pre-Publish Validation** (The Real Deal)
```bash
npm run pre-publish
```

**What it does:**
1. ✓ Validates git status
2. ✓ Reads current version
3. ✓ **Bumps version** (patch, minor, or major)
4. ✓ Reinstalls node_modules in monorepo
5. ✓ Validates version in node_modules matches
6. ✓ Validates all critical files are present
7. ✓ Runs validation tests (JSON, Python, bin scripts)
8. ✓ Outputs summary with readiness to publish

**Use when:** You're ready to publish and want full validation

**Output:** ✓ All validations passed! Ready to publish with: npm publish

---

### 4. **Bump Specific Version**
```bash
# Patch (default, e.g., 2.4.5 → 2.4.6)
npm run pre-publish

# Minor version
npm run pre-publish -- minor

# Major version
npm run pre-publish -- major
```

---

## Step-by-Step Workflow

### Example: Publishing Session Management Scripts

**Step 1: Make changes**
```bash
cd /home/jaguilar/aaxis/rnd/repos/gaia-ops
# ... make edits to source files ...
git add .
git commit -m "feat(session-management): add optimized scripts"
```

**Step 2: Dry run first**
```bash
npm run pre-publish:dry
```
Output:
```
[timestamp] ℹ️  Starting pre-publish validation...
[timestamp] ✓ No uncommitted changes - working tree clean
[timestamp] ✓ Current version: 2.4.5
[timestamp] ℹ️  Step 3: Bumping version (patch)...
[timestamp] ℹ️  [DRY RUN] Would bump version to: 2.4.6
...
[timestamp] ✓ Dry run completed - no changes made
[timestamp] ℹ️  To proceed with actual validation, run without --dry-run flag
```

**Step 3: Run full validation**
```bash
npm run pre-publish
```
Output:
```
[timestamp] ℹ️  Starting pre-publish validation...
[timestamp] ✓ No uncommitted changes - working tree clean
[timestamp] ✓ Current version: 2.4.5
[timestamp] ✓ Version bumped: 2.4.5 → 2.4.6
[timestamp] ℹ️  Reinstalling node_modules in monorepo...
[timestamp] ✓ npm install completed
[timestamp] ✓ node_modules version matches: 2.4.6
[timestamp] ✓ package.json
[timestamp] ✓ bin/gaia-init.js
... (validation tests pass)
======================================================================
ℹ️  PRE-PUBLISH VALIDATION SUMMARY
======================================================================

  Source:           /home/jaguilar/aaxis/rnd/repos/gaia-ops
  Monorepo:         /home/jaguilar/aaxis/rnd/repos
  node_modules:     /home/jaguilar/aaxis/rnd/repos/node_modules/@jaguilar87/gaia-ops

  Current Version:  2.4.5
  New Version:      2.4.6

  Dry Run:          NO
  Validate Only:    NO

======================================================================
✓ All validations passed!
ℹ️  Ready to publish with: npm publish
```

**Step 4: Publish to npm**
```bash
npm publish
```

**Step 5: Push git history**
```bash
git push origin main
```

---

## What Gets Validated

### ✓ Git Status
- Checks for uncommitted changes
- Ensures clean working tree

### ✓ Version Management
- Reads current version from package.json
- Calculates new version (patch/minor/major)
- Updates package.json

### ✓ Installation
- Reinstalls node_modules in monorepo
- Ensures @jaguilar87/gaia-ops is updated

### ✓ Critical Files Presence
```
✓ package.json
✓ bin/gaia-init.js
✓ tools/1-routing/agent_router.py
✓ hooks/pre_tool_use.py
✓ templates/settings.template.json
```

### ✓ JSON Validation
```
✓ templates/settings.template.json (valid JSON)
✓ config/clarification_rules.json (valid JSON)
✓ config/git_standards.json (valid JSON)
```

### ✓ Python Syntax
```
✓ hooks/pre_tool_use.py (valid Python)
✓ tools/1-routing/agent_router.py (valid Python)
```

### ✓ Bin Scripts
```
✓ Checks that all bin scripts exist and are readable
```

---

## Troubleshooting

### Problem: "node_modules installation is invalid"
```
Solution: npm install in the monorepo
  cd /home/jaguilar/aaxis/rnd/repos
  npm install
  npm run pre-publish
```

### Problem: "Version mismatch in node_modules"
```
Solution: node_modules is stale, reinstall
  rm -rf /home/jaguilar/aaxis/rnd/repos/node_modules/@jaguilar87/gaia-ops
  npm install
  npm run pre-publish
```

### Problem: "Python validation skipped"
```
Solution: Python3 is not installed (non-critical)
  Install python3:
    apt-get install python3  # Ubuntu/Debian
    brew install python3     # macOS

  Or simply: ignore warning if Python not needed
```

---

## Architecture Diagram

```
gaia-ops Source                Monorepo
┌──────────────────┐          ┌─────────────────────────┐
│ /gaia-ops/       │          │ /repos/                 │
│ ├─ bin/          │  copy    │ ├─ package.json         │
│ ├─ tools/        ├─────────→│ ├─ gaia-ops/ (source)   │
│ ├─ hooks/        │  files   │ └─ node_modules/        │
│ └─ templates/    │          │    └─ @jaguilar87/      │
│                  │          │       └─ gaia-ops/      │
│ package.json     │          │          (installed)    │
│ v2.4.5           │          │          v2.4.6         │
└──────────────────┘          └─────────────────────────┘
       │                             │
       │  npm run pre-publish        │
       ├────────────────────────────→│
       │                             │
       │  1. Bump version            │
       │  2. npm install             │
       │  3. Validate files          │
       │  4. Run tests               │
       │  5. Report status           │
       │                             │
       ←────────────────────────────→│
       │    ✓ All good!              │
       │    Ready to npm publish     │
       │                             │
```

---

## Best Practices

✅ **DO:**
- Always run `npm run pre-publish:dry` first
- Validate changes locally before npm publish
- Commit changes before running pre-publish
- Check the summary output carefully

❌ **DON'T:**
- Skip the pre-publish validation
- Publish without validating locally
- Ignore validation warnings
- Run pre-publish with uncommitted changes

---

## Examples

### Publishing bug fixes (patch)
```bash
npm run pre-publish:dry    # See what would happen
npm run pre-publish        # 2.4.5 → 2.4.6
npm publish
git push origin main
```

### Publishing new features (minor)
```bash
npm run pre-publish -- minor  # 2.4.5 → 2.5.0
npm publish
git push origin main
```

### Publishing breaking changes (major)
```bash
npm run pre-publish -- major  # 2.4.5 → 3.0.0
npm publish
git push origin main
```

---

## What Happens on Error

If any validation fails, the script will:

1. ❌ Stop execution immediately
2. 🔴 Display error message in red
3. 📝 Show which step failed
4. ⏹️ Exit with code 1 (no changes committed)
5. 💡 Suggest remediation

**Your working directory is safe** - no changes are committed if validation fails.

---

## Manual Workflow (If You Skip Pre-Publish)

If you don't use pre-publish validation:

```bash
# Make changes
git add .
git commit -m "fix: something"

# Manually bump version
vim package.json  # Update version field

# Manually test
npm install
# Manual checks...

# Publish
npm publish
git push origin main
```

**⚠️ NOT RECOMMENDED** - Easy to forget steps, make mistakes, publish broken code.

**RECOMMENDED:** Use `npm run pre-publish`

---

## Integration with CI/CD (Future)

This workflow can be integrated into GitHub Actions or other CI/CD:

```yaml
# .github/workflows/publish.yml
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version bump: patch, minor, or major'
        default: 'patch'

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-node@v2
        with:
          node-version: '18'
      - run: npm run pre-publish -- ${{ github.event.inputs.version }}
      - run: npm publish
```

---

## Questions?

- Check the validation script: `bin/pre-publish-validate.js`
- Review package.json scripts: `scripts` section
- Test locally: `npm run pre-publish:dry`

Enjoy safe, validated publishing! 🚀
