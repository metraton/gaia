import { fileURLToPath } from "node:url"
import { lstatSync, realpathSync, statSync } from "node:fs"
import { dirname, isAbsolute, parse, relative, resolve, sep } from "node:path"

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
  surface: NativeConsentPresentation
}

type RoleCapabilityContext = {
  role: string
  capabilities: string[]
  issuer: "opencode-runtime"
  attestation: string
  verified: true
}

type PermissionReply = "once" | "always" | "reject"

type WorkspaceContext = {
  cwd: string
  worktree?: string
}

export type NormalizedBridgeToolRequest = {
  tool: string
  args: Record<string, unknown>
  cwd?: string
  worktree?: string
  originalTool: unknown
  originalArgs: unknown
}

const FILE_TOOL_NAMES: Record<string, "Write" | "Edit" | "apply_patch"> = {
  write: "Write",
  edit: "Edit",
  applypatch: "apply_patch",
}

function normalizedToken(value: unknown): string {
  return typeof value === "string"
    ? value.trim().toLowerCase().replace(/[\s._-]+/g, "")
    : ""
}

function canonicalFileTool(value: unknown): "Write" | "Edit" | "apply_patch" | undefined {
  if (typeof value !== "string") return undefined
  return FILE_TOOL_NAMES[value.trim().toLowerCase()] ?? FILE_TOOL_NAMES[normalizedToken(value)]
}

function canonicalDirectory(value: unknown, label: string): string | undefined {
  if (value === undefined || value === null || value === "") return undefined
  if (typeof value !== "string" || !isAbsolute(value)) {
    throw new Error(`Gaia denied file tool: OpenCode ${label} is not an absolute path`)
  }
  let canonical: string
  try {
    canonical = realpathSync.native(value)
    if (!statSync(canonical).isDirectory()) throw new Error("not a directory")
  } catch {
    throw new Error(`Gaia denied file tool: OpenCode ${label} is not a readable directory`)
  }
  return canonical
}

function workspaceContext(input: any): WorkspaceContext {
  const directory = canonicalDirectory(input?.directory, "directory")
  const worktree = canonicalDirectory(input?.worktree, "worktree")
  if (!directory && !worktree) {
    throw new Error("Gaia denied file tool: OpenCode supplied no trustworthy directory or worktree")
  }
  if (directory && worktree) {
    const fromWorktree = relative(worktree, directory)
    if (fromWorktree === ".." || fromWorktree.startsWith(`..${sep}`) || isAbsolute(fromWorktree)) {
      throw new Error("Gaia denied file tool: OpenCode directory and worktree are ambiguous")
    }
  }
  return { cwd: directory ?? worktree!, worktree }
}

function canonicalTarget(raw: unknown, cwd: string): string {
  if (typeof raw !== "string" || !raw.trim() || raw.includes("\0")) {
    throw new Error("Gaia denied file tool: target path is missing or malformed")
  }
  const root = isAbsolute(raw) ? parse(raw).root : cwd
  const suffix = isAbsolute(raw) ? raw.slice(root.length) : raw
  let canonical = root
  let unresolved = false
  for (const segment of suffix.split(sep)) {
    if (!segment || segment === ".") continue
    if (segment === "..") {
      if (unresolved) {
        throw new Error("Gaia denied file tool: unresolved traversal is ambiguous")
      }
      canonical = dirname(canonical)
      continue
    }
    const candidate = resolve(canonical, segment)
    try {
      lstatSync(candidate)
    } catch (error: any) {
      if (error?.code !== "ENOENT") {
        throw new Error("Gaia denied file tool: target path cannot be resolved")
      }
      canonical = candidate
      unresolved = true
      continue
    }
    try {
      canonical = realpathSync.native(candidate)
    } catch {
      throw new Error("Gaia denied file tool: target path cannot be resolved")
    }
  }
  if (canonical === dirname(canonical)) {
    throw new Error("Gaia denied file tool: filesystem root is not a valid edit target")
  }
  return canonical
}

function valuesForKeys(args: Record<string, unknown>, accepted: Set<string>): unknown[] {
  return Object.entries(args)
    .filter(([key]) => accepted.has(normalizedToken(key)))
    .map(([, value]) => value)
}

