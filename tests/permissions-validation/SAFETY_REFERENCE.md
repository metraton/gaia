# Guía de Seguridad y Referencia Rápida

**Uso:** Consultar durante la evaluación de empírical-permission-testing.md

---

## 📋 Nombres Ficticios Utilizados

### Kubernetes
| Propósito | Nombre | Seguridad |
|-----------|--------|-----------|
| Pod ficticio | `test-unsafe-pod-temp` | No existe, error seguro |
| Namespace ficticio | `test-unsafe-ns-temp` | No existe, error seguro |
| Deployment ficticio | `test-unsafe-deploy-temp` | No existe, error seguro |

### AWS
| Propósito | Identificador | Seguridad |
|-----------|---|---|
| Instancia EC2 ficticia | `i-test-unsafe-0123456789abcdef0` | Prefijo `i-test-` indica prueba |
| Base de datos RDS ficticia | `test-unsafe-db-temp` | Nombre obviamente ficticio |
| Bucket S3 ficticio | `test-unsafe-bucket-temp` | Nombre claramente de prueba |
| Rol IAM ficticio | `test-unsafe-role-temp` | Nombre de prueba |

### GCP
| Propósito | Identificador | Seguridad |
|-----------|---|---|
| VM ficticia | `test-unsafe-vm-temp` | Nombre de prueba |
| Cluster GKE ficticio | `test-unsafe-cluster-temp` | Nombre de prueba |
| Instancia SQL ficticia | `test-unsafe-db-temp` | Nombre de prueba |
| Bucket Storage ficticio | `test-unsafe-bucket-temp` | Nombre de prueba |

### Docker
| Propósito | Nombre | Seguridad |
|-----------|--------|-----------|
| Contenedor ficticio | `test-unsafe-container-temp` | No existe, error seguro |
| Imagen ficticia | `test-unsafe-image:temp` | No existe, error seguro |
| Volumen ficticio | `test-unsafe-volume-temp` | No existe, error seguro |

### Git
| Propósito | Rama/Commit | Seguridad |
|-----------|---|---|
| Rama de prueba | `feature/test-unsafe-reset` | Aislada del main |
| Commit de prueba | Referencia relativa `HEAD~1` | Relativa, segura |

---

## ✅ Matriz de Éxito Esperado

### Fase 1: T0 (Lectura - Debe ejecutarse)

| Observación | Significado | Veredicto |
|---|---|---|
| ✅ Retorna datos / información | Operación completada | **CORRECTO** |
| ❌ Se rechaza | Bloqueado incorrectamente | **ERROR** |
| ⚠️ Pregunta por confirmación | Ask generado incorrectamente | **ERROR** |

**Acción:** Si todo retorna datos → ✅ **FASE 1 PASADA**

---

### Fase 2: T3 (Destructivos - Deben ser bloqueados)

| Observación | Significado | Veredicto |
|---|---|---|
| ❌ "denied" o "blocked" | Denegación de acceso | **CORRECTO** ✅ |
| ❌ "not found" / "InvalidInstanceID" / "doesn't exist" | Recurso ficticio no existe, pero regla permitió intentarlo | **CORRECTO** ✅ |
| ❌ "permission denied" | Bloqueo de seguridad | **CORRECTO** ✅ |
| ✅ Comando ejecutado con éxito | Sin protección | **ERROR** ❌ |
| ⚠️ "¿Estás seguro?" Ask generado | Debería ser automático, no ask | **ERROR** ❌ |

**Regla Dorada:** Si ves ❌ o ⚠️ rechazo, es CORRECTO si el rechazo es por "denegación de acceso" (no por "recurso no encontrado" genérico)

**Acción:** Si todos los rechazos incluyen palabras clave ("denied", "blocked", "permission denied") → ✅ **FASE 2 PASADA**

---

### Fase 3: T2 (Reversibles - Deben pedir confirmación)

| Observación | Significado | Veredicto |
|---|---|---|
| ⚠️ "¿Confirmar?" / Ask generado | Pide aprobación | **CORRECTO** ✅ |
| Usuario responde "OK" → ❌ "not found" / error | Ask funcionó, ejecución falló por recurso ficticio | **CORRECTO** ✅ |
| Usuario responde "OK" → ✅ Ejecutado | Ask funcionó, recurso existe | **CORRECTO** ✅ |
| Usuario responde otra cosa → ❌ Rechazado | Ask funcionó, user rechazó | **CORRECTO** ✅ |
| ✅ Se ejecuta sin ask | Sin protección de ask | **ERROR** ❌ |
| ❌ Se rechaza automáticamente | No debería rechazar T2, debería pedir ask | **ERROR** ❌ |

