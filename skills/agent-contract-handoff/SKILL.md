---
name: agent-contract-handoff
description: Use for the exact input/output schema and validation rules of agent_contract_handoff
---

# Agent Contract Handoff Reference

This skill owns schema, not workflow. Use `agent-protocol` to produce a turn and
`agent-response` to consume it. The envelope body is JSON, never YAML.

**Two senses of "contract" -- do not collapse them.** This skill's "contract"
is the *handoff* envelope: one row per turn, born at dispatch under the
injected `# Your Contract` block, mutated by `gaia contract set/add/fill`,
closed by `gaia contract finalize`. A *project context contract* is a
different thing -- a slice of project knowledge stored per workspace
(`project_context_contracts`, seeded by `seed_contract_permissions.py`). It
reaches the turn as the kernel's `# Project context` block: the dispatch
project's `can_read` sections (from `agent_contract_permissions`), as far as
the budget allows; the rest is pulled on demand (the verb that reaches them,
and the same-named sibling that reads a different table entirely, are in
`agent-protocol/read-map.md`). Updates to any section are proposed via
`update_contracts`. When a message says "contract" without
qualifying it, ask which one it means before assuming.

## Draft creation is implicit

A turn born at dispatch already has a row in `agent_contract_handoffs` AND a
pre-created on-disk draft (`dispatch_binding._precreate_draft` writes
`gaia.contract.drafts.initial_envelope` at birth) before it runs anything --
no prior `gaia contract init` call is required. Should the draft file be
missing anyway, the first `gaia contract set/add/fill/view --draft-id
<draft_id>` against that same, already-born `contract_id` materializes it
(`bin/cli/contract.py::_maybe_adopt_draft`). Two conditions gate it, and both must
hold: the id's agent-id prefix matches `AGENT_ID_PATTERN_TEXT`, and a row
already exists for that exact `contract_id`
(`gaia.store.writer.agent_contract_handoff_exists`). Neither condition mints
anything -- a well-formed id with no row behind it still fails with the same
"No draft found... run init" error as before this existed. `gaia contract
init` remains, unchanged, the explicit path for a turn that received no
injected identity at all. `gaia contract validate` never triggers this: it is
documented as never mutating the draft, so it never materializes one either.
See `agent-protocol` for when in the turn's cycle this first write happens.

## Input context

