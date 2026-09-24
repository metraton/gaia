# tests/opencode

Drivers and collectors for the OpenCode adapter: `opencode/plugin.ts` run under
bun, `opencode/bridge.py`, and the `gaia approvals opencode-*` CLIs. Every
driver runs the real plugin closure; what is doubled is the OpenCode host,
which never runs in the Python suite.

## The consent flow these drivers exercise (D37, D39)

A specialist whose call is blocked, or that requests a signature with
`gaia approvals request-set`, is asked nothing: the plugin opens no session,
sends no prompt and posts no message, and the specialist returns the approval
id in its contract. Only the orchestrator asks, in its own question-tool call
carrying what `gaia approvals question <id> [<id> ...]` prints in an OpenCode
shell (one placeholder per approval, whose text is the id). The plugin
replaces those placeholders with Gaia's questions, one line per sealed command
(D37), records SHOWN for the requesting session with the orchestrator as
presenter, and binds the answer by the host's `requestID`. Each signature
decides on its own questions only; a reply that is not one exact label per
asked question decides nothing. The grant stays bound to the requesting session
and agent, and the orchestrator's question result tells it which specialist to
resume with execution.

## What lives here

| File | Role |
|------|------|
| `*_driver.ts` | Scenario drivers: read a JSON scenario from `argv[2]`, run the real plugin against a stubbed host (`client.session.*`, question and lifecycle events), print one JSON line of observations. Invoked by the `tests/integration/test_opencode_*.py`, `tests/hooks/adapters/test_opencode_*.py` and `tests/approvals/test_approval_live_fixes_*.py` suites. |
| `consent_retry_driver.ts` | The consent chain end to end: dispatch, blocked attempt, the orchestrator's question call (`control-decision` asks the last blocked approval, or `approvalIDs`; `orchestrator-question` asks captured `gaia approvals question` output), reply, retry, shell-env delivery. Its bridge is the real `bridge.py` through `isolated_bridge.py`; audit traces (`control.opened`, `control.closed`, `decision.applied`, `retry.refused`, `permission.uncorrelated`) land in the scenario's `GAIA_DB`. Any session prompt the plugin sends is recorded in `controlPrompts`, which D39 keeps empty. |
| `presentation_driver.ts` | One blocked call and, with `ask`, the orchestrator's question call that presents it: the questions written, the bridge traces, the aborts; observes the `bin/gaia` spawn boundary (cwd). |
| `sdk_body_contract.ts` | The host stubs' type checks for `session.create` / `session.promptAsync` bodies, mirroring `@opencode-ai/sdk` 1.18.18; still used by `protected_edit_driver.ts`. |
| `isolated_bridge.py` | Asserts the private workspace before running `bridge.py` in-process. |
| `*.test.ts` + `test_*.py` collectors | Pure bun unit tests (`shell_env_delivery`, `binary_question_match`, `consent_retry_evaluate`, `second_root_attestation`) collected into pytest by a one-test wrapper each. |
| `test_*.py` | Contract and gate tests that read `plugin.ts` or drive the bridge directly. |
| `live/` | Opt-in smoke against a RUNNING `opencode serve`; see below. |

## Known, not yet covered

Observed 2026-09-16 while the plugin's retry gate was being narrowed: with a
plan-first COMMAND_SET grant pending for `git push origin main`, the bridge
answered `allowed=True` to a bash call carrying `git push origin main ` (one
trailing space) once the plugin let it through to policy as an unrelated
command. The host-neutral lane may be matching whitespace-drifted bytes against
the grant's exact command. The plugin gate now refuses that drift before policy
(`test_drift_after_yes_is_refused_before_policy_and_changes_no_state`), so the
observation is not reachable through the driver; the neutral lane itself has no
test asserting exact bytes and has not been investigated. Not fixed here.

## `live/` -- the smoke that needs a real host

`live/control_plane_smoke.ts` observes the literal `question.asked` /
`question.replied` / `session.idle` lifecycle a real host emits while Gaia
presents consent. It creates no session, sends no prompt, and records no
decision itself. It is opt-in: without `OPENCODE_SMOKE_BASE_URL` it prints
`SKIP` and exits 0, so the pytest suite never reaches it:

```
OPENCODE_SMOKE_BASE_URL=http://127.0.0.1:10788 \
OPENCODE_SMOKE_DIRECTORY=/home/jorge/ws/me \
OPENCODE_SMOKE_PASSWORD=<OPENCODE_SERVER_PASSWORD of the serve process> \
bun tests/opencode/live/control_plane_smoke.ts
```

The header documents the SDK and basic-auth variables. Start the watcher, ask
Gaia normally for an operation that needs a signature, and answer the question
the orchestrator opens in the host UI. The smoke was written when the question
opened in the specialist's session (D25) and derives the session it watches from
the signed `question.asked` event; under D39 that event arrives in the
orchestrator's session, and the smoke has not been re-run against that flow.
