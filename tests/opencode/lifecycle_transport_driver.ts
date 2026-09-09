/**
 * Drives the real GaiaOpenCodePlugin.event handler over the lifecycle
 * transport set (task 536, T8) and prints what it sent Gaia's bridge.
 *
 * Tool/lifecycle policy is a recording stub; identity uses the real issuer.
 * This exercises what the plugin forwards, never bridge.py's lifecycle routing (that is
 * covered separately by test_lifecycle_transport_gate.py against the real
 * bridge.handle).
 *
 * Usage: bun lifecycle_transport_driver.ts
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const requests: Record<string, unknown>[] = []

async function gaiaBridge(event: Record<string, unknown>) {
  if (event.event === "identity.attest") {
    const bridgePath = new URL("./isolated_bridge.py", import.meta.url).pathname
    const child = Bun.spawn(["python3", "-B", bridgePath], {
      cwd: process.env.WORKSPACE,
      stdin: "pipe", stdout: "pipe", stderr: "pipe",
    })
    child.stdin.write(JSON.stringify(event))
    child.stdin.end()
    const [code, output] = await Promise.all([child.exited, new Response(child.stdout).text()])
    if (code !== 0) throw new Error("Lifecycle fixture identity issuance failed")
    return JSON.parse(output)
  }
  if (event.event !== "tool.execute.before") requests.push(event)
  return { action: "allow" as const }
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge })

await plugin.event({ event: {
  type: "message.updated",
  properties: { info: { role: "assistant", sessionID: "ses-parent", agent: "gaia-orchestrator" } },
} })
await plugin["tool.execute.before"](
  { tool: "task", sessionID: "ses-parent", callID: "call-dispatch-1" },
  { args: { subagent_type: "developer" } },
)

await plugin.event({
  event: {
    type: "message.part.updated",
    properties: {
      part: {
        type: "tool",
        tool: "task",
        sessionID: "ses-parent",
        callID: "call-dispatch-1",
        state: { metadata: { sessionId: "ses-child-1" } },
      },
    },
  },
})

for (const type of ["session.idle", "session.error", "session.deleted", "session.compacted"]) {
  await plugin.event({ event: { type, properties: { sessionID: "ses-parent" } } })
}

console.log(JSON.stringify({ requests }))
