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

#### `pre_tool_use.py` (~319 lineas)
El guardian principal - valida TODAS las operaciones antes de ejecutarlas.

**Arquitectura modular (v2):**
- Usa sistema de modulos en `modules/` (core, security, tools)
- BashValidator para comandos bash
- TaskValidator para delegacion de agentes
- Estado compartido con post-hook via `.hooks_state.json`

**Optimizaciones de performance:**

1. **LRU Cache para clasificacion de tiers:**
   - `@lru_cache(512)` en `classify_command_tier()`
   - 60-70% mas rapido para comandos repetidos
   - Reduccion de latencia total: 30-40%

2. **Fast-path para comandos ultra-comunes:**
   - `ls`, `pwd`, `git status`, `kubectl get`, `terraform plan`
   - Clasificacion directa sin analisis completo
   - 88% mas rapido para estos comandos

3. **Lazy parsing de shell:**
   - Solo parsea si detecta operadores (`|`, `&&`, `||`, `;`)
   - Comandos simples evitan parser completamente
   - Valida directamente con `is_read_only_command()`

**Flujo de validacion (20 pasos con 5 fast-paths):**

```
1. Recibe tool call via stdin (JSON)
2. Extrae tool_name y parameters
3. Ruta segun tool:

   [BASH TOOL] (pasos 4-12)
   4. Extrae comando
   5. ¿Tiene operadores? -> Fast-path: NO
   6.   ├─> Valida comando simple
   7.   ├─> ¿Es read-only? -> Fast-path: SI
   8.   │    └─> AUTO-APRUEBA (exit 0)
   9.   └─> Clasifica tier (con cache LRU)
   10. SI tiene operadores:
   11.  ├─> Parsea con ShellCommandParser
   12.  └─> Valida cada componente

   [TASK TOOL] (pasos 13-33)
   13. Extrae subagent_type y prompt
   14. ¿Tiene resume parameter?

   [RESUME MODE] (pasos 15-22)
   15.  ├─> Valida formato agentId: ^a[0-9a-f]{6,7}$
   16.  ├─> Valida prompt existe
   17.  ├─> Skip validaciones pesadas (ya validado en fase 1)
   18.  ├─> Log: "RESUME: Continuing agent {id}"
   19.  ├─> Save state para post-hook
   20.  └─> Return None -> PERMITE ejecucion

   [NEW TASK MODE] (pasos 23-33)
   23.  ├─> Valida agent existe en AVAILABLE_AGENTS
   24.  ├─> Extrae metadata si existe (soft warning)
   25.  ├─> Clasifica tier automaticamente
   26.  ├─> ¿Es T3?
   27.  │    ├─> Busca approval en metadata/prompt
   28.  │    └─> Sin approval -> BLOQUEA
   29.  ├─> Ejecuta workflow guards (phase validators)
   30.  ├─> Guard phase_4_approval_mandatory
   31.  ├─> Save state para post-hook
   32.  └─> Log decision y metricas
   33.  └─> Return None -> PERMITE ejecucion
```

**Validacion de Resume:**

El hook detecta operaciones de resume para T3 two-phase workflow:

```python
# Fase 1: Planning
Task(subagent_type="terraform-architect", prompt="plan VPC...")
# -> Agente retorna plan + agentId: a12345

# Fase 2: Approval + Execution
Task(resume="a12345", prompt="User approved. Execute plan.")
# -> Hook valida:
#    ✓ Formato agentId (a + 6-7 hex chars)
#    ✓ Prompt existe
#    ✓ Skip heavy validations (agent ya validado)
#    ✓ Permite ejecucion con contexto completo
```

**Que valida:**

**Bash commands:**
- Tier de seguridad (T0/T1/T2/T3)
- Comandos bloqueados globalmente
- Componentes de comandos compuestos
- Auto-aprobacion de read-only

**Task delegations:**
- Agent existe en AVAILABLE_AGENTS
- Tier de operacion (clasificacion automatica)
- T3 requiere approval (busca en metadata/prompt)
- Workflow guards (phase validators)
- Resume: formato agentId + prompt presente

**Reglas de decision:**
```python
# BASH VALIDATION
if not has_operators(command):
    if is_read_only_command(command):
        return None  # Auto-aprueba (fast-path)

tier = classify_command_tier(command)  # Con LRU cache
if tier == T3 and not has_approval:
    return "BLOCKED: T3 requiere approval"

# TASK VALIDATION
if resume_id:
    if not re.match(r'^a[0-9a-f]{6,7}$', resume_id):
        return "BLOCKED: Invalid agentId format"
    if not prompt:
        return "BLOCKED: Resume requires prompt"
    return None  # Permite resume (skip heavy validations)

if agent_name not in AVAILABLE_AGENTS:
    return "BLOCKED: Agent no existe"

if tier == T3 and not has_approval:
    return "BLOCKED: T3 requiere approval. Usa AskUserQuestion."

return None  # Permite ejecucion
```

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

