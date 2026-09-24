/**
 * Drives the real GaiaOpenCodePlugin through one blocked tool call and prints
 * what it did about presenting the approval.
 *
 * The plugin runs here and executes the real `gaia approvals opencode-present`
 * CLI against the database in GAIA_DB; what is captured is the control-plane
 * prompt it sent, the bridge traces, the cwd of every Gaia process and the
 * abort the original invocation received.
 *
 * Usage: bun presentation_driver.ts '<scenario json>'
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"
import { assertPromptAsyncBody } from "./sdk_body_contract.ts"

const scenario = JSON.parse(process.argv[2])
const controlPrompts: Record<string, unknown>[] = []
const bridgeEvents: Record<string, unknown>[] = []
const deletedSessions: string[] = []

// The cwd of every `bin/gaia` process the plugin starts, observed at the spawn
// boundary: the workspace Gaia attributes the presentation to is derived from
// that cwd, so it is the fact under test, not the plugin's directory field.
const gaiaSpawnCwds: (string | undefined)[] = []
const hostSpawn = Bun.spawn
Bun.spawn = ((argv: string[], options?: { cwd?: string }) => {
  if (Array.isArray(argv) && argv.some((token) => String(token).endsWith("/bin/gaia"))) {
    gaiaSpawnCwds.push(options?.cwd)
  }
  return hostSpawn(argv, options as any)
}) as typeof Bun.spawn

async function gaiaBridge(event: Record<string, unknown>) {
  if (["permission.uncorrelated", "control.opened", "control.closed", "decision.applied"].includes(String(event.event))) {
    bridgeEvents.push(event)
    return { action: "allow" as const }
  }
  if (event.event === "identity.attest") {
    return { action: "allow" as const, attestation: `${event.sessionID}:${event.role}` }
  }
  if (event.event === "tool.execute.before") {
    if (String(event.tool).toLowerCase() === "task") return { action: "allow" as const }
    if (scenario.outcome === "timeout") {
      throw new Error("Gaia policy bridge timed out")
    }
    if (scenario.outcome === "rejected") {
      return { action: "deny" as const, reason: "Gaia rejected this tool call" }
    }
    if (scenario.outcome === "malformed") {
      return {} as any
    }
    return {
      action: "ask" as const,
      reason: scenario.reason ?? `[T3_BLOCKED] approval_id: ${scenario.approvalID}`,
      approval_id: scenario.approvalID,
    }
  }
  return { action: "allow" as const }
}

const client = {
  session: {
    async messages() {
      return { data: [{ info: { role: "assistant", agent: "gaia-orchestrator" } }] }
    },
    async promptAsync(request: Record<string, unknown>) {
      assertPromptAsyncBody(request)
      controlPrompts.push(request)
      if (scenario.controlPrompt === "rejected") {
        // The SDK client resolves the host's schema rejection as data, not a throw.
        return {
          error: { name: "BadRequestError", data: { message: 'schema rejection kind=Payload at ["system"]' } },
          response: { ok: false, status: 400 },
        }
      }
      return { data: undefined, response: { ok: true, status: 204 } }
    },
    async delete({ path }: any) {
      deletedSessions.push(path.id)
      return { data: true, response: { ok: true, status: 200 } }
    },
  },
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client, directory: scenario.directory })
const rootSessionID = "ses-t4-root"
const dispatchCallID = "call-t4-dispatch"

await plugin.event({ event: {
  type: "message.updated",
  properties: { info: { role: "assistant", sessionID: rootSessionID, agent: "gaia-orchestrator" } },
} })
await plugin["tool.execute.before"](
  { sessionID: rootSessionID, callID: dispatchCallID, tool: "task" },
  { args: { subagent_type: "gaia-system" } },
)
await plugin.event({ event: {
  type: "message.part.updated",
  properties: { part: {
    type: "tool",
    tool: "task",
    sessionID: rootSessionID,
    callID: dispatchCallID,
    state: { metadata: { sessionId: scenario.sessionID } },
  } },
} })
await plugin["tool.execute.after"](
  { sessionID: rootSessionID, callID: dispatchCallID, tool: "task", args: { subagent_type: "gaia-system" } },
  { metadata: { sessionId: scenario.sessionID }, output: "" },
)

let error: string | undefined
let originalInvocationExecuted = false
let requestResultOutput: string | undefined
if (scenario.requestOutput !== undefined) {
  // A request made proactively: the specialist's own `gaia approvals request-*`
  // call ran and printed its approval id; nothing was attempted or blocked.
  const result = { title: "", output: scenario.requestOutput, metadata: { exit: 0 } }
  try {
    await plugin["tool.execute.after"](
      { sessionID: scenario.sessionID, callID: scenario.callID, tool: "bash", args: scenario.args ?? {} },
      result,
    )
  } catch (thrown: any) {
    error = String(thrown?.message ?? thrown)
  }
  requestResultOutput = result.output
} else {
  try {
    await plugin["tool.execute.before"](
      { sessionID: scenario.sessionID, callID: scenario.callID, tool: scenario.tool ?? "bash" },
      { args: scenario.args ?? {} },
    )
    originalInvocationExecuted = true
  } catch (thrown: any) {
    // The throw is the host-visible abort signal for the original invocation.
    error = String(thrown?.message ?? thrown)
  }
}

// The blocked attempt ends the specialist's turn, so the host idles its session.
if (scenario.outcome === undefined || scenario.outcome === "pending" || scenario.outcome === "no-decision") {
  try {
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: scenario.sessionID } } })
  } catch (thrown: any) {
    error = String(thrown?.message ?? thrown)
  }
}

// A control still registered for the child session makes the plugin refuse any
// non-question tool result on it; a released one lets the probe fall through.
let controlSessionLingered: boolean | undefined
if (scenario.controlPrompt !== undefined) {
  try {
    await plugin["tool.execute.after"](
      { sessionID: scenario.sessionID, callID: "probe-after", tool: "bash" },
      { title: "probe", output: "", metadata: {} },
    )
    controlSessionLingered = false
  } catch (thrown: any) {
    controlSessionLingered = String(thrown?.message ?? thrown).includes("control-plane")
  }
}

console.log(JSON.stringify({
  controlPrompts, bridgeEvents, deletedSessions, controlSessionLingered, error, originalInvocationExecuted,
  gaiaSpawnCwds, requestResultOutput,
}))
