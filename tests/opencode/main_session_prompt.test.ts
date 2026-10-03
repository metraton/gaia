/**
 * Every real user message of a main session asks Gaia for its prompt context,
 * and compacting a main session asks for the compaction context; a child
 * session asks for neither, and its compaction still receives its kernel.
 *
 * The bridge is a stub, so what is asserted is which events the plugin sends,
 * which sessions it marks main, and what it attaches to the host's output.
 */

import { describe, expect, test } from "bun:test"
import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

type Sent = Record<string, any>

const BIRTH_BLOCK = "## Environment\n- gaia CLI: /opt/gaia/bin/gaia"
const NOTICE = "2 notifications due (`gaia notifications list --unread` to see them)"
const COMPACT_CONTEXT = "## Orchestrator identity"
const CHILD_KERNEL = "# Your Contract\ncontract_id: a1.child"

async function answer(event: Sent): Promise<Sent> {
  if (event.event === "chat.message") return { action: "allow", additional_context: BIRTH_BLOCK }
  if (event.event === "chat.prompt") return { action: "allow", additional_context: NOTICE }
  if (event.event === "session.compacting") {
    const context = event.main ? COMPACT_CONTEXT : event.sessionID === "ses-child" ? CHILD_KERNEL : undefined
    return context ? { action: "allow", updated_input: { context: [context] } } : { action: "allow" }
  }
  return { action: "allow" }
}

async function host(parents: Record<string, string | undefined> = {}) {
  const sent: Sent[] = []
  const gaiaBridge = async (event: Sent) => {
    sent.push(event)
    return answer(event)
  }
  const client = {
    session: {
      async get({ path }: { path: { id: string } }) {
        if (!(path.id in parents)) return { error: { name: "NotFoundError" } }
        return { data: { id: path.id, parentID: parents[path.id] } }
      },
    },
  }
  const plugin = await GaiaOpenCodePlugin({ gaiaBridge, client }) as any
  return { sent, plugin }
}

async function created(plugin: any, id: string, parentID?: string) {
  await plugin.event({ event: { type: "session.created", properties: { info: { id, parentID } } } })
}

async function message(plugin: any, sessionID: string, options: { agent?: string | null; synthetic?: boolean } = {}) {
  const messageID = `msg_${sessionID}_${Math.random().toString(36).slice(2)}`
  const output = {
    message: { id: messageID, sessionID, role: "user" },
    parts: [{ id: `prt_${messageID}`, sessionID, messageID, type: "text", text: "hello", synthetic: options.synthetic }],
  }
  const agent = options.agent === undefined ? "gaia-orchestrator" : options.agent ?? undefined
  await plugin["chat.message"]({ sessionID, agent, messageID }, output)
  return output
}

async function compact(plugin: any, sessionID: string) {
  const output = { context: [] as string[], prompt: undefined }
  await plugin["experimental.session.compacting"]({ sessionID }, output)
  return output
}

function prompts(sent: Sent[], sessionID: string): Sent[] {
  return sent.filter((event) => event.event === "chat.prompt" && event.sessionID === sessionID)
}

describe("per-message prompt context on OpenCode", () => {
  test("every real message of the main session carries the prompt context", async () => {
    const { sent, plugin } = await host()
    await created(plugin, "ses-main")
    const first = await message(plugin, "ses-main")
    const second = await message(plugin, "ses-main")
    const third = await message(plugin, "ses-main")

    expect(prompts(sent, "ses-main")).toHaveLength(3)
    expect(first.parts).toHaveLength(2)
    expect(first.parts[1]).toMatchObject({ text: `${BIRTH_BLOCK}\n\n${NOTICE}`, synthetic: true })
    for (const later of [second, third]) {
      expect(later.parts).toHaveLength(2)
      expect(later.parts[1]).toMatchObject({
        type: "text", text: NOTICE, synthetic: true, sessionID: "ses-main", messageID: later.message.id,
      })
    }
  })

  test("a child session never asks for the prompt context", async () => {
    const { sent, plugin } = await host({ "ses-unseen-child": "ses-main" })
    await created(plugin, "ses-main")
    await created(plugin, "ses-child", "ses-main")
    const child = await message(plugin, "ses-child", { agent: "developer" })
    const unseen = await message(plugin, "ses-unseen-child", { agent: "developer" })

    expect(prompts(sent, "ses-child")).toHaveLength(0)
    expect(prompts(sent, "ses-unseen-child")).toHaveLength(0)
    expect(child.parts).toHaveLength(1)
    expect(unseen.parts).toHaveLength(1)
  })

  test("injected and synthetic messages of the main session are passed over", async () => {
    const { sent, plugin } = await host()
    await created(plugin, "ses-main")
    const agentless = await message(plugin, "ses-main", { agent: null })
    const synthetic = await message(plugin, "ses-main", { synthetic: true })

    expect(prompts(sent, "ses-main")).toHaveLength(0)
    expect(agentless.parts).toHaveLength(1)
    expect(synthetic.parts).toHaveLength(1)
  })

  test("a failed prompt bridge leaves the message untouched and the next one still asks", async () => {
    const sent: Sent[] = []
    const plugin = await GaiaOpenCodePlugin({
      gaiaBridge: async (event: Sent) => {
        sent.push(event)
        if (event.event === "chat.prompt" && prompts(sent, "ses-main").length === 2) throw new Error("bridge down")
        return answer(event)
      },
      client: {},
    }) as any
    await created(plugin, "ses-main")
    await message(plugin, "ses-main")
    const failed = await message(plugin, "ses-main")
    const recovered = await message(plugin, "ses-main")

    expect(failed.parts).toHaveLength(1)
    expect(recovered.parts[1]).toMatchObject({ text: NOTICE })
  })
})

describe("primary compaction context on OpenCode", () => {
  test("compacting the main session marks it main and pushes the compaction context", async () => {
    const { sent, plugin } = await host({ "ses-resumed": undefined })
    await created(plugin, "ses-main")
    const main = await compact(plugin, "ses-main")
    const resumed = await compact(plugin, "ses-resumed")

    expect(main.context).toEqual([COMPACT_CONTEXT])
    expect(resumed.context).toEqual([COMPACT_CONTEXT])
    const compactions = sent.filter((event) => event.event === "session.compacting")
    expect(compactions.map((event) => [event.sessionID, event.main])).toEqual([["ses-main", true], ["ses-resumed", true]])
  })

  test("compacting a child is never marked main and still receives its kernel", async () => {
    const { sent, plugin } = await host()
    await created(plugin, "ses-main")
    await created(plugin, "ses-child", "ses-main")
    const child = await compact(plugin, "ses-child")
    const unknown = await compact(plugin, "ses-unknown")

    expect(child.context).toEqual([CHILD_KERNEL])
    expect(unknown.context).toEqual([])
    const compactions = sent.filter((event) => event.event === "session.compacting")
    expect(compactions.map((event) => event.main)).toEqual([false, false])
  })
})
