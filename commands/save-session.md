---
description: Persist the current Claude session (bundle + active context) to disk.
---

This command saves the complete current session into a new bundle with all context, artifacts, and git operations captured. Use it whenever you want a comprehensive snapshot of your work before closing the workspace.

## Quick command (recommended)

### Default session bundle
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/create_current_session_bundle.py
```

Creates: `2025-10-16-session-163244-abc12345`

### Custom labeled bundle
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/create_current_session_bundle.py --label agent-upgrades
```

Creates: `2025-10-16-agent-upgrades-163244-abc12345`

**Options:**
- `--label LABEL` - Add a custom descriptive label to the bundle name
- `--no-git-ops` - Skip capturing git operations

**Common labels:**
- `agent-upgrades` - Agent system improvements
- `feature-xyz` - Feature implementation
- `bugfix-abc` - Bug fix work
- `infrastructure` - Infrastructure changes
- `refactor` - Code refactoring

**Features:**
- Creates a brand new bundle with unique timestamp ID
- Captures complete session context: git operations, modified files, conversation summary
- Copies important artifacts and active context for full restoration capability
- Generates comprehensive metadata and human-readable summary template
- Ready for sharing, archival, or future restoration

**⚠️ Important: Update the Summary**
After creating a bundle, **manually update** the `summary.md` file to capture:
- Key accomplishments of the session
- Specific technical changes made
- Important decisions or findings
- Relevant next steps

The auto-generated summary is a template. For accurate session documentation,
edit: `$PROJECT_ROOT/.claude/session/bundles/<bundle-id>/summary.md`

## Alternative: Legacy snapshot (updates existing bundle)
```bash
python3 $PROJECT_ROOT/.claude/session/scripts/save_session_snapshot.py
```

- Updates an existing bundle with current active context only
- Use `--bundle-id=<id>` to target a specific bundle

## Flujo detallado (opcional)
Si prefieres hacerlo paso a paso o necesitas crear un nuevo bundle antes de guardar:

1. **Crear/actualizar bundle**
   Ejecuta cualquier comando del agente (p.ej. `/analizer T010`) para que el hook `subagent_stop` genere un bundle fresco.

2. **Inspeccionar bundles disponibles**
   ```bash
   python3 $PROJECT_ROOT/.claude/session/scripts/session-manager.py list
   ```

3. **Copiar contexto activo al bundle deseado**
   ```bash
   python3 $PROJECT_ROOT/.claude/session/scripts/save_session_snapshot.py --bundle-id <bundle-id>
   ```

4. **(Opcional) Empaquetar para compartir**
   ```bash
   tar -czf $PROJECT_ROOT/.claude/session/bundles/<bundle-id>.tar.gz \
     -C $PROJECT_ROOT/.claude/session/bundles/<bundle-id> .
   ```

## Notes
- The exported tarball contains the bundle (metadata, transcript, artifacts).
- Copying `session/active` preserves the live context (primers, json/md summaries).
- You can restore later with:
  ```bash
  python3 .claude/session/scripts/session-manager.py import <path-to-bundle-tar>
  ```
- Keep exports outside of version control unless you intend to commit them.
