# tools/migration

Scripts de migración one-shot para absorber datos locales del workspace `me`
desde el filesystem hacia `~/.gaia/gaia.db` (substrate v6).

## Dominios

| # | Dominio | Origen | Tabla destino |
|---|---------|--------|---------------|
| 01 | Episodes | `.claude/project-context/episodic-memory/episodes.jsonl` | `episodes` (+`episodes_fts`) |
| 02 | Memory | `~/.claude/projects/-home-jorge-ws-me/memory/*.md` | `memory` (+`memory_fts`) |
| 03 | Context contracts | `.claude/project-context/project-context.json` | `context_contracts` |
| 04 | Harness events | `.claude/events/events.jsonl` | `harness_events` |

Cada dominio tiene 2 archivos:

- `migrate_<NN>_<dominio>.py` — converter Python read-only sobre filesystem.
  No conecta a SQLite. Lee la fuente, parsea, emite `/tmp/migrate_<NN>_<dominio>.sql`
  con `INSERT OR IGNORE` literales.
- `migrate_<NN>_<dominio>.sh` — wrapper que llama al `.py` y luego ejecuta
  `sqlite3 ~/.gaia/gaia.db < /tmp/migrate_<NN>_<dominio>.sql`. El `sqlite3` con
  INSERT es interceptado por el hook `pre_tool_use` (flujo correcto).

## Orden de ejecución

```
bootstrap.sh                             # crea/inicializa ~/.gaia/gaia.db con schema.sql (out of scope; gaia-system anterior)
./migrate_01_episodes.sh                 # ~50-80 MB de SQL, batch 80
./migrate_02_memory.sh                   # 28 .md (MEMORY.md excluido)
./migrate_03_context_contracts.sh        # 12 secciones
./migrate_04_harness_events.sh           # ~5-10 MB de SQL, batch 200
./validate.sh                            # 5 aserciones read-only
```

Cada script imprime `[migrate_NN] OK` al terminar.

## Idempotencia

| Dominio | Estrategia | Re-ejecutable |
|---------|-----------|---------------|
| 01 episodes | `INSERT OR IGNORE` (PK = `episode_id`) | sí |
| 02 memory | `INSERT OR IGNORE` (PK = `(project, name)`) | sí |
| 03 context_contracts | `INSERT OR IGNORE` (PK = `(project, section_name)`) | sí |
| 04 harness_events | `INSERT` simple (sin PK natural) | **no — duplica filas** |

Para re-ejecutar 04 limpiamente:

```
sqlite3 ~/.gaia/gaia.db "DELETE FROM harness_events WHERE project='me';"
./migrate_04_harness_events.sh
```

## Validación

`validate.sh` corre 5 aserciones read-only contra `~/.gaia/gaia.db`:

| # | Check |
|---|-------|
| V1 | `COUNT(*) FROM episodes` == líneas no vacías de `episodes.jsonl` |
| V2 | `COUNT(*) FROM memory` == archivos `.md` (excluyendo `MEMORY.md`) |
| V3 | `COUNT(*) FROM context_contracts` == 12 |
| V4 | `COUNT(*) FROM harness_events` == líneas no vacías de `events.jsonl` |
| V5 | `COUNT(*) FROM episodes_fts` == `COUNT(*) FROM episodes` (FTS sync) |

Exit code: 0 si todas pasan, 1 si alguna falla.

## Cleanup

Una vez que `validate.sh` reporte `ALL PASS` y los datos se hayan absorbido en
los flujos normales de Gaia, todo el directorio `tools/migration/` se borra:

```
rm -rf /home/jorge/ws/me/gaia/tools/migration
rm -f  /tmp/migrate_0{1,2,3,4}_*.sql
```

Estos scripts son one-shot; no forman parte del runtime de Gaia.

## Notas y restricciones

- `set -euo pipefail` en todos los `.sh`.
- Los `.py` **no** importan `sqlite3` ni abren conexiones a la DB.
- Solo I/O de filesystem y escritura de SQL plano a `/tmp/`.
- `bash -n` y `python3 -m py_compile` deben pasar para todos los archivos.
- Project hardcoded a `'me'` en los 4 dominios.
- Comentarios internos en español.
