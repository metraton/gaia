/** Drive governed file tools through the unmodified production bridge seam. */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const scenario = JSON.parse(process.argv[2])
const permissionCreates: Record<string, unknown>[] = []

const client = {
  session: {
    permission: {
      create: async (payload: Record<string, unknown>) => {
        permissionCreates.push(payload)
        return { data: { id: `permission-${permissionCreates.length}` } }
      },
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

const results: Record<string, unknown>[] = []
for (const [index, step] of scenario.steps.entries()) {
  const before = permissionCreates.length
  const callID = step.callID ?? `call-${index}`
  const result: Record<string, unknown> = { label: step.label, callID }
  try {
    await plugin["tool.execute.before"](
      {
        sessionID: childSessionID,
        callID,
        tool: step.tool,
      },
      { args: step.args },
    )
    result.allowed = true
  } catch (error: any) {
    result.allowed = false
    result.error = String(error?.message ?? error)
  }
  result.permissionIndexes = Array.from(
    { length: permissionCreates.length - before },
    (_, offset) => before + offset,
  )
  results.push(result)
}

console.log(JSON.stringify({ results, permissionCreates }))
