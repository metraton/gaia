import { fileURLToPath } from "node:url"

type BridgeResponse = {
  action: "allow" | "ask" | "deny"
  reason?: string
  approval_id?: string
  updated_input?: Record<string, unknown>
  attestation?: string
}

type PendingApproval = {
  approvalID: string
  sessionID: string
  callID: string
  token: string
}

type RoleCapabilityContext = {
  role: string
  capabilities: string[]
  issuer: "opencode-runtime"
  attestation: string
  verified: true
}

type PermissionReply = "once" | "always" | "reject"

/** Host reply spellings mapped onto the protocol vocabulary Gaia accepts. */
const PERMISSION_REPLIES: Record<string, PermissionReply> = {
  once: "once",
  allow: "once",
  always: "always",
  reject: "reject",
  deny: "reject",
}

export function normalizePermissionReply(reply: unknown): PermissionReply {
  // An unrecognized reply must not grant capability by default: a spelling this
  // edge has not been taught is treated as a refusal, never as consent.
  if (typeof reply !== "string") return "reject"
  return PERMISSION_REPLIES[reply.trim().toLowerCase()] ?? "reject"
}

/** The event OpenCode is expected to deliver a permission reply on. */
export const PREFERRED_PERMISSION_EVENT = "permission.replied"

/**
 * Compatibility only: an older OpenCode build spells the same reply with a
 * versioned event name. The name stops at this edge -- Gaia's neutral layer is
 * handed a lane token and never a host event name.
 */
export const COMPATIBILITY_PERMISSION_EVENTS = ["permission.v2.replied"]

export type DecisionLane = "preferred" | "compatibility"

/** Ordered strongest-first: a lane's index is its precedence rank. */
export const DECISION_LANE_PRECEDENCE: DecisionLane[] = ["preferred", "compatibility"]

export function permissionDecisionLane(eventType: unknown): DecisionLane | undefined {
  if (eventType === PREFERRED_PERMISSION_EVENT) return "preferred"
  if (typeof eventType === "string" && COMPATIBILITY_PERMISSION_EVENTS.includes(eventType)) {
    return "compatibility"
  }
  return undefined
}

export type LaneAdmission = {
  lane: DecisionLane
  accepted: boolean
  duplicate: boolean
  supersededLane?: DecisionLane
}

/**
 * Collapses every delivery of one permission reply into a single effect.
 *
 * Mirrors the neutral ledger in hooks/adapters/consent_events.py: the first
 * delivery acts, a later one never acts, and a later delivery on a
 * higher-precedence lane takes over attribution for the request.
 */
export class PermissionDecisionRouter {
  private readonly lanes = new Map<string, DecisionLane>()

  admit(requestID: string, lane: DecisionLane): LaneAdmission {
    const prior = this.lanes.get(requestID)
    if (prior === undefined) {
      this.lanes.set(requestID, lane)
      return { lane, accepted: true, duplicate: false }
    }
    if (DECISION_LANE_PRECEDENCE.indexOf(lane) < DECISION_LANE_PRECEDENCE.indexOf(prior)) {
      this.lanes.set(requestID, lane)
      return { lane, accepted: false, duplicate: true, supersededLane: prior }
    }
    return { lane: prior, accepted: false, duplicate: true }
  }

  effectiveLane(requestID: string): DecisionLane | undefined {
    return this.lanes.get(requestID)
  }
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

async function gaiaCapture(args: string[]): Promise<{ ok: boolean; stdout: string }> {
  const child = Bun.spawn(["python3", gaiaPath, ...args], {
    env: { ...process.env, GAIA_HOST: "opencode" },
    stdout: "pipe",
    stderr: "pipe",
  })
  const [code, stdout] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
  ])
  return { ok: code === 0, stdout }
}

async function gaia(args: string[]): Promise<boolean> {
  return (await gaiaCapture(args)).ok
}

export type NativeConsentPresentation = {
  visibleLines: string[]
  metadata: Record<string, unknown>
}

/**
 * Read the sealed consent surface Gaia rendered for one presented approval.
 *
 * The text and the metadata are both Gaia's, never this edge's: a surface this
 * plugin composed would be a description of a consent request written by the
 * party asking for it. A response missing either half is refused rather than
 * shown, because the alternative is a permission prompt whose fields the user
 * cannot see.
 */
