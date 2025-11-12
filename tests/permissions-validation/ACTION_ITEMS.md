# Action Items: Cobertura de Permisos - Plan de Remediación

**Generado:** 2025-11-12  
**Análisis:** Coverage Report - empirical-permission-testing.md vs settings.template.json  
**Cobertura Actual:** 86.7% (39 de 45 casos cubiertos)  
**Estado:** GOOD with ACTION ITEMS

---

## Resumen Ejecutivo

El análisis de cobertura ha identificado **6 operaciones destructivas** (T3) que NO están bloqueadas en `settings.template.json`, pese a que deberían ser automáticamente rechazadas según las pruebas empíricas.

**Impacto de Seguridad:** ALTO

### Cobertura por Tier

| Tier | Descripción | Cobertura | Estado |
|------|-------------|-----------|--------|
| **T0** | Read Only (Allow) | **100%** ✓ | EXCELENTE |
| **T2** | Reversible (Ask) | **100%** ✓ | EXCELENTE |
| **T3** | Destructive (Deny) | **60%** ✗ | **CRÍTICO** |

---

## ITEM 1: kubectl delete - Bloqueo de Operaciones Destructivas de Kubernetes

**Severidad:** HIGH  
**Caso de Prueba:** 2.1, 2.2 - Fase 2 (T3 Tests)  
**Estado:** NO CUBIERTO

### Problema

```
Situación Actual:
├─ kubectl delete está en: "ask" (T2) - Pide confirmación
└─ kubectl delete debería estar en: "deny" (T3) - Rechazo automático

Impacto:
└─ Usuarios pueden eliminar namespaces y pods CRÍTICOS sin bloqueo automático
```

### Operaciones Afectadas

- `kubectl delete namespace <name>`
- `kubectl delete pod <name>`
- `kubectl delete deployment <name>`
- `kubectl delete service <name>`

### Solución Propuesta

**Opción A: Enfoque Simple (Menos Granular)**

Agregar a `settings.template.json` -> `permissions.deny`:

```json
"deny": [
  ...
  "Bash(kubectl delete namespace:*)",
  "Bash(kubectl delete cluster:*)"
]
```

Mantener en `ask`:

```json
"ask": [
  ...
  "Bash(kubectl delete pod:*)",
  "Bash(kubectl delete deployment:*)"
]
```

**Opción B: Enfoque Granular (Recomendado)**

```json
"deny": [
  "Bash(kubectl delete namespace:*)",
  "Bash(kubectl delete pdb:*)",
  "Bash(kubectl delete cluster:*)",
  "Bash(kubectl delete node:*)"
],
"ask": [
  "Bash(kubectl delete pod:*)",
  "Bash(kubectl delete deployment:*)",
  "Bash(kubectl delete statefulset:*)"
]
```

### Testing

```bash
# Debería ser RECHAZADO (bloqueado sin ask)
kubectl delete namespace production

# Debería generar ASK
kubectl delete pod test-pod-12345
```

### Referencia en Código

- Archivo: `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`
- Línea: ~176 (deny section) y ~212 (ask section)

---

## ITEM 2: git reset --hard - Bloqueo de Resets Destructivos

**Severidad:** CRITICAL  
**Caso de Prueba:** 2.15 - Fase 2 (T3 Tests)  
**Estado:** NO CUBIERTO

### Problema

```
Situación Actual:
├─ git reset --hard: NO ESTÁ bloqueado
├─ git reset --hard está permitido de facto
└─ Puede descartar cambios CRÍTICOS sin protección

Impacto:
└─ Pérdida de código local sin recuperación posible
```

### Operaciones Afectadas

- `git reset --hard`
- `git reset --hard HEAD~1`
- `git reset --hard <commit>`

### Solución

Agregar a `settings.template.json` -> `permissions.deny`:

```json
"deny": [
  ...
  "Bash(git reset.*--hard:*)"
]
```

### Testing

```bash
# Debería ser RECHAZADO (bloqueado sin ask)
git reset --hard HEAD~1
```

### Referencia en Código

- Archivo: `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`
- Línea: ~176 (deny section)
- Relación: Item 3 (git push --force)

---

## ITEM 3: git push --force - Bloqueo de Force Push

**Severidad:** CRITICAL  
**Caso de Prueba:** 2.15 - Fase 2 (T3 Tests)  
**Estado:** NO CUBIERTO

### Problema

```
Situación Actual:
├─ git push --force: NO ESTÁ bloqueado
├─ git push -f: NO ESTÁ bloqueado
└─ Puede sobrescribir historial remoto sin protección

Impacto:
└─ Pérdida de historial de commits compartido
└─ Afecta a todo el equipo
```

### Operaciones Afectadas

- `git push --force`
- `git push -f`
- `git push --force-with-lease` (considerar)

### Solución

Agregar a `settings.template.json` -> `permissions.deny`:

