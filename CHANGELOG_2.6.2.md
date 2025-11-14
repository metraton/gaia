# Changelog - Version 2.6.2

**Release Date:** 2025-11-14  
**Type:** Feature Release

---

## 🎉 What's New

### ✨ Absolute Paths Support
Now `gaia-init` accepts **both relative and absolute paths** for all directory arguments:

```bash
# Before (only relative paths worked reliably)
npx gaia-init --non-interactive \
  --gitops ./gitops \
  --terraform ./terraform

# Now (absolute paths fully supported!)
npx gaia-init --non-interactive \
  --gitops /home/user/project/gitops \
  --terraform /home/user/project/terraform \
  --app-services /home/user/project/services
```

### 🔗 Project Context Repo in Non-Interactive Mode
New `--project-context-repo` flag for non-interactive installations:

```bash
npx gaia-init --non-interactive \
  --gitops /path/to/gitops \
  --terraform /path/to/terraform \
  --app-services /path/to/services \
  --project-id my-project \
  --region us-east-1 \
  --cluster my-cluster \
  --project-context-repo git@bitbucket.org:org/repo.git
```

---

## 🔧 Changes

### Added
- **New function `normalizePath()`** - Handles both absolute and relative paths transparently
- **New CLI option `--project-context-repo`** - Specify git repository for project context in non-interactive mode
- **New environment variable `CLAUDE_PROJECT_CONTEXT_REPO`** - Alternative way to specify context repo

### Changed
- **`getConfiguration()`** - Now normalizes paths using `normalizePath()`
- **`validateAndSetupProjectPaths()`** - Enhanced to handle absolute paths correctly
- **CLI help and documentation** - Updated examples with absolute paths

### Improved
- Path handling is now more robust and user-friendly
- Better error messages for path-related issues
- Clearer documentation and examples

---

## 📝 Examples

### Example 1: Absolute Paths Without Context Repo

```bash
npx gaia-init --non-interactive \
  --gitops /home/jaguilar/aaxis/rnd/repos/gitops \
  --terraform /home/jaguilar/aaxis/rnd/repos/terraform \
  --app-services /home/jaguilar/aaxis/rnd/repos/app-services \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster
```

### Example 2: Absolute Paths With Context Repo

```bash
npx gaia-init --non-interactive \
  --gitops /home/jaguilar/aaxis/rnd/repos/gitops \
  --terraform /home/jaguilar/aaxis/rnd/repos/terraform \
  --app-services /home/jaguilar/aaxis/rnd/repos/app-services \
  --project-id aaxis-rnd \
  --region us-east-1 \
  --cluster rnd-cluster \
  --project-context-repo git@bitbucket.org:aaxisdigital/rnd-project-context.git
```

### Example 3: Using Environment Variable

```bash
export CLAUDE_PROJECT_CONTEXT_REPO="git@bitbucket.org:org/repo.git"

npx gaia-init --non-interactive \
  --gitops /path/to/gitops \
  --terraform /path/to/terraform \
  --app-services /path/to/services \
  --project-id my-project \
  --region us-central1 \
  --cluster my-cluster
```

### Example 4: Mixed Paths (Absolute and Relative)

```bash
npx gaia-init --non-interactive \
  --gitops /home/user/repos/gitops \
  --terraform ./terraform \
  --app-services ./services \
  --project-id my-project
```

---

## 🔄 Upgrade from 2.6.1

Simply update the package:

```bash
npm install @jaguilar87/gaia-ops@latest
```

No breaking changes - all existing commands continue to work as before.

---

## 🐛 Bug Fixes

None in this release.

---

## 📚 Documentation

- Added `EXAMPLES_ABSOLUTE_PATHS.md` - Comprehensive examples guide
- Added `TEST_ABSOLUTE_PATHS.sh` - Automated test suite for new features
- Updated inline documentation in `gaia-init.js`

---

## ✅ Testing

All scenarios tested:
- ✅ Absolute paths without context repo
- ✅ Absolute paths with context repo
- ✅ Relative paths (backward compatibility)
- ✅ Cleanup and reinstallation
- ✅ gaia-metrics command

---

## 🔗 Links

- **npm:** https://www.npmjs.com/package/@jaguilar87/gaia-ops
- **Previous Release:** [v2.6.1](https://www.npmjs.com/package/@jaguilar87/gaia-ops/v/2.6.1)

---

## 👥 Contributors

- **@jaguilar87** - Feature implementation and testing
- **Gaia (meta-agent)** - Code assistance and validation

---

**Enjoy the improved path handling! 🎉**

