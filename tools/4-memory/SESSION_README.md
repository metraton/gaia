# Agent Session Management

Sistema de gestión de sesiones de agentes con capacidad de resume para GAIA-OPS.

## ¿Qué es esto?

Un sistema que mantiene contexto de ejecución de agentes a través de interrupciones (pausas de aprobación, investigaciones multi-turno). Permite que un agente "retome" donde lo dejó sin perder contexto.

## ¿Dónde encaja?

```
Usuario solicita tarea
     ↓
[pre_delegate.py] → ¿Existe sesión previa?
     ↓                    ↓
   NO: crear nueva    SI: ¿Resumible?
     ↓                    ↓
Agente ejecuta ← YES: Continuar con contexto
     ↓
[agent_session.py] → Actualiza estado durante ejecución
     ↓
[subagent_stop.py] → Finaliza sesión al terminar
```

## Quick Start

```bash
# Crear sesión nueva
python3 tools/4-memory/agent_session.py create --agent terraform-architect --purpose approval_workflow

# Actualizar fase
python3 tools/4-memory/agent_session.py update agent-20260108-abc123 --phase approval

# Verificar si debe resumir
python3 tools/4-memory/agent_session.py should-resume agent-20260108-abc123

# Listar sesiones activas
python3 tools/4-memory/agent_session.py list --active-only

# Finalizar sesión
python3 tools/4-memory/agent_session.py finalize agent-20260108-abc123 completed

# Cleanup sesiones viejas
python3 tools/4-memory/agent_session.py cleanup --hours 24
```

## Arquitectura

### Componentes

| Archivo | Propósito |
|---------|-----------|
| `agent_session.py` | CRUD de sesiones + lógica de resume |
| `hooks/pre_delegate.py` | Check resume antes de delegar |
| `hooks/subagent_stop.py` | Finaliza sesión al terminar |
| `.claude/session/{agent_id}/` | Storage de estado de sesiones |

### Estado de Sesión

Cada sesión tiene un archivo `.claude/session/{agent_id}/state.json`:

```json
{
  "agent_id": "agent-20260108-180530-abc12345",
  "agent_name": "terraform-architect",
  "purpose": "approval_workflow",
  "created_at": "2026-01-08T18:05:30Z",
  "last_updated": "2026-01-08T18:10:15Z",
  "phase": "approval",
  "metadata": {
    "task_id": "T001",
    "tags": ["terraform", "infrastructure"]
  },
  "resume_ready": true,
  "history": [
    {
      "from_phase": "initializing",
      "to_phase": "investigating",
      "timestamp": "2026-01-08T18:06:00Z"
    },
    {
      "from_phase": "investigating",
      "to_phase": "approval",
      "timestamp": "2026-01-08T18:10:15Z"
    }
  ],
  "error_count": 0,
  "last_error": null
}
```

### Fases de Ejecución

| Fase | Descripción | Resumible |
|------|-------------|-----------|
| `initializing` | Sesión creada, agente iniciando | No |
| `investigating` | Agente recopilando información | **Sí** |
| `planning` | Agente creando plan de ejecución | **Sí** |
| `approval` | Esperando aprobación del usuario | **Sí** |
| `executing` | Agente ejecutando operaciones | No |
| `validating` | Agente verificando resultados | No |
| `completed` | Sesión terminada exitosamente | No (final) |
| `failed` | Sesión falló con errores | No (final) |
| `abandoned` | Sesión abandonada por usuario | No (final) |

## Decisión de Resume

`should_resume(agent_id)` retorna `True` solo si:

1. ✓ Sesión existe y state.json es válido
2. ✓ `resume_ready == True`
3. ✓ `phase` es resumible (approval, investigating, planning)
4. ✓ Última actividad < 30 minutos
5. ✓ Error count < 3

**Timeout por defecto:** 30 minutos

## Flujo de Uso

### Caso 1: Approval Workflow

```python
# 1. Agente inicia tarea
agent_id = create_session(
    agent_name="terraform-architect",
    purpose="approval_workflow",
    metadata={"task_id": "T001"}
)

# 2. Agente genera plan
update_state(agent_id, phase="planning")

# 3. Agente solicita aprobación
update_state(agent_id, phase="approval")

# [Usuario aprueba después de 10 minutos]

# 4. Pre-delegate hook check
if should_resume(agent_id):
    # Resume con contexto completo
    session = get_session(agent_id)
    # Agente continúa con session["history"] y session["metadata"]

# 5. Agente ejecuta
update_state(agent_id, phase="executing")

# 6. Agente termina
finalize_session(agent_id, outcome="completed", summary="Terraform applied successfully")
```

### Caso 2: Multi-turn Investigation

