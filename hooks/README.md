# Hooks de Gaia-Ops

**[🇺🇸 English version](README.en.md)**

Los hooks son puntos de intercepción que permiten validar y auditar operaciones antes y después de su ejecución. Son como guardias de seguridad que verifican cada acción.

## 🎯 Propósito

Los hooks garantizan que las operaciones cumplan con las políticas de seguridad y sean auditables. Proporcionan una capa de protección automática sin requerir intervención manual constante.

**Problema que resuelve:** Sin hooks, las operaciones peligrosas podrían ejecutarse sin validación. Los hooks interceptan comandos y aplican reglas de seguridad automáticamente, bloqueando operaciones no autorizadas.

## 🔄 Cómo Funciona

### Flujo de Arquitectura

```
Agente intenta ejecutar comando
        ↓
[pre_tool_use.py] ← intercepta ANTES
        ↓
    Valida operación
    ┌───────┴───────┐
    ↓               ↓
 PERMITIDO      BLOQUEADO
    ↓               ↓
Comando ejecuta  ERROR + log
    ↓
[post_tool_use.py] ← intercepta DESPUÉS
    ↓
Audita resultado
    ↓
Log a .claude/logs/
```

### Flujo de Ejemplo Real

```
Ejemplo: Agent intenta "kubectl apply -f deployment.yaml"

1. [gitops-operator] genera comando:
   kubectl apply -f deployment.yaml
   ↓
2. [pre_tool_use.py] intercepta:
   - Detecta: kubectl apply (operación T3)
   - Clasifica: write_operation, production
   - Consulta: settings.json permissions
   ↓
3. [PolicyEngine] evalúa:
   - Tier: T3 (execution)
   - Requiere: user_approval
   - Estado actual: no_approval_yet
   ↓
4. Decisión: BLOQUEAR temporalmente
   ↓
5. [Approval Gate] se activa:
   - Muestra cambios propuestos
   - Usuario revisa: deployment.yaml
   - Usuario aprueba: ✅
   ↓
6. [pre_tool_use.py] permite ejecución
   ↓
7. [kubectl] ejecuta:
   deployment.apps/auth configured
   ↓
8. [post_tool_use.py] audita:
   - Timestamp: 2025-11-14 10:23:45
   - Command: kubectl apply
   - Exit code: 0
   - Output: deployment configured
   - Approved by: user@example.com
   ↓
9. Log guardado en:
   .claude/logs/2025-11-14-audit.jsonl
```

## 📋 Hooks Disponibles

### Pre-Execution Hooks

#### `pre_tool_use.py` (~400 líneas)
El guardian principal - valida TODAS las operaciones antes de ejecutarlas.

**Qué valida:**
- Tier de seguridad (T0, T1, T2, T3)
- Permisos según settings.json
- Comandos bloqueados globalmente
- Contexto de ejecución

**Reglas de decisión:**
```python
if tier == "T3" and not has_approval():
    return BLOCK  # Requiere aprobación

if command in always_blocked:
    return BLOCK  # Nunca permitir

if permission == "deny":
    return BLOCK  # Explícitamente denegado

if permission == "ask":
    return ASK_USER  # Solicitar confirmación

return ALLOW  # Operación segura
```

**Ejemplos de bloqueo:**
- `rm -rf /` → Bloqueado (always_blocked)
- `terraform apply` sin approval → Bloqueado (T3)
- `kubectl delete namespace` → Solicita confirmación (T3 + destructivo)

---

#### `pre_phase_hook.py` (~200 líneas)
Valida transiciones entre fases del workflow (Phase 0-6).

**Qué valida:**
- Orden correcto de fases
- Prerequisitos completados
- Approval gates no omitidos

**Ejemplo:**
```
Phase 5 (Ejecución) requiere:
- Phase 4 (Approval) completada
- validation["approved"] == True
- No omisión de gates
```

---

#### `pre_kubectl_security.py` (~180 líneas)
Validación especializada para comandos de Kubernetes.

**Qué valida:**
- Namespace correcto
- No operaciones en kube-system
- No secrets expuestos en logs
- RBAC apropiado

**Ejemplos de protección:**
```
❌ kubectl delete namespace kube-system
   → BLOQUEADO (namespace crítico)

❌ kubectl get secret -o yaml
   → BLOQUEADO (puede exponer secrets)

✅ kubectl get pods -n production
   → PERMITIDO (read-only, namespace válido)
```

---

### Post-Execution Hooks

