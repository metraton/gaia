# Gaia-Ops Patterns — Reference

## Component Map

| Component | Location | Purpose |
|-----------|----------|---------|
| **Orchestrator** | `CLAUDE.md` | Routes requests, manages workflow |
| **Agents** | `agents/*.md` | Domain identity + scope |
| **Hooks** | `hooks/*.py` | Context injection, validation, audit |
| **Skills** | `skills/*/SKILL.md` | Injected procedural knowledge |
| **Tools** | `tools/` | Python utilities |
| **Config** | `config/` | System configuration |

## Documentation Template

```
1. User sends prompt
2. Orchestrator routes
3. **→ [THIS MODULE] ← acts here**
4. Agent executes
5. Orchestrator responds
```
