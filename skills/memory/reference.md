# Memory — Reference

Exact mechanics for the `memory` technique. Load this when materializing,
debugging, or auditing memory; the judgment and ownership flow stays in
`SKILL.md`. This file holds the write path (scope, the write form and its
warnings) and retrieval; three sibling files hold the rest, each loaded only
when its subject is the question:

- `reference-access-telemetry.md` -- the three read counters, which surface
  bumps which, and what reads them back.
- `reference-defect-promotion.md` -- the `gaia_system` initiative shape for a
  defect that must outlive the 90-day anomaly floor.
- `reference-curation.md` -- the curate flow (lifecycle, links, dedup, pruning,
  splitting), the `append` / `edit` / `checkpoint` worked forms, history
  coverage, and graph behavior.

## Project-scoped memory: reference `project_ref`, not the workspace

Memory rows are keyed by `(workspace, name)`, but a workspace is a
container that can be renamed, split, or hold a project that later
moves elsewhere (scan-v2: a project row re-keyed by a `movido`
adjudication carries `superseded_by`, but the memory row stays under
its original `workspace` key unless a human runs `move-memory`). A
`project_*` note that means "this is true of project X" should record
that fact durably rather than only implicitly through the workspace it
happens to live in today.

Folding a whole workspace into another is `gaia workspace retire <source>
--into <target>` (T3; `--dry-run` reports per table first). It re-keys every
curated row: `type=user` to `_gaia_user`, host-scoped to `_gaia_host`, the
rest to the target, with the links whose endpoints land together; a link
whose endpoints land in different scopes is dropped into the undo ledger.
Episodes and the other history rows keep the source name and are read
through the recorded alias, and `--workspace <source>` then resolves to the
target. A name collision aborts the whole retire unless `--on-conflict
memory=keep-target|keep-source` settles it; `--undo <ledger>` reverses it.

`memory.project_ref` (schema v25, scan-v2 SV1) is the stable anchor for
this: it should hold the project's `project_identity` -- the same
vantage-independent identity scan writes onto `projects.project_identity`
(git-common-dir realpath > normalized remote > realpath path). A note keyed
this way remains correctly attributed even after the project physically
moves workspace -- the `project_ref` value does not change on a move, only
the `projects` row's `(workspace, name)` does. A `project_*` note about the
workspace as a whole (not a single project within it) legitimately leaves
`project_ref` NULL.

**Required scope (deterministic, no guessing).** `gaia memory add` requires
**at least one** explicit scope flag -- `--project` (preferred) or
`--workspace`. It never writes with project and workspace both empty: that
would leave `project_ref` NULL purely for lack of input. The function does
**not** infer scope from the cwd and does **not** fall back silently. Scope
inference from natural language ("the century project") is the
**orchestrator's** job, not the function's -- the function only accepts
explicit, resolvable scope.

- `--project=<name>` resolves the name within `--workspace` to that project's
  `projects.project_identity` and persists it as `memory.project_ref`:

  ```bash
  gaia memory add --name=project_x_status --type=project \
    --project=x --workspace=me --body="..."
  ```

- `--project-ref=<identity>` anchors directly to a known identity string
  (scripting across workspaces); mutually exclusive with `--project`.
- `--workspace=<ws>` alone (no project flag) is the **explicit degraded
  lane**: a legitimate workspace-scoped note with `project_ref` NULL and
  exit 0. A `project_*` note about the workspace as a whole lives here. The
  write still lands but warns `no_owner` when no `--initiative` names a
  project either: a container is not one of the three owners.

**Errors are structured and machine-parseable** so the orchestrator can run
the command, read the failure, and *manage* it deterministically instead of
guessing. Every failure exits non-zero (1) and, with `--json`, prints
`{"error": "...", "code": "<code>", ...}` (text mode prints
`Error [<code>]: ...` to stderr). On any of them the row is **not** written --
there is no partial or silent-NULL write:

