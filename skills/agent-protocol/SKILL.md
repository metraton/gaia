---
name: agent-protocol
description: Use when producing any agent response
---

# Agent Protocol

## What is agent-protocol?

The producer's playbook for an agent turn: how to read where you stand in the increment, and how to emit your position back so the orchestrator can decide the next dispatch. The `agent_contract_handoff` is the coordination tool, not an administrative form -- the orchestrator reads your draft (`gaia contract view`) to route mid-turn and the runtime persists the outcome once you `gaia contract finalize`. Build it BY-VALUE with the `gaia contract` CLI across the turn (`init` once, then `set`/`add`/`fill --json` as you discover things, `finalize` once at the end) instead of composing and re-emitting one large fenced JSON block every message -- see "Building the contract" below. Regardless of how it was built, a fenced `agent_contract_handoff` block in your final response text is still required output every turn: the SubagentStop gate parses that fence, not your finalized DB row, so building by-value via the CLI is not a second protocol that exempts you from emitting it. This skill is produce-side judgment only. For the full field schema, conditional triggers, sub-field tables, the INPUT-vs-OUTPUT name collision, and the plan_status enum, see `agent-contract-handoff`. For the approval payload, see `agent-approval-protocol`; for orchestrator-side interpretation, see `agent-response`.

## The workflow as a map

```
INVESTIGATE -> PLAN -> EXECUTE -> VERIFY -> COMPLETE
   ^                                  |
   |                                  v
   +---------- (loop on fail) --------+

        |- BLOCKED          (escalate, cannot continue alone)
mid-loop |- NEEDS_INPUT     (need user decision)
        |- APPROVAL_REQUEST (T3 blocked, awaiting consent)
```

You receive a task (the injected INPUT envelope), work the increment, then emit one OUTPUT envelope. `state_tracker.py` enforces the legal transitions (`_LEGAL_TRANSITIONS`, `_MAX_IN_PROGRESS_RETRIES = 2`); this map tells you where you stand, not which transitions are allowed -- the runtime blocks the illegal ones for you.

**Frame before action.** Read the map before you move. An agent turn is not the whole of Gaia's work; it is one increment inside it -- a prior turn (the orchestrator's routing, the INPUT envelope injected into you) set you here, and a future turn consumes the OUTPUT envelope you leave. So situate yourself first (where in INVESTIGATE -> ... -> COMPLETE do I stand? what did the work before me settle, and what does the turn after me need?), let that raise the first questions the task poses, reason them through, and only then act. The contract you emit is the report of a move made with that awareness, not a form filled in after the fact. For the orient-first discipline itself, see `investigation`.

## Building the contract: by-value via the CLI

The contract is your non-negotiable artifact: the turn's outcome exists only in the draft you build and finalize, so build it as you go rather than composing it all at once at the end -- a draft you ran out of turn to finalize is still readable by the orchestrator (`gaia contract view`), which a block you never got to emit never was. Every write validates the FULL resulting envelope before persisting (validate-on-write, no false-pass): a rejected write leaves the draft at its last-known-good state and prints the concrete errors plus the canonical repair message, never a crash.

```
gaia contract init --agent-id <a...>          # once, at the start of the turn
gaia contract set agent_status.plan_status IN_PROGRESS
gaia contract add evidence_report.files_checked "path/to/file.py"
gaia contract fill --json '{"evidence_report": {"key_outputs": ["..."]}}'   # batch merge
gaia contract view                            # inspect the current draft, no mutation
gaia contract view --field evidence_report.files_checked   # read ONLY one dotted-path subtree
gaia contract validate                        # check the verdict without mutating
gaia contract finalize                        # once, at the end: writes the SOLE, idempotent row
```

`init` mints its own contract id and never reads a harness session variable -- the draft is addressed by that id (or resolved to your most-recently-touched draft, optionally scoped with `--agent-id`), so it is locatable the same way whether you are a fresh dispatch or a resumed one. `set`/`add`/`fill` mutate an in-memory copy, validate it, and persist ONLY on a passing verdict -- a rejected `set` (e.g. an out-of-enum `plan_status`) leaves the on-disk draft untouched and reports the enum text, not a stack trace. `finalize` is the SOLE writer of the terminal `agent_contract_handoffs` row and is idempotent: a second `finalize` of the same draft is a no-op that reports back the same `handoff_id`, so retrying it is always safe.