function normalizeSinglePath(args: Record<string, unknown>, cwd: string): Record<string, unknown> {
  const supplied = valuesForKeys(args, new Set(["path", "filepath"]))
  if (supplied.length === 0) {
    throw new Error("Gaia denied file tool: path or file_path is required")
  }
  const canonical = supplied.map((value) => canonicalTarget(value, cwd))
  if (canonical.some((value) => value !== canonical[0])) {
    throw new Error("Gaia denied file tool: path and file_path identify different targets")
  }
  return { ...args, file_path: canonical[0] }
}

const PATCH_PATH_MARKER = /^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$/

function normalizePatch(args: Record<string, unknown>, cwd: string): Record<string, unknown> {
  const supplied = valuesForKeys(args, new Set(["patch", "patchtext"]))
  if (supplied.length === 0 || supplied.some((value) => typeof value !== "string")) {
    throw new Error("Gaia denied file tool: apply_patch requires a patchText payload")
  }
  if (supplied.some((value) => value !== supplied[0])) {
    throw new Error("Gaia denied file tool: patch payload aliases disagree")
  }
  const patchText = supplied[0] as string
  const lines = patchText.split("\n")
  if (lines[0] !== "*** Begin Patch" || lines.at(-1) !== "*** End Patch") {
    throw new Error("Gaia denied file tool: apply_patch envelope is malformed")
  }
  const filePaths: string[] = []
  const normalizedLines = [lines[0]]
  let operation: "Add File" | "Update File" | "Delete File" | undefined
  let operationHasBody = false
  const requireCompleteOperation = () => {
    if (operation !== "Delete File" && operation && !operationHasBody) {
      throw new Error(`Gaia denied file tool: ${operation} contains no patch body`)
    }
  }
  for (const line of lines.slice(1, -1)) {
    if (!line.startsWith("*** ")) {
      if (!operation || operation === "Delete File") {
        throw new Error("Gaia denied file tool: apply_patch content is outside a file operation")
      }
      if (operation === "Add File" && !line.startsWith("+")) {
        throw new Error("Gaia denied file tool: Add File content must use added lines")
      }
      if (
        operation === "Update File"
        && !["@@", "+", "-", " "].some((prefix) => line.startsWith(prefix))
      ) {
        throw new Error("Gaia denied file tool: Update File contains malformed patch content")
      }
      operationHasBody = true
      normalizedLines.push(line)
      continue
    }
    const marker = PATCH_PATH_MARKER.exec(line)
    if (!marker) throw new Error(`Gaia denied file tool: unsupported apply_patch marker: ${line}`)
    const markerKind = marker[1] as "Add File" | "Update File" | "Delete File" | "Move to"
    if (markerKind === "Move to") {
      if (operation !== "Update File" || operationHasBody) {
        throw new Error("Gaia denied file tool: Move to must immediately follow Update File")
      }
    } else {
      requireCompleteOperation()
      operation = markerKind
      operationHasBody = markerKind === "Delete File"
    }
    const target = canonicalTarget(marker[2].trim(), cwd)
    filePaths.push(target)
    normalizedLines.push(`*** ${markerKind}: ${target}`)
  }
  requireCompleteOperation()
  if (filePaths.length === 0) {
    throw new Error("Gaia denied file tool: apply_patch contains no file target")
  }
  normalizedLines.push(lines.at(-1)!)
  return { ...args, patchText: normalizedLines.join("\n"), file_paths: filePaths }
}

/** Canonicalize governed file tools before any request reaches Gaia's bridge. */
export function normalizeBridgeToolRequest(
  tool: unknown,
  args: unknown,
  input: any,
): NormalizedBridgeToolRequest {
  const canonicalTool = canonicalFileTool(tool)
  if (!canonicalTool) {
    return {
      tool: typeof tool === "string" ? tool : "",
      args: args && typeof args === "object" && !Array.isArray(args)
        ? args as Record<string, unknown>
        : {},
      originalTool: tool,
      originalArgs: args,
    }
  }
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    throw new Error("Gaia denied file tool: tool arguments must be an object")
  }
  const context = workspaceContext(input)
  const originalArgs = args as Record<string, unknown>
  const normalizedArgs = canonicalTool === "apply_patch"
    ? normalizePatch(originalArgs, context.cwd)
    : normalizeSinglePath(originalArgs, context.cwd)
  return {
    tool: canonicalTool,
    args: normalizedArgs,
    cwd: context.cwd,
    worktree: context.worktree,
    originalTool: tool,
    originalArgs,
  }
}

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

