# Release Notes: Gaia-Ops 2.6.1

**Release Date:** 2025-11-14  
**Version:** 2.6.1

## 🎉 What's New

### ✨ New Command: `gaia-metrics`
Display comprehensive system metrics including:
- 🎯 Routing accuracy (current vs target)
- 💾 Context efficiency (token savings)
- 🤖 Agent invocations (usage distribution)
- 🔒 Security tier usage (T1/T2/T3 distribution)

```bash
npx gaia-metrics
```

### 📚 Complete Documentation Overhaul
- **16 new READMEs** across all directories (Spanish + English)
- **Human-first approach**: Clear, concise, beginner-friendly
- **ASCII flow diagrams** in every README
- **Real-world examples** for every feature
- **Documentation principles guide** for consistency

New READMEs added:
- `agents/README.md` - 6 specialist agents
- `bin/README.md` - Utility scripts
- `commands/README.md` - 11 slash commands
- `config/README.md` - 17 configuration files
- `hooks/README.md` - 7 security hooks
- `templates/README.md` - Installation templates
- `INSTALL.md` - Comprehensive installation guide

Each with `.en.md` English version!

## 🔧 Improvements

### Cleanup & Uninstall Enhancements
- **`gaia-cleanup` now removes `AGENTS.md`** (previously missed)
- **Broken symlink detection** - Cleanup now removes broken symlinks
- **Better messaging** with "[ALWAYS CREATED]" indicators

### Update Command Improvements
- **ALWAYS creates files** - `CLAUDE.md` and `settings.json` now recreated even if deleted
- **Smarter detection** - Better handling of missing files during updates

## 🐛 Fixes

- ✅ Cleanup script properly removes `AGENTS.md` symlink at project root
- ✅ Update script recreates files even if manually deleted
- ✅ Broken symlinks properly detected and removed
- ✅ Removed obsolete files and documentation

## 🗑️ Cleanup

Removed obsolete files:
- `reports/` directory (historical development reports)
- `speckit/templates/tasks-template-bkp.md` (backup file)
- `tests/permissions-validation/empirical-permission-testing.md` (replaced by automated tests)
- Ghost references in documentation

## 📦 Installation

### From npm Registry (Recommended)
```bash
npm install @jaguilar87/gaia-ops
npx gaia-init
```

### From Local Source
```bash
cd /path/to/gaia-ops
npm install
npm link
```

## 🔄 Upgrade from 2.6.0

```bash
npm install @jaguilar87/gaia-ops@latest
# Files will auto-update via postinstall hook
```

**⚠️ WARNING:** `CLAUDE.md` and `settings.json` will be overwritten. Back up customizations before upgrading.

## 📖 Documentation Highlights

### New Installation Guide
Comprehensive `INSTALL.md` with:
- 🧩 Analogies (Lego blocks, recipe ingredients)
- 📊 ASCII diagrams for installation flow
- 🎯 Real-world examples
- 🔧 Troubleshooting section
- 📚 Complete documentation index

### Documentation Principles
New guide at `config/documentation-principles.md`:
- **Clarity First**: No jargon, simple language
- **User-Oriented**: Solve problems, not describe code
- **Consistency**: Same structure everywhere
- **Visual**: ASCII diagrams for complex flows
- **Bilingual**: Spanish primary, English `.en.md`

## 🧪 Testing

Comprehensive test plan created (`TEST_PLAN.md`) covering:
- ✅ Fresh installation (interactive & non-interactive)
- ✅ Update scenarios (with/without files)
- ✅ Cleanup & uninstall
- ✅ Reinstallation
- ✅ Edge cases (permissions, corruption, etc.)

## 🤝 Contributors

- **@jaguilar87** - Project lead & implementation
- **Gaia (meta-agent)** - Documentation & testing

## 📊 Metrics Targets

Updated targets for 2.6.1:
- **Routing Accuracy:** ≥90%
- **Context Efficiency:** ≥80%
- **Clarification Rate:** 20-30%
- **Agent Response Time:** <2s

## 🔗 Resources

- **NPM Package:** https://www.npmjs.com/package/@jaguilar87/gaia-ops
- **Repository:** https://github.com/jaguilar87/gaia-ops (private)
- **Documentation:** See `INSTALL.md` for complete guide

## 🚀 Next Steps

After installing/upgrading:

1. **Configure your project:**
   ```bash
   npx gaia-init
   ```

2. **View system metrics:**
   ```bash
   npx gaia-metrics
   ```

3. **Read documentation:**
   ```bash
   cat INSTALL.md           # Installation guide
   cat agents/README.md     # Learn about agents
   cat commands/README.md   # Learn about commands
   ```

4. **Start using Gaia:**
   Open Claude Code and use `/gaia` commands!

---

**Enjoy the improved Gaia-Ops! 🎉**

For issues or questions, contact the maintainer.

