/**
 * Drives the real GaiaOpenCodePlugin through a background Task whose child
 * closes with a notice for the orchestrator, then two real parent messages,
 * and prints the synthetic context each message received.
 *
 * Usage: bun background_notice_driver.ts
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const NOTICE = "developer closed ses-child-bg in the background; read its row."

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
    if (code !== 0) throw new Error("Background notice fixture identity issuance failed")
    return JSON.parse(output)
  }
  if (event.event === "session.idle" && event.sessionID === "ses-child-bg") {
    return { contract_valid: true, orchestrator_notice: NOTICE, closed: { status: "already_closed" } }
  }
  return { action: "allow" as const }
}

const client = {
  session: {
    get: async ({ path }: { path: { id: string } }) => ({
      data: { id: path.id, parentID: path.id === "ses-parent" ? undefined : "ses-parent" },
    }),
    promptAsync: async () => ({ data: {} }),
  },
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client })

await plugin.event({ event: {
  type: "message.updated",
  properties: { info: { role: "assistant", sessionID: "ses-parent", agent: "gaia-orchestrator" } },
} })

const args = { subagent_type: "developer" }
await plugin["tool.execute.before"]({ tool: "task", sessionID: "ses-parent", callID: "call-bg" }, { args })
await plugin.event({ event: {
  type: "message.part.updated",
  properties: { part: {
    type: "tool", tool: "task", sessionID: "ses-parent", callID: "call-bg",
    state: { metadata: { sessionId: "ses-child-bg" } },
  } },
} })
const launch = { output: "running", metadata: { sessionId: "ses-child-bg", background: true } }
await plugin["tool.execute.after"]({ tool: "task", sessionID: "ses-parent", callID: "call-bg", args }, launch)
await plugin.event({ event: { type: "session.idle", properties: { sessionID: "ses-child-bg" } } })

async function parentMessage(): Promise<string> {
  const output = {
    message: { id: `msg_${Math.random().toString(36).slice(2)}`, sessionID: "ses-parent", role: "user" },
    parts: [{ type: "text", text: "next", sessionID: "ses-parent" }] as any[],
  }
  await plugin["chat.message"]({ sessionID: "ses-parent", agent: "gaia-orchestrator" }, output)
  return output.parts.filter((part) => part.synthetic).map((part) => part.text).join("\n")
}

const contexts = [await parentMessage(), await parentMessage()]
console.log(JSON.stringify({ notice: NOTICE, launchOutput: launch.output, contexts }))
