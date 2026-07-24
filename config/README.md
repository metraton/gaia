# Config

Configuration lives here, separate from hooks, because these are data files — not code. Hooks are Python scripts that run at runtime; config files are documents those scripts read to make decisions. Keeping them apart means you can audit and change system behavior without touching executable code.

**Surface routing is no longer a file in this folder.** It moved to the DB-backed `surface_routing` table in `~/.gaia/gaia.db`, whose source of truth is each agent's `routing:` frontmatter block (`agents/*.md`). At install time `tools/scan/seed_surface_routing.py` reads those frontmatters and seeds the table (a mirror of `seed_contract_permissions.py`); at request time `tools/context/surface_router.py` reads the table via `load_surface_routing_config()`. The retired `config/surface-routing.json` used to hold this table.

## When activated

This folder has no single activation event. Each file is read on-demand by the module that owns it. Surface routing itself is now read from the DB:

```
User submits a prompt
        |
tools/context/surface_router.py calls load_surface_routing_config()
        |
Reads the surface_routing table in ~/.gaia/gaia.db
        |
Returns surface match + recommended agent injected into orchestrator context
```

If the DB or table is absent/empty (a not-yet-seeded workspace), `load_surface_routing_config()` returns a degraded config (`"version": "missing"`) and routing falls back to the `reconnaissance_agent` default.

## What's here

```
config/
└── README.md
```

The folder currently holds only this README; it remains for future data files and stays symlinked into `.claude/config`.

## Conventions

**Routing schema (frontmatter → DB):** Each agent that owns a surface declares a `routing:` block in its `agents/*.md` frontmatter with `surface` (name), `adjacent_surfaces`, a `signals` block carrying `commands`/`artifacts`, `required_checks`, and optional `sub_surfaces` (for a surface that splits by owner, e.g. `planning_specs` → brief owned by the orchestrator via `brief-spec`, plan owned by `gaia-planner`). Keywords were retired as a signal source — `surface_router.py::_score_surface` scores `commands` and `artifacts` only, and a legacy `keywords` key in a signals block is ignored. The surface's `intent` is the agent's `description` field (not duplicated); `contract_sections` is derived from the agent's `project_context_contracts.read` (single source of truth). `surface_router.py` scores surfaces by whole-token matching task text against the signal lists; the highest-scoring surface wins.

**Git commit standards (not in this folder):** The Conventional Commits rules are inlined as module-level constants (`TYPE_ALLOWED`, `SUBJECT_MAX_LENGTH`, `SUBJECT_RULES`, `BODY_MAX_LINE_LENGTH`, `ENFORCEMENT`) in `hooks/modules/validation/commit_validator.py`.

**Adding a new surface:** Add a `routing:` block to the owning agent's frontmatter, add the agent to `build/gaia.manifest.json`, and re-run `gaia install` to re-seed the `surface_routing` table. Update the surface-router tests.

## See also

- [`tools/context/surface_router.py`](../tools/context/surface_router.py) — reads the DB-backed `surface_routing` table via `load_surface_routing_config()`; the routing pillar runtime consumer
- [`tools/scan/seed_surface_routing.py`](../tools/scan/seed_surface_routing.py) — install-time generator: agent frontmatters → `surface_routing` table (mirror of `seed_contract_permissions.py`)
- [`agents/`](../agents/) — the `routing:` frontmatter blocks are the source of truth for surface routing
- [`hooks/modules/validation/commit_validator.py`](../hooks/modules/validation/commit_validator.py) — enforces Conventional Commits; standards inlined as module-level constants