| `code` | Cause | How the orchestrator manages it |
|--------|-------|---------------------------------|
| `missing_scope` | Neither `--project` nor `--workspace` given | Re-run with `--workspace` (degraded lane), or resolve a project and re-run with `--project`. |
| `project_unresolved` | `--project=<name>` does not exist in the workspace | Ask the user which project, or list `projects` and retry. |
| `project_workspace_mismatch` | `--project` exists, but under a different workspace (see `found_in`) | Re-run with a workspace from `found_in`, or correct the project name. |
| `project_no_identity` | Project exists but has no `project_identity` yet | `gaia scan` first, then retry. |

When `--project` resolves, the note is anchored: `memory.project_ref` = the
project's durable identity.

## The write form and its warnings

The shape `SKILL.md` asks of a row is taught by the CLI at write time;
every number and list here is read from the symbol named, so one edit moves
both.

- **Claim vocabulary** -- `gaia/store/memory_claims.py::MEMORY_CLAIM_KINDS`,
  rendered into `gaia memory add --help`. The first word of a description
  names the kind; `gaia/store/memory_claims.py::memory_claim_kind` reads it
  without case, accents or a trailing colon. The vocabulary is open: another
  first word is allowed and simply has no kind.
- **Warnings** -- `bin/cli/memory.py::_add_warnings` and the supersedes check
  in `bin/cli/memory.py::_cmd_link`. Each prints `aviso: ...` to stderr (with
  `--json`, a `warnings: [{code, message}]` list) and the row is still
  written; the refusals above are unchanged.

| `code` | When | Threshold / reason |
|--------|------|--------------------|
| `description_long` | description over `bin/cli/memory.py::_DESCRIPTION_WARN_CHARS` | listings and the birth block show only the description |
| `body_long` | body over `bin/cli/memory.py::_BODY_WARN_CHARS` | a body is injected whole or dropped |
| `no_owner` | a non-user row with neither a project nor an initiative | a workspace is a container, not an owner |
| `rewrite_in_place` | `add` over an existing name with another body | a change is a new row plus `link <new> <old> --kind=supersedes` |
| `preference_or_bug` | a `type=user` row whose description's kind is `Preferencia` | asks whether it would hold if Gaia worked perfectly |
| `supersedes_reversed` | `link --kind=supersedes` whose dst was born after its src | the arrow goes from the new row to the old |

Every text-mode write (`add`, `append`, `edit`, `reclassify`, `link`) closes
with `bin/cli/memory.py::_WRITE_POINTER`, the write-side twin of the read
pointer. Whether a preference is really a harness rule is left to the
writer's judgment: no check reads the body for it.

Anchoring is **forward-only, by design**. Rows written before this
mechanism existed stay `project_ref IS NULL` -- the memory-row-to-project
mapping is genuinely ambiguous whenever a workspace hosts more than one
project, so no backfill can guess it. A `project_*` row gets anchored only
by an explicit `--project` / `--project-ref` at write time.
`gaia/store/writer.py::upsert_memory` treats `project_ref` with coalesce-or-omit discipline:
omitting it on a later update never clobbers a previously-set anchor back to
NULL (a later `add` that re-supplies only `--workspace` keeps the prior
anchor).

**Retrieval is cwd-INDEPENDENT (schema v32, commit `d2fba1c`).** An earlier
form of `gaia memory get-relevant` (`_cmd_get_relevant`) resolved an "active
project" from the launch directory and used it to restrict/reorder results
(`project_ref = active` prioritized, a *different* project's rows dropped
out). That cwd-based project inference has been **removed** -- the launch
directory no longer filters, restricts, or reorders anything. `_cmd_get_relevant`
now dispatches on explicit flags only, workspace-scoped, never per-project by
cwd:

