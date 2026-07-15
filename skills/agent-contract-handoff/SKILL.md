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

The `agent_contract_handoff` envelope, built BY-VALUE across the turn with the `gaia contract` CLI (`init`/`set`/`add`/`fill --json`, then `finalize`) rather than composed once and re-emitted as a single fenced block. Every write validates the FULL resulting envelope through one combined entry point, `gaia.contract.crosscheck.validate()` -- layer 1 (`gaia.contract.validator.validate_form`, pure/stdlib SHAPE check) plus layer 2 (`gaia.contract.crosscheck`, gaia.db cross-check for `approval_id`) -- before persisting anything, so a rejected write never lands (no false-pass). `gaia.contract.validator.py` is the single source of truth for shape, including the canonical rich repair message (`CANONICAL_REPAIR_MESSAGE`) both this path and the fence fallback return.

A still-emitted fenced `agent_contract_handoff` block is a supported migration fallback: `parse_contract` (regex `_RE_HANDOFF`, tag `_TAG_HANDOFF`) in `hooks/modules/agents/contract_validator.py` extracts the dict from the raw text, and `_validate_from_handoff` there hands SHAPE validation to the SAME `validate_form` core -- it is not a second, divergent validator. `contract_validator.py`'s `validate()` is explicitly the migration-only entry point for this path (see its docstring); it never re-implements the shape rules. The rest of this skill specifies the envelope either path must satisfy.

## OUTPUT top-level field table

