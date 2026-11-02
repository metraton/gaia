# tools/context

Reads a project's stored context and the routing table out of `gaia.db` for the dispatch kernel, the CLI and the routing simulator.

Every dispatched agent is born with a kernel that names its project and carries the context sections its agent may read. The data behind both lives in `~/.gaia/gaia.db`: `project_context_contracts` holds each project's sections, `agent_contract_permissions` holds which sections each agent may read and write, and `surface_routing` holds the routing signals. This folder is the read side of those tables, kept apart from the hooks so the CLI and the simulator can use the same answers without loading the hook runtime.

Nothing here writes. The tables are filled by `gaia scan` and the install seeders (`tools/scan/seed_contract_permissions.py`, `tools/scan/seed_surface_routing.py`), from each agent's `project_context_contracts:` and `routing:` frontmatter; a correction an agent proposes reaches them through `gaia context accept`, not through this folder.

## How it is wired in

Consumed. Each caller imports one function:

- `hooks/adapters/tool_policy.py` calls `context_provider.py::resolve_dispatch_project` and `context_provider.py::build_kernel_sections` when a dispatch is born; `hooks/modules/context/kernel_builder.py` calls `dispatch_project_entry` and `dispatch_project_key` to render it, and `hooks/adapters/subagent_stop_core.py` calls `dispatch_project_workspace` at the close.
- `bin/cli/context.py` calls `context_provider.py::name_variants` to match project names.
- `bin/cli/doctor.py` and `tools/gaia_simulator/` call `surface_router.py::load_surface_routing_config`; the simulator also calls `classify_surfaces`.

What breaks if you get it wrong here: a change to how a project or its sections are resolved changes what every newly born agent is told about its project, on both hosts, with no error anywhere -- the agent simply works from different context. `tests/tools/test_context_provider.py` covers the provider.

## What's here

```
__init__.py           re-exports the provider and router functions
context_provider.py   project lookup by name, slug or path; the kernel sections a dispatch gets; section permissions; a debugging CLI
surface_router.py     the surface_routing table loaded into memory, task-to-surface scoring, and the investigation brief
_paths.py             resolve_config_dir(), which nothing imports any more
```

`context_provider.py` can be run by hand to print the full payload it would assemble for an agent, task-to-surface routing and investigation brief included:

```bash
python3 tools/context/context_provider.py platform-architect "Create a VPC" --workspace me
```

That payload (`build_context_payload`) is reached only from this command; a dispatched agent gets the kernel from `build_kernel_sections`, which carries no routing, because the routing decision is already taken by the time the agent starts.

## Conventions

Which sections an agent reads or writes is declared in that agent's `project_context_contracts:` frontmatter under `agents/`, not here; this folder only reads the seeded table. `agent_contract_permissions` rows with a NULL `cloud_scope` apply to every provider, and a row with a provider applies only to it (`context_provider.py::load_provider_contracts`). The routing scorer matches only the `commands` and `artifacts` signals of a surface, as whole tokens.

## See also

- [`../scan/`](../scan/) -- the scanners and seeders that fill these tables.
- [`../../hooks/modules/context/`](../../hooks/modules/context/) -- the kernel builder that renders what this folder returns.
- [`../../agents/README.md`](../../agents/README.md) -- the frontmatter that declares each agent's sections and routing.
