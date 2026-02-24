# Configuracion de Gaia-Ops

**[English version](README.en.md)**

Configuracion central del sistema de orquestacion.

## Archivos

| Archivo | Proposito | Leido por |
|---------|-----------|-----------|
| `context-contracts.json` | Contratos base cloud-agnosticos: secciones `read`/`write` por agente | `context_provider.py`, `context_writer.py`, `pre_tool_use.py` |
| `cloud/gcp.json` | Extensiones GCP: `gcp_services`, `workload_identity`, `static_ips` | Mismo trio, merged en runtime |
| `cloud/aws.json` | Extensiones AWS: `vpc_mapping`, `load_balancers`, `api_gateway`, `irsa_bindings`, `aws_accounts` | Mismo trio, merged en runtime |
| `context-contracts.gcp.json` | **Legado** — conservado para compatibilidad hacia atras | Fallback si `context-contracts.json` no existe |
| `context-contracts.aws.json` | **Legado** — conservado para compatibilidad hacia atras | Fallback si `context-contracts.json` no existe |
| `git_standards.json` | Estandares de commits (Conventional Commits), tipos permitidos, footers prohibidos | `commit_validator.py` |
| `universal-rules.json` | Reglas de comportamiento inyectadas a todos los agentes | `context_provider.py` |

## Como funciona el merge base+cloud

En runtime, `context_provider.py` ejecuta la siguiente logica:

```
1. Leer context-contracts.json         <- secciones agnósticas (todos los clouds)
2. Detectar cloud_provider del project-context.json
3. Leer cloud/{provider}.json          <- secciones especificas del cloud
4. Merge: extender las listas read/write por agente (sin duplicados)
5. Resultado: contrato completo para el agente en ese cloud
```

Fallback si `context-contracts.json` no existe: usa `context-contracts.{provider}.json` (legado).

## Estructura

```
config/
├── context-contracts.json        <- base agnóstico (todos los agentes)
├── cloud/
│   ├── gcp.json                  <- extensiones GCP + section_schemas
│   └── aws.json                  <- extensiones AWS + section_schemas
├── context-contracts.gcp.json    <- legado (fallback)
├── context-contracts.aws.json    <- legado (fallback)
├── git_standards.json
├── universal-rules.json
├── README.md
└── README.en.md
```

## Agregar soporte para un nuevo cloud (Azure, etc.)

1. Crear `cloud/azure.json` con el mismo schema que `cloud/gcp.json`
2. Definir los agentes y sus secciones especificas de Azure
3. No hay cambios de codigo necesarios — `context_provider.py` lo detecta automaticamente

## Referencias

- [Agents](../agents/README.md)
- [Tools](../tools/README.md)
- [Tests](../tests/README.md)

---

**Actualizado:** 2026-02-24 | **Contratos activos:** base + 2 clouds (GCP, AWS)
