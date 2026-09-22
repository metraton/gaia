# tests/opencode

Drivers and collectors for the OpenCode adapter: `opencode/plugin.ts` run under
bun, `opencode/bridge.py`, and the `gaia approvals opencode-*` CLIs. Every
driver runs the real plugin closure; what is doubled is the OpenCode host,
which never runs in the Python suite.

## What lives here

| File | Role |
|------|------|
| `*_driver.ts` | Scenario drivers: read a JSON scenario from `argv[2]`, run the real plugin against a stubbed host (`client.session.*`, permission events), print one JSON line of observations. Invoked by the `tests/integration/test_opencode_*.py` and `tests/hooks/adapters/test_opencode_*.py` suites. |
| `consent_retry_driver.ts` | The consent chain end to end: dispatch, blocked attempt, control question, decision, retry, shell-env delivery. Its bridge is the real `bridge.py` through `isolated_bridge.py`; audit traces (`control.opened`, `control.closed`, `decision.applied`, `retry.refused`, `permission.uncorrelated`) land in the scenario's `GAIA_DB`. |
| `presentation_driver.ts` | What the plugin hands the host's permission mechanism for one blocked call; observes the `bin/gaia` spawn boundary (cwd). |
| `sdk_body_contract.ts` | The host stubs' type checks for `session.create` / `session.promptAsync` bodies, mirroring `@opencode-ai/sdk` 1.18.18. |
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
`question.replied` / `session.idle` lifecycle a real host emits when the Gaia
plugin presents consent in an already-known specialist child. It creates no
session, sends no synthetic control prompt, and records no decision itself. It
is opt-in: without `OPENCODE_SMOKE_BASE_URL` it prints `SKIP` and exits 0, so the
pytest suite never reaches it. Start it before exercising a normal Gaia flow:

```
OPENCODE_SMOKE_BASE_URL=http://127.0.0.1:10788 \
OPENCODE_SMOKE_DIRECTORY=/home/jorge/ws/me \
OPENCODE_SMOKE_PASSWORD=<OPENCODE_SERVER_PASSWORD of the serve process> \
bun tests/opencode/live/control_plane_smoke.ts
```

The header documents the SDK and basic-auth variables. After the watcher is
running, ask Gaia normally for an operation that reaches native consent and
answer the binary question in the host UI. Do not locate, copy, or open an
internal session: the smoke derives the specialist session from the signed
`question.asked` event and only observes it.

The smoke deliberately runs with the Gaia plugin loaded. The plugin, not the
smoke, already knows the specialist child from its Task binding and reuses it;
there is no newly-uncached control session for an SDK client to create. The
first consent-control prompt waits for that reused specialist session's
`session.idle` from the original blocked turn before the plugin presents it.
This pre-presentation wait is separate from the post-decision `session.idle`
that arms an approved retry or advances the next queued control. The
plugin emits exactly one native question with `multiple: false` and
`custom: false`; OpenCode's normalized `tool.execute.before` input and observed
`question.asked` copy may omit the optional `custom` field. Gaia treats only
that omission as the emitted `false`, at those two lifecycle boundaries and
only after its exact
question call has entered the exclusive control-owned lifecycle. Unrelated tool
calls or question events, extra entries, duplicate or late questions, field
drift, `custom: true` or a string value, and malformed `multiple` or `options`
fail closed. Legitimate later approvals remain queued, while safe-idle retry
arming, the pre-activation block, and FIFO scheduling remain covered by the
deterministic suites. The live watcher checks the signed question/reply/idle
ordering without replacing that policy evidence.
