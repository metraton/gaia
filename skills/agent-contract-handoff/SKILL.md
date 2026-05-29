---
name: agent-contract-handoff
description: Use when you need the exact field schema, required/conditional/optional status, or the trigger for any field of the agent_contract_handoff envelope -- input or output, top-level field, sub-field table, plan_status enum, or the JSON-not-YAML rule
metadata:
  user-invocable: false
  type: reference
---

# Agent Contract Handoff

The `agent_contract_handoff` is the uniform structured block every subagent emits at the end of its turn and the orchestrator consumes to decide the next dispatch. This skill is its field dictionary: every field, whether it is required, when a conditional field triggers, and which code symbol owns the rule.

## The name collision: INPUT vs OUTPUT

`agent_contract_handoff` names two different things, and this is where the distinction is owned. The runtime injects an INPUT envelope before the turn; the subagent emits an OUTPUT envelope after it. Same name, two directions.

### INPUT envelope (what the subagent receives)

The orchestrator-side context the runtime injects before a turn. Detection of `consolidation_required` reads from here -- see `requires_consolidation_report` in `contract_validator.py`. Five named sections:

| Section | Carries | Use |
|---------|---------|-----|
| `project_knowledge` | indexed workspace facts (`project_context_contracts`) | read before scanning the filesystem |
| `surface_routing` | routed surface(s), `multi_surface` flag, adjacent agents | know whether you are primary or assisting |
| `agent_contract_handoff` (input sub-block) | goal, acceptance criteria, scope, plus `consolidation_required` / `cross_check_required` flags | decides whether your OUTPUT must carry `consolidation_report` |
| `write_permissions` | exact `writable_sections` you may emit `update_contracts` clauses for | writing outside the list is rejected by the hook |
| `metadata` | session id, provider, contract version | traceability, not control flow |

### OUTPUT contract (what the subagent emits)

The fenced `agent_contract_handoff` block. Parsed by `parse_contract` (regex `_RE_HANDOFF`, tag `_TAG_HANDOFF`) in `contract_validator.py`; validated by `_validate_from_handoff` there and `validate_response_contract` in `response_contract.py`. The rest of this skill specifies the OUTPUT.

## OUTPUT top-level field table

| Field | Status | Trigger / consequence |
|-------|--------|-----------------------|
| `agent_status` | Required | always; container for the four status fields below |
| `agent_status.plan_status` | Required | always; enum (see state machine); invalid value -> `PLAN_STATUS:<x>` |
| `agent_status.agent_id` | Required | always; must match `_AGENT_ID_PATTERN` `^a[0-9a-f]{5,}$` (`response_contract.py`) so contract-repair can route back to you |
| `agent_status.pending_steps` | Required | missing -> `PENDING_STEPS` in `missing` |
| `agent_status.next_action` | Required | missing -> `NEXT_ACTION` in `missing` |
| `evidence_report` | Required | always present for every valid `plan_status`; see sub-field table |
| `consolidation_report` | Conditional | required when INPUT set `consolidation_required` / `cross_check_required` / `surface_routing.multi_surface` (`requires_consolidation_report`); else may be `null` |
| `approval_request` | Conditional | required when `plan_status` is `APPROVAL_REQUEST`; see sub-field table |
| `loop_state` | Conditional | agentic-loop turns only; `_check_loop_state_blocking` blocks `COMPLETE` when `iteration < max_iterations AND metric < threshold` |
| `memorialize_suggestions` | Optional | structured memory candidates for the user to triage; `parse_memorialize_suggestions` |
| `memory_suggestions` | Optional | advisory text-only notes (array of strings); `parse_memory_suggestions` |
| `update_contracts` | Optional | array of `{contract, payload}` for project-context writes; `parse_update_contracts`; see sub-field table |
| `rollback_executed` | Optional | advisory string; `parse_rollback_executed`; never rejected |
| `context_consumption` | Optional | advisory `{tokens_used, pct_window}`; `parse_context_consumption`; never rejected |

## Sub-field tables

### evidence_report

The required keys are EXACTLY 7 (`_EVIDENCE_REQUIRED_FIELDS` in `contract_validator.py`, `EVIDENCE_FIELDS` in `response_contract.py`). Each key must be present; its value may be `[]`. A missing key (not an empty list) lands the field name in `missing`.

| Key | Holds |
|-----|-------|
| `patterns_checked` | search patterns / queries you ran |
| `files_checked` | files you read |
| `commands_run` | commands executed (string or `{command, result}`) |
| `key_outputs` | distilled findings |
| `verbatim_outputs` | raw output excerpts |
| `cross_layer_impacts` | adjacent components your change invalidates -- flag, do not silently edit |
| `open_gaps` | what remains unresolved |

`verification` is a SEPARATE field, NOT one of the 7. It is required ONLY when `plan_status` is `COMPLETE`: it must be a dict and `verification.result` must equal `"pass"`. Missing -> `VERIFICATION_RESULT_REQUIRED_FOR_COMPLETE`; non-pass -> `VERIFICATION_RESULT_MUST_BE_PASS`. For non-COMPLETE statuses `verification` may be absent.

