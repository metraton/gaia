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
| `*.test.ts` + `test_*.py` collectors | Pure bun unit tests (`shell_env_delivery`, `binary_question_match`) collected into pytest by a one-test wrapper each. |
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

`live/control_plane_smoke.ts` measures the literal `question.asked` /
`question.replied` events a real host emits for Gaia's consent question. It is
opt-in: without `OPENCODE_SMOKE_BASE_URL` it prints `SKIP` and exits 0, so the
pytest suite never reaches it. Run it by hand:

```
OPENCODE_SMOKE_BASE_URL=http://127.0.0.1:10788 \
OPENCODE_SMOKE_DIRECTORY=/home/jorge/ws/me \
OPENCODE_SMOKE_PASSWORD=<OPENCODE_SERVER_PASSWORD of the serve process> \
bun tests/opencode/live/control_plane_smoke.ts
```

The header of the file documents every variable (`OPENCODE_SMOKE_AGENT`,
`OPENCODE_SMOKE_ROOT_ONLY`, `OPENCODE_SMOKE_SDK_DIR`, basic auth). It talks to
a real LLM through the host and deletes the sessions it creates.

Why it cannot measure the control plane while the Gaia plugin is loaded
(measured 2026-09-16, five runs): the plugin's `tool.execute.before` sends every
tool call to the bridge, and the adapter denies the `question` call of any
session the plugin did not open itself -- `gaia-orchestrator` is refused as a
"control-plane role declared without an attested runtime context", and a native
agent's child session is refused by the child-session-binding backstop until a
Task dispatch binds it. These are identity gates, not T3. So an external SDK
client can measure the host's `question.asked` shape only against a serve
WITHOUT the plugin, and the plugin's own correlation can only be exercised
through a real dispatch (a specialist attempting a T3 command) with the plugin
loaded. The structural correlation (`matchesBinaryQuestion`) makes the host's
key order irrelevant to the plugin; the smoke remains the tool for judging any
future host change in the event shape itself.