The shape you are building is unchanged -- `agent_status` + `evidence_report`, with `consolidation_report` and `approval_request` null in the common case; any human-facing prose still belongs in the optional `user_facing_summary`, not spilled across the machine-audience fields. What changed is HOW it gets built: field-by-field across the turn, not composed as one block at the end.

**Building by-value via the CLI does not replace emitting the fence -- it is still required in your final response text, every turn.** The SubagentStop gate never reads your finalized draft row from the DB; it parses the fenced `agent_contract_handoff` block out of your response text (`agent_output` -- `completion.last_message`, or the transcript fallback) via `parse_contract`, THEN validates that parsed dict. `gaia contract finalize` writes the authoritative `agent_contract_handoffs` row, but that row is not what the gate reads to decide whether your turn passes. With `GAIA_CONTRACT_FULL_VERDICT_GATE` default ON (M4), the full-verdict gate hard-rejects (exit 2) any turn whose response text lacks a valid fence -- including a turn where you built and finalized a perfectly valid draft via `gaia contract` but never echoed it as a fenced block in your last message. This is the M4 footgun: a "CLI-only, no fence" agent is rejected, not passed on the strength of its DB row. Always close your turn with the fenced block -- built field-by-field via the CLI across the turn, and then emitted (echoed) as a fence in the response text at the end.

### Fence fallback (still supported, not a second protocol -- and still the required output)

"Fallback" names how the fence relates to the CLI build sequence -- the CLI is the primary way to construct the contract turn-by-turn -- not whether emitting the fence itself is optional. It is not: the fence in your response text is the ONE thing the SubagentStop gate parses, so it is mandatory output on every turn regardless of whether the underlying draft was built via `gaia contract set`/`add`/`fill` or composed directly. An agent that still composes the fenced `agent_contract_handoff` JSON block directly (never touching the CLI) is not on a deprecated path in the sense of being unenforced -- the fence is parsed and the resulting dict is validated through the EXACT SAME core (`gaia.contract.validator.validate_form` / `gaia.contract.crosscheck.validate`) that every `gaia contract` write goes through. The requirements are identical: `evidence_report` carries 7 required keys + `verification` on COMPLETE (the honesty rule below), and the block body must be valid JSON (`json.loads` -- not YAML: comments, trailing commas, or unquoted keys make the block unparseable and the runtime treats it as missing). Prefer building incrementally via the CLI -- it validates incrementally and survives a truncated turn -- but the fence itself, correctly emitted in the response text, is required either way, not merely tolerated.

For every field, its required/conditional status, and its trigger, see `agent-contract-handoff`; a rendered example per `plan_status` is in this skill's own `examples.md`. The canonical repair message (`CANONICAL_REPAIR_MESSAGE`) that both paths return on rejection lives at `gaia/contract/validator.py` -- the single source of truth, never duplicated inline.

## What the emitted fence MUST satisfy (the four rejections to avoid)

Emitting a fence is necessary but not sufficient: the gate parses it and then validates the parsed dict through `gaia.contract.validator.validate_form`, which hard-rejects (exit 2) on four named SHAPE codes (`FormErrorCode`, the SSOT): `MISSING_FIELD`, `PLAN_STATUS`, `AGENT_ID_FORMAT`, `VERIFICATION_RESULT`. `MISSING_FIELD` is the general "required field absent" code and fires for two distinct conditions below (points 1 and 2), so the checklist runs to five points covering those four codes. A *present* fence that misses any point below is rejected exactly as if it were absent. Run this checklist against your block -- or your `gaia contract view` -- before closing the turn. This is the shape inline so you do not have to open `agent-contract-handoff` to get it right; the reference still owns the full per-field detail.

1. **`evidence_report` carries ALL 7 keys -> or `MISSING_FIELD`.** Every one must be *present*; an empty list `[]` is valid, but a *missing* key is a rejection. The seven, exactly: `patterns_checked`, `files_checked`, `commands_run`, `key_outputs`, `verbatim_outputs`, `cross_layer_impacts`, `open_gaps`. (`verification` is a SEPARATE, 8th field, required only on `COMPLETE` -- see point 5; it is NOT one of the 7.) Emitting six keys and omitting `cross_layer_impacts` or `verbatim_outputs` "because there were none" is the #1 rejection -- emit them as `[]`, do not drop them.

2. **`agent_status` carries ALL 4 sub-fields -> or `MISSING_FIELD`.** `plan_status` (absent -> `MISSING_FIELD`; present-but-out-of-enum -> `PLAN_STATUS`, see point 3), `agent_id` (see point 4), `pending_steps` (presence-only; `[]` is valid on a `COMPLETE`), and `next_action` (present and non-empty -- use `"done"` on a terminal `COMPLETE`, never omit it or leave it `""`). Dropping `pending_steps` or `next_action` because the turn is finished is a rejection; a finished turn still emits `pending_steps: []` and `next_action: "done"`.