#### `post_tool_use.py` (~300 líneas)
Audita TODAS las operaciones después de ejecutarse.

**Qué audita:**
- Timestamp de ejecución
- Comando ejecutado
- Exit code
- Output (sanitizado)
- Usuario que aprobó (si T3)
- Duración de ejecución

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

#### `post_phase_hook.py` (~150 líneas)
Audita transiciones de fase y actualiza estado del workflow.

**Qué audita:**
- Fase completada
- Tiempo en fase
- Decisiones tomadas
- Errores (si los hubo)

---

### Lifecycle Hooks

#### `session_start.py` (~100 líneas)
Se ejecuta al inicio de cada sesión de Claude Code.

**Qué hace:**
- Carga project-context.json
- Inicializa logs
- Valida estructura de .claude/
- Restaura sesión activa (si existe)

---

#### `subagent_stop.py` (~120 líneas)
Se ejecuta cuando un subagente termina su trabajo.

**Qué hace:**
- Recopila output del agente
- Actualiza session/active/
- Log de finalización
- Notifica al orquestador

---

## 🚀 Cómo Funcionan los Hooks

### Invocación Automática

Claude Code invoca hooks automáticamente - no requieren llamado manual:

```
Agent → pre_tool_use.py → VALIDATE → ALLOW/BLOCK
                            ↓
                      If ALLOW:
                            ↓
                      Execute command
                            ↓
Agent ← post_tool_use.py ← AUDIT
```

### Configuración de Permisos

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

### Logs de Auditoría

Todos los hooks escriben a `.claude/logs/`:

```bash
# Ver logs de hoy
cat .claude/logs/$(date +%Y-%m-%d)-audit.jsonl | jq .

# Buscar operaciones T3
cat .claude/logs/*.jsonl | jq 'select(.tier == "T3")'

# Buscar operaciones bloqueadas
cat .claude/logs/*.jsonl | jq 'select(.action == "blocked")'
```

## 🔧 Características Técnicas

### Estructura de Hooks

Cada hook es un script Python con interface estandarizada:

```python
def execute_hook(context: dict) -> dict:
    """
    Args:
        context: Información del comando/fase
    
    Returns:
        {
            "action": "allow" | "block" | "ask",
            "reason": "Explicación",
            "metadata": {}
        }
    """
    pass
```

### Tiers de Seguridad

| Tier | Tipo de Operación | Requiere Approval | Hook Validación |
|------|-------------------|-------------------|-----------------|
| **T0** | Read-only (get, list) | No | pre_tool_use |
| **T1** | Validation (validate, dry-run) | No | pre_tool_use |
| **T2** | Planning (plan, simulate) | No | pre_tool_use |
| **T3** | Execution (apply, delete) | **Sí** ✅ | pre_tool_use + pre_phase |

### PolicyEngine

El motor de políticas dentro de `pre_tool_use.py` que clasifica comandos:

```python
class PolicyEngine:
    def classify_command(self, cmd: str) -> dict:
        # Analiza comando
        # Retorna: tier, risk_level, requires_approval
```

**Clasificación:**
- **Keywords:** terraform apply → T3
- **Patterns:** kubectl delete → T3, ask
- **Context:** production namespace → higher risk

### Tests de Hooks

Los hooks tienen ~74 tests de integración:

```bash
# Ver tests
python3 -m pytest tests/integration/ -v

# Tests específicos de hooks
python3 -m pytest tests/integration/test_hooks_integration.py -v
```

## 📖 Referencias

**Archivos de hooks:**
```
hooks/
├── pre_tool_use.py        (~400 líneas) - Guardian principal
├── post_tool_use.py       (~300 líneas) - Auditor principal
├── pre_phase_hook.py      (~200 líneas) - Validador de fases
├── post_phase_hook.py     (~150 líneas) - Auditor de fases
├── pre_kubectl_security.py (~180 líneas) - K8s security
├── session_start.py       (~100 líneas) - Inicialización
└── subagent_stop.py       (~120 líneas) - Finalización
```

**Configuración relacionada:**
- `.claude/settings.json` - Permisos y tiers

**Tests relacionados:**
- `tests/integration/test_hooks_integration.py` (~55 tests)
- `tests/integration/test_hooks_workflow.py` (~19 tests)
- `tests/permissions-validation/` (~53 tests)

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Total de hooks:** 7 hooks (4 pre, 2 post, 1 lifecycle)  
**Cobertura de tests:** ~120 tests  
**Mantenido por:** Gaia (meta-agent) + equipo de seguridad