| Field | Status | Trigger / consequence |
|-------|--------|-----------------------|
| `agent_status` | Required | always; container for the four status fields below; absent -> named code `MISSING_FIELD` (field `agent_status`) |
| `agent_status.plan_status` | Required | always; enum (see state machine); absent -> `MISSING_FIELD`; present but out-of-enum -> named code `PLAN_STATUS` |
| `agent_status.agent_id` | Required | always; must match `^a[0-9a-f]{5,}$`; absent -> `MISSING_FIELD`; present but malformed -> named code `AGENT_ID_FORMAT` -- so contract-repair can route back to you |
| `agent_status.pending_steps` | Required | presence-only check (`[]` is valid); missing -> `MISSING_FIELD` (field `agent_status.pending_steps`) |
| `agent_status.next_action` | Required | must be present and non-empty; missing -> `MISSING_FIELD` (field `agent_status.next_action`) |
| `evidence_report` | Required | always present for every valid `plan_status`; see sub-field table |
| `consolidation_report` | Conditional | required when INPUT set `consolidation_required` / `cross_check_required` / `surface_routing.multi_surface` (`requires_consolidation_report`); else may be `null` |
| `approval_request` | Conditional | required when `plan_status` is `APPROVAL_REQUEST`; see sub-field table |
| `loop_state` | Conditional | agentic-loop turns only; `_check_loop_state_blocking` blocks `COMPLETE` when `iteration < max_iterations AND metric < threshold` |
| `user_facing_summary` | Optional | a brief prose summary written ONCE for the human reader; `parse_user_facing_summary`. The only human-audience field in the contract -- every other field is machine-audience for the orchestrator. On a single-agent `COMPLETE` (N=1) the orchestrator relays it near-verbatim (adapted to the user's language) instead of re-synthesizing `key_outputs`. Absent, or N>1 (multi-agent), the orchestrator falls back to synthesizing `key_outputs`. Purely additive: never required, never rejected. |
| `memorialize_suggestions` | Optional | structured memory candidates for the user to triage; `parse_memorialize_suggestions` |
| `memory_suggestions` | Optional | advisory text-only notes (array of strings); `parse_memory_suggestions` |
| `update_contracts` | Optional | array of `{contract, payload}` for project-context writes; `parse_update_contracts`; see sub-field table |
| `rollback_executed` | Optional | advisory string; `parse_rollback_executed`; never rejected |
| `context_consumption` | Optional | advisory `{tokens_used, pct_window}`; `parse_context_consumption`; never rejected |

## Named shape-error codes (SSOT)

Four stable, named codes (`FormErrorCode` in `gaia/contract/validator.py`) are the SSOT for every SHAPE rejection, whichever path produced it -- the `gaia contract` CLI's write-time validation, the SubagentStop hook gate, and the fence fallback all resolve to the same four:

| Code | Fires when |
|------|------------|
| `AGENT_ID_FORMAT` | `agent_status.agent_id` is present but does not match `^a[0-9a-f]{5,}$` |
| `PLAN_STATUS` | `agent_status.plan_status` is present but outside the canonical enum |
| `VERIFICATION_RESULT` | `plan_status` is `COMPLETE` and `evidence_report.verification.result` is missing or not `"pass"` |
| `MISSING_FIELD` | a required field is absent (`agent_status`, one of its four sub-fields, `evidence_report`, or one of its seven keys) |

Each rejection carries `{code, field, detail}` (dotted `field` path, human `detail`) plus the byte-stable `repair_message` -- ALWAYS `CANONICAL_REPAIR_MESSAGE`, the single source of truth for the reissued fenced template, so a caller that injects it keeps a cache-stable surface regardless of which specific code fired. Read it at `gaia/contract/validator.py::CANONICAL_REPAIR_MESSAGE`; it is never duplicated inline elsewhere.

**Legacy token relabeling (fence-fallback path only).** `hooks/modules/agents/contract_validator.py`'s migration-only `validate()` still surfaces the OLDER uppercase token vocabulary (`AGENT_ID`, `PLAN_STATUS`, `PENDING_STEPS`, `NEXT_ACTION`, `VERIFICATION_RESULT_MUST_BE_PASS`, `VERIFICATION_RESULT_REQUIRED_FOR_COMPLETE`, per-key evidence tokens) in its `missing` list, for backward compatibility with pre-existing callers. `_legacy_tokens_for_form_error` is a pure relabeling of the four SSOT codes above -- it does not re-derive validity. The `gaia contract` CLI and the hook gate's full-verdict path speak the four SSOT codes directly; only the legacy fence-`validate()` caller sees the older tokens.

## Sub-field tables

### evidence_report

The required keys are EXACTLY 7 (`REQUIRED_EVIDENCE_FIELDS` in `gaia/contract/validator.py` -- the SSOT both the CLI and the fence fallback resolve against; relabeled to the legacy `_EVIDENCE_REQUIRED_FIELDS` / `EVIDENCE_FIELDS` uppercase tokens only on the fence-fallback path, see "Named shape-error codes" above). Each key must be present; its value may be `[]`. A missing key (not an empty list) is a `MISSING_FIELD` (field `evidence_report.<key>`), relabeled to the uppercase key name in `missing` on the fence-fallback path.

| Key | Holds |
|-----|-------|
| `patterns_checked` | search patterns / queries you ran |
| `files_checked` | files you read |
| `commands_run` | commands executed (string or `{command, result}`) |
| `key_outputs` | distilled findings |
| `verbatim_outputs` | raw output excerpts |
| `cross_layer_impacts` | adjacent components your change invalidates -- flag, do not silently edit |
| `open_gaps` | what remains unresolved |

`verification` is a SEPARATE field, NOT one of the 7. It is required ONLY when `plan_status` is `COMPLETE`: it must be a dict and `verification.result` must equal `"pass"`. Either failure (missing/malformed, or present but not `"pass"`) is the single named code `VERIFICATION_RESULT` on the CLI/core path; the fence-fallback path relabels the same rejection to the legacy tokens `VERIFICATION_RESULT_REQUIRED_FOR_COMPLETE` (missing) / `VERIFICATION_RESULT_MUST_BE_PASS` (non-pass) for backward compatibility. For non-COMPLETE statuses `verification` may be absent.

**Audience boundary.** `key_outputs` and every other `evidence_report` key are written for the **orchestrator** -- distilled findings it reasons over to route the next turn. The optional top-level `user_facing_summary` is the **single** field written for the **human**. Keeping the two distinct is what lets the orchestrator relay a human-shaped summary on N=1 without re-synthesizing machine-shaped evidence, and lets it still synthesize from `key_outputs` when the summary is absent or when multiple agents must be consolidated.

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

**Cross-repo resource references.** When a payload value points at a resource living in *another* repository, reference it as `host/owner/repo:table/name` (e.g. `github.com/org/bildwiz-iac:tf_modules/gcp-gke`). The `host/owner/repo` prefix is the canonical workspace identity (normalized by `normalize_remote_url` in `gaia/project.py`); the `:table/name` suffix names the domain table and resource within that workspace. Use this form so the reference is unambiguous in multi-repo setups instead of a bare local name.

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

## Draft lifecycle: persistence, resume, and the `degraded` flag

The `gaia contract` draft is addressed by its own contract id (minted by `init`, never a harness session id) and persists on disk across a resume: `set`/`add`/`fill` on a resumed turn continue the SAME draft rather than starting over, and `plan_status` legitimately stays `IN_PROGRESS` across N resumes -- the SubagentStop gate does not reject a mid-conversation `IN_PROGRESS`. The orchestrator can inspect that in-progress draft via `gaia contract view` between messages without the agent re-emitting anything (or `gaia contract view --field <dotted-path>` to read ONLY one subtree of the envelope, addressed with the same dotted-path scheme `set` uses); the view injected into a resumed prompt is deliberately MINIMAL and byte-stable (not the full variable envelope re-inserted above the prompt) so the resume stays cache-safe.

`finalize` is the SOLE, idempotent writer of the terminal `agent_contract_handoffs` row. If a turn ends without a `finalize` call (crash, forgotten call, truncation), the SubagentStop hook backstops the row: it finalizes whatever draft exists (or writes a minimal row when none does), marking it `degraded=true` / `auto_captured`, via the same idempotent UPSERT keyed on the draft's contract id -- so exactly one row survives even under a race between the agent's own `finalize` and the hook's backstop (never-lost, exactly-once). **"Verified-via-finalize" means `task_status == 'COMPLETE'` AND the row carries NO `degraded` flag** -- a downstream reader (episodic memory, metrics) must check both, not `task_status` alone, to distinguish an agent-verified `COMPLETE` from a hook-backstopped degraded capture of the same nominal status.

## The JSON rule

On the fence-fallback path, the block body must be valid JSON: `parse_contract` runs it through `json.loads`; YAML, comments, trailing commas, or unquoted keys raise `JSONDecodeError`, the parser returns `None`, and the runtime treats the block as missing (forced reissue). On the CLI path, `gaia contract set`/`add` accept a JSON-typed VALUE when it parses as JSON and fall back to a plain string otherwise, and `fill --json` requires a valid JSON object outright (a malformed patch is rejected before any merge). Either way, emit JSON, not the YAML it resembles.

## Handoffs

- `agent-protocol` -- how to produce the contract (workflow, judgment, when to emit each status).
- `agent-response` -- how the orchestrator interprets the contract.
- `agent-approval-protocol` -- the full APPROVAL_REQUEST sealed-payload schema.
- `gaia/contract/validator.py` -- the SSOT for shape validation and `CANONICAL_REPAIR_MESSAGE`; `bin/README.md` documents the `gaia contract` CLI surface.