## Auto-Aprobacion de Comandos Read-Only

### Como Funciona

Cuando Claude Code ejecuta un comando bash:

1. **Hook recibe el comando** via stdin como JSON
2. **Verifica si es read-only** usando `is_read_only_command()`
3. **Si es read-only**, retorna JSON con `permissionDecision: "allow"`
4. **Claude Code bypassa el ASK prompt** y ejecuta inmediatamente

### Comandos Auto-Aprobados

**Simples (siempre seguros):**
- System info: `uname`, `hostname`, `whoami`, `date`, `uptime`, `free`
- File listing: `ls`, `pwd`, `tree`, `which`, `stat`, `file`
- Text processing: `awk`, `cut`, `grep`, `head`, `tail`, `sort`, `wc`
- Network: `ping`, `dig`, `nslookup`, `netstat`, `ss`
- Git read-only: `git status`, `git diff`, `git log`, `git branch`

**Compuestos (si TODOS los componentes son seguros):**
- `cat file | grep foo` -> auto-aprueba
- `ls && pwd` -> auto-aprueba
- `tail file || echo error` -> auto-aprueba

**Condicionales (seguros excepto con flags peligrosos):**
- `sed` -> seguro excepto con `-i` (in-place edit)
- `curl` -> seguro excepto con `-T`, `-X POST`, `--data`
- `find` -> seguro excepto con `-delete`, `-exec rm`

### Comandos NO Auto-Aprobados

**Siempre bloqueados:**
- `rm`, `rm -rf`, `shred`
- `dd`, `fdisk`, `parted`
- `sudo`, `su`
- `kill -9`, `killall -9`
- `terraform apply`, `kubectl apply`, `git push`

**Compuestos con componentes peligrosos:**
- `ls && rm -rf /` -> BLOQUEADO (rm es peligroso)
- `cat | kubectl apply` -> BLOQUEADO (kubectl apply es peligroso)

---

## Configuracion de Permisos

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

---

## Logs de Auditoria

Todos los hooks escriben a `.claude/logs/`:

```bash
# Ver logs de hoy
cat .claude/logs/$(date +%Y-%m-%d)-audit.jsonl | jq .

# Buscar operaciones T3
cat .claude/logs/*.jsonl | jq 'select(.tier == "T3")'

# Buscar operaciones bloqueadas
cat .claude/logs/*.jsonl | jq 'select(.action == "blocked")'

# Ver auto-aprobaciones
grep "AUTO-APPROVED" .claude/logs/pre_tool_use-*.log
```

---

## Tiers de Seguridad

| Tier | Tipo de Operacion | Requiere Approval | Auto-Aprueba |
|------|-------------------|-------------------|--------------|
| **T0** | Read-only (get, list) | No | Si |
| **T1** | Validation (validate, dry-run) | No | No |
| **T2** | Planning (plan, simulate) | No | No |
| **T3** | Execution (apply, delete) | **Si** | No |

---

## Tests de Hooks

Los hooks tienen tests de integracion:

```bash
# Ver tests
python3 -m pytest tests/integration/ -v

# Tests especificos de hooks
python3 -m pytest tests/integration/test_hooks_integration.py -v

# Test manual de comandos
python3 .claude/hooks/pre_tool_use.py "ls -la"
python3 .claude/hooks/pre_tool_use.py --test
```

---

## Referencias

**Archivos de hooks:**
```
hooks/
├── pre_tool_use.py        (~319 lineas) - Guardian principal v2 (modular)
├── post_tool_use.py       (~300 lineas) - Auditor principal
└── subagent_stop.py       (~200 lineas) - Workflow metrics
```

**Nota:** El parser de shell (`shell_parser.py`) ahora está en `modules/tools/shell_parser.py`

**Configuracion relacionada:**
- `.claude/settings.json` - Permisos y tiers

**Tests relacionados:**
- `tests/integration/test_hooks_integration.py`
- `tests/integration/test_hooks_workflow.py`
- `tests/workflow/test_workflow_metrics.py`

---

**Version:** 4.1.0
**Ultima actualizacion:** 2026-01-16
**Total de hooks:** 3 hooks activos (1 pre, 1 post, 1 metrics)
**Mantenido por:** Gaia (meta-agent)
