# @jaguilar87/gaia-ops

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia-ops.svg)](https://www.npmjs.com/package/@jaguilar87/gaia-ops)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia-ops.svg)](https://nodejs.org)

**[🇺🇸 English version](README.en.md)**

Sistema de orquestación multi-agente para Claude Code - Toolkit de automatización DevOps.

## Descripción General

**Gaia-Ops** proporciona un sistema completo de orquestación de agentes para Claude Code, habilitando automatización inteligente de workflows DevOps a través de agentes IA especializados.

### Características

- **Soporte multi-cloud** - Funciona con GCP, AWS, y listo para Azure
- **6 agentes especialistas** (terraform-architect, gitops-operator, gcp-troubleshooter, aws-troubleshooter, devops-developer, Gaia)
- **3 meta-agentes** (Explore, Plan, Gaia)
- **Sistema de clarificación genérico** (Phase 0) - Detección automática de ambigüedades desde project-context.json con preguntas dinámicas y multi-tab
- **Helper AskUserQuestion** - Función `ask()` para forzar UX consistente (SIEMPRE preguntas, nunca texto plano)
- **Puertas de aprobación** para operaciones T3 (terraform apply, kubectl apply, etc.)
- **Validación de commits Git** con Conventional Commits
- **Sistema de provisión de contexto** para ruteo inteligente de agentes
- **Documentación completa** (workflow de orquestación, estándares git, catálogo de agentes)

## Instalación

### Inicio Rápido (Recomendado)

Usa el instalador interactivo integrado para configurar Gaia-Ops en cualquier proyecto:

```bash
# Opción 1: Ejecutar directamente con npx
npx gaia-init

# Opción 2: Si prefieres especificar el paquete completo
npx @jaguilar87/gaia-ops
```

O si lo instalas globalmente:

```bash
npm install -g @jaguilar87/gaia-ops
gaia-init
```

Esto hará:
1. Auto-detectar tu estructura de proyecto (GitOps, Terraform, AppServices)
2. Hacerte algunas preguntas sobre tu proyecto
3. Instalar Claude Code si no está presente
4. Crear directorio `.claude/` con symlinks a este paquete
5. Generar `CLAUDE.md` con las rutas correctas
6. Generar symlink `AGENTS.md`
7. Crear `project-context.json` con tu configuración

### Instalación Manual

Si prefieres configuración manual:

```bash
npm install @jaguilar87/gaia-ops
```

Luego crea los symlinks:

```bash
mkdir -p .claude
cd .claude
ln -s ../node_modules/@jaguilar87/gaia-ops/agents agents
ln -s ../node_modules/@jaguilar87/gaia-ops/tools tools
ln -s ../node_modules/@jaguilar87/gaia-ops/hooks hooks
ln -s ../node_modules/@jaguilar87/gaia-ops/commands commands
ln -s ../node_modules/@jaguilar87/gaia-ops/templates templates
ln -s ../node_modules/@jaguilar87/gaia-ops/config config
ln -s ../node_modules/@jaguilar87/gaia-ops/CHANGELOG.md CHANGELOG.md
```

## Uso

Una vez instalado, el sistema de agentes está listo para usar con Claude Code:

```bash
claude-code
```

Claude Code cargará automáticamente `CLAUDE.md` y tendrá acceso a todos los agentes vía el directorio `.claude/`.

## Estructura del Proyecto

```
node_modules/@jaguilar87/gaia-ops/
├── agents/              # Definiciones de agentes
│   ├── terraform-architect.md
│   ├── gitops-operator.md
│   ├── gcp-troubleshooter.md
│   ├── aws-troubleshooter.md
│   ├── devops-developer.md
│   └── gaia.md
├── tools/               # Herramientas de orquestación
│   ├── context_provider.py
│   ├── agent_router.py
│   ├── clarify_engine.py
│   ├── approval_gate.py
│   ├── commit_validator.py
│   └── task_manager.py
├── hooks/               # Hooks de Claude Code (Python)
│   ├── pre_tool_use.py
│   ├── post_tool_use.py
│   ├── subagent_stop.py
│   ├── session_start.py
│   └── pre_kubectl_security.py
├── commands/            # Comandos slash
│   ├── gaia.md
│   ├── save-session.md
│   ├── restore-session.md
│   ├── session-status.md
│   └── speckit.*.md (7 comandos)
├── config/              # Configuración y documentación
│   ├── AGENTS.md
│   ├── orchestration-workflow.md
│   ├── git-standards.md
│   ├── context-contracts.md
│   ├── context-contracts.gcp.json
│   ├── context-contracts.aws.json
│   ├── agent-catalog.md
│   └── git_standards.json
├── templates/           # Templates de instalación
│   ├── CLAUDE.template.md
│   └── settings.template.json
├── speckit/             # Metodología Spec-Kit
│   ├── README.md
│   ├── templates/
│   ├── scripts/
│   └── governance.md
├── CHANGELOG.md         # Historial de versiones
├── package.json
└── index.js             # Funciones auxiliares
```

## Estructura de Tu Proyecto

Después de la instalación:

```
tu-proyecto/
├── .claude/                 # Symlinks a node_modules/@jaguilar87/gaia-ops/
│   ├── agents/              → node_modules/@jaguilar87/gaia-ops/agents/
│   ├── tools/               → node_modules/@jaguilar87/gaia-ops/tools/
│   ├── hooks/               → node_modules/@jaguilar87/gaia-ops/hooks/
│   ├── commands/            → node_modules/@jaguilar87/gaia-ops/commands/
│   ├── config/              → node_modules/@jaguilar87/gaia-ops/config/
│   ├── templates/           → node_modules/@jaguilar87/gaia-ops/templates/
│   ├── CHANGELOG.md         → node_modules/@jaguilar87/gaia-ops/CHANGELOG.md
│   ├── logs/                # Específico del proyecto (NO symlink)
│   ├── tests/               # Específico del proyecto (NO symlink)
│   └── project-context.json # Específico del proyecto (NO symlink)
├── CLAUDE.md                # Generado desde template
├── gitops/                  # Tus manifiestos GitOps
├── terraform/               # Tu código Terraform
├── app-services/            # Tu código de aplicación
├── node_modules/
│   └── @jaguilar87/
│       └── gaia-ops/        # Este paquete
└── package.json
```

## API

Si necesitas acceder a las rutas del paquete programáticamente:

```javascript
import {
  getAgentPath,
  getToolPath,
  getConfigPath
} from '@jaguilar87/gaia-ops';

const agentPath = getAgentPath('gitops-operator');
// → /path/to/node_modules/@jaguilar87/gaia-ops/agents/gitops-operator.md

const toolPath = getToolPath('context_provider.py');
// → /path/to/node_modules/@jaguilar87/gaia-ops/tools/context_provider.py

const configPath = getConfigPath('orchestration-workflow.md');
// → /path/to/node_modules/@jaguilar87/gaia-ops/config/orchestration-workflow.md
```

## Versionamiento

Este paquete sigue [Versionamiento Semántico](https://semver.org/):

    - **MAJOR:** Cambios que rompen compatibilidad en el comportamiento del orquestador
    - **MINOR:** Nuevas características, agentes o mejoras
    - **PATCH:** Correcciones de bugs, clarificaciones, errores tipográficos

    Versión actual: **2.2.3**

Ver [CHANGELOG.md](./CHANGELOG.md) para el historial de versiones.

## Documentación

- **Instrucciones Principales:** [CLAUDE.md](./CLAUDE.md) (154 líneas)
- **Vista General del Sistema:** [config/AGENTS.md](./config/AGENTS.md) (95 líneas)
- **Workflow de Orquestación:** [config/orchestration-workflow.md](./config/orchestration-workflow.md) (735 líneas)
- **Estándares Git:** [config/git-standards.md](./config/git-standards.md) (682 líneas)
- **Contratos de Contexto:** [config/context-contracts.md](./config/context-contracts.md) (673 líneas)
- **Catálogo de Agentes:** [config/agent-catalog.md](./config/agent-catalog.md) (603 líneas)

## Requisitos

- **Node.js:** >=18.0.0
- **Python:** >=3.9
- **Claude Code:** Última versión
- **Git:** >=2.30

## Gestión de Contexto de Proyecto

Gaia-Ops usa un contexto de proyecto versionado como SSOT. Después de la instalación, clona tu contexto de proyecto:

```bash
cd .claude
git clone git@bitbucket.org:tuorg/tu-project-context.git project-context
```

Esto mantiene `project-context.json` versionado separadamente, mientras los datos de `session/` permanecen locales.

Ver [rnd-project-context](https://bitbucket.org/aaxisdigital/rnd-project-context) como ejemplo.

## Soporte

- **Issues:** [GitHub Issues](https://github.com/metraton/gaia-ops/issues)
- **Repositorio:** [github.com/metraton/gaia-ops](https://github.com/metraton/gaia-ops)
- **Autor:** Jorge Aguilar <jaguilar1897@gmail.com>

## Licencia

MIT License - Ver [LICENSE](./LICENSE) para detalles.
