# Archivos de Configuracion de Gaia-Ops

**[English version](README.en.md)**

Configuracion central y documentacion de referencia del sistema de orquestacion.

## Proposito

Define el comportamiento del sistema, estandares y contratos entre componentes. Proporciona la "fuente de verdad" para como debe operar el sistema.

## Archivos Principales

### Documentacion de Sistema

| Archivo | Lineas | Proposito |
|---------|--------|-----------|
| `AGENTS.md` | ~95 | Overview del sistema de agentes |
| `orchestration-workflow.md` | ~735 | Workflow Phase 0-6 |
| `agent-catalog.md` | ~603 | Capacidades de cada agente |

### Estandares y Convenciones

| Archivo | Lineas | Proposito |
|---------|--------|-----------|
| `git-standards.md` | ~682 | Commits, branching, workflow |
| `git_standards.json` | - | Version programatica |

### Contratos de Contexto

| Archivo | Proposito |
|---------|-----------|
| `context-contracts.md` | Define contexto por agente |
| `context-contracts.gcp.json` | Schema para GCP |
| `context-contracts.aws.json` | Schema para AWS |

### Reglas y Politicas

| Archivo | Proposito |
|---------|-----------|
| `clarification_rules.json` | Motor de clarificacion (Phase 0) |
| `delegation-matrix.md` | Decisiones de delegacion |

### Machine Learning

| Archivo | Proposito |
|---------|-----------|
| `embeddings_info.json` | Metadata de embeddings |
| `intent_embeddings.json` | Vectores de intents |
| `intent_embeddings.npy` | NumPy para carga rapida |

### Metricas

| Archivo | Proposito |
|---------|-----------|
| `metrics_targets.json` | Objetivos de performance |

## Uso

### Para Agentes

```python
import json
with open('.claude/config/git_standards.json') as f:
    standards = json.load(f)
```

### Para Desarrolladores

```bash
cat .claude/config/orchestration-workflow.md
cat .claude/config/git-standards.md
```

## Estructura

```
config/
├── AGENTS.md
├── orchestration-workflow.md
├── agent-catalog.md
├── git-standards.md
├── git_standards.json
├── context-contracts.md
├── context-contracts.gcp.json
├── context-contracts.aws.json
├── clarification_rules.json
├── delegation-matrix.md
├── embeddings_info.json
├── intent_embeddings.json
├── intent_embeddings.npy
├── metrics_targets.json
├── documentation-principles.md
└── documentation-principles.en.md
```

## Referencias

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Tests](../tests/README.md)

---

**Actualizado:** 2025-12-06 | **Archivos:** 17