- **(no flag, the default)** -- the TRANSVERSAL DIGEST (`_render_digest`): a
  cross-project worklist of live-pending threads (`class=thread`, `status` in
  `carry_forward`/`open`), grouped by the canonical `memory.initiative` key,
  identical regardless of the directory the session started in. This is the
  orchestrator's SessionStart view.
- **`--sections=carry_forward,anchor,thread_open`** -- the class/status
  SECTION renderer (`_render_sections`); this is the subagent-dispatch path
  (`--sections=anchor` gives a dispatched subagent only the durable "About
  you / What I know" anchors). Workspace-scoped, never filtered or
  prioritized by the launch directory.
- **`--initiative=X`** -- PROJECT MODE (`_render_project_mode`): the explicit
  replacement for the old implicit cwd guess. Rather than inferring which
  project is "active" from where the command was launched, the caller names
  the initiative directly (normalized the same way the write side stores it,
  via `normalize_initiative`); the special value `otros` targets the
  NULL-initiative bucket. Returns the WHOLE live-pending corpus of that one
  initiative -- no top-N cap, no overflow footer, `body` projected alongside
  `class`/`status`, and `description` verbatim (uncapped, no ellipsis).
  `--max-chars` is accepted and ignored here. The attention cap that governs
  the digest and section renderers exists because THEY feed an unrequested
  SessionStart block, where the budget is scarce; applying the same cap to a
  corpus the caller explicitly asked for, for triage, would silently
  withhold part of the answer it was asked to return. This is deliberate --
  do not reintroduce the cap here to make the two modes "consistent."
- **`--types=...`** -- the legacy per-type flow (`_cmd_get_relevant_by_type`),
  kept verbatim for back-compat; also workspace-scoped only.

The *workspace* itself (not the project within it) may still be cwd-inferred
when `--workspace` is omitted -- `_resolve_workspace` falls back to
`gaia/project.py::current`, same as the write side's default -- but that is
workspace identity, not project-level filtering/reordering, and it was never
the "active project" mechanism this note is about.

This closes an asymmetry the earlier form had: read-side cwd inference used
to be justified as "read-only and deliberate... a wrong guess only re-ranks
what is shown," in contrast with the write side, which has always refused to
infer project scope from the cwd (see "Required scope" above). With the cwd
guess removed from retrieval too, both sides now share the same discipline:
neither infers project scope from the launch directory -- `add` demands an
explicit `--project`/`--project-ref`/`--workspace`, and `get-relevant` demands
an explicit `--initiative` to narrow to one project's pending work.

## Digest and anchor budgets: query mechanics

`class=anchor` and `class=thread status=carry_forward` surface through
two separate SessionStart queries, each with its own budget. `SKILL.md`
keeps the practical consequence -- an anchor never reaches the digest;
this section holds the query mechanics behind it.

- **The digest (`carry_forward`/`open`) never carries anchors at all.**
  Its query filters `class='thread' AND status IN ('carry_forward',
  'open')`, so an `anchor` row is invisible to it regardless of budget.
  Its own overflow mechanism trims whole initiatives from the tail
  (top-K initiatives, with a global "+N más" and a per-initiative "+N
  más en X" hint) when the ~1500-char cap is exceeded -- it never
  competes with anchors for that budget.
- **The anchor call (`sections=["anchor"]`) never carries pendings at
  all.** Its query filters `class='anchor'` only, and is additionally
  capped at a small fixed quota (`_RELEVANT_PER_CLASS_QUOTA["anchor"]`,
  identity anchors pinned first, then most-recently-updated) before it
  is even rendered -- independent of and much tighter than the
  digest's own worklist budget.
- **A three-way, single-call trim order also exists in the CLI** --
  `gaia memory get-relevant --sections=carry_forward,anchor,thread_open`
  (one call, all three sections) trims one bullet at a time in the
  fixed order `thread_open` → `anchor` → `carry_forward` when the
  combined render overflows the char cap. No live caller requests that
  three-section combination today; it is reachable only by an explicit
  manual invocation.
