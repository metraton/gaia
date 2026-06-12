# Config

Configuration lives here, separate from hooks, because these are data files — not code. Hooks are Python scripts that run at runtime; config files are JSON documents that those scripts read to make decisions. Keeping them apart means you can audit and change system behavior (what git commit patterns are allowed, which surfaces route where) without touching executable code. It also makes the config files version-controllable and reviewable on their own terms.

The routing table is consumed at a well-defined point in the request lifecycle: on every user prompt. It is not loaded eagerly at startup — it is parsed exactly when `surface_router.py` is invoked. (Git commit standards used to live here too, in `git_standards.json`; they are now inlined as module-level constants in `hooks/modules/validation/commit_validator.py` — the single runtime consumer of those rules — so this folder no longer owns them.)

## When activated

This folder has no single activation event. Each file is read on-demand by the module that owns it.

```
User submits a prompt
        |
hooks/user_prompt_submit.py fires
        |
tools/context/surface_router.py calls load_surface_routing_config()
        |
Reads config/surface-routing.json
        |
Returns surface match + recommended agent injected into orchestrator context
```

If `surface-routing.json` is absent, `load_surface_routing_config()` returns a degraded config (`"version": "missing"`) and routing falls back to the `reconnaissance_agent` default.

## What's here

```
config/
├── surface-routing.json   # Surface→agent routing table; consumed by tools/context/surface_router.py on every prompt
└── README.md
```

## Conventions

**surface-routing.json schema:** Top-level keys are `version`, `reconnaissance_agent`, and `surfaces`. Each surface entry has `intent` (human description), `primary_agent` (agent id to dispatch), `adjacent_surfaces` (list of related surface names), `contract_sections` (context sections injected for that surface), `required_checks` (agent checklist), and `signals` with `keywords`, `commands`, and `artifacts` sub-lists. `surface_router.py` scores surfaces by matching task text against all signal lists; the highest-scoring surface wins.

**Git commit standards (no longer in this folder):** The Conventional Commits rules — allowed types, subject/body length limits, subject rules — are inlined as module-level constants (`TYPE_ALLOWED`, `SUBJECT_MAX_LENGTH`, `SUBJECT_RULES`, `BODY_MAX_LINE_LENGTH`, `ENFORCEMENT`) in `hooks/modules/validation/commit_validator.py`. Forbidden-footer detection lives, hardcoded, in `bash_validator`. To add a new commit type, edit `TYPE_ALLOWED` in `commit_validator.py`.

**Adding a new surface:** Add an entry to `surfaces` in `surface-routing.json` with all required sub-keys. Update L1 routing tests and the `gaia_system` signals if the new surface involves Gaia components.

## See also

- [`tools/context/surface_router.py`](../tools/context/surface_router.py) — loads and scores `surface-routing.json`; the routing pillar source of truth
- [`hooks/user_prompt_submit.py`](../hooks/user_prompt_submit.py) — calls `classify_surfaces` from `surface_router` on every prompt
- [`hooks/modules/validation/commit_validator.py`](../hooks/modules/validation/commit_validator.py) — enforces Conventional Commits on every `git commit`; standards inlined as module-level constants
- [`skills/git-conventions/`](../skills/git-conventions/) — the agent-facing skill teaching the commit conventions
