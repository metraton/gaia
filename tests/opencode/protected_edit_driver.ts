/** Drive governed file tools through the unmodified production bridge seam. */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"
import { assertPromptAsyncBody, assertSessionCreateBody } from "./sdk_body_contract.ts"

const scenario = JSON.parse(process.argv[2])
const permissionAsks: Record<string, unknown>[] = []
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

let questionIndex = 0
async function rejectActiveControl(): Promise<void> {
  const prompt = controlPrompts.at(-1) as any
  const encoded = prompt?.body?.parts?.[0]?.text?.split("\n").at(-1)
  const questions = encoded ? JSON.parse(encoded).questions : undefined
  if (!Array.isArray(questions)) throw new Error("driver observed no structured Gaia control question")
  const question = questions[0]
  const requestID = `question-protected-${++questionIndex}`
  const callID = `question-call-protected-${questionIndex}`
  await plugin["tool.execute.before"](
    { sessionID: childSessionID, callID, tool: "question" },
    { args: { questions } },
  )
  await plugin.event({ event: {
    type: "question.asked",
    properties: { sessionID: childSessionID, id: requestID, questions },
  } })
  const selected = question.options[1].label
  await plugin.event({ event: {
    type: "question.replied",
    properties: { sessionID: childSessionID, requestID, answers: [[selected]] },
  } })
  await plugin["tool.execute.after"](
    { sessionID: childSessionID, callID, tool: "question", args: { questions } },
    { output: "User has answered your questions.", metadata: { answers: [[selected]] } },
  )
  await plugin.event({ event: { type: "session.idle", properties: { sessionID: childSessionID } } })
}
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
  const before = permissionAsks.length
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
  // Host permission delivery is a separate event, not a successful return from
  // the pre-tool hook. Scenarios request it explicitly; the real hook correlates it.
  if (step.requestPermission) {
    const permission = {
      id: `permission-${permissionAsks.length + 1}`,
      sessionID: childSessionID,
      callID,
      title: "host permission",
      metadata: {},
    }
    const permissionOutput = { status: "ask" as const }
    await plugin["permission.ask"](permission, permissionOutput)
    permissionAsks.push({ permission, status: permissionOutput.status })
    if (permissionOutput.status === "ask") {
      await plugin.event({ event: { type: "session.idle", properties: { sessionID: childSessionID } } })
      await rejectActiveControl()
    }
    result.allowed = result.allowed === true && permissionOutput.status === "allow"
  } else if (controlPrompts.length > promptsBefore) {
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: childSessionID } } })
  }
  result.permissionIndexes = Array.from(
    { length: permissionAsks.length - before },
    (_, offset) => before + offset,
  )
  results.push(result)
}

console.log(JSON.stringify({ results, permissionAsks, controlPrompts }))
