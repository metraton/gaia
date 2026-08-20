/**
 * Drives the real GaiaOpenCodePlugin through a consent retry and reports every
 * boundary it crossed.
 *
 * The claim this driver exists to support is about a SEQUENCE of tool calls
 * sharing one identity, so nothing in the sequence may be hand-written: the
 * plugin closure runs, its own `bridge()` is reached through a recorder that
 * forwards verbatim to the real `opencode/bridge.py`, and `requestApproval`
 * executes the real `gaia approvals opencode-present` / `opencode-decide`
 * CLIs against the database in GAIA_DB.
 *
 * Two seams are doubled, and only two, because OpenCode owns both and no
 * OpenCode host runs here: `session.permission.create` (the native permission
 * mechanism) and the host's decision to invoke a tool at all. The second is
 * why a `before` step in this scenario proves what the PLUGIN does with an
 * invocation carrying a given session/call identity, and never that OpenCode
 * would deliver that invocation -- an invocation this driver issues is this
 * driver's, and the Python side states that limit rather than asserting past
 * it.
 *
 * Usage: bun consent_retry_driver.ts '<scenario json>'
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const bridgePath = new URL("../../opencode/bridge.py", import.meta.url).pathname

type Exchange = {
  sent: Record<string, unknown>
  received: unknown
  /** JSON.stringify of the args the plugin forwarded, for byte comparison. */
  sentArgsJSON: string
}

const exchanges: Exchange[] = []
const permissionCreates: Record<string, unknown>[] = []
const stepResults: Record<string, unknown>[] = []

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
  return received
}

const scenario = JSON.parse(process.argv[2])

const client = {
  session: {
    permission: {
      create: async (payload: Record<string, unknown>) => {
        permissionCreates.push(payload)
        return { data: { id: scenario.permissionID ?? "perm-1" } }
      },
    },
  },
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client })

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
      record.allowed = true
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
    } else if (step.kind === "replied") {
      await plugin.event({
        event: {
          type: step.eventType ?? "permission.replied",
          properties: { requestID: step.requestID, reply: step.reply },
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

console.log(JSON.stringify({ steps: stepResults, exchanges, permissionCreates }))
