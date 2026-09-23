/** Drive governed file tools through the unmodified production bridge seam. */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"
import { assertPromptAsyncBody, assertSessionCreateBody } from "./sdk_body_contract.ts"

const scenario = JSON.parse(process.argv[2])
const controlPrompts: Record<string, unknown>[] = []

const client = {
  session: {
    async create(request: Record<string, unknown>) {
      assertSessionCreateBody(request)
      return { data: { id: `control-${controlPrompts.length + 1}` } }
    },
    async promptAsync(request: Record<string, unknown>) {
      assertPromptAsyncBody(request)
      controlPrompts.push(request)
      return { data: undefined, response: { ok: true, status: 204 } }
    },
  },
}
const plugin: any = await GaiaOpenCodePlugin({
  client,
  directory: scenario.directory,
  worktree: scenario.worktree,
})

const rootSessionID = "ses-bootstrap-root"
const childSessionID = "ses-bootstrap-child"
const dispatchCallID = "call-bootstrap-dispatch"
await plugin.event({
  event: {
    type: "message.updated",
    properties: {
      info: { role: "assistant", sessionID: rootSessionID, agent: "gaia-orchestrator" },
    },
  },
})

await plugin["tool.execute.before"](
  { sessionID: rootSessionID, callID: dispatchCallID, tool: "task" },
  { args: { subagent_type: "gaia-system" } },
)
await plugin["tool.execute.after"](
  {
    sessionID: rootSessionID,
    callID: dispatchCallID,
    tool: "task",
    args: { subagent_type: "gaia-system" },
  },
  { metadata: { sessionId: childSessionID }, output: "" },
)
// The real host's callID<->child-session binding signal (measured:
// project_gaia_opencode_lifecycle_medido_2026_08_26), on the DISPATCHING
// session's own part -- without it the backstop
// (opencode-adapter:child-session-binding-backstop) denies every child
// call fail-closed. Same pattern as consent_retry_driver.ts's "task-part"
// step (commit 4888748).
await plugin.event({
  event: {
    type: "message.part.updated",
    properties: {
      part: {
        type: "tool",
        tool: "task",
        sessionID: rootSessionID,
        callID: dispatchCallID,
        state: { metadata: { sessionId: childSessionID } },
      },
    },
  },
})

const results: Record<string, unknown>[] = []
for (const [index, step] of scenario.steps.entries()) {
  const promptsBefore = controlPrompts.length
  const callID = step.callID ?? `call-${index}`
  const result: Record<string, unknown> = { label: step.label, callID, beforeReturned: false }
  try {
    await plugin["tool.execute.before"](
      {
        sessionID: childSessionID,
        callID,
        tool: step.tool,
      },
      { args: step.args },
    )
    result.beforeReturned = true
    result.allowed = true
  } catch (error: any) {
    result.allowed = false
    result.error = String(error?.message ?? error)
  }
  if (controlPrompts.length > promptsBefore) {
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: childSessionID } } })
  }
  results.push(result)
}

console.log(JSON.stringify({ results, controlPrompts }))