```json
"deny": [
  ...
  "Bash(git push.*--force:*)",
  "Bash(git push.*-f:*)"
]
```

### Nota de Diseño

El patrón regex debe captar:
- `git push --force origin main`
- `git push -f origin main`
- `git push origin main --force`
- `git push -f` (push a upstream actual)

### Testing

```bash
# Debería ser RECHAZADO (bloqueado sin ask)
git push --force origin main
git push -f

# Para sobrescribir, usar alternativa más segura:
# git push --force-with-lease (en "ask", no "deny")
```

### Referencia en Código

- Archivo: `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`
- Línea: ~176 (deny section)
- Relación: Item 2 (git reset --hard)

---

## ITEM 4: gsutil rm / rb - Bloqueo de Eliminación de Buckets GCP

**Severidad:** HIGH  
**Caso de Prueba:** 2.10 - Fase 2 (T3 Tests)  
**Estado:** NO CUBIERTO

### Problema

```
Situación Actual:
├─ gsutil rb: NO ESTÁ en deny
├─ gsutil rm -r: NO ESTÁ en deny
└─ Buckets GCP pueden ser eliminados sin protección

Impacto:
└─ Pérdida permanente de datos en Storage
└─ Sin recuperación posible
```

### Operaciones Afectadas

- `gsutil rb gs://bucket-name` (remove bucket)
- `gsutil rm -r gs://bucket-name/**` (recursive delete)

### Solución

Agregar a `settings.template.json` -> `permissions.deny`:

```json
"deny": [
  ...
  "Bash(gsutil rb:*)",
  "Bash(gsutil rm.*-r:*)"
]
```

### Testing

```bash
# Debería ser RECHAZADO (bloqueado sin ask)
gsutil rb gs://my-production-bucket

gsutil rm -r gs://my-production-bucket/**

# Debería generar ASK (operación segura)
gsutil cp gs://source/* gs://destination/
```

### Referencia en Código

- Archivo: `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`
- Línea: ~176 (deny section)
- Nota: gsutil cp/ls están en "allow" (línea ~103)

---

## ITEM 5: AWS S3 - Revisión de Patrones de Eliminación

**Severidad:** HIGH  
**Caso de Prueba:** 2.5 - Fase 2 (T3 Tests)  
**Estado:** COBERTURA PARCIAL / VALIDACIÓN REQUERIDA

### Problema

```
Situación Actual:
├─ Patrón: "Bash(aws s3.*rb:*)" existe
├─ Patrón: "Bash(aws s3 rm.*--recursive:*)" existe
└─ Pregunta: ¿Los patrones regex captan TODOS los casos?

Impacto:
└─ Posible brecha si los patrones no son suficientemente amplios
```

### Operaciones Potencialmente Afectadas

- `aws s3 rb s3://bucket-name` (remove bucket)
- `aws s3 rb s3://bucket-name --force` (force remove)
- `aws s3 rm s3://bucket-name --recursive` (recursive delete)

### Solución

**Fase 1: Validación**

Ejecutar test_permissions_validation.py para verificar que los patrones funcionan:

```bash
cd /home/jaguilar/aaxis/rnd/repos/gaia-ops
python tests/permissions-validation/test_permissions_validation.py \
  --settings-file templates/settings.template.json
```

**Fase 2: Si Falla, Actualizar Patrones**

Si los patrones actuales no funcionan, reemplazar en `settings.template.json`:

```json
"deny": [
  ...
  "Bash(aws s3 rb:*)",
  "Bash(aws s3.*--recursive:*)"
]
```

O más específico:

```json
"deny": [
  "Bash(aws s3 rb s3:*)",
  "Bash(aws s3 rm.*--recursive:*)"
]
```

### Testing

```bash
# Debería ser RECHAZADO
aws s3 rb s3://production-bucket
aws s3 rb s3://production-bucket --force
aws s3 rm s3://production-bucket --recursive

# Debería generar ASK (no DENY)
aws s3 cp s3://source/* s3://destination/
aws s3 ls
```

### Referencia en Código

- Archivo: `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`
- Línea: ~190 (deny section - aws s3 patterns)
- Línea: ~111-112 (allow section - aws s3 ls/get patterns)

---

## ITEM 6: docker volume rm - Cobertura Parcial

**Severidad:** MEDIUM  
**Caso de Prueba:** 2.13 - Fase 2 (T3 Tests)  
**Estado:** COBERTURA PARCIAL

### Problema

```
Situación Actual:
├─ docker volume rm: Posible cobertura parcial
├─ docker volume prune: Parcialmente cubierto
└─ Necesita validación

Impacto:
└─ Volúmenes Docker pueden no estar completamente protegidos
```

### Operaciones Afectadas

- `docker volume rm volume-name`
- `docker volume rm -f volume-name`
- `docker volume prune --all` (ya en deny)

### Solución

Verificar que está en deny:

```json
"deny": [
  ...
  "Bash(docker volume rm:*)"
]
```

