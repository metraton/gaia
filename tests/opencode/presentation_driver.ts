/**
 * Drives the real GaiaOpenCodePlugin and prints what it handed OpenCode's
 * native permission mechanism.
 *
 * The affirmative claim in this task's gate is about a DELIVERED payload, so it
 * may not be asserted over a shape written by hand: the plugin runs here, its
 * own requestApproval executes the real `gaia approvals opencode-present` CLI
 * against the database in GAIA_DB, and the object captured below is the exact
 * argument the plugin passes to session.permission.create. Only the two edges
 * OpenCode itself owns are doubles -- the policy bridge answer and the
 * permission mechanism -- because the point is what crosses them.
 *
 * Usage: bun presentation_driver.ts '<scenario json>'
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const scenario = JSON.parse(process.argv[2])
const created: Record<string, unknown>[] = []

async function gaiaBridge(event: Record<string, unknown>) {
  if (event.event === "identity.attest") {
    return { action: "allow" as const, attestation: `${event.sessionID}:${event.role}` }
  }
  if (event.event === "tool.execute.before") {
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
    permission: {
      create: async (payload: Record<string, unknown>) => {
        created.push(payload)
        return { data: { id: scenario.permissionID ?? "perm-1" } }
      },
    },
  },
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client })

let error: string | undefined
try {
  await plugin["tool.execute.before"](
    { sessionID: scenario.sessionID, callID: scenario.callID, tool: scenario.tool ?? "bash" },
    { args: scenario.args ?? {} },
  )
} catch (thrown: any) {
  // tool.execute.before always ends a non-allow decision by throwing; the
  // presented payload is what this driver exists to report, so the throw is
  // recorded rather than propagated.
  error = String(thrown?.message ?? thrown)
}

console.log(JSON.stringify({ created, error }))
