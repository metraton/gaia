# Agentes Especialistas de Gaia-Ops

**[English version](README.en.md)**

Los agentes son especialistas de IA que manejan tareas especificas en tu infraestructura DevOps.

## Proposito

Dividir el trabajo complejo en especialidades manejables. Cada agente se enfoca en lo que mejor sabe hacer - como tener un equipo de expertos en lugar de un generalista.

## Como Funciona

```
Usuario envia pregunta
        |
[Orquestador] -> [Agent Router]
        |
   Selecciona agente
        |
  terraform | gitops | gcp | aws | devops | speckit | gaia
        |
[Context Provider] -> Agente ejecuta
        |
   Resultado
```

## Agentes Disponibles

| Agente | Experto en | Tiers |
|--------|-----------|-------|
| **terraform-architect** | Infraestructura como codigo | T0-T3 |
| **gitops-operator** | Kubernetes y despliegues | T0-T3 |
| **cloud-troubleshooter** | Diagnostico GCP | T0 |
| **cloud-troubleshooter** | Diagnostico AWS | T0 |
| **devops-developer** | Codigo y CI/CD | T0-T2 |
| **speckit-planner** | Especificacion y planificacion de features | T0-T2 |
| **gaia** | Sistema de agentes | T0-T2 |

## Tiers de Seguridad

| Tier | Descripcion | Aprobacion |
|------|-------------|------------|
| T0 | Solo lectura | No |
| T1 | Validacion | No |
| T2 | Planificacion | No |
| T3 | Ejecucion | **Si** |

## Invocacion

### Automatica (Recomendado)

```bash
# El orquestador selecciona automaticamente
"Despliega auth-service version 1.2.3"
# -> gitops-operator

"Planificar feature de notificaciones"
# -> speckit-planner
```

### Manual

```python
Task(
  subagent_type="gitops-operator",
  description="Deploy auth service",
  prompt="Deploy auth-service version 1.2.3"
)

Task(
  subagent_type="speckit-planner",
  description="Plan notification feature",
  prompt="Create spec for push notification system"
)
```

## Routing Inteligente

- Keywords: Terminos especificos del dominio
- Semantic matching: Embeddings vectoriales
- Context awareness: Contexto del proyecto

**Precision actual:** ~92.7%

## Referencias

- [config/orchestration-workflow.md](../config/orchestration-workflow.md)
- [config/agent-catalog.md](../config/agent-catalog.md)
- [config/context-contracts.md](../config/context-contracts.md)

---

**Actualizado:** 2025-12-10 | **Agentes:** 7
