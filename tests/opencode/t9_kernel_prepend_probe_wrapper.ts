/**
 * Observability-only OpenCode plugin entry point for gate 1011 (task 537,
 * T9, plan 65).
 *
 * Loads the REAL, unmodified GaiaOpenCodePlugin export from
 * opencode/plugin.ts and forwards every event, unmodified, to the REAL
 * bridge.py subprocess -- exactly the ``realBridge`` pattern
 * tests/opencode/prompt_mutation_probe_wrapper.ts already uses. The only
 * addition is a console.error line around the ``identity.attest`` event so
 * the live probe can cite it literally as its positive-liveness precondition
 * (see agent-protocol's "no fourth" evidence rule); it changes nothing about
 * what the production bridge decides or returns.
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const bridgePath = new URL("../../opencode/bridge.py", import.meta.url).pathname

async function realBridge(event: Record<string, unknown>): Promise<Record<string, unknown>> {
  const child = Bun.spawn(["python3", bridgePath], {
    env: { ...process.env, GAIA_HOST: "opencode" },
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
  if (code !== 0) throw new Error("Gaia policy bridge exited without a response")
  return JSON.parse(output)
}

async function observingBridge(event: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (event.event === "identity.attest") {
    console.error(`[t9-probe] identity.attest sessionID=${event.sessionID} role=${event.role}`)
    const response = await realBridge(event)
    console.error(`[t9-probe] identity.attest -> action=${response.action} attestation=${response.attestation ? "issued" : "none"}`)
    return response
  }
  return realBridge(event)
}

export default {
  id: "gaia",
  server: (input: any) => GaiaOpenCodePlugin({ ...input, gaiaBridge: observingBridge }),
}
