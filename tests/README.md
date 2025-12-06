# Claude Agent System - Test Suite

**[English version](README.en.md)**

Suite de tests para validar el sistema de orquestacion de agentes Claude.

## Metricas (2025-12-06)

| Metrica | Valor |
|---------|-------|
| **Total Tests** | 359 |
| **Pass Rate** | 100% |
| **Tiempo** | ~2.2s |
| **Routing Accuracy** | 92.7% |

## Estructura

```
tests/
├── system/           # Tests de estructura e integridad
├── tools/            # Tests de routing y contexto
├── validators/       # Tests de aprobacion y commits
└── integration/      # Tests end-to-end de hooks
```

## Ejecutar Tests

```bash
# Todos los tests
python3 -m pytest tests/ -v

# Por categoria
python3 -m pytest tests/system/ -v
python3 -m pytest tests/tools/ -v
python3 -m pytest tests/validators/ -v
python3 -m pytest tests/integration/ -v

# Con cobertura
python3 -m pytest tests/ --cov=.claude/tools --cov-report=term
```

## Categorias de Tests

### system/ (~10 tests)
- Estructura de directorios
- Definiciones de agentes
- Archivos de configuracion

### tools/ (~15 tests)
- Agent router (routing semantico)
- Context provider (generacion de contexto)

### validators/ (~10 tests)
- Approval gate (workflow de aprobacion)
- Commit validator (Conventional Commits)

### integration/ (~74 tests)
- Pre/post hook validation
- PolicyEngine command classification
- GitOps security
- Settings permission matching

## Dependencias

```bash
pip install pytest pytest-cov
```

## Golden Set de Routing

El test de precision evalua 26 requests semanticos:

| Agent | Precision |
|-------|-----------|
| terraform-architect | 95% |
| gitops-operator | 93% |
| gcp-troubleshooter | 90% |
| devops-developer | 92% |

---

**Actualizado:** 2025-12-06 | **Tests:** 359
