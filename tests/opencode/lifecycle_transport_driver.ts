/**
 * Drives the real GaiaOpenCodePlugin.event handler over the lifecycle
 * transport set (task 536, T8) and prints what it sent Gaia's bridge.
 *
 * gaiaBridge is a recording stub, not the real bridge: this driver exercises
 * only what the plugin forwards, never bridge.py's own routing (that is
 * covered separately by test_lifecycle_transport_gate.py against the real
 * bridge.handle).
 *
 * Usage: bun lifecycle_transport_driver.ts
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const requests: Record<string, unknown>[] = []

async function gaiaBridge(event: Record<string, unknown>) {
  requests.push(event)
  return { action: "allow" as const }
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge })

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
