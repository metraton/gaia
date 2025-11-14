# Plan de Testing Exhaustivo - Gaia-Ops 2.6.1

## 🎯 Objetivo
Validar todos los comandos y casos de uso antes de publicar versión 2.6.1

## 📋 Casos de Prueba

### 1. Instalación Limpia (Fresh Install)

#### 1.1 Instalación Interactiva con Directorios Detectados
- [ ] Crear proyecto con `gitops/`, `terraform/`, `app-services/`
- [ ] Ejecutar `npm install ../gaia-ops`
- [ ] Ejecutar `npx gaia-init` (interactivo)
- [ ] Verificar:
  - [ ] `.claude/` creado
  - [ ] Symlinks correctos
  - [ ] `CLAUDE.md` generado
  - [ ] `AGENTS.md` creado
  - [ ] `settings.json` generado
  - [ ] `project-context.json` generado

#### 1.2 Instalación Interactiva SIN Directorios
- [ ] Crear proyecto vacío
- [ ] Ejecutar `npm install ../gaia-ops`
- [ ] Ejecutar `npx gaia-init` (interactivo)
- [ ] Responder preguntas manualmente
- [ ] Verificar archivos generados

#### 1.3 Instalación No-Interactiva con Flags
- [ ] Proyecto vacío
- [ ] Ejecutar con flags:
  ```bash
  npx gaia-init --non-interactive \
    --gitops ./gitops \
    --terraform ./terraform \
    --project-id test-project \
    --cluster test-cluster
  ```
- [ ] Verificar archivos generados

#### 1.4 Instalación No-Interactiva con Variables de Entorno
- [ ] Configurar env vars
- [ ] Ejecutar `npx gaia-init --non-interactive`
- [ ] Verificar archivos generados

---

### 2. Update (npm install después de cambios)

#### 2.1 Update Normal
- [ ] Instalación existente
- [ ] Modificar `CLAUDE.md` manualmente
- [ ] Ejecutar `npm install` (trigger postinstall)
- [ ] Verificar:
  - [ ] `CLAUDE.md` SOBRESCRITO con template
  - [ ] `settings.json` SOBRESCRITO
  - [ ] Symlinks intactos
  - [ ] `project-context.json` PRESERVADO

#### 2.2 Update con Archivos Faltantes
- [ ] Instalación existente
- [ ] Eliminar `CLAUDE.md`
- [ ] Eliminar `settings.json`
- [ ] Ejecutar `npm install`
- [ ] Verificar que se RECREAN ambos archivos

#### 2.3 Update con Symlinks Rotos
- [ ] Instalación existente
- [ ] Romper symlinks manualmente
- [ ] Ejecutar `npm install`
- [ ] Verificar que se RECREAN symlinks

---

### 3. Cleanup (gaia-cleanup)

#### 3.1 Cleanup Completo
- [ ] Instalación existente
- [ ] Ejecutar `npx gaia-cleanup`
- [ ] Verificar ELIMINADOS:
  - [ ] `CLAUDE.md`
  - [ ] `AGENTS.md`
  - [ ] `.claude/settings.json`
  - [ ] Todos los symlinks en `.claude/`
- [ ] Verificar PRESERVADOS:
  - [ ] `.claude/logs/`
  - [ ] `.claude/tests/`
  - [ ] `.claude/project-context.json`
  - [ ] `.claude/session/`

#### 3.2 Cleanup con Symlinks Rotos
- [ ] Crear symlinks rotos manualmente
- [ ] Ejecutar `npx gaia-cleanup`
- [ ] Verificar que elimina symlinks rotos

---

### 4. Uninstall (gaia-uninstall)

#### 4.1 Uninstall Completo
- [ ] Instalación existente
- [ ] Ejecutar `npx gaia-uninstall`
- [ ] Verificar:
  - [ ] Cleanup ejecutado
  - [ ] Package desinstalado
  - [ ] `node_modules/@jaguilar87/gaia-ops/` eliminado
