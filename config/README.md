# Configuración de Gaia-Ops

**[English version](README.en.md)**

Este directorio contiene toda la configuración del sistema: archivos JSON operacionales, documentación arquitectural, guías de desarrollo y estándares.

## Propósito

Centraliza todos los archivos de configuración y documentación que son consumidos programáticamente o referencialmente por los componentes de gaia-ops.

## Archivos de Configuración (JSON)

| Archivo | Propósito | Consumido por |
|---------|-----------|---------------|
| `clarification_rules.json` | Reglas del motor de clarificación (Phase 0) | `tools/3-clarification/engine.py` |
| `context-contracts.aws.json` | Schema de contexto para agentes AWS | `tools/2-context/context_provider.py` |
| `context-contracts.gcp.json` | Schema de contexto para agentes GCP | `tools/2-context/context_provider.py` |
| `git_standards.json` | Estándares Git programáticos | `tools/4-validation/commit_validator.py` |
| `metrics_targets.json` | Objetivos de performance del sistema | `bin/gaia-metrics.js` |
| `universal-rules.json` | Reglas universales de orquestación | `index.js` |

## Documentación (Markdown)

### Arquitectura

| Archivo | Descripción |
|---------|-------------|
| `agent-catalog.md` | Catálogo completo de agentes con capacidades y ejemplos |
| `delegation-matrix.md` | Matriz de delegación del orquestador |
| `orchestration-workflow.md` | Flujo completo Phase 0-6 del orquestador |

### Guías de Desarrollo

| Archivo | Descripción |
|---------|-------------|
| `documentation-principles.md` | Principios para escribir documentación |
| `git-standards.md` | Estándares Git para commits y PRs |

### Estándares (`standards/`)

| Archivo | Descripción |
|---------|-------------|
| `standards/security-tiers.md` | Definición de tiers T0-T3 |
| `standards/output-format.md` | Formato de salida para agentes |
| `standards/command-execution.md` | Estándares de ejecución de comandos |
| `standards/anti-patterns.md` | Anti-patrones a evitar |

## Uso

### Para Agentes

```python
import json
from pathlib import Path

# Cargar configuración
config_path = Path('.claude/config/git_standards.json')
with open(config_path) as f:
    standards = json.load(f)
```

### Para Desarrolladores

```bash
# Ver configuraciones
cat .claude/config/git_standards.json | jq .

# Validar JSON
jq empty .claude/config/*.json

# Ver documentación
cat .claude/config/orchestration-workflow.md
```

## Estructura

```
config/
├── *.json                          # 6 archivos de configuración
├── *.md                            # 8 archivos de documentación
└── standards/                      # Estándares del sistema
    ├── README.md
    ├── security-tiers.md
    ├── output-format.md
    ├── command-execution.md
    └── anti-patterns.md
```

## Referencias

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Commands](../commands/README.md)

---

**Actualizado:** 2026-01-08 | **Archivos JSON:** 6 | **Archivos MD:** 8
