/**
 * Drives the real GaiaOpenCodePlugin and prints the permission payload it can
 * serialize at the host hook boundary.
 *
 * The affirmative claim in this task's gate is about a DELIVERED payload, so it
 * may not be asserted over a shape written by hand: the plugin runs here, its
 * own requestApproval executes the real `gaia approvals opencode-present` CLI
 * against the database in GAIA_DB, and the object captured below is the exact
 * permission object the plugin enriches in permission.ask. The driver invokes
 * that hook explicitly after the fail-closed throw; OpenCode 1.18.23 does not,
 * so this is serializer evidence rather than host-delivery evidence.
 *
 * Usage: bun presentation_driver.ts '<scenario json>'
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const scenario = JSON.parse(process.argv[2])
const asked: Record<string, unknown>[] = []

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

const controlQuestions: Record<string, unknown>[] = []
let plugin: any
const client = {
  session: {
    messages: async () => ({ data: [{ info: { role: "assistant", agent: "gitops-operator" } }] }),
    create: async () => ({ data: { id: "ses-presentation-control" } }),
    promptAsync: async ({ path, body }: any) => {
      const payload = JSON.parse(String(body.parts[0].text).split("\n").at(-1)!)
      const request = { id: "que-presentation", sessionID: path.id, questions: payload.questions }
      controlQuestions.push(request)
      await plugin.event({ event: { type: "question.asked", properties: request } })
      return { data: true }
    },
  },
}

plugin = await GaiaOpenCodePlugin({ gaiaBridge, client })

let error: string | undefined
try {
  await plugin["tool.execute.before"](
    { sessionID: scenario.sessionID, callID: scenario.callID, tool: scenario.tool ?? "bash" },
    { args: scenario.args ?? {} },
  )
} catch (thrown: any) {
  error = String(thrown?.message ?? thrown)
}

// Repository-only seam: OpenCode 1.18.23 does not invoke this hook after a
// pre-tool failure. Calling it explicitly keeps the presentation serializer
// testable without claiming that the host delivers this sequence.
try {
  if (!error?.startsWith("Gaia blocked this invocation")) throw new Error(error ?? "approval was not blocked")
  const permission = {
    id: scenario.permissionID ?? "perm-1",
    sessionID: scenario.sessionID,
    callID: scenario.callID,
    title: "host permission",
    metadata: {},
  }
  const permissionOutput = { status: "ask" as const }
  await plugin["permission.ask"](permission, permissionOutput)
  asked.push({ permission, status: permissionOutput.status })
} catch (thrown: any) {
  error ??= String(thrown?.message ?? thrown)
}

console.log(JSON.stringify({ asked, error, controlQuestions }))