3. **`plan_status`, when present, must be one of the five canonical values -> or `PLAN_STATUS`.** The enum: `IN_PROGRESS`, `APPROVAL_REQUEST`, `COMPLETE`, `BLOCKED`, `NEEDS_INPUT`. An absent `plan_status` is `MISSING_FIELD` (point 2); a *present* value outside this enum is a distinct rejection, `PLAN_STATUS` (`validator.py:298-309`), not folded into `MISSING_FIELD`. That same invalidity also suppresses the `evidence_report`/`verification` classification for the turn -- one code per invalidity, never stacked.

4. **`agent_id` matches `^a[0-9a-f]{5,}$` -> or `AGENT_ID_FORMAT`.** It is a lowercase `a` followed by 5 or more HEXADECIMAL digits (`0-9`, `a-f`). It is an opaque draft handle you MINT, not your name and not a label: use the id you passed to `gaia contract init --agent-id`, or mint any conforming value (`secrets.token_hex(3)` prefixed with `a`) and reuse it for the whole turn.
   - Valid: `a7e4d2`, `a1b2c3`, `af0091`, `ab7e4d2c9`.
   - Invalid (real rejection samples): `gaia-system` (your name is not an id), `aworkspace1` (`w`,`o`,`r`,`k`,`s`,`p` are not hex, and no `-`), `a00000gaiasystem` (`g`,`i`,`y`,`s`,`m` are not hex -- looking id-shaped is not the same as being hex). If any character after the leading `a` is outside `0-9a-f`, it is rejected.

5. **On `COMPLETE`, `evidence_report.verification.result == "pass"` -> or `VERIFICATION_RESULT`.** `verification` must be a dict AND its `result` must be exactly `"pass"`. A `COMPLETE` with `verification` missing, or with `result` set to anything other than `"pass"` (including `"fail"`), is rejected. The rule is not a formality: if the check did not genuinely pass, you are `IN_PROGRESS` or `BLOCKED`, not `COMPLETE` -- see "The verification honesty rule" below. For every non-`COMPLETE` status, `verification` may be `null`.

## When to emit each `plan_status`

Choose by what is true of your position. The enum and meanings are owned by `agent-contract-handoff`; here is when to reach for each:

- **`COMPLETE`** -- you finished the increment AND verification genuinely passed. Reaching for it because the loop felt long is the failure mode below.
- **`APPROVAL_REQUEST`** -- a T3 command was blocked with an `approval_id`. Pass the id through verbatim; do not retry the command. Hand off to `agent-approval-protocol` for the payload.
- **`BLOCKED`** -- you hit something outside your authority (wrong surface, missing capability) or need information another surface owns. Name the gap in `open_gaps` and suggest the next agent.
- **`NEEDS_INPUT`** -- you need a user decision to continue. List the explicit options in `next_action`.
- **`IN_PROGRESS`** -- mid-loop during retry or verify-fail. Rarely terminal; the runtime caps consecutive `IN_PROGRESS` at 2, so do not park here to avoid a decision. Your draft persists across a resume (`SendMessage`) by its own contract id -- `gaia contract set`/`add` on resume continues the SAME draft rather than starting over, so `IN_PROGRESS` across N resumes is filling in one contract incrementally, not re-declaring it each time. The orchestrator can read that in-progress draft (`gaia contract view`) between your messages without you re-emitting anything.

## The verification honesty rule

Report `verification.result = "pass"` only when it genuinely passed. `verification` is required only on `COMPLETE`, and on COMPLETE `result` must be `"pass"` -- a `COMPLETE` with `result = "fail"` is a contradiction the runtime rejects (`VERIFICATION_RESULT_MUST_BE_PASS`). The deeper failure mode is not the rejected contract; it is the habit of defining success by command exit code. A clean exit does not mean the change worked -- verification is the moment you confirm the change produced the intended outcome. Reach for the strongest genuine check within your reach, using everything at your disposal -- what your context already gives you, what the system offers, what the skill catalog holds, even a capability you obtain when it genuinely sharpens the check -- and where the output has a surface you can observe, observe it rather than assert it. The method fits the domain (infra: `dry-run`; code: `test`; skills: `self-review`; email: `metric`). `self-review` is the floor, not the default: settle for it only when no stronger genuine check is within reach, and then state what you checked and what you observed -- never a hollow pass.

