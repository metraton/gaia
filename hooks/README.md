# Hooks de Gaia-Ops

**[English version](README.en.md)**

Los hooks son puntos de intercepcion que permiten validar y auditar operaciones antes y despues de su ejecucion. Son como guardias de seguridad que verifican cada accion.

## Proposito

Los hooks garantizan que las operaciones cumplan con las politicas de seguridad y sean auditables. Proporcionan una capa de proteccion automatica sin requerir intervencion manual constante.

**Problema que resuelve:** Sin hooks, las operaciones peligrosas podrian ejecutarse sin validacion. Los hooks interceptan comandos y aplican reglas de seguridad automaticamente, bloqueando operaciones no autorizadas.

## Como Funciona

### Flujo de Arquitectura

```
Agente intenta ejecutar comando
        |
[pre_tool_use.py] <- intercepta ANTES
        |
    Valida operacion
    +-------+-------+
    |               |
 PERMITIDO      BLOQUEADO
    |               |
Comando ejecuta  ERROR + log
    |
[post_tool_use.py] <- intercepta DESPUES
    |
Audita resultado
    |
Log a .claude/logs/
```

## Hooks Disponibles

### Pre-Execution Hooks

#### `pre_tool_use.py` (~400 lineas)
El guardian principal - valida TODAS las operaciones antes de ejecutarlas.

**Que valida:**
- Tier de seguridad (T0, T1, T2, T3)
- Permisos segun settings.json
- Comandos bloqueados globalmente
- Contexto de ejecucion

**Reglas de decision:**
```python
if tier == "T3" and not has_approval():
    return BLOCK  # Requiere aprobacion

if command in always_blocked:
    return BLOCK  # Nunca permitir

if permission == "deny":
    return BLOCK  # Explicitamente denegado

if permission == "ask":
    return ASK_USER  # Solicitar confirmacion

return ALLOW  # Operacion segura
```

---

#### `pre_phase_hook.py` (~200 lineas)
Valida transiciones entre fases del workflow (Phase 0-6).

**Que valida:**
- Orden correcto de fases
- Prerequisitos completados
- Approval gates no omitidos

---

#### `pre_kubectl_security.py` (~180 lineas)
Validacion especializada para comandos de Kubernetes.

**Que valida:**
- Namespace correcto
- No operaciones en kube-system
- No secrets expuestos en logs
- RBAC apropiado

---

### Post-Execution Hooks

#### `post_tool_use.py` (~300 lineas)
Audita TODAS las operaciones despues de ejecutarse.

**Que audita:**
- Timestamp de ejecucion
- Comando ejecutado
- Exit code
- Output (sanitizado)
- Usuario que aprobo (si T3)
- Duracion de ejecucion

**Formato de log:**
```json
{
  "timestamp": "2025-11-14T10:23:45Z",
  "event": "tool_executed",
  "tier": "T3",
  "command": "kubectl apply -f deployment.yaml",
  "exit_code": 0,
  "duration_ms": 1234,
  "approved_by": "user@example.com",
  "output_summary": "deployment configured"
}
```

---

#### `post_phase_hook.py` (~150 lineas)
Audita transiciones de fase y actualiza estado del workflow.

---

### Workflow Metrics Hook

#### `subagent_stop.py` (~200 lineas)
Se ejecuta cuando un subagente termina su trabajo. Captura metricas y detecta anomalias.

**Que hace:**
- Captura metricas de ejecucion (duracion, exit code)
- Detecta anomalias (ejecucion lenta, fallos)
- Senala Gaia cuando hay problemas
- Log a workflow-episodic/metrics.jsonl

**Anomalias detectadas:**
- Slow execution (> 120s)
- Failed executions (exit_code != 0)
- Consecutive failures (3+ in a row)

---

## Como Funcionan los Hooks

### Invocacion Automatica

Claude Code invoca hooks automaticamente - no requieren llamado manual:

```
Agent -> pre_tool_use.py -> VALIDATE -> ALLOW/BLOCK
                            |
                      If ALLOW:
                            |
                      Execute command
                            |
Agent <- post_tool_use.py <- AUDIT
```

### Configuracion de Permisos

Los hooks leen `.claude/settings.json` para decisiones:

```json
{
  "security_tiers": {
    "T0": {"approval_required": false},
    "T1": {"approval_required": false},
    "T2": {"approval_required": false},
    "T3": {"approval_required": true}
  },
  "always_blocked": [
    "rm -rf /",
    "sudo reboot"
  ],
  "ask_permissions": [
    "kubectl delete",
    "terraform destroy"
  ]
}
```

### Logs de Auditoria

Todos los hooks escriben a `.claude/logs/`:

```bash
# Ver logs de hoy
cat .claude/logs/$(date +%Y-%m-%d)-audit.jsonl | jq .

# Buscar operaciones T3
cat .claude/logs/*.jsonl | jq 'select(.tier == "T3")'

# Buscar operaciones bloqueadas
cat .claude/logs/*.jsonl | jq 'select(.action == "blocked")'
```

## Caracteristicas Tecnicas

### Estructura de Hooks

Cada hook es un script Python con interface estandarizada:

```python
def execute_hook(context: dict) -> dict:
    """
    Args:
        context: Informacion del comando/fase
    
    Returns:
        {
            "action": "allow" | "block" | "ask",
            "reason": "Explicacion",
            "metadata": {}
        }
    """
    pass
```

### Tiers de Seguridad

| Tier | Tipo de Operacion | Requiere Approval | Hook Validacion |
|------|-------------------|-------------------|-----------------|
| **T0** | Read-only (get, list) | No | pre_tool_use |
| **T1** | Validation (validate, dry-run) | No | pre_tool_use |
| **T2** | Planning (plan, simulate) | No | pre_tool_use |
| **T3** | Execution (apply, delete) | **Si** | pre_tool_use + pre_phase |

### Tests de Hooks

Los hooks tienen tests de integracion:

```bash
# Ver tests
python3 -m pytest tests/integration/ -v

# Tests especificos de hooks
python3 -m pytest tests/integration/test_hooks_integration.py -v
```

## Referencias

**Archivos de hooks:**
```
hooks/
├── pre_tool_use.py        (~400 lineas) - Guardian principal
├── post_tool_use.py       (~300 lineas) - Auditor principal
├── pre_phase_hook.py      (~200 lineas) - Validador de fases
├── post_phase_hook.py     (~150 lineas) - Auditor de fases
├── pre_kubectl_security.py (~180 lineas) - K8s security
└── subagent_stop.py       (~200 lineas) - Workflow metrics
```

**Configuracion relacionada:**
- `.claude/settings.json` - Permisos y tiers

**Tests relacionados:**
- `tests/integration/test_hooks_integration.py`
- `tests/integration/test_hooks_workflow.py`
- `tests/workflow/test_workflow_metrics.py`

---

**Version:** 2.0.0  
**Ultima actualizacion:** 2025-12-06  
**Total de hooks:** 6 hooks (4 pre, 1 post, 1 metrics)  
**Mantenido por:** Gaia (meta-agent)
