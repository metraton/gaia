/**
 * Drives the real GaiaOpenCodePlugin through a child turn the SubagentStop gate
 * rejects once and then accepts, and prints what reached the child and the parent.
 *
 * The bridge is scripted per lifecycle event and the OpenCode client is a
 * recording stub; identity uses the real issuer.
 *
 * Usage: bun subagent_stop_gate_driver.ts
 */

import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const idleVerdicts = [
  {
    contract_valid: false,
    repair_prompt: "[CONTRACT REJECTED] repair me",
    orchestrator_notice: "developer was returned for repair; continue task_id ses-child-1 to collect it.",
    closed: { status: "repair_requested" },
  },
  {
    contract_valid: true,
    user_message: "Summary for the user: the work is done.",
    closed: { status: "already_closed" },
  },
]
const prompts: unknown[] = []
let sends = 0

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
    if (code !== 0) throw new Error("Gate fixture identity issuance failed")
    return JSON.parse(output)
  }
  sends += 1
  if (event.event === "session.idle" && event.sessionID === "ses-child-1") return idleVerdicts.shift()
  return { action: "allow" as const }
}

const client = {
  session: {
    get: async ({ path }: { path: { id: string } }) => ({
      data: { id: path.id, parentID: path.id === "ses-parent" ? undefined : "ses-parent" },
    }),
    promptAsync: async (request: unknown) => {
      prompts.push(request)
      return { data: {} }
    },
  },
}

const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client })

await plugin.event({ event: {
  type: "message.updated",
  properties: { info: { role: "assistant", sessionID: "ses-parent", agent: "gaia-orchestrator" } },
} })

async function dispatch(callID: string, taskID?: string): Promise<string> {
  const args: Record<string, unknown> = { subagent_type: "developer" }
  if (taskID) args.task_id = taskID
  await plugin["tool.execute.before"]({ tool: "task", sessionID: "ses-parent", callID }, { args })
  await plugin.event({ event: {
    type: "message.part.updated",
    properties: { part: {
      type: "tool", tool: "task", sessionID: "ses-parent", callID,
      state: { metadata: { sessionId: "ses-child-1" } },
    } },
  } })
  await plugin.event({ event: { type: "session.idle", properties: { sessionID: "ses-child-1" } } })
  const output = { output: "child finished", metadata: { sessionId: "ses-child-1" } }
  await plugin["tool.execute.after"]({ tool: "task", sessionID: "ses-parent", callID, args }, output)
  return output.output
}

const firstTaskOutput = await dispatch("call-dispatch-1")

const sendsBefore = sends
const repair = (prompts[0] as any)?.body?.parts ?? []
await plugin["chat.message"](
  { sessionID: "ses-child-1", agent: "developer" },
  { message: { id: "msg-repair" }, parts: repair },
)
const sendsAfterSyntheticRepair = sends - sendsBefore

const secondTaskOutput = await dispatch("call-dispatch-2", "ses-child-1")

console.log(JSON.stringify({ prompts, firstTaskOutput, secondTaskOutput, sendsAfterSyntheticRepair }))
