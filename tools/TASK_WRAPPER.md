# Task Wrapper - Audit Logging for Task Tool Invocations

## 📋 Overview

**Task Wrapper** es un componente de auditoría que intercepta todas las invocaciones del **Task tool** en Claude Code y genera logs automáticamente.

**Propósito:** Capturar cuándo se invocan agentes (terraform-architect, gitops-operator, etc.) y registrar su ejecución en la cadena de auditoría del sistema.

---

## 🎯 Objetivo

Complementar `post_tool_use.py` (que registra ejecuciones de Bash) con **registro de invocaciones de Task tool** para agentes especializados.

| Herramienta | Auditoría | Responsable |
|-------------|-----------|-------------|
| Bash commands | ✅ Registrada | post_tool_use.py |
| Task tool (agentes) | ✅ Registrada | **task_wrapper.py** (NUEVO) |
| Git operations | ✅ Registrada | pre_tool_use.py (security tiers) |

---

## 🔧 Instalación

### 1. Archivo Principal

El archivo `task_wrapper.py` ya está instalado en:
```
.claude/tools/task_wrapper.py
```

### 2. Permisos

```bash
chmod +x .claude/tools/task_wrapper.py
```

### 3. Verificar Instalación

```bash
python3 .claude/tools/task_wrapper.py --help
```

---

## 📊 Estructura de Logs

Task Wrapper genera logs en **5 archivos** simultáneamente:

### 1. **session-{SESSION_ID}.jsonl**
- Todos los eventos de la sesión actual
- Incluye invocaciones Y completaciones de tareas
- Búsqueda rápida de "¿qué pasó hoy?"

### 2. **audit-{YYYY-MM-DD}.jsonl**
- Audit trail diario
- Consolidada con todos los eventos (Bash + Task + Git)
- Para compliance y trazabilidad

### 3. **agent-{AGENT_TYPE}-{YYYY-MM-DD}.jsonl**
- Logs por agente específico
- Ejemplo: `agent-terraform-architect-2025-11-11.jsonl`
- Para análisis por especialidad

### 4. **Pre-Commit Hooks** (Futuro)
- Auditoría de commits git
- Configurado pero no activo todavía

### 5. **Metrics** (Futuro)
- Rendimiento y estadísticas
- `metrics-{YYYY-MM}.jsonl`

---

## 📝 Formato de Registro

Cada línea es un **evento JSON JSONL** con estructura consistente:

### Invocación (Task comienza)

```json
{
  "timestamp": "2025-11-11T21:07:34.863353",
  "session_id": "default",
  "tool_name": "Task",
  "agent_type": "terraform-architect",
  "description": "Validar configuración de Terraform en RND",
  "tier": "T1",
  "command": "Task(terraform-architect): Validar configuración de Terraform en RND",
  "prompt_hash": "357b23c099c8c4d8",
  "prompt_preview": "Ejecutar terraform validate en terraform/tf_live/rnd/...",
  "duration_ms": 1500.0,
  "result_hash": "",
  "result_preview": "",
  "status": "RUNNING"
}
```

### Completación (Task termina)

```json
{
  "timestamp": "2025-11-11T21:07:38.054259",
  "session_id": "default",
  "tool_name": "Task",
  "agent_type": "terraform-architect",
  "description": "Validar configuración de Terraform",
  "tier": "T1",
  "command": "Task(terraform-architect): Validar configuración de Terraform",
  "duration_ms": 1500.0,
  "exit_code": 0,
  "result_message": "Success! The configuration is valid.",
  "status": "SUCCESS"
}
```

---

## 🔐 Security Tiers

Task Wrapper clasifica automáticamente cada agente por **Security Tier**:

| Tier | Agentes | Descripción |
|------|---------|-------------|
| **T0** | gcp-troubleshooter, aws-troubleshooter, Explore | Read-only, no cambios |
| **T1** | terraform-architect (validate), Plan | Validación solamente |
| **T2** | terraform-architect (plan) | Cambios potenciales, ask for approval |
| **T3** | gitops-operator, terraform-architect (apply) | Write operations, REQUIRE approval |

**Lógica de clasificación:**
```python
def _classify_agent_tier(self, agent_type: str) -> str:
    if agent_type in ["gcp-troubleshooter", "aws-troubleshooter"]:
        return "T0"
    elif agent_type in ["Plan", "terraform-architect"]:
        return "T1"
    elif agent_type in ["gitops-operator"]:
        return "T3"
    else:
        return "T2"
```

---

## 🚀 Uso

### Manual (para testing)

```bash
# Invocación de tarea
python3 .claude/tools/task_wrapper.py \
  --agent terraform-architect \
  --description "Validar configuración" \
  --prompt "Ejecutar terraform validate" \
  --duration 1.5 \
  --status RUNNING

# Completación de tarea
python3 .claude/tools/task_wrapper.py \
  --agent terraform-architect \
  --description "Validar configuración" \
  --duration 1.5 \
  --exit-code 0 \
  --result-message "Success!" \
  --completion
```

