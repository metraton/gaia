/**
 * Drives the real GaiaOpenCodePlugin through the two places the orchestrator
 * resumes a specialist instead of dispatching a fresh one: continuing a
 * background Task by task_id after its child went idle, and the notice the
 * orchestrator reads after the user rejects a signature.
 *
 * Usage: bun resume_continuity_driver.ts
 */

import { GaiaOpenCodePlugin, presenterNotice } from "../../opencode/plugin.ts"

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
    if (code !== 0) throw new Error("Resume fixture identity issuance failed")
    return JSON.parse(output)
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

async function dispatch(callID: string, background: boolean, taskID?: string): Promise<void> {
  const args: Record<string, unknown> = { subagent_type: "developer" }
  if (taskID) args.task_id = taskID
  await plugin["tool.execute.before"]({ tool: "task", sessionID: "ses-parent", callID }, { args })
  await plugin.event({ event: {
    type: "message.part.updated",
    properties: { part: {
      type: "tool", tool: "task", sessionID: "ses-parent", callID,
      state: { metadata: { sessionId: "ses-child-bg" } },
    } },
  } })
  const metadata = { sessionId: "ses-child-bg", ...(background ? { background: true } : {}) }
  await plugin["tool.execute.after"](
    { tool: "task", sessionID: "ses-parent", callID, args },
    { output: background ? "running" : "child finished", metadata },
  )
}

const stages: Record<string, string | null> = {}

async function attempt(stage: string, run: () => Promise<void>): Promise<void> {
  try {
    await run()
    stages[stage] = null
  } catch (error) {
    stages[stage] = error instanceof Error ? error.message : String(error)
  }
}

await attempt("backgroundLaunch", () => dispatch("call-bg-1", true))
await plugin.event({ event: { type: "session.idle", properties: { sessionID: "ses-child-bg" } } })
await attempt("resumeAfterBackgroundIdle", () => dispatch("call-bg-2", false, "ses-child-bg"))

const approval = { approvalID: `P-${"a".repeat(32)}`, role: "developer", sessionID: "ses-child-bg" } as any

console.log(JSON.stringify({
  stages,
  rejectedNotice: presenterNotice(approval, "decided", "reject"),
}))
