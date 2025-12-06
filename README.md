# @jaguilar87/gaia-ops

[![npm version](https://badge.fury.io/js/@jaguilar87%2Fgaia-ops.svg)](https://www.npmjs.com/package/@jaguilar87/gaia-ops)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Node.js Version](https://img.shields.io/node/v/@jaguilar87/gaia-ops.svg)](https://nodejs.org)

**[English version](README.en.md)**

Sistema de orquestacion multi-agente para Claude Code - Toolkit de automatizacion DevOps.

## Descripcion General

**Gaia-Ops** proporciona un sistema completo de orquestacion de agentes para Claude Code, habilitando automatizacion inteligente de workflows DevOps a traves de agentes IA especializados.

### Caracteristicas

- **Soporte multi-cloud** - GCP, AWS, Azure-ready
- **6 agentes especialistas** (terraform-architect, gitops-operator, gcp-troubleshooter, aws-troubleshooter, devops-developer, Gaia)
- **3 meta-agentes** (Explore, Plan, Gaia)
- **Episodic Memory** - Sistema de memoria para patrones operacionales
- **Pre-carga hibrida de standards** - 78% reduccion de tokens por invocacion
- **Puertas de aprobacion** para operaciones T3
- **Validacion de commits Git** con Conventional Commits
- **359 tests** al 100% pasando

## Instalacion

### Inicio Rapido

```bash
# Ejecutar directamente con npx
npx gaia-init

# O instalacion global
npm install -g @jaguilar87/gaia-ops
gaia-init
```

Esto hara:
1. Auto-detectar tu estructura de proyecto (GitOps, Terraform, AppServices)
2. Instalar Claude Code si no esta presente
3. Crear directorio `.claude/` con symlinks a este paquete
4. Generar `CLAUDE.md` y `project-context.json`

### Instalacion Manual

```bash
npm install @jaguilar87/gaia-ops
```

Luego crea los symlinks:

```bash
mkdir -p .claude && cd .claude
ln -s ../node_modules/@jaguilar87/gaia-ops/agents agents
ln -s ../node_modules/@jaguilar87/gaia-ops/tools tools
ln -s ../node_modules/@jaguilar87/gaia-ops/hooks hooks
ln -s ../node_modules/@jaguilar87/gaia-ops/commands commands
ln -s ../node_modules/@jaguilar87/gaia-ops/config config
ln -s ../node_modules/@jaguilar87/gaia-ops/templates templates
```

## Uso

Una vez instalado, el sistema de agentes esta listo:

```bash
claude-code
```

Claude Code cargara automaticamente `CLAUDE.md` y tendra acceso a todos los agentes via `.claude/`.

## Estructura del Proyecto

```
node_modules/@jaguilar87/gaia-ops/
├── agents/              # Definiciones de agentes
├── tools/               # Herramientas de orquestacion
├── hooks/               # Hooks de Claude Code
├── commands/            # Comandos slash
├── config/              # Configuracion y documentacion
├── templates/           # Templates de instalacion
├── speckit/             # Metodologia Spec-Kit
└── tests/               # Suite de tests (359 tests)
```

## API

```javascript
import { getAgentPath, getToolPath, getConfigPath } from '@jaguilar87/gaia-ops';

const agentPath = getAgentPath('gitops-operator');
const toolPath = getToolPath('context_provider.py');
```

## Versionamiento

Este paquete sigue [Versionamiento Semantico](https://semver.org/):

- **MAJOR:** Cambios que rompen compatibilidad
- **MINOR:** Nuevas caracteristicas
- **PATCH:** Correcciones de bugs

Version actual: **3.0.0**

Ver [CHANGELOG.md](./CHANGELOG.md) para el historial de versiones.

## Documentacion

- [config/AGENTS.md](./config/AGENTS.md) - Vista general del sistema
- [config/orchestration-workflow.md](./config/orchestration-workflow.md) - Workflow de orquestacion
- [config/git-standards.md](./config/git-standards.md) - Estandares Git
- [config/context-contracts.md](./config/context-contracts.md) - Contratos de contexto

## Requisitos

- **Node.js:** >=18.0.0
- **Python:** >=3.9
- **Claude Code:** Ultima version
- **Git:** >=2.30

## Gestion de Contexto de Proyecto

Gaia-Ops usa un contexto de proyecto versionado como SSOT:

```bash
cd .claude
git clone git@bitbucket.org:tuorg/tu-project-context.git project-context
```

## Soporte

- **Issues:** [GitHub Issues](https://github.com/metraton/gaia-ops/issues)
- **Repositorio:** [github.com/metraton/gaia-ops](https://github.com/metraton/gaia-ops)
- **Autor:** Jorge Aguilar <jaguilar1897@gmail.com>

## Licencia

MIT License - Ver [LICENSE](./LICENSE) para detalles.
