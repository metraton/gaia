/**
 * Drives the real GaiaOpenCodePlugin closure over the identity race and prints
 * what it sent Gaia.
 *
 * The scenario deliberately omits message.updated: this is the window where a
 * task call reaches tool.execute.before before the event bus has named the
 * session. The host's message record is supplied through a client stub -- the
 * same object the bus event would have carried -- so what the plugin resolves
 * still comes from the host and never from a name this driver hands the edge.
 * identity.attest is answered by the real Gaia-side bridge; tool events are
 * stubbed, because the policy for them is evaluated elsewhere.
 *
 * Usage: bun race_identity_driver.ts '<scenario json>'
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const bridgePath = new URL("../../opencode/bridge.py", import.meta.url).pathname
const requests: Record<string, unknown>[] = []

async function gaiaBridge(event: Record<string, unknown>) {
  requests.push(event)
  if (event.event !== "identity.attest") return { action: "allow" as const }
  const child = Bun.spawn(["python3", bridgePath], {
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
  })
  child.stdin.write(JSON.stringify(event))
  child.stdin.end()
  const [code, output] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
  ])
  if (code !== 0) throw new Error("Gaia attestation bridge exited without a response")
  return JSON.parse(output)
}

const scenario = JSON.parse(process.argv[2])
const hostMessages: Record<string, { info: Record<string, unknown> }[]> =
  scenario.messages ?? {}
const messageReads: string[] = []

const client = scenario.clientHasSessionApi === false
  ? {}
  : {
      session: {
        async messages({ sessionID }: { sessionID: string }) {
          messageReads.push(sessionID)
          return { data: hostMessages[sessionID] ?? [] }
        },
      },
    }

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client })

let denial: string | undefined
try {
  for (const step of scenario.steps) {
    if (step.kind === "message") {
      await plugin.event({
        event: {
          type: "message.updated",
          properties: {
            info: { role: "assistant", sessionID: step.sessionID, agent: step.agent },
          },
        },
      })
    } else if (step.kind === "before") {
      await plugin["tool.execute.before"](
        { sessionID: step.sessionID, callID: step.callID, tool: step.tool },
        { args: step.args ?? {} },
      )
    } else {
      throw new Error(`unknown scenario step: ${step.kind}`)
    }
  }
} catch (error: any) {
  denial = error?.message ?? String(error)
}

console.log(JSON.stringify({ requests, messageReads, denial }))
