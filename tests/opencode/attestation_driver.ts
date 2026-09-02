/**
 * Drives the real GaiaOpenCodePlugin closure and prints what it sent Gaia.
 *
 * The affirmative half of this task's coverage may not assert over a
 * hand-written payload: three earlier rounds of this plan passed while
 * asserting a shape no adapter emits. So the plugin runs here, its own
 * roleContext() composes the claim, and identity.attest is answered by the real
 * Gaia-side bridge -- only the tool events are stubbed, because policy for them
 * is what the Python test then evaluates in process.
 *
 * Usage: bun attestation_driver.ts '<scenario json>'
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
const plugin: any = await GaiaOpenCodePlugin({
  gaiaBridge,
  client: {},
})

for (const step of scenario.steps) {
  if (step.kind === "message") {
    await plugin.event({
      event: {
        type: "message.updated",
        properties: { info: { role: "assistant", sessionID: step.sessionID, agent: step.agent } },
      },
    })
  } else if (step.kind === "before") {
    await plugin["tool.execute.before"](
      { sessionID: step.sessionID, callID: step.callID, tool: step.tool },
      { args: step.args ?? {} },
    )
  } else if (step.kind === "after") {
    await plugin["tool.execute.after"](
      { sessionID: step.sessionID, callID: step.callID, tool: step.tool, args: step.args ?? {} },
      { output: step.output ?? "", metadata: step.metadata ?? {} },
    )
  } else if (step.kind === "after-task") {
    await plugin["tool.execute.after"](
      { sessionID: step.sessionID, callID: step.callID, tool: "task", args: step.args ?? {} },
      { metadata: { sessionId: step.childSessionID }, output: "" },
    )
  } else {
    throw new Error(`unknown scenario step: ${step.kind}`)
  }
}

console.log(JSON.stringify(requests))