The injected input is the dispatch kernel: `# Your Contract` (identity, goal,
role/surface, `project` with its declared `workflow` line when the project
declares one, `can_read`/`can_write`, and -- on a plan-task-bound turn -- the
acceptance gates), `# How the user works` (only the user's rows marked
`executor`), `# Standing notes` (the project's live anchors, one line each, when
it has any), `# Environment` (one line: OS, the gaia CLI path, the tools on
PATH including the user's own executables), `# Project context` and
`# Your CLI`. `# Project context` carries the dispatch project's `can_read`
sections whole when they fit, else one `get-contract` pointer per section,
else a single count line; it is absent when the row's project does not
resolve, none of its `can_read` sections is stored, or the read fails
(`hooks/modules/context/kernel_builder.py::build_project_context_block`).
No surface routing arrives. Pull what the block only points at on demand
before querying anything wider (`agent-protocol/read-map.md`).
`update_contracts` may propose any section: `can_write` does not gate a
proposal, the orchestrator decides (`hooks/modules/context/context_writer.py`).

## Minimal increment

Every draft, including a mid-turn checkpoint, contains:

- `agent_status.agent_state`, `agent_status.agent_id`, `pending_steps`, and a
  non-empty `next_action`;
- `evidence_report` with all seven keys: `patterns_checked`, `files_checked`,
  `commands_run`, `key_outputs`, `verbatim_outputs`, `cross_layer_impacts`, and
  `open_gaps` (empty lists are valid, though on Claude Code a finalized row
  whose `commands_run` or `files_checked` is empty gets the turn's Bash
  commands and Read paths written in at the close, each prefixed
  `[recovered from transcript] `: the bare call, never what it showed);
- `consolidation_report` and `approval_request`, normally `null`.

`agent_id` matches `^a[0-9a-f]{16,}$`. The canonical states are
`IN_PROGRESS`, `APPROVAL_REQUEST`, `BLOCKED`, `NEEDS_INPUT`,
`NEEDS_VERIFICATION`, and `COMPLETE`. Only `COMPLETE` is terminal.

## Which fields are lists, and what a second write does

Eight fields hold the lists you write every turn: the seven `evidence_report`
keys above -- `patterns_checked`, `files_checked`, `commands_run`,
`key_outputs`, `verbatim_outputs`, `cross_layer_impacts`, `open_gaps` -- plus
`agent_status.pending_steps`. They are not the only lists in the envelope:
`update_contracts` is list-valued too (`ADVISORY_UNTYPED_FIELDS` in `gaia/contract/validator.py` --
allowlisted, and deliberately left untyped so a malformed one never blocks a
close). Nothing about them is unguarded, which is the next thing a reader asks
once they know they exist: the overwrite screen is generic, firing on any
populated list a patch would displace, and it starts at the envelope root where
these three sit. They are seeded absent rather than as `[]`, so a first write to
one discards nothing.

The three write verbs differ on every one of those lists, and the difference is
not cosmetic -- it decides whether your earlier evidence survives:

- **`fill` writes a list only while that list is still EMPTY.** A patch that
  would discard entries already there is refused WHOLE: non-zero exit, nothing
  written, every colliding field named with its existing and incoming counts.
  It neither appends nor replaces; where the intent is ambiguous it makes you
  state which one you meant. Re-sending a patch whose list is byte-identical to
  what is stored is not a collision, so a corrected patch may be re-issued.
- **`add` EXTENDS a list by one entry.** This is the verb for the second,
  third and fourth batch of evidence into the same field.
- **`set` REPLACES a list outright, unguarded.** This is the verb for
  correcting a field you already populated, and for clearing one to `[]`.

Writing your evidence across several calls is the NORMAL path, not an edge
case: one call carrying a full envelope routinely exceeds the shell's
command-length limit (`fill --json-file` is the channel for long prose). So
write each list field with `fill` at most ONCE, spread `fill` calls across
DIFFERENT fields, and switch to `add` the moment you return to a field you
already wrote.

## State-conditional close requirements

- `APPROVAL_REQUEST`: `approval_request` is non-null and
  `approval_request.exact_content` is non-blank. All approval-set data stays in
  this object; see `subagent-request-approval`.
- `COMPLETE`: `pending_steps` is `[]`, `next_action` is exactly `done`, and
  `evidence_report.verification.result` is `pass`.
- plan-task-bound producers do not self-complete. They close their increment as
  `NEEDS_VERIFICATION`; the verifier is bound through `parent_handoff_id`.
- `consolidation_report` is required when input marks consolidation,
  cross-check, or multi-surface work.
- Every clean close, in any closing state: `conclusion` is a non-blank
  string -- the specialist's verdict for the orchestrator, in its own technical
  register: what holds, on what evidence, what is open. The orchestrator reads
  it on the dispatch result's summary line, validates it against the row and
  the artifacts, and only then answers the user; it never reaches the user's
  display directly. `finalize` refuses a close without it
  (`conclusion_required`) and names the `gaia contract set --draft-id <id>
  conclusion '...'` call that fills it. During the rename, a close that wrote
  only `user_facing_summary` is stored as `conclusion` and stamped
  `conclusion_alias`. Rows closed by the reaper, the SubagentStop backstop or
  salvage are cut, not clean, and are exempt.

`finalize` persists any valid state and converges the row born at dispatch. It
does not turn a non-terminal state into `COMPLETE`. `validate` is read-only.

## Optional typed verification and progress

`evidence_report.verification.type` is the classifier the validator reads, over
an OPEN vocabulary: `command`, `code`, `semantic`, `self_review` and `none` are
the names it knows, and any other word is accepted as written. Declaring a type
is a claim that a check ran, and the claim is priced in a companion field --
`command`/`code` require a non-empty `command`, `semantic` a truthy
`requires_human` (a rubric the verifier judges; a human only in the cases
`gaia-planner` step 5 names), `self_review` a non-empty `reviewed`, any other
word at least ONE of those three, and `none` (no oracle was required) nothing. An omission is
a `VERIFICATION_SHAPE` rejection, escapable in one deep-merging write. Spelling
folds on separators only, so `self-review` and `self_review` are one type and
`observation` stays itself. `type` is optional: absent or blank, no evidence is
demanded, which is what keeps every contract that never declared one valid.

Finalize then checks what the contract cites against the turn's tool record
(`gaia/contract/content_checks.py`), and refuses a refuted citation as
`content_unverified`:

- A verification carrying a `command`, whatever its `type` word, names a
  command this turn ran, exactly as run, or cites evidence in
  `verification.evidence_ids` (the id `gaia evidence add` prints, a positive
  integer within SQLite's 64-bit range; anything else is refused as malformed
  before any lookup) that proves it: the row exists, its polarity is positive, and it
  was recorded under this turn's brief and, for a turn bound to a plan task,
  under that task; a turn with no plan task is proven only by its brief's
  evidence recorded with no task.
- Verdicts are read after normalization, not by spelling: Unicode folded, check
  and cross marks read as pass and fail, case, punctuation, emphasis, markup
  tags, quotes, slashes and spacing dropped. A command made only of verdict
  words, counts and numbers (`**PASS** ✅`, `<b>pass</b>`, `pass/ok`,
  `12880 passed, 0 failed`) is a verdict, and always refused.
- When `result` is `pass` (after the same normalization), the last recorded
  run of that command must have exited 0, or the exit declared in
  `verification.expect_exit` when a nonzero exit is the expected outcome
  (`grep -q` finding nothing). Any other result is not interpreted, and the
  unmade exit comparison is named in `content_checks.skipped`.
- In a turn with no bound tool record, the command must be something a shell
  could run: past leading `NAME=value` words, its first word with markup tags
  dropped is a path (absolute, `./`, `../` or `~/`), a program on PATH, or a
  builtin that runs one (`cd`, `source`, `export`), and not a verdict. `PASS.`,
  `all tests pass`, `exit 0`, `<b>verified</b>`, `[PASS](/ci)` and `n/a` are
  refused.
- A `verbatim_outputs` entry written as `{"command": ..., "excerpt": ...}` is
  attributed: its excerpt (an `output` key is read the same way), with `...`
  marking elisions, must appear in order in that command's recorded output,
  each fragment on whole-token boundaries (`5 passed` does not match
  `15 passed`). An excerpt with nothing but whitespace or elisions is refused,
  and so is one attributed to a command this turn never ran, unless the entry
  cites an `evidence_id`. A plain string, or a quote with no `command` and no
  `evidence_id`, names nothing to compare against: it is kept, uncompared, and
  named in `content_checks.skipped`. Any other key of the entry (`stdout`,
  `Output`) and any quote that is not a string is named there too, key and
  reason. Write the dict form for any output you want proven.
- An `evidence_id` on a verbatim entry must exist and belong to this turn's
  work; it may be negative, since a quote can attest a failure.

A comparison the record cannot settle is skipped and named in the row's
`content_checks.skipped`, never refused: no output or exit kept, or an output
truncated before the excerpt (the record keeps its first 500 characters). Every
row these checks closed carries `content_checks`, and is checked again on a
re-finalize; only a row closed clean before they existed is not.

`verification.method` is a different, free-text field: prose naming HOW the
check was done, stored verbatim and never read as a classifier. It does not
substitute for `type` -- a block carrying only `method` declares no type and is
asked for no evidence. Write both. Use typed COMMAND_SET progress fields only when the
runtime returns them; do not invent schema. The v42 store exposes ordered
`next_index`, `consumed_indexes_json`, `failed_index`, and `failure_reason`.

## Conditional objects

`consolidation_report` contains `ownership_assessment` (`owned_here`,
`cross_surface_dependency`, or `not_my_surface`), `confirmed_findings`,
`suspected_findings`, `conflicts`, `open_gaps`, and `next_best_agent`.

`approval_request` contains the consent data reference: `operation`,
`exact_content`, `scope`, `risk_level`, `rollback`, `verification`, and when
minted, `approval_id`, copied verbatim from the request's output.

`failure_report` is optional. When present it atomically contains non-empty
`attempted`, `symptom`, and `evidence`; optional `component`; and optional
`severity` (`info`, `warning`, `error`). Add it with one `fill --json` call.

## Other optional fields

- `report_prose`: narrow narrative field -- why/discovery-order/purpose-frame/synthesis, never
  evidence, addressed to the orchestrator and the next agent, not the end user; full definition and
  the line against `conclusion` in `agent-protocol`.
- `memory_delta`: the one memory proposal channel, never write authority --
  `{"version": 1, "proposals": [...]}`, each proposal an `operation` and its
  object (`create`, `append`, `reclassify`, `link`).
- `update_contracts`: `{contract, payload}` entries, a proposal for any
  section and never write authority: project context is written by the
  orchestrator alone. SubagentStop writes nothing for them, whatever
  `agent_state` the turn closes in; it checks the shape, and the dispatch
  result announces "N project-context updates proposed for <project> — contract <id>"
  to the orchestrator, which decides and accepts with `gaia context accept <id>`
  (`--dry-run` shows the target workspaces). Accepting deep-merges the
  payload; lists replace whole and no delete sentinel exists. A single
  `{section: payload}` object is read as the array it abbreviates, any other
  shape is refused with a repair hint. A `project_identity` payload is split
  by project key: each key is written into the workspace that owns that
  project, underscore and hyphen spellings alike, and a key no workspace owns
  is refused, never written to the dispatch's workspace
  (`hooks/modules/context/context_writer.py::apply_proposal`). Refusals are
  stamped on the contract row as `update_contracts_refused`, one reason per
  entry or key (`gaia/store/writer.py::record_update_contracts_outcome`), and
  the dispatch result names them to the orchestrator beside the proposal
  notice (`hooks/modules/agents/proposal_notices.py::proposal_notice`).

## The evidence clause of `update_contracts`

One `update_contracts` entry is not a `can_write` section at all: `{"contract":
"evidence", "payload": {...}}` is the subagent's lane to deposit structured
evidence for an acceptance criterion, reached through the same `gaia contract
set/add/fill` calls used for everything else in this envelope -- no separate
command surface. It is handled by `_apply_evidence_entries`
(`hooks/modules/context/context_writer.py`), validated by
`_validate_evidence_payload` / `contract_validator.validate_evidence_update_
contract_payload`, and inserted via `gaia.evidence.store.insert_evidence`.

`payload` fields: `brief_id` (required, integer), `ac_id` (required, non-empty
string), `type` (required, one of `text`/`file`/`command_output`/`url`/
`screenshot`), exactly one of `text` or `artifact_path`, and optional
`task_id`, `created_by_agent`, `size_bytes`. When `artifact_path` is given it
must already resolve under the canonical Gaia evidence root
(`gaia.evidence.fs.require_canonical_artifact_path` --
`~/.gaia/evidence/{workspace}/{brief_slug}/{ac_id}/...`, minted by `gaia
evidence add`); a repository-relative path, a `/tmp` path, or anything else
outside that root is rejected by name, not inserted. Multiple evidence entries
in the same `update_contracts` array fail together (D8): one invalid entry
rejects the whole batch of evidence entries, though other, non-evidence
`update_contracts` entries in the same call are unaffected.

A file large enough to need `artifact_path` rather than inline `text` is
staged first under the canonical Gaia scratch directory (`~/.gaia/scratch`,
see `command-execution`) if it did not already exist as a real deliverable --
staged under the current turn's own `contract_id` (`<agent_id>.<token>`, bare
or with one trailing extension) so Gaia's own retention rule can attribute
and reclaim the staged copy once this contract closes -- then deposited with
`gaia evidence add` (which mints the canonical path) -- never referenced from
scratch or from a workspace/client repo path directly.

## Validator ownership

`gaia/contract/validator.py` owns form codes: `MISSING_FIELD`, `PLAN_STATUS`,
`AGENT_ID_FORMAT`, `VERIFICATION_RESULT`, `VERIFICATION_SHAPE`,
`APPROVAL_REQUEST_SHAPE`, `COMPLETE_SHAPE`, and `FAILURE_REPORT_SHAPE`.
`gaia.contract.crosscheck.validate` adds DB cross-checks.
Do not reproduce those rules elsewhere.

The CLI draft is the only managed copy. The turn's final message carries no
echo of the envelope: what `gaia contract finalize` promoted to the row is the
whole delivery, and the stop gate reads nothing else.
