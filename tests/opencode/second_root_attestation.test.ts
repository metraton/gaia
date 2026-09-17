/**
 * A root session the user opens after the first one must still reach the
 * control plane; a session the host records as a child never may.
 *
 * Measured 2026-09-17 (run 6fee99b9): the first root of the serve process was
 * attested and dispatched; the two roots opened later in the UI were refused
 * "declared without an attested runtime context" for every skill load and
 * dispatch, because the plugin issued the parentless claim only to the first
 * session it had seen. The host's own session record is what separates a
 * later root from a not-yet-bound child, so it is stubbed here as the host
 * would answer it; the bridge is a stub that mints a token for every claim it
 * is asked for, so what is asserted is which claims the plugin asks for.
 */

import { describe, expect, test } from "bun:test"
import { GaiaOpenCodePlugin } from "../../opencode/plugin.ts"

type Sent = Record<string, any>

function host(parents: Record<string, string | undefined>, options: { sessionApi?: boolean } = {}) {
  const sent: Sent[] = []
  let minted = 0
  const gaiaBridge = async (event: Sent) => {
    sent.push(event)
    if (event.event === "identity.attest") {
      return { action: "allow" as const, attestation: `opencode-attest:${++minted}` }
    }
    return { action: "allow" as const }
  }
  const client = options.sessionApi === false
    ? {}
    : {
        session: {
          async get({ path }: { path: { id: string } }) {
            if (!(path.id in parents)) return { error: { name: "NotFoundError" } }
            return { data: { id: path.id, parentID: parents[path.id] } }
          },
        },
      }
  return { sent, plugin: GaiaOpenCodePlugin({ gaiaBridge, client }) as Promise<any> }
}

async function named(plugin: any, sessionID: string, agent: string) {
  await plugin.event({
    event: { type: "message.updated", properties: { info: { role: "assistant", sessionID, agent } } },
  })
}

async function loadsSkill(plugin: any, sessionID: string, callID: string) {
  await plugin["tool.execute.before"]({ sessionID, callID, tool: "skill" }, { args: { name: "agent-protocol" } })
}

function before(sent: Sent[], sessionID: string): Sent {
  const found = sent.find((event) => event.event === "tool.execute.before" && event.sessionID === sessionID)
  if (!found) throw new Error(`no tool.execute.before was sent for ${sessionID}`)
  return found
}

function attestsFor(sent: Sent[], sessionID: string): Sent[] {
  return sent.filter((event) => event.event === "identity.attest" && event.sessionID === sessionID)
}

describe("a second root session in one host run", () => {
  test("is attested parentless and presents the control-plane claim", async () => {
    const { sent, plugin: pending } = host({ "ses-first": undefined, "ses-second": undefined })
    const plugin = await pending
    await named(plugin, "ses-first", "gaia-orchestrator")
    await loadsSkill(plugin, "ses-first", "call-1")
    await named(plugin, "ses-second", "gaia-orchestrator")
    await loadsSkill(plugin, "ses-second", "call-2")

    const claims = attestsFor(sent, "ses-second")
    expect(claims).toHaveLength(1)
    expect(claims[0].parentAttestation).toBeUndefined()
    const second = before(sent, "ses-second")
    expect(second.roleContext?.role).toBe("gaia-orchestrator")
    expect(typeof second.roleContext?.attestation).toBe("string")
    expect(second.identityGap).toBeUndefined()
    expect(second.agentID).toBeUndefined()
    expect(before(sent, "ses-first").roleContext?.attestation).not.toBe(second.roleContext.attestation)
  })

  test("a later session the host records as a child is not admitted, and the trace says why", async () => {
    const { sent, plugin: pending } = host({ "ses-first": undefined, "ses-child": "ses-first" })
    const plugin = await pending
    await named(plugin, "ses-first", "gaia-orchestrator")
    await named(plugin, "ses-child", "gaia-orchestrator")
    await loadsSkill(plugin, "ses-child", "call-3")

    expect(attestsFor(sent, "ses-child")).toHaveLength(0)
    const child = before(sent, "ses-child")
    expect(child.roleContext).toBeUndefined()
    expect(child.identityGap).toBe("host records a parent and no dispatch bound the session")
  })

  test("a host that cannot describe the session admits nothing", async () => {
    const { sent, plugin: pending } = host({}, { sessionApi: false })
    const plugin = await pending
    await named(plugin, "ses-first", "gaia-orchestrator")
    await named(plugin, "ses-late", "gaia-orchestrator")
    await loadsSkill(plugin, "ses-late", "call-4")

    expect(attestsFor(sent, "ses-late")).toHaveLength(0)
    const late = before(sent, "ses-late")
    expect(late.roleContext).toBeUndefined()
    expect(late.identityGap).toBe("host session record unavailable, later session not admitted as primary")
  })

  test("the first session is primary without asking the host", async () => {
    const { sent, plugin: pending } = host({}, { sessionApi: false })
    const plugin = await pending
    await named(plugin, "ses-first", "gaia-orchestrator")
    await loadsSkill(plugin, "ses-first", "call-5")

    expect(attestsFor(sent, "ses-first")).toHaveLength(1)
    expect(before(sent, "ses-first").roleContext?.role).toBe("gaia-orchestrator")
  })
})
