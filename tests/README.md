# Claude Agent System - Test Suite

**[English version](README.en.md)**

Suite de tests para validar el sistema de orquestacion de agentes Claude.

## Metricas (2026-02-16)

| Metrica | Valor |
|---------|-------|
| **Total Tests** | 669 |
| **Pass Rate** | 100% |
| **Tiempo** | ~1.2s |
| **Archivos de test** | 18 |

## Estructura

```
tests/
├── fixtures/         # JSON fixtures (project-context AWS/GCP/full)
├── hooks/            # Tests de hooks y modulos de seguridad
│   └── modules/
│       ├── security/ # safe_commands, blocked_commands, tiers, gitops_validator
│       ├── tools/    # bash_validator, shell_parser, task_validator
│       ├── core/     # config_loader, paths, state
│       └── skills/   # skill_loader
├── system/           # Tests de estructura, permisos, agentes, configuracion
└── tools/            # Tests de context_provider, episodic
```

## Ejecutar Tests

```bash
# Todos los tests
python3 -m pytest tests/ -v

# Por categoria
python3 -m pytest tests/system/ -v
python3 -m pytest tests/hooks/ -v
python3 -m pytest tests/tools/ -v

# Con cobertura
python3 -m pytest tests/ --cov=hooks --cov=tools --cov-report=term
```

## Tests por Archivo

| Archivo | Tests | Categoria |
|---------|-------|-----------|
| `test_safe_commands.py` | 111 | Security |
| `test_blocked_commands.py` | 67 | Security |
| `test_tiers.py` | 54 | Security |
| `test_permissions_system.py` | 52 | System |
| `test_episodic.py` | 46 | Tools |
| `test_gitops_validator.py` | 42 | Security |
| `test_task_validator.py` | 41 | Tools |
| `test_shell_parser.py` | 39 | Tools |
| `test_bash_validator.py` | 37 | Tools |
| `test_skill_loader.py` | 36 | Skills |
| `test_state.py` | 20 | Core |
| `test_config_loader.py` | 18 | Core |
| `test_paths.py` | 17 | Core |
| `test_directory_structure.py` | 14 | System |
| `test_context_provider.py` | 11 | Tools |
| `test_agent_definitions.py` | 11 | System |
| `test_configuration_files.py` | 9 | System |
| `test_schema_compatibility.py` | 4 | System |

## Cobertura Pendiente

Modulos sin tests dedicados:
- `hooks/modules/audit/event_detector.py`, `logger.py`, `metrics.py`

## Dependencias

```bash
pip install pytest pytest-cov
```

---

**Actualizado:** 2026-02-16 | **Tests:** 669
