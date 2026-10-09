/**
 * A requested compaction runs when the main session goes idle, off the idle
 * event, and a session the host still holds (409) is retried a bounded number
 * of times.
 *
 * The host client is a stub; what is asserted is when session.summarize is
 * called, with what, and how often.
 */

import { describe, expect, test } from "bun:test"
import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

const BUSY = { error: { name: "SessionBusyError" }, response: { ok: false, status: 409 } }
const OK = { data: true, response: { ok: true, status: 200 } }
const MODEL = { providerID: "anthropic", modelID: "sonnet" }

async function host(summaries: unknown[], maxAttempts = 3) {
  const summarized: any[] = []
  const client = {
    session: {
      async get({ path }: { path: { id: string } }) {
        return { data: { id: path.id, parentID: path.id === "ses-child" ? "ses-main" : undefined } }
      },
      async messages() {
        return { data: [{ info: { role: "assistant", ...MODEL } }] }
      },
      async summarize(call: any) {
        summarized.push(call)
        return summaries[Math.min(summarized.length - 1, summaries.length - 1)]
      },
    },
  }
  const plugin = await GaiaOpenCodePlugin({
    gaiaBridge: async () => ({ action: "allow" }),
    client,
    compaction: { delayMs: 1, maxAttempts },
  }) as any
  await plugin.event({ event: { type: "session.created", properties: { info: { id: "ses-main" } } } })
  await plugin.event({ event: { type: "session.created", properties: { info: { id: "ses-child", parentID: "ses-main" } } } })
  return { summarized, plugin }
}

const request = (plugin: any, sessionID: string) =>
  plugin.tool.gaia_compact_when_idle.execute({}, { sessionID })

const idle = (plugin: any, sessionID: string) =>
  plugin.event({ event: { type: "session.idle", properties: { sessionID } } })

async function settle(summarized: unknown[], count: number) {
  for (let waited = 0; summarized.length < count && waited < 2000; waited += 5) await Bun.sleep(5)
  await Bun.sleep(40)
}

describe("compaction requested for the main session", () => {
  test("waits for idle, then summarizes off the idle event with the session's model", async () => {
    const { summarized, plugin } = await host([OK])

    await request(plugin, "ses-main")
    expect(summarized).toHaveLength(0)

    await idle(plugin, "ses-main")
    expect(summarized).toHaveLength(0)

    await settle(summarized, 1)
    expect(summarized).toEqual([{ path: { id: "ses-main" }, body: MODEL }])
  })

  test("runs once: a later idle does not compact again", async () => {
    const { summarized, plugin } = await host([OK])

    await request(plugin, "ses-main")
    await idle(plugin, "ses-main")
    await settle(summarized, 1)
    await idle(plugin, "ses-main")
    await settle(summarized, 2)

    expect(summarized).toHaveLength(1)
  })

  test("an idle without a request compacts nothing", async () => {
    const { summarized, plugin } = await host([OK])

    await idle(plugin, "ses-main")
    await settle(summarized, 1)

    expect(summarized).toHaveLength(0)
  })

  test("a busy session is retried until it accepts", async () => {
    const { summarized, plugin } = await host([BUSY, BUSY, OK], 5)

    await request(plugin, "ses-main")
    await idle(plugin, "ses-main")
    await settle(summarized, 3)

    expect(summarized).toHaveLength(3)
  })

  test("a session that stays busy is retried only up to the bound", async () => {
    const { summarized, plugin } = await host([BUSY], 3)

    await request(plugin, "ses-main")
    await idle(plugin, "ses-main")
    await settle(summarized, 4)

    expect(summarized).toHaveLength(3)
  })

  test("a refusal other than busy is not retried", async () => {
    const { summarized, plugin } = await host([{ error: { name: "NotFoundError" }, response: { ok: false, status: 404 } }], 5)

    await request(plugin, "ses-main")
    await idle(plugin, "ses-main")
    await settle(summarized, 2)

    expect(summarized).toHaveLength(1)
  })
})

describe("compaction requested for a session that is not main", () => {
  test("a child session cannot set the flag", async () => {
    const { summarized, plugin } = await host([OK])

    const answer = await request(plugin, "ses-child")
    await idle(plugin, "ses-child")
    await settle(summarized, 1)

    expect(answer).toContain("not requested")
    expect(summarized).toHaveLength(0)
  })
})
