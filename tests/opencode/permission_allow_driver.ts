/**
 * Drives the real GaiaOpenCodePlugin across BOTH halves of OpenCode's own
 * permission gate: a bash call Gaia ALLOWED, and a request Gaia never ruled on.
 *
 * OpenCode submits a tool to that gate AFTER tool.execute.before has already
 * returned Gaia's verdict, so what is measured here is whether the host's
 * second gate preserves the first one's answer. Nothing is hand-written: the
 * plugin runs, its real allow branch executes, and the status below is the one
 * the plugin itself wrote onto the host's mutable output object.
 *
 * Usage: bun permission_allow_driver.ts '<scenario json>'
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const scenario = JSON.parse(process.argv[2])
const bridgeEvents: Record<string, unknown>[] = []

async function gaiaBridge(event: Record<string, unknown>) {
  bridgeEvents.push(event)
  if (event.event === "identity.attest") {
    return { action: "allow" as const, attestation: `${event.sessionID}:${event.role}` }
  }
  if (event.event === "tool.execute.before") {
    return {
      action: "allow" as const,
      shell_env: {
        session_id: event.sessionID,
        call_id: event.callID,
        agent_type: event.agent,
      },
    }
  }
  return { action: "allow" as const }
}

const client = {
  session: {
    async messages() {
      return { data: [{ info: { role: "assistant", agent: "gaia-orchestrator" } }] }
    },
    async create({ body }: any) {
      return { data: { id: `control-${scenario.callID}`, title: body.title } }
    },
    async promptAsync() {},
  },
}

const plugin: any = await GaiaOpenCodePlugin({
  gaiaBridge,
  client,
  directory: scenario.directory ?? "/tmp",
})

let beforeError: string | undefined
if (scenario.runBefore !== false) {
  try {
    await plugin["tool.execute.before"](
      { sessionID: scenario.sessionID, callID: scenario.callID, tool: "bash" },
      { args: { command: scenario.command ?? "echo gaia" } },
    )
  } catch (thrown: any) {
    beforeError = String(thrown?.message ?? thrown)
  }
}

const permission = {
  id: scenario.permissionID ?? "perm-1",
  sessionID: scenario.askSessionID ?? scenario.sessionID,
  callID: scenario.askCallID ?? scenario.callID,
  title: "host permission",
  metadata: {},
}
const output = { status: "ask" as "ask" | "deny" | "allow" }
await plugin["permission.ask"](permission, output)

let secondStatus: string | undefined
if (scenario.askTwice) {
  const replay = { ...permission, metadata: {} }
  const replayOutput = { status: "ask" as "ask" | "deny" | "allow" }
  await plugin["permission.ask"](replay, replayOutput)
  secondStatus = replayOutput.status
}

// Every key is emitted explicitly rather than left undefined: JSON.stringify
// drops an undefined value, and a key the reader must find absent-or-null is
// indistinguishable from a key the driver forgot to compute.
console.log(JSON.stringify({
  status: output.status,
  secondStatus: secondStatus ?? null,
  beforeError: beforeError ?? null,
  bridgeEvents,
}))