### Integración con Claude Code (Futuro)

Cuando se implemente la integración completa, Task Wrapper se invocará automáticamente:

```python
# En Claude Code internamente:
task_wrapper.log_task_invocation(
    agent_type="gitops-operator",
    description="Apply changes to tcm-non-prod",
    prompt=user_prompt,
    duration=duration,
    status="RUNNING"
)
```

---

## 📊 Análisis de Logs

### Ver todos los eventos de hoy

```bash
cat .claude/logs/audit-$(date +%Y-%m-%d).jsonl | jq '.'
```

### Filtrar por agente

```bash
grep '"agent_type":"terraform-architect"' .claude/logs/session-*.jsonl
```

### Filtrar por tier

```bash
grep '"tier":"T3"' .claude/logs/audit-*.jsonl
```

### Contar eventos por estado

```bash
grep -o '"status":"[^"]*"' .claude/logs/session-*.jsonl | sort | uniq -c
```

### Análisis con Python

```python
import json
from pathlib import Path

log_file = Path(".claude/logs/session-default.jsonl")
events = [json.loads(line) for line in log_file.read_text().split('\n') if line]

# Estadísticas
print(f"Total events: {len(events)}")
print(f"Failed: {sum(1 for e in events if e['status'] == 'FAILED')}")
print(f"Agents used: {set(e['agent_type'] for e in events)}")
```

---

## 📈 Proof of Concept Results

Ejecución del 2025-11-11 21:07:

| Métrica | Valor |
|---------|-------|
| Total eventos | 5 |
| RUNNING (invocaciones) | 3 |
| SUCCESS (completaciones) | 1 |
| FAILED (errores) | 1 |
| Agentes únicos | 3 |
| Security tiers cubiertos | T0, T1, T3 |

### Archivos Generados

```
agent-gcp-troubleshooter-2025-11-11.jsonl    1 registro    0.44 KB
agent-gitops-operator-2025-11-11.jsonl       2 registros   0.81 KB
agent-terraform-architect-2025-11-11.jsonl   2 registros   0.89 KB
audit-2025-11-11.jsonl                       5 registros   2.15 KB
session-default.jsonl                        5 registros   2.15 KB
```

---

## 🔄 Próximos Pasos

### Fase 2: Integración con Claude Code Hooks

**Objetivo:** Automatizar invocaciones sin manual calls

**Archivos a modificar:**
- `.claude/settings.json` - Agregar TaskToolUse hook
- `.claude/hooks/task_invocation_logger.py` - Nuevo hook

**Estimado:** 2-3 horas

### Fase 3: Shell Wrapper (Opción A)

**Objetivo:** Capturar Bash directo también

**Ubicación:** `.claude/tools/shell_wrapper.sh`

**Estimado:** 2-3 horas

### Fase 4: Pre-Commit Hooks

**Objetivo:** Auditar operaciones Git

**Ubicación:** `.git/hooks/pre-commit`

**Estimado:** 1-2 horas

---

## 🐛 Troubleshooting

### Log no se crea

**Verificar:**
1. Directorio `.claude/logs/` existe
2. Permisos de escritura: `ls -ld .claude/logs/`
3. `task_wrapper.py` es executable: `chmod +x .claude/tools/task_wrapper.py`

### Logs vacíos

**Verificar:**
1. Variable de entorno: `echo $CLAUDE_SESSION_ID`
2. Si es vacía, usa `--session-id` en comando

### Formato incorrecto

**Verificar:**
1. JSON válido: `cat logs/*.jsonl | python3 -m json.tool`
2. Todos los campos requeridos presentes

---

## 📚 Referencias

- **Post-tool use:** `.claude/hooks/post_tool_use.py` (hermano)
- **Agent catalog:** `.claude/config/agent-catalog.md`
- **Security tiers:** `.claude/config/orchestration-workflow.md`
- **CLAUDE.md:** Orchestrator logic

---

## ✅ Checklist de Validación

- [x] task_wrapper.py creado y funcional
- [x] Genera logs en formato JSONL
- [x] Clasifica security tiers correctamente
- [x] Crea múltiples archivos de log (session, audit, agent)
- [x] Proof of concept exitoso (5 eventos registrados)
- [ ] Integrado con Claude Code hooks
- [ ] Shell wrapper implementado
- [ ] Pre-commit hooks configurados
- [ ] Documentación en CLAUDE.md
- [ ] Tests automatizados en .claude/tests/

---

**Fecha:** 2025-11-11
**Estado:** ✅ Proof of Concept COMPLETADO
**Próximo:** Fase 2 - Integración con Claude Code Hooks