- [ ] Verificar PRESERVADOS:
  - [ ] `.claude/logs/`
  - [ ] `.claude/project-context.json`

#### 4.2 Uninstall Manual (npm uninstall directo)
- [ ] Instalación existente
- [ ] Ejecutar `npm uninstall @jaguilar87/gaia-ops`
- [ ] Verificar que preuninstall hook ejecuta cleanup

---

### 5. Reinstalación

#### 5.1 Reinstall Después de Cleanup
- [ ] Estado post-cleanup
- [ ] Ejecutar `npm install ../gaia-ops`
- [ ] Ejecutar `npx gaia-init`
- [ ] Verificar instalación completa

#### 5.2 Reinstall Después de Uninstall
- [ ] Estado post-uninstall
- [ ] Ejecutar `npm install ../gaia-ops`
- [ ] Ejecutar `npx gaia-init`
- [ ] Verificar instalación completa

---

### 6. Casos Edge

#### 6.1 Directorio `.claude/` Pre-Existente
- [ ] Crear `.claude/` manualmente
- [ ] Ejecutar instalación
- [ ] Verificar manejo correcto

#### 6.2 Permisos Incorrectos
- [ ] Configurar permisos restrictivos
- [ ] Ejecutar comandos
- [ ] Verificar manejo de errores

#### 6.3 Node_Modules Corrupto
- [ ] Corromper `node_modules/`
- [ ] Ejecutar comandos
- [ ] Verificar manejo de errores

---

### 7. Comando de Métricas (NUEVO - A IMPLEMENTAR)

#### 7.1 Ver Métricas del Sistema
- [ ] Implementar comando `gaia-metrics`
- [ ] Mostrar:
  - [ ] Routing accuracy
  - [ ] Context efficiency
  - [ ] Agent invocations
  - [ ] Tier usage
- [ ] Probar en instalación existente

---

## 🔧 Mejoras Requeridas

### Scripts a Actualizar:

1. **gaia-cleanup.js**
   - ✅ Ya elimina CLAUDE.md
   - ✅ Ya elimina settings.json
   - ✅ Ya elimina symlinks
   - ⚠️ Mejorar: Detectar y eliminar symlinks ROTOS
   - ⚠️ Agregar: Eliminar AGENTS.md

2. **gaia-update.js**
   - ✅ Ya sobrescribe CLAUDE.md
   - ✅ Ya sobrescribe settings.json
   - ⚠️ Mejorar: SIEMPRE crear archivos (incluso si no existen)
   - ⚠️ Agregar: Recrear AGENTS.md si falta

3. **gaia-uninstall.js**
   - ✅ Ya ejecuta cleanup
   - ✅ Ya desinstala package
   - ✓ Delegación correcta a cleanup

4. **gaia-metrics.js** (NUEVO)
   - ⚠️ Crear comando nuevo
   - Mostrar métricas del sistema

---

## 📦 Publicación 2.6.1

### Pre-Publicación
- [ ] Todos los tests pasando
- [ ] Scripts actualizados
- [ ] CHANGELOG actualizado
- [ ] Version bumped a 2.6.1

### Publicación
- [ ] `npm version 2.6.1`
- [ ] `npm publish`

### Post-Publicación
- [ ] Probar instalación desde npm
- [ ] Probar todos los comandos
- [ ] Verificar en proyecto limpio

---

## 📊 Resultados Esperados

| Comando | Resultado Esperado |
|---------|-------------------|
| `npx gaia-init` | Instalación completa con archivos generados |
| `npm install` (update) | CLAUDE.md y settings.json sobrescritos |
| `npx gaia-cleanup` | Archivos generados eliminados, datos preservados |
| `npx gaia-uninstall` | Desinstalación completa, datos preservados |
| `npx gaia-metrics` | Métricas del sistema mostradas |

---

**Fecha:** 2025-11-14  
**Version:** 2.6.1  
**Tester:** Gaia (meta-agent)