### consolidation_report

Required keys when present (`_CONSOLIDATION_REQUIRED_FIELDS`):

| Key | Holds |
|-----|-------|
| `ownership_assessment` | enum `owned_here` \| `cross_surface_dependency` \| `not_my_surface` (`VALID_OWNERSHIP_ASSESSMENTS`); invalid -> `OWNERSHIP_ASSESSMENT:<x>` |
| `confirmed_findings` | findings you verified |
| `suspected_findings` | findings you suspect but did not confirm |
| `conflicts` | contradictions found across surfaces |
| `open_gaps` | unresolved items needing another surface |
| `next_best_agent` | which agent should take the next round |

### approval_request

Present when `plan_status` is `APPROVAL_REQUEST` (`APPROVAL_REQUEST_REQUIRED_FIELDS`). `rollback` and `verification` are BLOCKING (missing -> `APPROVAL_REQUEST_ROLLBACK` / `APPROVAL_REQUEST_VERIFICATION`); the rest are advisory warnings.

| Key | Status | Holds |
|-----|--------|-------|
| `operation` | required (advisory) | what the command does |
| `exact_content` | required (advisory) | the command verbatim the user approves |
| `scope` | required (advisory) | what it touches |
| `risk_level` | required (advisory) | enum `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` (`VALID_RISK_LEVELS`) |
| `rollback` | required (blocking) | how to undo |
| `verification` | required (blocking) | how success is confirmed |
| `approval_id` | optional | the id the hook produced, when one was issued |

For the full sealed-payload schema and the approval lifecycle, see `agent-approval-protocol`.

### memorialize_suggestions entry

`parse_memorialize_suggestions`; malformed entries are skipped with warnings and never fail the contract.

| Key | Status | Notes |
|-----|--------|-------|
| `description` | required | `MEMORIALIZE_REQUIRED_FIELDS` |
| `body` | required | `MEMORIALIZE_REQUIRED_FIELDS` |
| `slug` | optional | -- |
| `type` | optional | enum `atom` \| `decision` \| `negative` (`MEMORIALIZE_VALID_TYPES`); off-enum kept with a warning |
| `class` | optional | enum `anchor` \| `thread` \| `log` (`MEMORIALIZE_VALID_CLASSES`); off-enum kept with a warning |
| `rationale` | optional | -- |

### update_contracts entry

Each entry is one `{contract, payload}` pair (`parse_update_contracts`). The `contract` must match a name in the INPUT `write_permissions.writable_sections`; a write to a contract outside that list is rejected by the hook. `payload` is the dict merged under that contract. Combine all deltas for one contract into a single payload; include only keys to add or update.

| Key | Status | Holds |
|-----|--------|-------|
| `contract` | required | a contract name from your `writable_sections`; off-list -> rejected |
| `payload` | required | the dict merged under that contract; keys you omit are preserved |

**Merge semantics** (how the runtime applies `payload` -- the field is additive, never destructive):

| Operation | Behavior |
|-----------|----------|
| ADD | new keys inserted into the section |
| MERGE | existing dicts recursively merged |
| UNION | lists merged, no duplicates |
| OVERWRITE | scalar values replaced |
| NO-DELETE | keys you do not mention are preserved |

**Well-formed payload (index, not snapshot).** A payload indexes what statically exists in the project -- identifiers, names, relationships, semi-stable metadata. It must not carry live-state: cloud runtime status (pod counts, instance status, VPC IDs), API-discovered facts that change without a rescan (load-balancer DNS, IP addresses, OIDC-derived IAM bindings), or any field whose scanner needs a live cloud API call. Stale live-state in context gives the next agent false confidence; obtain it on demand via the cloud CLI instead. For the produce-side judgment of *when* to emit and *what* to prioritize, see `agent-protocol`.

## plan_status enum + state machine

The five canonical values (`VALID_PLAN_STATUSES` in `gaia.state`, re-exported by `response_contract.py`):

| Value | Meaning |
|-------|---------|
| `IN_PROGRESS` | mid-loop; default during retry / verify-fail |
| `APPROVAL_REQUEST` | a T3 command was blocked; `approval_request` populated |
| `COMPLETE` | increment finished and verification passed |
| `BLOCKED` | cannot continue alone; name the gap in `open_gaps` |
| `NEEDS_INPUT` | a user decision is required; list options in `next_action` |

Legal transitions between these and the retry cap live in `state_tracker.py` -- `_LEGAL_TRANSITIONS` and `_MAX_IN_PROGRESS_RETRIES` (= 2). The runtime enforces them; this skill does not reproduce the transition table.

## The JSON rule

The block body must be valid JSON. `parse_contract` runs it through `json.loads`; YAML, comments, trailing commas, or unquoted keys raise `JSONDecodeError`, the parser returns `None`, and the runtime treats the block as missing (forced reissue). Emit JSON, not the YAML it resembles.

## Handoffs

- `agent-protocol` -- how to produce the contract (workflow, judgment, when to emit each status).
- `agent-response` -- how the orchestrator interprets the contract.
- `agent-approval-protocol` -- the full APPROVAL_REQUEST sealed-payload schema.