Si NO está, agregar:

```json
"deny": [
  "Bash(docker volume rm:*)"
]
```

### Testing

```bash
# Debería ser RECHAZADO
docker volume rm my-volume
docker volume rm -f my-volume

# docker volume prune debería estar en deny
docker volume prune --all
```

### Referencia en Código

- Archivo: `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`
- Línea: ~195 (deny section - docker volume patterns)

---

## Plan de Implementación

### Fase 1: Preparación (Estimado: 15 min)

1. Hacer backup de `settings.template.json`:
   ```bash
   cp templates/settings.template.json templates/settings.template.json.backup
   ```

2. Revisar cambios:
   ```bash
   git diff templates/settings.template.json
   ```

### Fase 2: Implementar Cambios (Estimado: 30 min)

1. **ITEM 2** (git reset --hard) - CRITICAL
2. **ITEM 3** (git push --force) - CRITICAL
3. **ITEM 1** (kubectl delete) - HIGH
4. **ITEM 3** (gsutil) - HIGH
5. **ITEM 5** (AWS S3) - HIGH (Validation)
6. **ITEM 6** (docker volume) - MEDIUM

### Fase 3: Validación (Estimado: 45 min)

1. Ejecutar test_permissions_validation.py:
   ```bash
   python tests/permissions-validation/test_permissions_validation.py \
     --settings-file templates/settings.template.json
   ```

2. Ejecutar empirical-permission-testing.md en sesión nueva de Claude:
   - Debe pasar TODOS los casos de Fase 2 (T3 - Deny)

3. Verificar que Fase 1 (T0) y Fase 3 (T2) sigan funcionando:
   - T0: Todas las operaciones READ deben ejecutarse sin bloqueo
   - T2: Todas las operaciones REVERSIBLE deben pedir confirmación

### Fase 4: Documentación (Estimado: 15 min)

1. Documentar cambios en release notes
2. Actualizar COVERAGE_REPORT.json
3. Hacer commit con mensaje descriptivo

---

## Checklist de Implementación

- [ ] **ITEM 1:** kubectl delete bloqueado para namespaces/clusters
  - [ ] Patrón agregado a deny
  - [ ] Patrones específicos en ask
  - [ ] Tested

- [ ] **ITEM 2:** git reset --hard bloqueado
  - [ ] Patrón agregado a deny
  - [ ] Regex validado
  - [ ] Tested

- [ ] **ITEM 3:** git push --force bloqueado
  - [ ] Patrón --force agregado a deny
  - [ ] Patrón -f agregado a deny
  - [ ] Regex validado
  - [ ] Tested

- [ ] **ITEM 4:** gsutil rm/rb bloqueado
  - [ ] Patrón gsutil rb agregado a deny
  - [ ] Patrón gsutil rm -r agregado a deny
  - [ ] Tested

- [ ] **ITEM 5:** AWS S3 patterns validados
  - [ ] test_permissions_validation.py ejecutado
  - [ ] Patrones revisados si es necesario
  - [ ] Tested

- [ ] **ITEM 6:** docker volume rm validado
  - [ ] Patrón confirmado en deny
  - [ ] Tested

- [ ] **Validación Global:**
  - [ ] test_permissions_validation.py pasa
  - [ ] empirical-permission-testing.md Fase 2 pasa
  - [ ] empirical-permission-testing.md Fase 1 pasa
  - [ ] empirical-permission-testing.md Fase 3 pasa

- [ ] **Documentación:**
  - [ ] Release notes actualizadas
  - [ ] COVERAGE_REPORT.json regenerado
  - [ ] Commit creado

---

## Recursos

- **Análisis Completo:** `/home/jaguilar/aaxis/rnd/repos/gaia-ops/tests/permissions-validation/COVERAGE_REPORT.json`
- **Resumen de Cobertura:** `/home/jaguilar/aaxis/rnd/repos/gaia-ops/tests/permissions-validation/COVERAGE_REPORT.txt`
- **Pruebas Empíricas:** `/home/jaguilar/aaxis/rnd/repos/gaia-ops/tests/permissions-validation/empirical-permission-testing.md`
- **Validador:** `/home/jaguilar/aaxis/rnd/repos/gaia-ops/tests/permissions-validation/test_permissions_validation.py`
- **Template de Configuración:** `/home/jaguilar/aaxis/rnd/repos/gaia-ops/templates/settings.template.json`

---

## Timeline Estimado

| Fase | Actividad | Estimado | Dependencias |
|------|-----------|----------|--------------|
| 1 | Preparación | 15 min | - |
| 2 | Implementación | 30 min | Fase 1 |
| 3 | Validación | 45 min | Fase 2 |
| 4 | Documentación | 15 min | Fase 3 |
| **TOTAL** | | **105 min (1.75h)** | |

---

**Status:** Ready for Implementation  
**Approved By:** Analysis Engine  
**Last Updated:** 2025-11-12
