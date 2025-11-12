# Gaia Workflow - Direct Repository Updates

**Fecha:** 2025-11-12
**Propósito:** Asegurar que Gaia actualice gaia-ops directamente, evitando archivos intermedios en `/tmp/`

---

## Principio

Cuando Gaia (meta-agent gaia-ops) realiza cambios, debe:
1. **Generar cambios directamente en gaia-ops** (no en `/tmp/`)
2. **Validar cambios en su lugar final**
3. **Facilitar commits atómicos y seguros**

---

## Workflow Recomendado para Gaia

### Phase 1: Investigación (Lectura)
```bash
# Leer archivos del repositorio
read .claude/config/settings.json
read tests/permissions-validation/manual-permission-validation.md
read README.md
# Resultado: Entender estado actual
```

### Phase 2: Planificación (En Memoria)
```
# No generar archivos temporales
# Solo documentar plan en prompt
# Describir qué cambios se harán y dónde
```

### Phase 3: Realización (Escritura Directa)
```bash
# Escribir directamente en gaia-ops
write /home/jaguilar/aaxis/rnd/repos/gaia-ops/config/new-feature.md
edit /home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.json
# Resultado: Cambios en su ubicación final
```

### Phase 4: Validación
```bash
# Validar JSON/YAML/Markdown
python3 -m json.tool archivo.json
# Resultado: Confirmar formato correcto
```

### Phase 5: Commit
```bash
# Commit atómico desde el repositorio
git add archivo1 archivo2
git commit -m "feat(...): descripción"
# Resultado: Cambios versionados
```

---

## Checklist para Invocaciones de Gaia

Cuando invoques a Gaia, verifica:

- [ ] **Ruta correcta**: Todos los cambios van a `/home/jaguilar/aaxis/rnd/repos/gaia-ops/`
- [ ] **No usar /tmp/**: Evitar archivos intermedios innecesarios
- [ ] **Validación en lugar**: Verificar formato antes de commit
- [ ] **Commit inmediato**: No dejar archivos sin versionar
- [ ] **Mensaje claro**: Commit message describe qué, por qué, no cómo

---

## Rutas Críticas de gaia-ops

### Configuración
```
gaia-ops/config/
├── orchestration-workflow.md          (Definición de orquestación)
├── git-standards.md                   (Estándares Git)
├── context-contracts.md               (Contratos de contexto)
├── agent-catalog.md                   (Catálogo de agentes)
├── clarification_rules.json           (Reglas de clarificación)
└── settings-template.md               (Template de configuración)
```

### Plantillas
```
gaia-ops/templates/
├── settings.template.json             (Template de permisos - CRÍTICO)
├── prompt.template.md                 (Template de prompt)
└── ...
```

### Pruebas
```
gaia-ops/tests/
├── permissions-validation/
│   ├── manual-permission-validation.md   (Casos de prueba)
│   ├── comprehensive-command-specifications.md
│   └── ...
└── system/
    └── ...
```

### Documentación
```
gaia-ops/
├── CLAUDE.md                          (Orquestador principal)
├── README.md                          (Overview)
└── docs/
    └── ...
```

---

## Ejemplo: Mejora de Permissions

### Mal (No hacer)
```
Gaia genera → /tmp/settings.template.improved.json
Persona copia → gaia-ops/templates/
Resultado: Dos fuentes de verdad, posibles inconsistencias
```

### Bien (Hacer)
```
Gaia lee → gaia-ops/templates/settings.template.json
Gaia valida reglas
Gaia escribe → gaia-ops/templates/settings.template.json (DIRECTO)
Gaia valida JSON
Gaia commit → con mensaje descriptivo
Resultado: SSOT (Single Source of Truth), versionado, atómico
```

---

## Variables de Entorno para Gaia

Para futuras sesiones, establecer:

```bash
export GAIA_REPO_PATH="/home/jaguilar/aaxis/rnd/repos/gaia-ops"
export GAIA_DIRECT_WRITE=true      # Always write to repo, not /tmp
export GAIA_AUTO_COMMIT=false      # Wait for approval before commit
export GAIA_VALIDATE_BEFORE_WRITE=true  # Validate format first
```

---

## Integration con Claude Code

Cuando invoques Gaia desde Claude Code:

```python
# Instrucción para Gaia
prompt = """
Update gaia-ops directly:
1. Read: gaia-ops/templates/settings.template.json
2. Plan: How to improve permissions
3. Write: gaia-ops/templates/settings.template.json (DIRECT)
4. Validate: JSON syntax
5. Commit: With clear message

Work directly in repository, not in /tmp/
Use absolute paths: /home/jaguilar/aaxis/rnd/repos/gaia-ops/...
Validate before each write operation.
"""
```

---

## Checklist Post-Gaia

Después de que Gaia realice cambios:

- [ ] ✅ Archivos en gaia-ops (no en /tmp/)
- [ ] ✅ JSON/YAML validado
- [ ] ✅ Commit realizado
- [ ] ✅ `git status` limpio
- [ ] ✅ `git log` muestra commit nuevo
- [ ] ✅ No hay archivos no-versionados

---

## Ejemplo de Validación

```bash
# Verificar que todo está en su lugar
cd /home/jaguilar/aaxis/rnd/repos/gaia-ops

# 1. Ver cambios
git status
# Esperado: "working tree clean" o cambios staged

# 2. Ver commits
git log --oneline -3
# Esperado: Commit más reciente tiene cambios de Gaia

# 3. Validar archivos críticos
python3 -m json.tool templates/settings.template.json
# Esperado: JSON válido

# 4. Confirmar no hay archivos temporales
ls /tmp/*.json /tmp/*.md 2>/dev/null
# Esperado: Archivos viejos (si hay)
```

---

## Notas Importantes

1. **SSOT (Single Source of Truth):** gaia-ops es la fuente única de verdad
2. **Validación:** Siempre validar antes de escribir
3. **Commits Atómicos:** Cada cambio = 1 commit con mensaje claro
4. **No /tmp/:** Archivos temporales son anti-pattern para gaia-ops
5. **Direct Paths:** Usar rutas absolutas completas

---

**Status:** Activo desde 2025-11-12
**Última Validación:** Manual - todos los archivos confirmados en gaia-ops ✅
