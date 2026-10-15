/**
 * A tool call that arrives before the session's agent name has travelled the
 * event bus recovers it from the host's message record. OpenCode hands the
 * plugin its v1 client, which addresses a session as `{ path: { id } }`; a
 * request shaped `{ sessionID }` leaves the route's {id} unfilled.
 */

import { expect, test } from "bun:test"
import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

test("the agent of an unseen session is read back from the host with the v1 request shape", async () => {
  const sent: Record<string, any>[] = []
  const client = {
    session: {
      async get({ path }: { path: { id: string } }) {
        return { data: { id: path.id } }
      },
      async messages(options: { path?: { id?: string } }) {
        if (options?.path?.id !== "ses-main") return { error: { name: "NotFoundError" }, response: { ok: false, status: 404 } }
        return { data: [{ info: { role: "assistant", agent: "gaia-orchestrator" } }] }
      },
    },
  }
  const plugin = await GaiaOpenCodePlugin({
    gaiaBridge: async (event: Record<string, any>) => {
      sent.push(event)
      return { action: "allow" }
    },
    client,
  }) as any

  await plugin["tool.execute.before"]({ tool: "websearch", sessionID: "ses-main", callID: "call-1" }, { args: { query: "x" } })

  const call = sent.find((event) => event.event === "tool.execute.before")
  expect(call?.agent).toBe("gaia-orchestrator")
})
