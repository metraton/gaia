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
is NOT injected: the kernel's `can_read` / `can_write` lists (from
`agent_contract_permissions`) name which of those slices the turn may pull
on demand (the verb that reaches them, and the same-named sibling that reads a
different table entirely, are in `agent-protocol/read-map.md`) and which it may
propose updates to via `update_contracts`. When a message says "contract" without
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
role/surface, `project`, `can_read`/`can_write`, and -- on a plan-task-bound
turn -- the acceptance gates), `# Your CLI`, and `# What I know about you`.
Project context is NOT preloaded and no surface routing arrives: pull the
sections you need on demand, within the `can_read` menu, before querying
anything wider (`agent-protocol/read-map.md`). Only sections in `can_write`
may appear in `update_contracts`.

## Minimal increment

Every draft, including a mid-turn checkpoint, contains:

- `agent_status.agent_state`, `agent_status.agent_id`, `pending_steps`, and a
  non-empty `next_action`;
- `evidence_report` with all seven keys: `patterns_checked`, `files_checked`,
  `commands_run`, `key_outputs`, `verbatim_outputs`, `cross_layer_impacts`, and
  `open_gaps` (empty lists are valid);
- `consolidation_report` and `approval_request`, normally `null`.

`agent_id` matches `^a[0-9a-f]{16,}$`. The canonical states are
`IN_PROGRESS`, `APPROVAL_REQUEST`, `BLOCKED`, `NEEDS_INPUT`,
`NEEDS_VERIFICATION`, and `COMPLETE`. Only `COMPLETE` is terminal.

## State-conditional close requirements

- `APPROVAL_REQUEST`: `approval_request` is non-null and
  `approval_request.exact_content` is non-blank. All approval-set data stays in
  this object; see `agent-approval-protocol`.
- `COMPLETE`: `pending_steps` is `[]`, `next_action` is exactly `done`, and
  `evidence_report.verification.result` is `pass`.
- plan-task-bound producers do not self-complete. They close their increment as
  `NEEDS_VERIFICATION`; the verifier is bound through `parent_handoff_id`.
- `consolidation_report` is required when input marks consolidation,
  cross-check, or multi-surface work.

`finalize` persists any valid state and converges the row born at dispatch. It
does not turn a non-terminal state into `COMPLETE`. `validate` is read-only.

## Optional typed verification and progress

`evidence_report.verification.type` is the classifier the validator reads, over
an OPEN vocabulary: `command`, `code`, `semantic`, `self_review` and `none` are
the names it knows, and any other word is accepted as written. Declaring a type
is a claim that a check ran, and the claim is priced in a companion field --
`command`/`code` require a non-empty `command`, `semantic` a truthy
`requires_human`, `self_review` a non-empty `reviewed`, any other word at least
ONE of those three, and `none` (no oracle was required) nothing. An omission is
a `VERIFICATION_SHAPE` rejection, escapable in one deep-merging write. Spelling
folds on separators only, so `self-review` and `self_review` are one type and
`observation` stays itself. `type` is optional: absent or blank, no evidence is
demanded, which is what keeps every contract that never declared one valid.

`verification.method` is a different, free-text field: prose naming HOW the
check was done, stored verbatim and never read as a classifier. It does not
substitute for `type` -- a block carrying only `method` declares no type and is
asked for no evidence. Write both. Use typed COMMAND_SET progress fields only when the
runtime returns them; do not invent schema. The v42 store exposes ordered
`next_index`, `consumed_indexes_json`, `failed_index`, and `failure_reason`.

## work_phase -- the observable WORK cycle, orthogonal to agent_state

`work_phase` is an optional top-level field naming where the producer is in
the WORK cycle: `framing`, `investigating`, `planning`, `executing`,
`verifying`. It is deliberately a second, separate axis from
`agent_status.agent_state`: `agent_state` is the COMMUNICATION state machine
(how this turn currently reports back) that feeds routing and the blind
finalize/verification gate -- a pure function of `(agent_state,
plan_task_id)` -- and that gate must never grow a second dimension.
`work_phase` never widens or narrows it.

Seeded `null` by `gaia contract init` (same discoverability convention as
`consolidation_report`/`approval_request`/`failure_report`); absence or an
explicit `null` reaches no check on any `agent_state`. Presence is validated
in full: a value outside the five names above is a `WORK_PHASE_SHAPE`
rejection. See `agent-protocol` for the work-cycle discipline that writes
it at each transition, and for the trivial-turn exemption (a turn with no
distinguishable phase never sets it).

## Conditional objects

`consolidation_report` contains `ownership_assessment` (`owned_here`,
`cross_surface_dependency`, or `not_my_surface`), `confirmed_findings`,
`suspected_findings`, `conflicts`, `open_gaps`, and `next_best_agent`.

`approval_request` contains the consent data reference: `operation`,
`exact_content`, `scope`, `risk_level`, `rollback`, `verification`, and when
minted, `approval_id`. COMMAND_SET adds its ordered command set and request
fingerprint as specified by `agent-approval-protocol`.

`failure_report` is optional. When present it atomically contains non-empty
`attempted`, `symptom`, and `evidence`; optional `component`; and optional
`severity` (`info`, `warning`, `error`). Add it with one `fill --json` call.

## Other optional fields

- `user_facing_summary`: human prose, for the end user.
- `report_prose`: narrow narrative field -- why/discovery-order/purpose-frame/synthesis, never
  evidence, addressed to the orchestrator and the next agent, not the end user; full definition and
  the line against `user_facing_summary` in `agent-protocol`.
- `memory_delta`, `memorialize_suggestions`, `memory_suggestions`: proposals,
  never write authority.
- `update_contracts`: `{contract, payload}` entries, deep-merged only into the
  input write allowlist; lists replace whole and no delete sentinel exists.
- `rollback_executed`, `context_consumption`: advisory fields.

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
`APPROVAL_REQUEST_SHAPE`, `COMPLETE_SHAPE`, `FAILURE_REPORT_SHAPE`, and
`WORK_PHASE_SHAPE`. `gaia.contract.crosscheck.validate` adds DB cross-checks.
Do not reproduce those rules elsewhere.

The CLI draft is the only managed copy. The turn's final message carries no
echo of the envelope: what `gaia contract finalize` promoted to the row is the
whole delivery, and the stop gate reads nothing else.
