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

#### `pre_tool_use.py` (~750 lineas)
El guardian principal - valida TODAS las operaciones antes de ejecutarlas.

**Caracteristicas principales (v3.3.2):**

1. **Auto-aprobacion de comandos read-only:**
   - Comandos simples: `ls`, `cat`, `grep`, `git status` -> NO ASK
   - Comandos compuestos seguros: `cat file | grep foo`, `ls && pwd` -> NO ASK
   - Retorna `permissionDecision: "allow"` para bypass del ASK prompt

2. **Configuracion unificada (`SAFE_COMMANDS_CONFIG`):**
   ```python
   SAFE_COMMANDS_CONFIG = {
       "always_safe": {"ls", "cat", "grep", ...},
       "always_safe_multiword": {"git status", "kubectl get", ...},
       "conditional_safe": {"sed": [r"-i\b"], "curl": [r"-T\b"], ...}
   }
   ```

3. **ShellCommandParser singleton:**
   - Parsea comandos compuestos (pipes, &&, ||, ;)
   - Valida cada componente individualmente
   - Si TODOS son seguros -> auto-aprueba

**Que valida:**
- Tier de seguridad (T0, T1, T2, T3)
- Permisos segun settings.json
- Comandos bloqueados globalmente
- Contexto de ejecucion
- Componentes de comandos compuestos

**Reglas de decision:**
```python
# 1. Auto-aprobacion para read-only
if is_read_only_command(command):
    return AUTO_APPROVE  # Retorna JSON con permissionDecision: "allow"

# 2. Validacion de seguridad
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

#### `shell_parser.py` (~290 lineas)
Parser nativo de Python para comandos shell compuestos.

**Que hace:**
- Parsea pipes: `cmd1 | cmd2` -> `["cmd1", "cmd2"]`
- Parsea AND: `cmd1 && cmd2` -> `["cmd1", "cmd2"]`
- Parsea OR: `cmd1 || cmd2` -> `["cmd1", "cmd2"]`
- Preserva strings con comillas: `echo 'a|b'` -> `["echo 'a|b'"]`

**Uso:**
```python
from shell_parser import ShellCommandParser

parser = ShellCommandParser()
components = parser.parse("ls | grep foo && wc -l")
# ["ls", "grep foo", "wc -l"]
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
├── pre_tool_use.py        (~750 lineas) - Guardian principal + auto-approval
├── post_tool_use.py       (~300 lineas) - Auditor principal
├── pre_phase_hook.py      (~200 lineas) - Validador de fases
├── post_phase_hook.py     (~150 lineas) - Auditor de fases
├── pre_kubectl_security.py (~180 lineas) - K8s security
├── shell_parser.py        (~290 lineas) - Parser de comandos compuestos
└── subagent_stop.py       (~200 lineas) - Workflow metrics
```

**Configuracion relacionada:**
- `.claude/settings.json` - Permisos y tiers

**Tests relacionados:**
- `tests/integration/test_hooks_integration.py`
- `tests/integration/test_hooks_workflow.py`
- `tests/workflow/test_workflow_metrics.py`

---

**Version:** 3.3.2  
**Ultima actualizacion:** 2025-12-11  
**Total de hooks:** 7 hooks (5 pre, 1 post, 1 metrics)  
**Mantenido por:** Gaia (meta-agent)
