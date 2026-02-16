# Gaia-Ops Configuration Files

**[Version en espanol](README.md)**

Central configuration for the orchestration system.

## Purpose

Defines git standards, skill triggers and universal rules for system behavior.

## Files

| File | Purpose |
|------|---------|
| `git_standards.json` | Commit standards (Conventional Commits), branching, validation |
| `skill-triggers.json` | Trigger-to-skill mapping for on-demand loading |
| `universal-rules.json` | Universal rules applicable to all agents |

## Usage

### For Agents

```python
import json
with open('.claude/config/git_standards.json') as f:
    standards = json.load(f)
```

### For Developers

```bash
cat .claude/config/git_standards.json
cat .claude/config/skill-triggers.json
```

## Structure

```
config/
├── git_standards.json
├── skill-triggers.json
├── universal-rules.json
├── README.md
└── README.en.md
```

## References

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Tests](../tests/README.md)

---

**Updated:** 2026-02-13 | **Files:** 3 (+ 2 READMEs)
