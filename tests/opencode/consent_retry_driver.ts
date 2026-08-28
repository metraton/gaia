/**
 * Drives the real GaiaOpenCodePlugin through a consent retry and reports every
 * boundary it crossed.
 *
 * The claim this driver exists to support is about a SEQUENCE of tool calls
 * bound by content while each keeps its real call identity, so nothing in the sequence may be hand-written: the
 * plugin closure runs, its own `bridge()` is reached through a recorder that
 * forwards verbatim to the real `opencode/bridge.py`, and `requestApproval`
 * executes the real `gaia approvals opencode-present` / `opencode-decide`
 * CLIs against the database in GAIA_DB.
 *
 * Two seams are doubled, and only two, because OpenCode owns both and no
 * OpenCode host runs here: a permission request shape and the host's decision
 * to invoke a tool at all. OpenCode 1.18.23 does not deliver the first after a
 * failed pre-tool hook, so it is a serializer fixture, not host evidence. The second is
 * why a `before` step in this scenario proves what the PLUGIN does with an
 * invocation carrying a given session/call identity, and never that OpenCode
 * would deliver that invocation -- an invocation this driver issues is this
 * driver's, and the Python side states that limit rather than asserting past
 * it.
 *
 * Usage: bun consent_retry_driver.ts '<scenario json>'
 */

const pluginURL = process.env.GAIA_OPENCODE_PLUGIN_URL
  ?? new URL("../../opencode/plugin.ts", import.meta.url).href
const pluginModule = await import(pluginURL)
const { GaiaOpenCodePlugin } = pluginModule
if (pluginModule.default?.server !== GaiaOpenCodePlugin) {
  throw new Error("installed export default.server is not GaiaOpenCodePlugin")
}

const bridgePath = new URL("../../opencode/bridge.py", import.meta.url).pathname

type Exchange = {
  sent: Record<string, unknown>
  received: unknown
  /** JSON.stringify of the args the plugin forwarded, for byte comparison. */
  sentArgsJSON: string
}

const exchanges: Exchange[] = []
const permissionAsks: Record<string, unknown>[] = []
const controlQuestions: Record<string, any>[] = []
const stepResults: Record<string, unknown>[] = []
let lastBridgeAction: string | undefined

async function gaiaBridge(event: Record<string, unknown>) {
  const child = Bun.spawn(["python3", bridgePath], {
    env: { ...process.env, GAIA_HOST: "opencode" },
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
  })
  child.stdin.write(JSON.stringify(event))
  child.stdin.end()
  const [code, stdout, stderr] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ])
  if (code !== 0) {
    throw new Error(`Gaia policy bridge exited ${code}: ${stderr}`)
  }
  const received = JSON.parse(stdout.trim().split("\n").pop() ?? "null")
  exchanges.push({
    sent: event,
    received,
    sentArgsJSON: JSON.stringify(event.args ?? null),
  })
  lastBridgeAction = (received as any)?.action
  return received
}

const scenario = JSON.parse(process.argv[2])

let plugin: any
let controlSequence = 0
const client = {
  session: {
    create: async () => ({ data: { id: `ses-consent-control-${++controlSequence}` } }),
    promptAsync: async ({ path, body }: any) => {
      const text = body.parts[0].text as string
      const payload = JSON.parse(text.split("\n").at(-1)!)
      const request = { id: `que-control-${controlSequence}`, sessionID: path.id, questions: payload.questions }
      controlQuestions.push(request)
      await plugin.event({ event: { type: "question.asked", properties: request } })
      return { data: true }
    },
  },
}

plugin = await GaiaOpenCodePlugin({ gaiaBridge, client })

for (const step of scenario.steps) {
  const record: Record<string, unknown> = { kind: step.kind, label: step.label }
  try {
    if (step.kind === "before") {
      // One args object per step, built here so the Python side can compare the
      // bytes the plugin forwarded across two steps that declare the same input.
      await plugin["tool.execute.before"](
        { sessionID: step.sessionID, callID: step.callID, tool: step.tool ?? "bash" },
        { args: step.args ?? { command: step.command } },
      )
      if (lastBridgeAction === "allow") {
        record.allowed = true
        stepResults.push(record)
        continue
      }
      record.allowed = true
      record.originalExecutionReachable = true
      record.error = "ORIGINAL_EXECUTION_REACHABLE: exported tool.execute.before returned after a non-allow decision"
      stepResults.push(record)
      continue
    } else if (step.kind === "after") {
      await plugin["tool.execute.after"](
        {
          sessionID: step.sessionID,
          callID: step.callID,
          tool: step.tool ?? "bash",
          args: { command: step.command },
        },
        { output: step.output ?? "", metadata: step.metadata ?? {} },
      )
      record.allowed = true
    } else if (step.kind === "message") {
      await plugin.event({
        event: {
          type: "message.updated",
          properties: {
            info: { role: "assistant", sessionID: step.sessionID, agent: step.agent },
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "after-task") {
      await plugin["tool.execute.after"](
        { sessionID: step.sessionID, callID: step.callID, tool: "task", args: step.args ?? {} },
        { metadata: { sessionId: step.childSessionID }, output: "" },
      )
      record.allowed = true
    } else if (step.kind === "task-part") {
      // The real host's callID<->child-session binding signal (measured:
      // project_gaia_opencode_lifecycle_medido_2026_08_26), on the
      // DISPATCHING session's own part -- see lifecycle_transport_driver.ts
      // for the identical shape exercised against a recording stub.
      await plugin.event({
        event: {
          type: "message.part.updated",
          properties: {
            part: {
              type: "tool",
              tool: "task",
              sessionID: step.sessionID,
              callID: step.callID,
              state: { metadata: { sessionId: step.childSessionID } },
            },
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "replied") {
      await plugin.event({
        event: {
          type: step.eventType ?? "permission.replied",
          properties: {
            sessionID: step.sessionID ?? scenario.sessionID,
            permissionID: step.requestID,
            response: step.reply,
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "question-reply") {
      const request = controlQuestions.at(-1)
      if (!request) throw new Error("no control-plane question was asked")
      const option = step.decision === "once" ? request.questions[0].options[0].label
        : step.decision === "reject" ? request.questions[0].options[1].label
        : String(step.decision)
      await plugin.event({
        event: {
          type: "question.replied",
          properties: { sessionID: request.sessionID, requestID: request.id, answers: [[option]] },
        },
      })
      record.allowed = true
    } else if (step.kind === "question-reject") {
      const request = controlQuestions.at(-1)
      if (!request) throw new Error("no control-plane question was asked")
      await plugin.event({
        event: {
          type: "question.rejected",
          properties: { sessionID: request.sessionID, requestID: request.id },
        },
      })
      record.allowed = true
    } else {
      throw new Error(`unknown scenario step: ${step.kind}`)
    }
  } catch (thrown: any) {
    // tool.execute.before ends every non-allow decision by throwing. The throw
    // IS the observation for a blocked step, so it is recorded rather than
    // propagated -- a driver that died here would report nothing.
    record.allowed = false
    record.error = String(thrown?.message ?? thrown)
  }
  stepResults.push(record)
}

console.log(JSON.stringify({
  artifact: pluginURL,
  exportedEntry: "default.server",
  steps: stepResults,
  exchanges,
  permissionAsks,
  controlQuestions,
}))
