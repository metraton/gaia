import { fileURLToPath } from "node:url"

type BridgeResponse = {
  action: "allow" | "ask" | "deny"
  reason?: string
  approval_id?: string
  updated_input?: Record<string, unknown>
}

type PendingApproval = {
  approvalID: string
  sessionID: string
  callID: string
  token: string
}

const bridgePath = fileURLToPath(new URL("./bridge.py", import.meta.url))
const gaiaPath = fileURLToPath(new URL("../bin/gaia", import.meta.url))

async function bridge(event: Record<string, unknown>): Promise<BridgeResponse> {
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
  return JSON.parse(output) as BridgeResponse
}

async function gaia(args: string[]): Promise<boolean> {
  const child = Bun.spawn(["python3", gaiaPath, ...args], {
    env: { ...process.env, GAIA_HOST: "opencode" },
    stdout: "pipe",
    stderr: "pipe",
  })
  return (await child.exited) === 0
}

function approvalID(response: BridgeResponse): string | undefined {
  if (response.approval_id) return response.approval_id
  return response.reason?.match(/approval_id:\s*(P-[A-Za-z0-9-]+)/)?.[1]
}

export function toolResult(output: any): Record<string, unknown> {
  const text = typeof output?.output === "string" ? output.output : ""
  const match = text.match(/(?:Command exited with code|exit code)\s+(\d+)/i)
  const metadata = output?.metadata ?? {}
  const structured = metadata.exitCode ?? metadata.exit_code ?? metadata.exit ?? metadata.code ?? output?.exitCode ?? output?.exit_code
  const status = metadata.status ?? output?.status
  const error = metadata.error ?? output?.error
  const structuredNumber = Number(structured)
  let exitCode = structured !== undefined && Number.isInteger(structuredNumber)
    ? structuredNumber
    : match ? Number(match[1]) : 0
  if ((error || status === "error" || status === "failed") && exitCode === 0) exitCode = 1
  return {
    output: text,
    metadata,
    exit_code: exitCode,
    is_error: exitCode !== 0,
  }
}

export const GaiaOpenCodePlugin = async (input: any) => {
  const pending = new Map<string, PendingApproval>()
  const agentBySession = new Map<string, string>()
  const agentByCall = new Map<string, string>()

  async function decide(approval: PendingApproval, reply: "once" | "always" | "reject") {
    await gaia([
      "approvals", "opencode-decide", approval.approvalID,
      "--session-id", approval.sessionID,
      "--call-id", approval.callID,
      "--token", approval.token,
      "--reply", reply,
      "--json",
    ])
  }

  async function requestApproval(response: BridgeResponse, sessionID: string, callID: string) {
    const id = approvalID(response)
    if (!id) return
    const approval = { approvalID: id, sessionID, callID, token: crypto.randomUUID() }
    const presented = await gaia([
      "approvals", "opencode-present", id,
      "--session-id", sessionID,
      "--call-id", callID,
      "--token", approval.token,
      "--json",
    ])
    if (!presented) throw new Error("Gaia could not present the approval request")

    const created = await input.client.session.permission.create({
      sessionID,
      action: "gaia-approval",
      resources: [response.reason ?? id],
      metadata: { gaiaApprovalID: id, gaiaCallID: callID },
    })
    if (created.data.effect === "allow" || created.data.effect === "deny") {
      await decide(approval, created.data.effect === "allow" ? "once" : "reject")
      return
    }
    pending.set(created.data.id, approval)
  }

  return {
    event: async ({ event }) => {
      if (event.type === "message.updated") {
        const info = event.properties?.info
        if (info?.role === "assistant" && typeof info.sessionID === "string" && typeof info.agent === "string") {
          agentBySession.set(info.sessionID, info.agent)
        }
        return
      }
      if (event.type !== "permission.v2.replied") return
      const approval = pending.get(event.properties.requestID)
      if (!approval) return
      pending.delete(event.properties.requestID)
      await decide(approval, event.properties.reply)
    },
    "tool.execute.before": async (call, output) => {
      const agent = agentBySession.get(call.sessionID)
      if (call.tool === "task") {
        const requested = output.args?.subagent_type ?? output.args?.agent
        if (typeof requested === "string") agentByCall.set(call.callID, requested)
      }
      const response = await bridge({
        event: "tool.execute.before",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: agent,
        agent,
        tool: call.tool,
        args: output.args,
      })
      if (response.action === "allow") {
        if (response.updated_input) output.args = response.updated_input
        return
      }
      if (approvalID(response)) {
        await requestApproval(response, call.sessionID, call.callID)
      }
      throw new Error(response.reason ?? "Gaia denied this tool call without a persisted approval")
    },
    "tool.execute.after": async (call, output) => {
      const agent = agentBySession.get(call.sessionID)
      if (call.tool === "task") {
        const sessionID = output.metadata?.sessionId
        const dispatchedAgent = agentByCall.get(call.callID)
        if (typeof sessionID === "string" && dispatchedAgent) {
          agentBySession.set(sessionID, dispatchedAgent)
        }
      }
      await bridge({
        event: "tool.execute.after",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: agent,
        agent,
        tool: call.tool,
        args: call.args,
        result: toolResult(output),
      })
    },
  }
}
