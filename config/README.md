# Archivos de Configuración de Gaia-Ops

**[🇺🇸 English version](README.en.md)**

Este directorio contiene la configuración central y documentación de referencia del sistema de orquestación. Es como la biblioteca de conocimiento que consultan los agentes para entender cómo trabajar.

## 🎯 Propósito

Los archivos de configuración definen el comportamiento del sistema, estándares del proyecto y contratos entre componentes. Proporcionan la "fuente de verdad" para cómo debe operar el sistema.

**Problema que resuelve:** Los sistemas complejos necesitan configuración centralizada y documentación de referencia. En lugar de tener información dispersa, todo está organizado en un solo lugar accesible.

## 🔄 Cómo Funciona

### Flujo de Arquitectura

```
[Agentes] necesitan información
        ↓
   Consultan config/
        ↓
    ┌──────┴──────┐
    ↓              ↓
[Standards]   [Contracts]
    ↓              ↓
Aplican reglas   Usan contexto
    ↓              ↓
Operación consistente
```

### Flujo de Ejemplo Real

```
Ejemplo: Agent necesita validar un commit message

1. [devops-developer] recibe commit message
   ↓
2. Consulta → config/git-standards.md
   ↓
3. Lee reglas de Conventional Commits:
   - Formato: <type>(<scope>): <description>
   - Types permitidos: feat, fix, docs, etc.
   - Footer prohibido: "Verified by Claude Code"
   ↓
4. Valida contra git_standards.json
   ↓
5. Resultado:
   ✅ "feat(auth): add OAuth2 support" → VÁLIDO
   ❌ "updated stuff" → INVÁLIDO (no sigue formato)
```

## 📋 Archivos Principales

### Documentación de Sistema

#### `AGENTS.md`
Tabla de contenidos del sistema de agentes - punto de entrada para entender la arquitectura.

**Qué contiene:**
- Overview del sistema de agentes
- Links a documentación detallada
- Guía de inicio rápido
- Notas de compatibilidad con otros AI assistants

**Cuándo consultarlo:**
- Primera vez usando el sistema
- Para entender arquitectura general
- Al hacer onboarding de nuevos desarrolladores

---

#### `orchestration-workflow.md` (~735 líneas)
La documentación más completa del flujo de orquestación Phase 0-6.

**Qué contiene:**
- Workflow completo en 6 fases
- Decisiones de routing
- Provisión de contexto
- Approval gates
- Actualización de SSOTs
- Ejemplos detallados

**Cuándo consultarlo:**
- Para entender cómo fluye una solicitud
- Para modificar el workflow
- Para diagnosticar problemas de routing

---

#### `agent-catalog.md` (~603 líneas)
Catálogo completo de todos los agentes con capacidades detalladas.

**Qué contiene:**
- Descripción de cada agente
- Triggers semánticos
- Capacidades por tier (T0-T3)
- Ejemplos de uso
- Herramientas permitidas

**Cuándo consultarlo:**
- Para decidir qué agente usar
- Para entender capacidades específicas
- Al agregar nuevos agentes

---

### Estándares y Convenciones

#### `git-standards.md` (~682 líneas)
Estándares completos para commits, branching y Git workflows.

**Qué contiene:**
- Conventional Commits (detallado)
- Reglas de branch naming
- Workflow de Git Flow
- Footers prohibidos
- Ejemplos de commits buenos/malos

**Cuándo consultarlo:**
- Antes de hacer commits
- Al revisar PRs
- Al configurar hooks de validación

---

#### `git_standards.json`
Versión programática de los estándares Git para validación automatizada.

**Qué contiene:**
```json
{
  "commit_types": ["feat", "fix", "docs", ...],
  "forbidden_footers": ["Verified by Claude Code"],
  "subject_max_length": 72,
  "branch_patterns": {
    "feature": "feature/*",
    "bugfix": "bugfix/*"
  }
}
```

**Cuándo consultarlo:**
- Código de validación (commit_validator.py)
- Tests de validación
- Configuración de CI/CD

---

### Contratos de Contexto

#### `context-contracts.md` (~673 líneas)
Define qué información necesita cada agente para operar efectivamente.

**Qué contiene:**
- Contrato de contexto por agente
- Secciones de project-context.json que necesita
- Campos opcionales vs obligatorios
- Ejemplos de contexto completo

**Cuándo consultarlo:**
- Al modificar project-context.json
- Al agregar nuevos agentes
- Para debugging de contexto faltante

---

#### `context-contracts.gcp.json`
Contrato específico para agentes de GCP (gcp-troubleshooter, terraform-architect GCP).

**Qué contiene:**
```json
{
  "required": {
    "gcp_project_id": "string",
    "gcp_region": "string"
  },
  "optional": {
    "gcp_zone": "string",
    "gke_clusters": "array"
  }
}
```

---

#### `context-contracts.aws.json`
Contrato específico para agentes de AWS (aws-troubleshooter, terraform-architect AWS).

**Qué contiene:**
```json
{
  "required": {
    "aws_account_id": "string",
    "aws_region": "string"
  },
  "optional": {
    "aws_profile": "string",
    "eks_clusters": "array"
  }
}
```

---

### Reglas y Políticas

#### `clarification_rules.json`
Configuración del motor de clarificación (Phase 0).

**Qué contiene:**
```json
{
  "global_settings": {
    "ambiguity_threshold": 30,
    "max_questions": 5
  },
  "patterns": [
    {
      "pattern": "ambiguous_service",
      "keywords": ["service", "api", "backend"],
      "question": "Which service? (auth, billing, notifications)"
    }
  ]
}
```