export function readConsentPresentation(stdout: string): NativeConsentPresentation {
  let emitted: any
  try {
    emitted = JSON.parse(stdout.trim().split("\n").pop() ?? "")
  } catch {
    throw new Error("Gaia did not emit a consent presentation to render")
  }
  const lines = emitted?.visible_lines
  const metadata = emitted?.metadata
  if (!Array.isArray(lines) || lines.length === 0 || !metadata) {
    throw new Error(
      emitted?.presentation_error
        ? `Gaia could not seal a complete consent surface: ${emitted.presentation_error}`
        : "Gaia returned no user-visible consent surface for this approval",
    )
  }
  return { visibleLines: lines.map(String), metadata }
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

/** The one issuer spelling Gaia's adapter trusts for a host claim. */
export const ROLE_ISSUER = "opencode-runtime"

export const GaiaOpenCodePlugin = async (input: any) => {
  const pending = new Map<string, PendingApproval>()
  const agentBySession = new Map<string, string>()
  const agentByCall = new Map<string, string>()
  // Replaces a real dependency with a test double only: send is the Gaia
  // policy bridge. The run its attestation ledger is scoped to is derived by
  // the Gaia-side process from the process that spawned it, so this edge does
  // not name that scope -- a field it sent would be a scope its own caller
  // could name, and a claim checked against a ledger the claimant chooses
  // carries no provenance.
  const send: (event: Record<string, unknown>) => Promise<BridgeResponse> =
    typeof input?.gaiaBridge === "function" ? input.gaiaBridge : bridge
  // A claim the host process was granted, never one this edge composed: the
  // plugin receives caller-supplied names and cannot be the issuer of the
  // authority they would otherwise assert.
  const attestationBySession = new Map<string, string>()
  // The session this run's parentless claim may be issued to. A child session
  // cannot exist before the primary one has taken a turn, so the first session
  // seen is the primary and every later one must inherit a grant instead.
  let rootSessionID: string | undefined
  // The dispatch that created each child session, keyed by that child's
  // session. It holds no entry for the primary session, which is what makes
  // agentID absent there: Gaia reads any truthy agent_id as a subagent before
  // it consults the attested control-plane context, so a role name in that
  // field would leave the attested lane unreachable.
  const dispatchBySession = new Map<string, string>()
  const decisions = new PermissionDecisionRouter()

  function roleContext(sessionID: string): RoleCapabilityContext | undefined {
    const role = agentBySession.get(sessionID)
    const attestation = attestationBySession.get(sessionID)
    // A name with no issued claim is not an identity: Gaia refused to attest
    // it, so this edge presents nothing rather than a claim of its own making.
    if (!role || !attestation) return undefined
    return {
      role,
      capabilities: [],
      issuer: ROLE_ISSUER,
      attestation,
      verified: true,
    }
  }

  async function attest(sessionID: string, role: string, grantor?: string) {
    if (attestationBySession.has(sessionID)) return
    let parentAttestation: string | undefined
    if (grantor !== undefined) {
      parentAttestation = attestationBySession.get(grantor)
      // An unattested dispatcher has no grant to pass on, and the chain must
      // record a grantor that holds one.
      if (!parentAttestation) return
    } else if (sessionID !== rootSessionID) {
      return
    }
    const response = await send({
      event: "identity.attest",
      sessionID,
      role,
      issuer: ROLE_ISSUER,
      parentAttestation,
    })
    if (response.action === "allow" && typeof response.attestation === "string" && response.attestation) {
      attestationBySession.set(sessionID, response.attestation)
    }
  }

  async function decide(
    approval: PendingApproval,
    reply: PermissionReply,
    lane: DecisionLane = "preferred",
  ) {
    await gaia([
      "approvals", "opencode-decide", approval.approvalID,
      "--session-id", approval.sessionID,
      "--call-id", approval.callID,
      "--token", approval.token,
      "--reply", reply,
      "--decision-lane", lane,
      "--json",
    ])
  }

  async function requestApproval(response: BridgeResponse, sessionID: string, callID: string) {
    const id = approvalID(response)
    if (!id) return
    const approval = { approvalID: id, sessionID, callID, token: crypto.randomUUID() }
    const presented = await gaiaCapture([
      "approvals", "opencode-present", id,
      "--session-id", sessionID,
      "--call-id", callID,
      "--token", approval.token,
      "--json",
    ])
    if (!presented.ok) throw new Error("Gaia could not present the approval request")
    const surface = readConsentPresentation(presented.stdout)

    const created = await input.client.session.permission.create({
      sessionID,
      action: "gaia-approval",
      // The whole sealed surface occupies the user-visible slot: operation,
      // every exact command in order with its fingerprint, scope, impact,
      // risk, rollback and verification. Metadata mirrors it for a program;
      // a field that lived only there would be a field nobody consented to.
      resources: surface.visibleLines,
      metadata: { gaiaApprovalID: id, gaiaCallID: callID, gaiaConsent: surface.metadata },
    })
    // A host that answers the request immediately, or answers with nothing at
    // all, must not leave a presented approval waiting for a reply event.
    const decided = created?.data
    if (!decided?.id) throw new Error("OpenCode did not return a permission request to correlate")
    if (decided.effect === "allow" || decided.effect === "deny") {
      await decide(approval, normalizePermissionReply(decided.effect))
      return
    }
    pending.set(decided.id, approval)
  }

  return {
    event: async ({ event }) => {
      if (event.type === "message.updated") {
        const info = event.properties?.info
        if (info?.role === "assistant" && typeof info.sessionID === "string" && typeof info.agent === "string") {
          agentBySession.set(info.sessionID, info.agent)
          if (rootSessionID === undefined) rootSessionID = info.sessionID
          await attest(info.sessionID, info.agent)
        }
        return
      }
      const lane = permissionDecisionLane(event.type)
      if (!lane) return
      const requestID = event.properties.requestID
      // Admitted before the approval lookup so a later delivery on the
      // preferred lane is still recorded once a compatibility one has acted.
      const admission = decisions.admit(requestID, lane)
      if (!admission.accepted) return
      const approval = pending.get(requestID)
      if (!approval) return
      pending.delete(requestID)
      await decide(approval, normalizePermissionReply(event.properties.reply), lane)
    },
    "tool.execute.before": async (call, output) => {
      const agent = agentBySession.get(call.sessionID)
      if (call.tool === "task") {
        const requested = output.args?.subagent_type ?? output.args?.agent
        if (typeof requested === "string") agentByCall.set(call.callID, requested)
      }
      const response = await send({
        event: "tool.execute.before",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: dispatchBySession.get(call.sessionID),
        agent,
        roleContext: roleContext(call.sessionID),
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
          dispatchBySession.set(sessionID, call.callID)
          await attest(sessionID, dispatchedAgent, call.sessionID)
        }
      }
      await send({
        event: "tool.execute.after",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: dispatchBySession.get(call.sessionID),
        agent,
        roleContext: roleContext(call.sessionID),
        tool: call.tool,
        args: call.args,
        result: toolResult(output),
      })
    },
  }
}