```python
# Turn 1: Investigar problema
agent_id = create_session(
    agent_name="gitops-operator",
    purpose="investigation"
)
update_state(agent_id, phase="investigating", metadata={"findings": ["issue A", "issue B"]})

# [Usuario hace pregunta adicional después de 5 minutos]

# Turn 2: Continuar investigación
if should_resume(agent_id):
    session = get_session(agent_id)
    previous_findings = session["metadata"]["findings"]
    # Agente continúa desde donde dejó

update_state(agent_id, metadata={"findings": previous_findings + ["issue C"]})

# Turn 3: Finalizar
finalize_session(agent_id, outcome="completed")
```

## Integración con Hooks

### pre_delegate.py

Se ejecuta **ANTES** de delegar tarea a agente:

```bash
echo '{"agent_name": "terraform-architect", "agent_id": "agent-123"}' | \
python3 hooks/pre_delegate.py
```

Output:
```json
{
  "should_resume": true,
  "reason": "session_resumable",
  "agent_id": "agent-123",
  "resume_metadata": {
    "previous_phase": "approval",
    "created_at": "2026-01-08T18:00:00Z",
    "history": [...]
  }
}
```

### subagent_stop.py

Se ejecuta **DESPUÉS** de que agente termina. Automáticamente llama `finalize_session()` si `task_info` contiene `agent_id`.

## Cleanup Automático

Las sesiones viejas se eliminan automáticamente:

```bash
# Manual
python3 tools/4-memory/agent_session.py cleanup --hours 24

# Programado (agregar a cron)
0 * * * * cd /path/to/gaia-ops && python3 tools/4-memory/agent_session.py cleanup --hours 24
```

## Testing

```bash
# Test completo del flujo
python3 hooks/pre_delegate.py --test

# Test CLI agent_session
cd tools/4-memory
python3 agent_session.py create --agent test-agent --purpose test
python3 agent_session.py list
```

## Troubleshooting

### Sesión no resume

**Síntomas:** `should_resume()` retorna `False`

**Causas comunes:**
- Fase no resumible (check `phase`)
- Timeout excedido (> 30 minutos)
- Demasiados errores (`error_count >= 3`)
- `resume_ready == False`

**Debug:**
```bash
python3 tools/4-memory/agent_session.py get <agent_id>
```

### Sesión corrupta

**Síntomas:** Error al cargar state.json

**Solución:**
```bash
# Ver estado raw
cat .claude/session/<agent_id>/state.json

# Eliminar sesión corrupta
rm -rf .claude/session/<agent_id>
```

### Hooks no se ejecutan

**Síntomas:** Sessions no se finalizan

**Verificar:**
1. Hooks son ejecutables: `chmod +x hooks/*.py`
2. Python path correcto en imports
3. Logs en `.claude/logs/pre_delegate.log`

## Métricas

```bash
# Ver sesiones activas
python3 tools/4-memory/agent_session.py list --active-only

# Ver todas las sesiones
python3 tools/4-memory/agent_session.py list

# Filtrar por agente
python3 tools/4-memory/agent_session.py list --agent terraform-architect
```

## Desacoplamiento

**Agent Sessions NO está acoplado a Episodes:**

- Episodes = memoria episódica de alto nivel (prompt → outcome)
- Agent Sessions = telemetría de ejecución técnica (agent → state)

**Para correlacionar:** Query por timestamp si es necesario.

```python
# Ejemplo: Encontrar sessions para un episode
episode = episodic.get_episode("ep_123")
timestamp_start = episode["timestamp"]
timestamp_end = datetime.fromisoformat(timestamp_start) + timedelta(seconds=episode["duration_seconds"])

# Query sessions en ese rango (implementar si necesario)
```

## Performance

- **Storage:** ~1-5KB por sesión
- **Resume check:** <10ms
- **Cleanup:** <100ms para 1000 sesiones
- **No impacto** en ejecución de agente

## Seguridad

- ✓ State.json en `.claude/session/` (no versionado)
- ✓ No contiene secrets (solo metadata)
- ✓ Cleanup automático previene acumulación
- ✓ Error handling robusto (no rompe flujo si falla)

## Próximos Pasos (P1)

**NO implementar hasta que P0 esté validado en producción 2+ semanas:**

- Multi-turn resume (múltiples interacciones en una sesión)
- Context pruning (limitar a últimos 5 turns)
- Session analytics (resume rate, success rate)
- Episode-session query helpers

## Referencias

- **Especificación:** `/IMPLEMENTATION_SUMMARY.md` (sección P0)
- **Tests:** `/tests/test_agent_session.py` (TODO)
- **Hooks README:** `/hooks/README.md`
- **Episodic Memory:** `/tools/4-memory/episodic.py`

---

**Versión:** P0 (MVP)
**Última actualización:** 2026-01-08
**Mantenido por:** Gaia (meta-agent)