## When to populate `update_contracts`

`update_contracts` is the optional array (`{contract, payload}` each) that writes your discoveries into shared project-context so the next agent does not start from zero on what only you saw. It is produce-side judgment: emit a delta when ANY is true -- a section you own is **empty**, discovered data **drifts** from what is indexed, you found **new resources** not listed, or you uncovered a **pattern/structure/config** not yet captured. Skip when findings match what is already indexed; a redundant write only adds noise to the audit trail. Emit as you discover, not at task end. For the field shape, merge semantics, and the index-not-snapshot boundary on payload contents, see `agent-contract-handoff`.

**Prioritize when a section is empty or sparse** -- capture the highest-value keys first:

| Priority | Capture | Why |
|----------|---------|-----|
| P0 | resource identifiers (names, IDs, paths) | direct targeting in future searches |
| P1 | structural relationships (what connects to what) | cross-agent reasoning |
| P2 | configuration values (versions, replicas, limits) | drift detection |
| P3 | behavioral patterns (conventions, naming schemes) | consistency enforcement |

Capture P0 on every investigation; P1-P3 only when naturally encountered -- do not investigate solely to populate context.

**Mutative triggers fire even without investigation.** An investigative trigger fires when you *discover* something that already existed; a mutative trigger fires when an action *creates or changes* workspace state. When tool output says *installed*, *added*, *configured*, *applied*, or *upgraded* for a named package or service (`npm install`, `pip install`, `kubectl apply`, `helm install/upgrade`, `brew install`, `auth configure`), record the new state in `update_contracts` before the turn ends. Read-only output is not a trigger: `kubectl get`, `helm list`, `npm list`, `terraform plan`, and `*describe*` index through scanners, not the contract.

## Anti-patterns

- **Defining success by exit code** -- `exit 0` is not verification. The change either produced the intended outcome or it did not; the honesty rule is the test.
- **Emitting `COMPLETE` without verification** -- the validator blocks it, but the worse cost is reaching for `COMPLETE` because the loop felt long. If unverified, you are still `IN_PROGRESS`.
- **Assuming the orchestrator remembers** -- every turn starts from the contract. If you saw something relevant and did not write it into your draft (`set`/`add`/`fill`), it does not exist for the next agent, even though the draft itself now persists across resumes.
- **Inventing a `plan_status`** -- only the five canonical values exist (`IN_PROGRESS`, `APPROVAL_REQUEST`, `COMPLETE`, `BLOCKED`, `NEEDS_INPUT`). A novel status is not silently coerced -- it is rejected outright with the distinct `PLAN_STATUS` code (`validator.py:298-309`), and that same invalidity suppresses the `evidence_report`/`verification` classification for the turn (one code per invalidity). Either way the turn fails; there is no silent fallback.
- **Caching live-state in `update_contracts`** -- writing runtime facts (pod counts, IPs, instance status) into project-context misleads the next agent the moment they change. Index what statically exists; fetch the live value on demand.
- **Spending the budget on narration instead of building the draft** -- prose is not the deliverable; the contract is. Reporting progress in ad-hoc `SendMessage` prose instead of writing it into the draft (`set`/`add`) is the turn the orchestrator cannot route on, no matter how much was done. The persisted draft is the substrate for incremental progress -- use it, not narration.
- **Never calling `finalize`** -- a draft that is `set`/`add`ed all the way to a genuine `COMPLETE` verdict but never finalized is not yet an authoritative row; the SubagentStop backstop will capture SOMETHING on your behalf if you forget, but it lands `degraded=true` -- distinguishable from your own verified finalize, and a worse outcome than finalizing yourself.
- **Finalizing via the CLI and never emitting the fence** -- the M4 footgun. The gate parses the fenced block out of your response text, not your finalized DB row; a valid `gaia contract finalize` with no fence in the final message still hard-rejects the turn. Building by-value via the CLI is HOW you construct the contract across the turn -- it is not a substitute for emitting the fence at the end.

## Handoffs

- **`agent-contract-handoff`** -- the full field schema, conditional triggers, sub-field tables, INPUT-vs-OUTPUT distinction, and plan_status enum.
- **`agent-approval-protocol`** -- sealed_payload schema, `approval_id` format, full APPROVAL_REQUEST envelope.
- **`agent-response`** -- how the orchestrator parses and acts on your contract.
- **Domain skills** (`gaia-patterns`, ...) -- what counts as evidence and which verification method fits per surface.