**Cuándo consultarlo:**
- Al ajustar sensibilidad de clarification
- Para agregar nuevos patterns de ambigüedad
- Para debugging de Phase 0

---

#### `delegation-matrix.md`
Matriz de decisión para cuándo delegar vs ejecutar directamente.

**Qué contiene:**
- Decisiones binarias (IF/THEN)
- Triggers de delegación
- Ejemplos de casos límite

**Cuándo consultarlo:**
- Al optimizar routing
- Para entender por qué se delegó (o no)

---

### Configuración de Machine Learning

#### `embeddings_info.json`
Metadata sobre los embeddings usados para semantic matching.

**Qué contiene:**
```json
{
  "model": "all-MiniLM-L6-v2",
  "dimensions": 384,
  "last_generated": "2025-11-12",
  "intents_count": 45
}
```

---

#### `intent_embeddings.json`
Intents con sus embeddings vectoriales para routing semántico.

**Formato:**
```json
[
  {
    "intent": "deploy kubernetes service",
    "embedding": [0.123, -0.456, ...],
    "agent": "gitops-operator"
  }
]
```

---

#### `intent_embeddings.npy`
Versión NumPy de los embeddings para carga rápida en Python.

---

### Métricas y Targets

#### `metrics_targets.json`
Objetivos de performance del sistema.

**Qué contiene:**
```json
{
  "routing_accuracy": 0.927,
  "clarification_rate": [0.20, 0.30],
  "context_efficiency": 0.85,
  "test_pass_rate": 1.0
}
```

**Cuándo consultarlo:**
- Para evaluar health del sistema
- Al hacer análisis de performance
- Para benchmarking

---

### Documentación de Principios

#### `documentation-principles.md` (NUEVO)
Principios y estándares para escribir documentación en gaia-ops.

**Qué contiene:**
- Lenguaje simple y directo
- Uso de diagramas ASCII
- Estructura consistente de READMEs
- Guía práctica para Gaia

**Cuándo consultarlo:**
- Al crear/actualizar READMEs
- Para mantener consistencia
- Al hacer mejoras de documentación

---

## 🚀 Uso de Archivos de Configuración

### Para Agentes

Los agentes consultan config/ automáticamente cuando necesitan:

```python
# Ejemplo: Agent carga git standards
import json
with open('.claude/config/git_standards.json') as f:
    standards = json.load(f)

# Valida commit message
if commit_type not in standards['commit_types']:
    raise ValidationError(f"Invalid type: {commit_type}")
```

### Para Desarrolladores

Consulta los archivos Markdown para entender el sistema:

```bash
# Ver workflow completo
cat .claude/config/orchestration-workflow.md

# Ver estándares Git
cat .claude/config/git-standards.md

# Ver catálogo de agentes
cat .claude/config/agent-catalog.md
```

### Para Gaia (Meta-Agent)

Gaia lee config/ para análisis y optimización:

```python
# Gaia analiza métricas
import json
with open('.claude/config/metrics_targets.json') as f:
    targets = json.load(f)

routing_target = targets['routing_accuracy']
# Compara con métricas actuales...
```

## 🔧 Características Técnicas

### Estructura del Directorio

```
config/
├── AGENTS.md                              # System overview
├── orchestration-workflow.md              # Phase 0-6 workflow
├── agent-catalog.md                       # Agent capabilities
├── git-standards.md                       # Git conventions
├── git_standards.json                     # Git rules (programmatic)
├── context-contracts.md                   # Agent context needs
├── context-contracts.gcp.json             # GCP context schema
├── context-contracts.aws.json             # AWS context schema
├── clarification_rules.json               # Clarification config
├── delegation-matrix.md                   # Delegation decisions
├── embeddings_info.json                   # ML metadata
├── intent_embeddings.json                 # Intent vectors
├── intent_embeddings.npy                  # NumPy embeddings
├── metrics_targets.json                   # Performance targets
├── documentation-principles.md            # Doc standards (NEW)
└── documentation-principles.en.md         # Doc standards EN (NEW)
```

**Total:** 17 archivos de configuración

### Tipos de Archivos

| Tipo | Propósito | Consumidores |
|------|-----------|--------------|
| **.md** | Documentación legible | Humanos, Gaia |
| **.json** | Configuración programática | Herramientas Python, Tests |
| **.npy** | Datos ML optimizados | agent_router.py |

### Actualización

**Frecuencia:**
- `*.md` - Cuando cambia funcionalidad
- `*_standards.json` - Al actualizar reglas
- `*_embeddings.*` - Cuando se agregan nuevos intents
- `metrics_targets.json` - Después de benchmarks

**Responsable:**
- Documentación: Gaia
- Config programática: Desarrolladores + Tests
- Embeddings: Scripts de generación (offline)

## 📖 Referencias

**Herramientas que usan config/:**
- `tools/1-routing/agent_router.py` - Lee embeddings
- `tools/2-context/context_provider.py` - Lee contracts
- `tools/3-clarification/engine.py` - Lee clarification_rules
- `tools/4-validation/commit_validator.py` - Lee git_standards
- `agents/gaia.md` - Lee todos los archivos

**Documentación relacionada:**
- [Agents](../agents/README.md) - Sistema de agentes
- [Tools](../tools/README.md) - Herramientas de orquestación
- [Tests](../tests/README.md) - Suite de tests

---

**Versión:** 1.0.0  
**Última actualización:** 2025-11-14  
**Total de archivos:** 17 archivos de configuración  
**Mantenido por:** Gaia (meta-agent) + equipo DevOps