function traceableBridgeRequest(event: Record<string, unknown>): Record<string, unknown> {
  const traceableArgs = (value: unknown) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {}
    const args = value as Record<string, unknown>
    const traced: Record<string, unknown> = { keys: Object.keys(args) }
    for (const [key, item] of Object.entries(args)) {
      const token = normalizedToken(key)
      if (token === "path" || token === "filepath" || token === "filepaths") {
        traced[key] = item
      } else if ((token === "patch" || token === "patchtext") && typeof item === "string") {
        traced[key] = item.split("\n").filter((line) => line.startsWith("*** "))
      }
    }
    return traced
  }
  const role = event.roleContext as Record<string, unknown> | undefined
  return {
    ...event,
    args: traceableArgs(event.args),
    originalArgs: traceableArgs(event.originalArgs),
    roleContext: role ? {
      role: role.role,
      issuer: role.issuer,
      verified: role.verified,
      attestation_present: typeof role.attestation === "string" && Boolean(role.attestation),
    } : undefined,
  }
}

async function bridge(event: Record<string, unknown>): Promise<BridgeResponse> {
  if (process.env.GAIA_DEBUG) {
    console.error(`[gaia-opencode-bridge:request] ${JSON.stringify(traceableBridgeRequest(event))}`)
  }
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
  const response = JSON.parse(output) as BridgeResponse
  if (process.env.GAIA_DEBUG) {
    console.error(`[gaia-opencode-bridge:response] ${JSON.stringify({
      action: response.action,
      approval_id: response.approval_id,
      has_updated_input: Boolean(response.updated_input),
    })}`)
  }
  return response
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

/** Apply a bridge response's updated_input onto the live args object, field
 * by field, never by whole-object reassignment. OpenCode 1.18.23 hands this
 * hook the args object it will actually pass to the tool; a full
 * `output.args = updatedInput` replaces the reference the host already
 * captured and is a measured no-op (memory:
 * project_gaia_opencode_lifecycle_medido_2026_08_26) for both task and bash.
 * Mutating the existing object in place is what the host observes. */
export function applyUpdatedInput(
  output: { args?: Record<string, unknown> },
  updatedInput: Record<string, unknown> | undefined,
): void {
  if (!updatedInput) return
  const args = output.args ?? (output.args = {})
  for (const [key, value] of Object.entries(updatedInput)) {
    args[key] = value
  }
}

/** The one issuer spelling Gaia's adapter trusts for a host claim. */
export const ROLE_ISSUER = "opencode-runtime"

const LIVENESS_PREFIX = "[gaia-opencode:liveness]"

async function announceLiveness(input: any): Promise<void> {
  const loadedAt = new Date().toISOString()
  const message = `${LIVENESS_PREFIX} pid=${process.pid} loaded_at=${loadedAt} export=GaiaOpenCodePlugin`
  const app = input?.client?.app
  // Call through the receiver the host handed us, never a rebind: an
  // extracted-then-rebound reference (`log.call(app, ...)`) is one more hop
  // than the host's own SDK ever takes to reach this method, and the ledger's
  // measured this._client failures line up with exactly that extra hop.
  if (typeof app?.log === "function") {
    try {
      await app.log({
        body: {
          service: "gaia-opencode-plugin",
          level: "info",
          message: "Gaia OpenCode plugin loaded",
          extra: { pid: process.pid, loaded_at: loadedAt, export: "GaiaOpenCodePlugin" },
        },
      })
      return
    } catch (error) {
      throw new Error(`${LIVENESS_PREFIX} host log failed: ${error}`)
    }
  }
  // A raw stream write, never console.error: Bun colorizes error-level
  // console output with ANSI escapes whenever FORCE_COLOR is set in the
  // environment, even off a TTY, which corrupts the literal line every
  // consumer of this liveness signal (tests, host-log scans) matches against.
  process.stderr.write(`${message}\n`)
}

export const GaiaOpenCodePlugin = async (input: any) => {
  await announceLiveness(input)
  const pending = new Map<string, PendingApproval>()
  const pendingByCall = new Map<string, PendingApproval>()
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
  // The dispatch call that created each child session, keyed by that child's
  // session. Written only when the parent's tool.execute.after reports the
  // child it produced, so it is empty for the whole run of the child it
  // describes: read it directly and every tool call a subagent makes carries no
  // agent_id. Read it through dispatchHandle instead.
  const dispatchBySession = new Map<string, string>()
  // One issuance per session even when two edges reach it at once. Without it a
  // tool call landing while the event handler's attest is still in flight sees
  // a named session with no claim yet and composes no context at all.
  const attestInFlight = new Map<string, Promise<void>>()
  const decisions = new PermissionDecisionRouter()

  /** The dispatch handle Gaia reads as agent_id, or undefined for the primary.
   *
   * One predicate answers "is this session a dispatch?" for every site that
   * asks. A session other than the root one exists only because a dispatch
   * created it, so it is a subagent from its first tool call -- which is long
   * before the parent's tool.execute.after can report which call created it.
   * Keying the answer on that record alone left agent_id absent for the whole
   * child run, and Gaia's delegate mode reads an absent agent_id as an --agent
   * main thread and denies the specialist its tools.
   *
   * The primary must stay undefined here: Gaia reads any truthy agent_id as a
   * subagent before it consults the attested control-plane context, so a handle
   * on that session would leave the attested lane unreachable.
   */
  function isPrimarySession(sessionID: string): boolean {
    return rootSessionID !== undefined && sessionID === rootSessionID
  }

  function dispatchHandle(sessionID: string): string | undefined {
    // Both arms fail closed on an unknown primary: no handle is issued, so an
    // unidentifiable session is never handed the unrestricted subagent lane.
    if (rootSessionID === undefined || isPrimarySession(sessionID)) return undefined
    return dispatchBySession.get(sessionID) ?? sessionID
  }

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
    } else if (!isPrimarySession(sessionID)) {
      // A parentless claim belongs to the primary session alone, and which
      // session that is is isPrimarySession's answer, not a second reading of
      // rootSessionID that can drift from the one dispatchHandle applies.
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

  function attestOnce(sessionID: string, role: string, grantor?: string): Promise<void> {
    const running = attestInFlight.get(sessionID)
    if (running) return running
    const started = attest(sessionID, role, grantor).finally(() => {
      attestInFlight.delete(sessionID)
    })
    attestInFlight.set(sessionID, started)
    return started
  }

  /** Read the session's agent back from the host's own message record. */
  async function hostAgent(sessionID: string, dispatching?: string): Promise<string | undefined> {
    // OpenCode 1.18.23 passes tool.execute.before exactly {tool, sessionID,
    // callID} at every trigger site, so this edge has no agent to read from the
    // call. The name that identifies the session travels the event bus instead,
    // which can still be undelivered when a dispatch arrives -- and a dispatch
    // presenting no identity is refused. This asks the host for the same
    // message object the bus event would have carried, so the name is still the
    // host's and this edge composes none of its own.
    try {
      const messages = await input.client?.session?.messages?.({ sessionID })
      const list = messages?.data
      if (!Array.isArray(list)) return undefined
      for (let index = list.length - 1; index >= 0; index--) {
        const info = list[index]?.info
        if (info?.role === "assistant" && typeof info.agent === "string" && info.agent) {
          // handleSubtask persists the callee's own placeholder into the
          // CALLER's transcript before triggering this edge, so the newest
          // assistant name here can be the agent being dispatched rather than
          // the one dispatching. Skipping the whole run of them costs a denial
          // on a self-dispatch, which the next message.updated repairs; reading
          // one would mint a claim only a host restart clears.
          if (dispatching && info.agent === dispatching) continue
          return info.agent
        }
      }
    } catch {
      return undefined
    }
    return undefined
  }

  async function identify(sessionID: string, dispatching?: string): Promise<string | undefined> {
    let agent = agentBySession.get(sessionID)
    if (!agent) {
      agent = await hostAgent(sessionID, dispatching)
      if (!agent) return undefined
      agentBySession.set(sessionID, agent)
      if (rootSessionID === undefined) rootSessionID = sessionID
    }
    await attestOnce(sessionID, agent)
    return agent
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
    pendingByCall.set(`${sessionID}:${callID}`, { ...approval, surface })
  }

  return {
    event: async ({ event }) => {
      if (event.type === "message.updated") {
        const info = event.properties?.info
        if (info?.role === "assistant" && typeof info.sessionID === "string" && typeof info.agent === "string") {
          agentBySession.set(info.sessionID, info.agent)
          if (rootSessionID === undefined) rootSessionID = info.sessionID
          await attestOnce(info.sessionID, info.agent)
        }
        return
      }
      const lane = permissionDecisionLane(event.type)
      if (!lane) return
      const requestID = event.properties.permissionID
      const sessionID = event.properties.sessionID
      if (typeof requestID !== "string" || typeof sessionID !== "string") return
      // Admitted before the approval lookup so a later delivery on the
      // preferred lane is still recorded once a compatibility one has acted.
      const admission = decisions.admit(requestID, lane)
      if (!admission.accepted) return
      const approval = pending.get(requestID)
      if (!approval) return
      if (approval.sessionID !== sessionID) return
      pending.delete(requestID)
      await decide(approval, normalizePermissionReply(event.properties.response ?? event.properties.reply), lane)
    },
    "permission.ask": async (permission: any, output: { status: "ask" | "deny" | "allow" }) => {
      const sessionID = permission?.sessionID
      const callID = permission?.callID
      const key = typeof sessionID === "string" && typeof callID === "string"
        ? `${sessionID}:${callID}`
        : undefined
      const approval = key ? pendingByCall.get(key) : undefined
      if (!approval) {
        // This hook must never turn an uncorrelated or unsupported host request
        // into consent. Keep the host's request denied and make the capability
        // failure observable to the host log/stderr.
        output.status = "deny"
        console.error("[gaia-opencode:permission] denied uncorrelated permission request")
        return
      }
      if (permission.id === undefined || permission.sessionID !== approval.sessionID) {
        output.status = "deny"
        console.error("[gaia-opencode:permission] denied permission request with invalid correlation")
        return
      }
      pendingByCall.delete(key!)
      pending.set(permission.id, approval)
      permission.title = "Gaia approval required"
      permission.pattern = approval.surface.visibleLines
      permission.metadata = {
        ...(permission.metadata ?? {}),
        gaiaApprovalID: approval.approvalID,
        gaiaCallID: approval.callID,
        gaiaConsent: approval.surface.metadata,
      }
      // The host owns the prompt and its reply. Do not auto-allow when a host
      // lacks the old creation API; OpenCode will deliver permission.replied.
      output.status = "ask"
    },
    "tool.execute.before": async (call, output) => {
      const requested = call.tool === "task"
        ? (output.args?.subagent_type ?? output.args?.agent)
        : undefined
      const dispatching = typeof requested === "string" ? requested : undefined
      const agent = await identify(call.sessionID, dispatching)
      if (dispatching) agentByCall.set(call.callID, dispatching)
      const normalized = normalizeBridgeToolRequest(call.tool, output.args, input)
      const response = await send({
        event: "tool.execute.before",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: dispatchHandle(call.sessionID),
        agent,
        roleContext: roleContext(call.sessionID),
        tool: normalized.tool,
        args: normalized.args,
        cwd: normalized.cwd,
        worktree: normalized.worktree,
        originalTool: normalized.originalTool,
        originalArgs: normalized.originalArgs,
      })
      if (response.action === "allow") {
        applyUpdatedInput(output, response.updated_input)
        return
      }
      if (approvalID(response)) {
        await requestApproval(response, call.sessionID, call.callID)
        // Let OpenCode continue into its real permission.ask hook. Throwing here
        // aborts the call before the host can create/correlate the request.
        return
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
          await attestOnce(sessionID, dispatchedAgent, call.sessionID)
        }
      }
      await send({
        event: "tool.execute.after",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: dispatchHandle(call.sessionID),
        agent,
        roleContext: roleContext(call.sessionID),
        tool: call.tool,
        args: call.args,
        result: toolResult(output),
      })
    },
  }
}

// The installed OpenCode loader (decompiled: dk()/lk()/pk()) takes a fast
// path when the module's default export matches {id, server: <function>},
// calling ONLY default.server(app, options) and never scanning the rest of
// the module's exports. Without this, the loader's fallback invokes EVERY
// exported function/{server:fn} value in this file as if it were its own
// plugin entry point -- including the helpers, the class, and the constants
// below -- which is what broke live loading after 4daa9bd removed this
// export. The official docs name only the named-export form; the decompiled
// loader is ground truth over the docs.
export default {
  id: "gaia",
  server: GaiaOpenCodePlugin,
}
