/**
 * Probe-only OpenCode plugin entry point for gate 1005 (task/T6, plan 65).
 *
 * The REAL production bridge (opencode/bridge.py) has no live path today that
 * mutates a dispatched task's prompt via ``updated_input`` -- that is T9's
 * kernel-injection work, not this task's. This wrapper loads the REAL,
 * unmodified GaiaOpenCodePlugin export from opencode/plugin.ts (the file this
 * task fixed) and forwards every event to the real bridge.py EXCEPT one: the
 * task tool's ``tool.execute.before``, where it substitutes the decision a
 * future kernel-injection policy would make (an ``updated_input.prompt``
 * override) so the REAL field-by-field ``applyUpdatedInput`` mechanism can be
 * driven end to end, in a real host process, against a real dispatched child,
 * without waiting on the not-yet-built policy that will one day supply that
 * decision for real. Every other event (identity.attest, tool.execute.after,
 * the bash tool) is answered by the real bridge.py subprocess, unmodified.
 *
 * This is the same test-double pattern tests/opencode/attestation_driver.ts
 * already uses to isolate one decision point while keeping the rest of the
 * plugin's real code path live.
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const bridgePath = new URL("../../opencode/bridge.py", import.meta.url).pathname

const MUTATED_MARKER = process.env.GAIA_PROBE_MUTATED_PROMPT ?? "PROBE_MUTATED_534_DEFAULT"

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

async function probeBridge(event: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (event.event === "tool.execute.before" && event.tool === "task") {
    console.error(`[probe-wrapper] substituting updated_input.prompt for call ${event.callID}`)
    return { action: "allow", updated_input: { prompt: MUTATED_MARKER } }
  }
  return realBridge(event)
}

export default {
  id: "gaia",
  server: (input: any) => GaiaOpenCodePlugin({ ...input, gaiaBridge: probeBridge }),
}
