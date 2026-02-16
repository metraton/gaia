# Archivos de Configuracion de Gaia-Ops

**[English version](README.en.md)**

Configuracion central del sistema de orquestacion.

## Proposito

Define estandares de git, triggers de skills y reglas universales para el comportamiento del sistema.

## Archivos

| Archivo | Proposito |
|---------|-----------|
| `git_standards.json` | Estandares de commits (Conventional Commits), branching, validacion |
| `skill-triggers.json` | Mapeo de triggers a skills para carga on-demand |
| `universal-rules.json` | Reglas universales aplicables a todos los agentes |

## Uso

### Para Agentes

```python
import json
with open('.claude/config/git_standards.json') as f:
    standards = json.load(f)
```

### Para Desarrolladores

```bash
cat .claude/config/git_standards.json
cat .claude/config/skill-triggers.json
```

## Estructura

```
config/
├── git_standards.json
├── skill-triggers.json
├── universal-rules.json
├── README.md
└── README.en.md
```

## Referencias

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Tests](../tests/README.md)

---

**Actualizado:** 2026-02-13 | **Archivos:** 3 (+ 2 READMEs)
