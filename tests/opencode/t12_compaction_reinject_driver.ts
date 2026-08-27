/**
 * Drives the real GaiaOpenCodePlugin["experimental.session.compacting"]
 * hook (gate 1015, task 540, T12) with the EXACT input/output shape the
 * installed OpenCode 1.18.23 binary was decompiled to call it with:
 * `i.trigger("experimental.session.compacting", {sessionID}, {context: [],
 * prompt: void 0})` inside SessionCompaction.process.
 *
 * gaiaBridge is a recording+injecting stub standing in for the sealed,
 * not-yet-landed hooks/adapters/opencode.py::adapt_pre_compact (protected
 * file, pending approval): it returns the SAME updated_input.context shape
 * that method's design produces, so this driver proves what the ALREADY
 * LANDED plugin.ts hook does with that response, not what the adapter would
 * compute -- see test_opencode_t12_compaction_reinject.py for the real
 * kernel text (build_dispatch_kernel) this driver is fed.
 *
 * Usage: bun t12_compaction_reinject_driver.ts
 * Env: GAIA_T12_KERNEL (the kernel text to inject), GAIA_T12_SESSION_ID
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const sessionID = process.env.GAIA_T12_SESSION_ID ?? "ses-child-1"
const kernel = process.env.GAIA_T12_KERNEL ?? ""

const requests: Record<string, unknown>[] = []

async function gaiaBridge(event: Record<string, unknown>) {
  requests.push(event)
  if (event.event === "session.compacting" && kernel) {
    return { action: "allow" as const, updated_input: { context: [kernel] } }
  }
  return { action: "allow" as const }
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge })

const output: { context: string[]; prompt: undefined } = { context: [], prompt: undefined }
await plugin["experimental.session.compacting"]({ sessionID }, output)

console.log(JSON.stringify({ output, requests }))
