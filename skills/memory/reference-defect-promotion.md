# Memory — Promoted defect: the `gaia_system` initiative shape

The exact row, body and promotion path for a defect that must outlive the raw
anomaly floor. Load this when promoting a Gaia defect, or when reading the
`gaia_system` corpus back. The ownership split that decides who promotes is in
`SKILL.md`; the general write path is in `reference.md`.

`episode_anomalies` is the raw defect floor. It is written unrequested at
SubagentStop and it prunes itself to 90 days by cascade from `episodes`,
because most of what lands there is noise with an expiry date. A defect
that must outlive that window is **promoted** into curated memory under
`initiative='gaia_system'`, where one read
(`gaia memory get-relevant --initiative=gaia_system --json`) returns the
whole corpus for triage without reopening the sessions that produced it.

Promotion is a curation act, governed by the ownership split in `SKILL.md`
("Who writes"): the orchestrator decides what earns it, `gaia-operator`
executes the write. A dispatched subagent never promotes -- `subagent_memory_write_guard`
rejects `gaia memory add` from any dispatch other than `gaia-operator`,
categorically and unapprovably (no `approval_id`, not a T3 grant). Its
participation is to PROPOSE (a `failure_report` in its contract, or a
`memorialize_suggestions` entry).

## The row: existing flags only, no new schema

| Flag | Value | Why it is fixed |
|------|-------|-----------------|
| `--name` / `--type` | `feedback_<component>_<symptom>` / `feedback` | The slug prefix IS the type. `feedback` is the type for a post-mortem the system must remember, and it is one of `MEMORIALIZE_VALID_TYPES`, so a subagent can propose it. |
| `--class` | `thread` | A defect is open work, not background knowledge. Only `class=thread` is visible to the initiative digest's query. |
| `--status` | `carry_forward` | The digest selects `status IN ('carry_forward','open')`; `carry_forward` states the defect must reach the next session, overriding the `log` default that `feedback_*` would otherwise take. |
| `--initiative` | `gaia_system` | The one grouping key that makes the corpus retrievable in a single query. Normalized to lowercase_snake by the writer. |
| scope | forced to the host sentinel | `gaia_system` is in `HOST_SCOPED_INITIATIVES`, so `apply_host_scope` (`gaia/store/writer.py::apply_host_scope`) overrides whatever `--workspace` was passed and writes into the sentinel workspace `_gaia_host` instead -- the CLI still accepts `--workspace`, it just is not honored (the write reports the override: `bin/cli/memory.py::_cmd_add`'s `host_scoped_notice`). `--project`/`--project-ref` is not merely discouraged, it is REJECTED: combining it with a host-scoped initiative raises `MemoryHostScopeError` (`code=host_scope_no_project`) before any row is written. |
| `--description` | one line, the symptom in the reader's words | Listings and the digest render `description`, not the body. |

Nothing here is new schema. `initiative`, `class`, and `status` are columns
that already exist and flags `gaia memory add` already accepts. A dedicated
`type=defect` was rejected deliberately: it would require a migration and a
change to the `memory.type` CHECK, while `initiative='gaia_system'` costs
neither.

## The body: four named fields, fixed order

The body is structure, not prose. Exactly four second-level headings, spelled
and ordered as below:

```markdown
## Symptom
What was observed, stated as the observable fact -- not the diagnosis.

## Component
The owner of the defect, as `file + symbol`, a CLI verb, or a hook module.
One target, named precisely enough to open.

## Evidence
The observed proof, verbatim: command output, a query result, an anomaly id.
One bullet per item; do not paraphrase what was captured.

## Reproduction
The exact command or sequence that makes the defect appear again, with
absolute paths.
```

Four rules make that a shape rather than a suggestion:

- All four headings are present, spelled exactly, in that order.
- A field that is genuinely unknown carries the single word `unknown`. An
  omitted heading is a malformed defect, not an empty one -- a reader
  splitting the body cannot tell absence from oversight.
- No other `##` heading appears in the body. Deeper structure inside a
  field uses bullets or `###`.
- Recovery is therefore a split of the body on `^## `, yielding the four
  fields in a fixed order. That is what "recoverable without reopening the
  session" means, and it is why the headings are not negotiable per author.

## The promotion path

Two sources feed the corpus, and both converge on the SAME row because the
flags above are constants of the path, not choices of the promoter.

**Source A -- a raw floor anomaly.** An `episode_anomalies` row of type
`agent_reported_defect` (`hooks/modules/agents/defect_capture.py`,
`DEFECT_ANOMALY_TYPE`), whose payload carries `agent`, `attempted`,
`symptom`, `component`, `evidence` and a severity. Map it:

| Body field | Comes from |
|------------|-----------|
| `## Symptom` | `attempted` + `symptom`, as "attempted X; Y broke" |
| `## Component` | `component`, which is optional in the report -- `unknown` when null |
| `## Evidence` | `evidence[]`, one bullet per item, verbatim |
| `## Reproduction` | **Not carried by the raw floor.** Supplied at promotion. |

That last row is why promotion is a deliberate act and not a copy job: the
capture channel records what an agent observed, and nobody can re-run a
defect from that alone. Adding the reproduction is the work promotion does.
The anomaly's `severity` does not become a body field -- it informs whether
the defect is worth promoting at all.

**Source B -- a subagent proposal.** A `memorialize_suggestions` entry with
`type: "feedback"` whose `body` already carries the four headings. The
suggestion block carries `slug`, `type`, `class`, `description`, `body` and
`rationale` -- it carries neither `status` nor `initiative`
(`MEMORIALIZE_VALID_TYPES` and the copied keys in
`hooks/modules/agents/response_contract.py`). Those two are supplied by the
writer from the table above, which is exactly what keeps two promotions from
two different agents landing in the same shape.

The write, identical for both sources:

```bash
gaia memory add \
  --name="feedback_<component>_<symptom>" \
  --type=feedback \
  --class=thread \
  --status=carry_forward \
  --initiative=gaia_system \
  --workspace="<ws>" \
  --description="<one-line symptom>" \
  --body-file=-
```

Read back with `gaia memory show <slug> --json` (single row) or
`gaia memory get-relevant --initiative=gaia_system --json` (the corpus).

## Promotion is one-way

The two floors have opposite retention. `episode_anomalies` is pruned at 90
days by cascade from `episodes`; curated memory has no retention at all --
only a soft delete, with `trg_memory_history` archiving every prior version.
So a promoted defect is permanent by construction, and there is no demotion:
nothing carries a curated row back down to a floor that expires, and deleting
it is discouraged by the same convention that governs every other row.

The consequences to hold before promoting:

- **Promote what deserves permanence**, not everything that failed. The raw
  floor already keeps the noise for 90 days and `gaia metrics` already
  aggregates it; promotion is for the defect a future session must be able
  to pick up as work.
- **A resolved defect ends by `status`, never by deletion** --
  `gaia memory reclassify <slug> --status=closed` (no longer relevant) or
  `--status=graduated` (fixed, or promoted to an anchor). It leaves the
  digest and stays in the corpus.
- **Re-promoting an existing slug is refused** -- `add` over a name that
  already exists in the workspace fails with `name_exists`. A defect whose
  understanding changed is a new row linked with
  `gaia memory link <new> <old> --kind=supersedes`; `--replace` is only for
  correcting an error. Search before promoting.
