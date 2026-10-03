/**
 * A main session receives the birth block once, attached to its first real
 * message; a child session never receives it.
 *
 * OpenCode fires chat.message on child sessions, and on messages the host or a
 * plugin injects (no agent, synthetic parts), as well as on the user's own
 * (measured on 1.18.32, evidence 896). The bridge is a stub, so what is
 * asserted is which messages the plugin asks Gaia to start a session for and
 * what it attaches to them.
 */

import { describe, expect, test } from "bun:test"
import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

type Sent = Record<string, any>

const BIRTH_BLOCK = "## Environment\n- gaia CLI: /opt/gaia/bin/gaia"

const DELIVERS = async () => ({ action: "allow", additional_context: BIRTH_BLOCK })

function host(
  answer: (event: Sent) => Promise<Sent> = DELIVERS,
  parents?: Record<string, string | undefined>,
) {
  const sent: Sent[] = []
  const lookups: string[] = []
  const gaiaBridge = async (event: Sent) => {
    sent.push(event)
    if (event.event === "chat.message") return answer(event)
    return { action: "allow" as const }
  }
  const client = parents === undefined
    ? {}
    : {
        session: {
          async get({ path }: { path: { id: string } }) {
            lookups.push(path.id)
            if (!(path.id in parents)) return { error: { name: "NotFoundError" } }
            return { data: { id: path.id, parentID: parents[path.id] } }
          },
        },
      }
  return { sent, lookups, plugin: GaiaOpenCodePlugin({ gaiaBridge, client }) as Promise<any> }
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

function startsFor(sent: Sent[], sessionID: string): Sent[] {
  return sent.filter((event) => event.event === "chat.message" && event.sessionID === sessionID)
}

describe("session birth on OpenCode", () => {
  test("the main session gets the block once, as a synthetic part of its first message", async () => {
    const { sent, plugin: pending } = host()
    const plugin = await pending
    await created(plugin, "ses-main")
    const first = await message(plugin, "ses-main")
    const second = await message(plugin, "ses-main")

    expect(startsFor(sent, "ses-main")).toHaveLength(1)
    expect(first.parts).toHaveLength(2)
    expect(first.parts[1]).toMatchObject({
      type: "text",
      text: BIRTH_BLOCK,
      synthetic: true,
      sessionID: "ses-main",
      messageID: first.message.id,
    })
    expect(first.parts[1].id.startsWith("prt_")).toBe(true)
    expect(second.parts).toHaveLength(1)
  })

  test("a child session never asks for the block", async () => {
    const { sent, plugin: pending } = host()
    const plugin = await pending
    await created(plugin, "ses-main")
    await created(plugin, "ses-child", "ses-main")
    const child = await message(plugin, "ses-child", { agent: "developer" })

    expect(startsFor(sent, "ses-child")).toHaveLength(0)
    expect(child.parts).toHaveLength(1)
  })

  test("a resumed main session the plugin never saw created gets the block once", async () => {
    const { sent, lookups, plugin: pending } = host(DELIVERS, { "ses-resumed": undefined })
    const plugin = await pending
    const first = await message(plugin, "ses-resumed")
    const second = await message(plugin, "ses-resumed")

    expect(startsFor(sent, "ses-resumed")).toHaveLength(1)
    expect(first.parts).toHaveLength(2)
    expect(first.parts[1]).toMatchObject({ text: BIRTH_BLOCK, synthetic: true })
    expect(second.parts).toHaveLength(1)
    expect(lookups).toEqual(["ses-resumed"])
  })

  test("an unseen session the host records as a child never asks for the block", async () => {
    const { sent, lookups, plugin: pending } = host(DELIVERS, { "ses-child": "ses-main" })
    const plugin = await pending
    const first = await message(plugin, "ses-child", { agent: "developer" })
    await message(plugin, "ses-child", { agent: "developer" })

    expect(startsFor(sent, "ses-child")).toHaveLength(0)
    expect(first.parts).toHaveLength(1)
    expect(lookups).toEqual(["ses-child"])
  })

  test("an unseen session the host cannot describe gets nothing until it can", async () => {
    const parents: Record<string, string | undefined> = {}
    const { sent, plugin: pending } = host(DELIVERS, parents)
    const plugin = await pending
    const unknown = await message(plugin, "ses-late")
    parents["ses-late"] = undefined
    const described = await message(plugin, "ses-late")

    expect(unknown.parts).toHaveLength(1)
    expect(described.parts).toHaveLength(2)
    expect(startsFor(sent, "ses-late")).toHaveLength(1)
  })

  test("a session announced at creation is decided without asking the host", async () => {
    const { lookups, plugin: pending } = host(DELIVERS, {})
    const plugin = await pending
    await created(plugin, "ses-main")
    await created(plugin, "ses-child", "ses-main")
    await message(plugin, "ses-main")
    await message(plugin, "ses-child", { agent: "developer" })

    expect(lookups).toHaveLength(0)
  })

  test("injected messages are passed over and the block waits for the user's own", async () => {
    const { sent, plugin: pending } = host()
    const plugin = await pending
    await created(plugin, "ses-main")
    const agentless = await message(plugin, "ses-main", { agent: null })
    const synthetic = await message(plugin, "ses-main", { synthetic: true })
    const real = await message(plugin, "ses-main")

    expect(agentless.parts).toHaveLength(1)
    expect(synthetic.parts).toHaveLength(1)
    expect(real.parts).toHaveLength(2)
    expect(startsFor(sent, "ses-main")).toHaveLength(1)
  })

  test("a failed bridge leaves the user's message untouched and is not retried", async () => {
    const { sent, plugin: pending } = host(async () => {
      throw new Error("bridge down")
    })
    const plugin = await pending
    await created(plugin, "ses-main")
    const first = await message(plugin, "ses-main")
    await message(plugin, "ses-main")

    expect(first.parts).toHaveLength(1)
    expect(startsFor(sent, "ses-main")).toHaveLength(1)
  })
})