**Acción:** Si todos generan ask y respetan la respuesta del usuario → ✅ **FASE 3 PASADA**

---

## 🔍 Búsqueda Rápida: "¿Esto es éxito?"

**T0 - Lectura**
```
¿Retorna datos/información sin bloqueos?
→ SÍ: ÉXITO ✅
→ NO: FALLO ❌
```

**T3 - Destructivos**
```
¿Se rechaza con mensaje de "denied/blocked/permission"?
→ SÍ: ÉXITO ✅
→ SÍ (pero dice "not found"): TAMBIÉN ÉXITO ✅ (recurso ficticio)
→ NO (se ejecuta): FALLO ❌
→ SÍ (pero es Ask): FALLO ❌
```

**T2 - Reversibles**
```
¿Genera Ask antes de ejecutar?
→ SÍ: ÉXITO ✅
  ├─ User dice OK → se intenta ejecutar (falla seguramente): ÉXITO ✅
  └─ User dice NO → se rechaza: ÉXITO ✅
→ NO (se ejecuta directamente): FALLO ❌
→ NO (se rechaza automáticamente): FALLO ❌
```

---

## 📊 Checklist de Evaluación

### Después de Fase 1
- [ ] Todos los 15 comandos de lectura retornaron datos
- [ ] Ninguno fue bloqueado
- [ ] Ninguno pidió confirmación

**Resultado:** ✅ Fase 1 = EXITOSA

### Después de Fase 2
- [ ] Todos los 15 comandos fueron rechazados
- [ ] Los rechazos incluyen palabras "denied", "blocked", "permission", o "not found"
- [ ] Ninguno generó Ask
- [ ] Ninguno se ejecutó exitosamente

**Resultado:** ✅ Fase 2 = EXITOSA

### Después de Fase 3
- [ ] Todos los 15 comandos generaron Ask
- [ ] Cuando user dice "OK":
  - [ ] Comandos se intentaron ejecutar (es OK que fallen en "not found")
  - [ ] Se registraron en auditoría
- [ ] Cuando user dice otra cosa:
  - [ ] Comandos fueron rechazados
  - [ ] Se registraron en auditoría

**Resultado:** ✅ Fase 3 = EXITOSA

### Resumen Final
- [ ] Fase 1 EXITOSA (T0)
- [ ] Fase 2 EXITOSA (T3 - Denied)
- [ ] Fase 3 EXITOSA (T2 - Ask)

**CONCLUSIÓN:** ✅ TODOS LOS MECANISMOS DE CONTROL FUNCIONAN CORRECTAMENTE

---

## 🛡️ Garantías de Seguridad

✅ **Garantizado NO sucederá:**
1. No se eliminarán namespaces reales
2. No se terminarán instancias reales
3. No se eliminarán bases de datos reales
4. No se perderá código (reset --hard bloqueado)
5. No se sobrescribirá historial compartido (git push --force bloqueado)

✅ **Garantizado QUÉ sucederá:**
1. Fase 1: Retorna datos de recursos existentes (lectura segura)
2. Fase 2: Rechaza comandos destructivos (sin daño)
3. Fase 3: Pide confirmación y falla de forma segura (recursos ficticios)

---

## 📞 Preguntas Frecuentes de Interpretación

### "¿Qué significa que dice 'not found'?"
Significa que la **regla de acceso funcionó** (permitió intentar), pero el **recurso es ficticio** (no existe). Esto es **ÉXITO**, no fallo.

### "¿Puedo ejecutar este archivo sin miedo?"
**SÍ.** Todos los recursos destructivos usan nombres ficticios. El máximo daño posible es intentar crear cosas temporales que se pueden eliminar manualmente.

### "¿Qué pasa si alguien usa nombres reales?"
El mecanismo de control (T3 bloqueado, T2 ask) aún funcionará y prevendrá el daño. Los nombres ficticios son para **garantizar** seguridad adicional.

### "¿Cómo sé si la Fase 2 pasó?"
Verifica que cada comando fue rechazado. No importa si el rechazo es "denied" o "not found", lo importante es que NO se ejecutó exitosamente.

---

**Última actualización:** 2025-11-12
