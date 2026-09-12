import { fileURLToPath } from "node:url"
import { lstatSync, realpathSync, statSync } from "node:fs"
import { createHash } from "node:crypto"
import { dirname, isAbsolute, parse, relative, resolve, sep } from "node:path"
import { ShellEnvDelivery } from "./shell-env"

type BridgeResponse = {
  action: "allow" | "ask" | "deny"
  reason?: string
  approval_id?: string
  updated_input?: Record<string, unknown>
  attestation?: string
  shell_env?: { session_id: string; call_id: string; agent_type: string }
  sections_provided?: string[]
}

type PendingApproval = {
  approvalID: string
  sessionID: string
  callID: string
  token: string
  surface: NativeConsentPresentation
}

type BoundRetry = PendingApproval & {
  agentID: string
  correlationID: string
  commands: string[]
  fingerprints: string[]
  expectedIndex: number
  usedCallIDs: Set<string>
}

export type BinaryDecisionRequest = {
  sessionID: string
  approvalID: string
  correlationID: string
  visibleText: string
  metadata: Record<string, unknown>
  question: {
    header: string
    question: string
    options: Array<{ label: string; description: string }>
    multiple: false
  }
}

type ControlDecision = {
  approval: PendingApproval
  request: BinaryDecisionRequest
  retry: BoundRetry
  questionCallID?: string
  questionID?: string
  closed: boolean
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

function canonicalBridgeToolName(value: unknown): string {
  const fileTool = canonicalFileTool(value)
  if (fileTool) return fileTool
  if (normalizedToken(value) === "question") return "AskUserQuestion"
  return typeof value === "string" ? value : ""
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
      tool: canonicalBridgeToolName(tool),
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

export function normalizePermissionReply(reply: unknown): PermissionReply | undefined {
  if (typeof reply !== "string") return undefined
  return PERMISSION_REPLIES[reply.trim().toLowerCase()]
}

/** The event OpenCode is expected to deliver a permission reply on. */
export const PREFERRED_PERMISSION_EVENT = "permission.replied"

/**
 * Compatibility only: an older OpenCode build spells the same reply with a
 * versioned event name. The name stops at this edge -- Gaia's neutral layer is
 * handed a lane token and never a host event name.
 */
export const COMPATIBILITY_PERMISSION_EVENTS = ["permission.v2.replied"]

export type DecisionLane = "control" | "preferred" | "compatibility"

/** Ordered strongest-first: a lane's index is its precedence rank. */
export const DECISION_LANE_PRECEDENCE: DecisionLane[] = ["control", "preferred", "compatibility"]

/**
 * Lifecycle events that carry no host decision to gate -- OpenCode never
 * awaits this hook's return before continuing a session's lifecycle -- so
 * forwarding them to Gaia's bridge is transport, not a permission check.
 */
const LIFECYCLE_EVENT_TYPES = new Set([
  "session.idle",
  "session.error",
  "session.deleted",
  "session.compacted",
])

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

  admit(correlationID: string, lane: DecisionLane): LaneAdmission {
    const prior = this.lanes.get(correlationID)
    if (prior === undefined) {
      this.lanes.set(correlationID, lane)
      return { lane, accepted: true, duplicate: false }
    }
    if (DECISION_LANE_PRECEDENCE.indexOf(lane) < DECISION_LANE_PRECEDENCE.indexOf(prior)) {
      this.lanes.set(correlationID, lane)
      return { lane, accepted: false, duplicate: true, supersededLane: prior }
    }
    return { lane: prior, accepted: false, duplicate: true }
  }

  effectiveLane(correlationID: string): DecisionLane | undefined {
    return this.lanes.get(correlationID)
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
  const child = Bun.spawn(["python3", bridgePath, "--shell-env-v1"], {
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

function commandFingerprint(command: string): string {
  return createHash("sha256").update(command, "utf8").digest("hex")
}

function boundRetry(approval: PendingApproval): BoundRetry {
  const metadata = approval.surface.metadata
  const binding = metadata.binding as Record<string, unknown> | undefined
  const commands = Array.isArray(metadata.commands) ? metadata.commands : []
  const fingerprints = Array.isArray(metadata.fingerprints) ? metadata.fingerprints : []
  const correlationID = metadata.correlation_id
  if (
    metadata.approval_id !== approval.approvalID
    || typeof correlationID !== "string"
    || !correlationID
    || !binding
    || typeof binding.agent_id !== "string"
    || !binding.agent_id
    || binding.session_id !== approval.sessionID
    || binding.call_id !== approval.callID
    || commands.length === 0
    || commands.length !== fingerprints.length
    || commands.some((command) => typeof command !== "string" || !command)
    || fingerprints.some((fingerprint, index) => (
      typeof fingerprint !== "string"
      || fingerprint !== commandFingerprint(String(commands[index]))
    ))
  ) {
    throw new Error("Gaia refused malformed or drifted consent retry metadata")
  }
  return {
    ...approval,
    agentID: binding.agent_id,
    correlationID,
    commands: commands.map(String),
    fingerprints: fingerprints.map(String),
    expectedIndex: 0,
    usedCallIDs: new Set([approval.callID]),
  }
}

export function binaryDecisionRequest(
  approval: PendingApproval,
  controlSessionID: string,
): BinaryDecisionRequest {
  const retry = boundRetry(approval)
  const visibleText = approval.surface.visibleLines.join("\n")
  if (!visibleText.includes(approval.approvalID)) {
    throw new Error("Gaia consent surface does not visibly identify its approval")
  }
  const approve = `Approve once [${approval.approvalID}]`
  const reject = `Reject [${approval.approvalID}]`
  return {
    sessionID: controlSessionID,
    approvalID: approval.approvalID,
    correlationID: retry.correlationID,
    visibleText,
    metadata: { ...approval.surface.metadata },
    question: {
      header: "Gaia approval",
      question: `${visibleText}\n\nDECISION: ${approval.approvalID} / ${retry.correlationID}`,
      options: [
        { label: approve, description: "Activate this exact request once" },
        { label: reject, description: "Create no grant and perform no operation" },
      ],
      multiple: false,
    },
  }
}

export function readBinaryDecision(
  request: BinaryDecisionRequest,
  answers: unknown,
): PermissionReply | undefined {
  if (!Array.isArray(answers) || answers.length !== 1 || !Array.isArray(answers[0])) {
    return undefined
  }
  if (answers[0].length !== 1) return undefined
  const selected = answers[0][0]
  if (selected === request.question.options[0].label) return "once"
  if (selected === request.question.options[1].label) return "reject"
  return undefined
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

export function questionAnswers(output: any): Record<string, string> | undefined {
  const nativeAnswers = output?.metadata?.answers
  if (!Array.isArray(nativeAnswers)) return undefined
  if (!nativeAnswers.every((answer) => Array.isArray(answer) && answer.every((label) => typeof label === "string"))) {
    return undefined
  }
  return Object.fromEntries(
    nativeAnswers.flatMap((answer: string[], questionIndex: number) =>
      answer.map((label, answerIndex) => [`${questionIndex}:${answerIndex}`, label]),
    ),
  )
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
  const controlBySession = new Map<string, ControlDecision>()
  const controlByQuestion = new Map<string, ControlDecision>()
  const retryBySession = new Map<string, BoundRetry>()
  const retryByCall = new Map<string, BoundRetry>()
  const agentBySession = new Map<string, string>()
  const authorizedDispatches = new Map<string, {
    role: string; requestedChildSessionID?: string; childSessionID?: string; reservedChildSessionID?: string; completed: boolean
  }>()
  const childBindings = new Map<string, { parentSessionID: string; callID: string; role: string }>()
  const provisionalBindings = new Map<string, { parentSessionID: string; callID: string; role: string }>()
  const bindingInFlight = new Map<string, Promise<void>>()
  const shellIdentities = new ShellEnvDelivery()
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
  // The early host binding names the dispatch before the child finishes.
  const dispatchBySession = new Map<string, string>()
  // One issuance per session even when two edges reach it at once. Without it a
  // tool call landing while the event handler's attest is still in flight sees
  // a named session with no claim yet and composes no context at all.
  const attestInFlight = new Map<string, Promise<void>>()
  // The backend ledger is read-modify-write: serialize all issuers in this plugin instance.
  let issuanceTail: Promise<void> = Promise.resolve()
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
    const issuing = issuanceTail.then(() => send({
      event: "identity.attest",
      sessionID,
      role,
      issuer: ROLE_ISSUER,
      parentAttestation,
    }))
    issuanceTail = issuing.then(() => undefined, () => undefined)
    const response = await issuing
    if (response.action === "allow" && typeof response.attestation === "string" && response.attestation) {
      attestationBySession.set(sessionID, response.attestation)
    }
  }

  function attestOnce(sessionID: string, role: string, grantor?: string): Promise<void> {
    const binding = childBindings.get(sessionID)
    if (binding && (binding.role !== role || (grantor && grantor !== binding.parentSessionID))) {
      return Promise.reject(new Error("Gaia child identity conflicts with its authorized dispatch"))
    }
    grantor = binding?.parentSessionID ?? grantor
    // A pre-binding message must not install a no-op promise that absorbs issuance.
    if (!isPrimarySession(sessionID) && !grantor) return Promise.resolve()
    const running = attestInFlight.get(sessionID)
    if (running) return running
    const started = attest(sessionID, role, grantor).finally(() => {
      attestInFlight.delete(sessionID)
    })
    attestInFlight.set(sessionID, started)
    return started
  }

  /** Correlate a host child binding with one previously allowed parent Task. */
  async function bindChild(
    parentSessionID: string, callID: string, childSessionID: string, reportStart = false,
  ): Promise<void> {
    const dispatch = authorizedDispatches.get(JSON.stringify([parentSessionID, callID]))
    const provisional = provisionalBindings.get(childSessionID)
    const prior = provisional ?? childBindings.get(childSessionID)
    const previousDispatch = prior && authorizedDispatches.get(JSON.stringify([prior.parentSessionID, prior.callID]))
    const sequentialResume = !provisional && prior && prior.callID !== callID
      && prior.parentSessionID === parentSessionID && prior.role === dispatch?.role
      && previousDispatch?.completed && !dispatch?.completed
      && dispatch?.requestedChildSessionID === childSessionID
    const knownRole = agentBySession.get(childSessionID)
    const parent = roleContext(parentSessionID)
    if (!dispatch || !callID || !childSessionID || isPrimarySession(childSessionID)
      || !isPrimarySession(parentSessionID) || parent?.role !== "gaia-orchestrator"
      || !parent.attestation
      || (dispatch.requestedChildSessionID && dispatch.requestedChildSessionID !== childSessionID)
      || (dispatch.completed && dispatch.childSessionID !== childSessionID)
      || (dispatch.childSessionID && dispatch.childSessionID !== childSessionID)
      || (dispatch.reservedChildSessionID && dispatch.reservedChildSessionID !== childSessionID)
      || (prior && !sequentialResume && (prior.parentSessionID !== parentSessionID || prior.callID !== callID
        || prior.role !== dispatch.role))
      || (knownRole && knownRole !== dispatch.role)) {
      throw new Error("Gaia refused an uncorrelated or conflicting child binding")
    }
    const running = bindingInFlight.get(childSessionID)
    if (running) return running
    // Reservations exclude conflicts but confer no identity, even to concurrent message callbacks.
    const reservation = { parentSessionID, callID, role: dispatch.role }
    if (sequentialResume) shellIdentities.clearSession(childSessionID)
    dispatch.reservedChildSessionID = childSessionID
    provisionalBindings.set(childSessionID, reservation)
    const ready: Promise<void> = Promise.resolve().then(async () => {
      if (reportStart) {
        const response = await send({
          event: "message.part.updated", sessionID: parentSessionID, callID,
          state: { metadata: { sessionId: childSessionID } },
        })
        if (response.action !== "allow"
          && (response.action !== undefined || !Array.isArray(response.sections_provided))) {
          throw new Error("Gaia refused the authorized child start binding")
        }
      }
      await attestOnce(childSessionID, dispatch.role, parentSessionID)
      if (!attestationBySession.has(childSessionID)) {
        throw new Error("Gaia could not attest the authorized child binding")
      }
      dispatch.childSessionID = childSessionID
      childBindings.set(childSessionID, reservation)
      dispatchBySession.set(childSessionID, callID)
      agentBySession.set(childSessionID, dispatch.role)
    }).finally(() => {
      if (provisionalBindings.get(childSessionID) === reservation) {
        provisionalBindings.delete(childSessionID)
        if (dispatch.reservedChildSessionID === childSessionID) delete dispatch.reservedChildSessionID
      }
      if (bindingInFlight.get(childSessionID) === ready) bindingInFlight.delete(childSessionID)
    })
    bindingInFlight.set(childSessionID, ready)
    return ready
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
    const ready = bindingInFlight.get(sessionID)
    if (ready) await ready
    let agent = agentBySession.get(sessionID)
    if (!agent) {
      const recovered = await hostAgent(sessionID, dispatching)
      const started = bindingInFlight.get(sessionID)
      if (started) await started
      agent = agentBySession.get(sessionID) ?? recovered
      if (!agent) return undefined
      agentBySession.set(sessionID, agent)
      if (rootSessionID === undefined) rootSessionID = sessionID
    }
    await attestOnce(sessionID, agent)
    return agent
  }

  /** Read the requested Task role without accepting contradictory aliases. */
  function dispatchRole(args?: Record<string, unknown>): string | undefined {
    if (args?.subagent_type !== undefined && args?.agent !== undefined
      && args.subagent_type !== args.agent) {
      throw new Error("Gaia dispatch role aliases disagree")
    }
    const role = args?.subagent_type ?? args?.agent
    return typeof role === "string" ? role : undefined
  }

  async function decide(
    approval: PendingApproval,
    reply: PermissionReply,
    lane: DecisionLane = "preferred",
  ): Promise<boolean> {
    return gaia([
      "approvals", "opencode-decide", approval.approvalID,
      "--session-id", approval.sessionID,
      "--call-id", approval.callID,
      "--token", approval.token,
      "--reply", reply,
      "--decision-lane", lane === "compatibility" ? "compatibility" : "preferred",
      "--json",
    ])
  }

  function closeControl(control: ControlDecision): void {
    control.closed = true
    if (control.questionID && controlByQuestion.get(control.questionID) === control) {
      controlByQuestion.delete(control.questionID)
    }
  }

  function clearPendingApproval(approvalID: string): void {
    for (const [key, approval] of pendingByCall) {
      if (approval.approvalID === approvalID) pendingByCall.delete(key)
    }
    for (const [key, approval] of pending) {
      if (approval.approvalID === approvalID) pending.delete(key)
    }
    for (const control of controlBySession.values()) {
      if (control.approval.approvalID === approvalID) closeControl(control)
    }
  }

  async function applyDecision(
    control: ControlDecision,
    reply: PermissionReply,
    lane: DecisionLane,
  ): Promise<boolean> {
    const existing = retryBySession.get(control.approval.sessionID)
    if (reply === "once" && existing && existing.correlationID !== control.retry.correlationID) {
      closeControl(control)
      return false
    }
    const admission = decisions.admit(control.request.correlationID, lane)
    if (!admission.accepted) {
      closeControl(control)
      return false
    }
    const applied = await decide(control.approval, reply, lane)
    if (applied && reply === "once") {
      retryBySession.set(control.approval.sessionID, {
        ...control.retry,
        usedCallIDs: new Set(control.retry.usedCallIDs),
      })
    }
    clearPendingApproval(control.approval.approvalID)
    return applied
  }

  async function openBinaryDecision(approval: PendingApproval): Promise<ControlDecision> {
    const session = input?.client?.session
    if (typeof session?.create !== "function" || typeof session?.promptAsync !== "function" || !rootSessionID) {
      throw new Error("OpenCode control plane cannot provide a binary question")
    }
    const created = await session.create({
      body: { parentID: rootSessionID, title: `Gaia ${approval.approvalID}` },
    })
    const controlSessionID = created?.data?.id ?? created?.id
    if (
      typeof controlSessionID !== "string"
      || !controlSessionID
      || controlSessionID === rootSessionID
      || controlSessionID === approval.sessionID
      || controlBySession.has(controlSessionID)
    ) {
      throw new Error("OpenCode did not create a fresh control-plane decision session")
    }
    const request = binaryDecisionRequest(approval, controlSessionID)
    const control: ControlDecision = {
      approval,
      request,
      retry: boundRetry(approval),
      closed: false,
    }
    controlBySession.set(controlSessionID, control)
    const instruction = [
      "You are a mechanical consent control plane.",
      "Invoke the question tool exactly once with the JSON below and do nothing else.",
      "Do not answer the question, infer consent, rewrite any text, or emit approval prose.",
      JSON.stringify({ questions: [request.question] }),
    ].join("\n")
    try {
      await session.promptAsync({
        path: { id: controlSessionID },
        body: {
          agent: "gaia-orchestrator",
          system: ["Only the question tool is available. Free text has no decision authority."],
          tools: { "*": false, question: true },
          parts: [{ type: "text", text: instruction }],
        },
      })
    } catch (error) {
      closeControl(control)
      throw error
    }
    return control
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
    const pendingApproval = { ...approval, surface }
    const control = await openBinaryDecision(pendingApproval)
    if (!control.closed) pendingByCall.set(`${sessionID}:${callID}`, pendingApproval)
  }

  function consentRetry(
    retry: BoundRetry | undefined,
    call: { sessionID?: string; callID?: string },
    agent: string | undefined,
    tool: string,
    args: Record<string, unknown>,
  ): Record<string, unknown> | undefined {
    const command = args.command
    if (
      !retry
      || tool.toLowerCase() !== "bash"
      || call.sessionID !== retry.sessionID
      || typeof call.callID !== "string"
      || !call.callID
      || retry.usedCallIDs.has(call.callID)
      || agent !== retry.agentID
      || typeof command !== "string"
      || retry.commands[retry.expectedIndex] !== command
      || retry.fingerprints[retry.expectedIndex] !== commandFingerprint(command)
    ) {
      return undefined
    }
    return {
      approval_id: retry.approvalID,
      correlation_id: retry.correlationID,
      agent_id: retry.agentID,
      session_id: retry.sessionID,
      original_call_id: retry.callID,
      retry_call_id: call.callID,
      command,
      command_fingerprint: commandFingerprint(command),
      expected_index: retry.expectedIndex,
    }
  }

  return {
    dispose: async () => {
      shellIdentities.clear()
      pending.clear()
      pendingByCall.clear()
      controlByQuestion.clear()
      controlBySession.clear()
      retryByCall.clear()
      retryBySession.clear()
    },
    event: async ({ event }) => {
      if (event.type === "question.asked") {
        const sessionID = event.properties?.sessionID
        const requestID = event.properties?.id
        const control = typeof sessionID === "string" ? controlBySession.get(sessionID) : undefined
        if (!control || control.closed || typeof requestID !== "string" || !requestID) return
        if (
          control.questionID
          || controlByQuestion.has(requestID)
          || JSON.stringify(event.properties?.questions) !== JSON.stringify([control.request.question])
        ) {
          closeControl(control)
          return
        }
        control.questionID = requestID
        controlByQuestion.set(requestID, control)
        return
      }
      if (event.type === "question.rejected") {
        const requestID = event.properties?.requestID ?? event.properties?.id
        const control = typeof requestID === "string" ? controlByQuestion.get(requestID) : undefined
        if (control) closeControl(control)
        return
      }
      if (event.type === "question.replied") {
        const requestID = event.properties?.requestID
        const sessionID = event.properties?.sessionID
        const control = typeof requestID === "string" ? controlByQuestion.get(requestID) : undefined
        if (!control || control.closed || control.request.sessionID !== sessionID) return
        const reply = readBinaryDecision(control.request, event.properties?.answers)
        if (!reply) {
          clearPendingApproval(control.approval.approvalID)
          return
        }
        await applyDecision(control, reply, "control")
        return
      }
      if (event.type === "message.updated") {
        const info = event.properties?.info
        if (info?.role === "assistant" && typeof info.sessionID === "string" && typeof info.agent === "string") {
          const ready = bindingInFlight.get(info.sessionID)
          if (ready) await ready
          const binding = childBindings.get(info.sessionID)
          if (binding && binding.role !== info.agent) {
            throw new Error("Gaia child role conflicts with its authorized dispatch")
          }
          agentBySession.set(info.sessionID, info.agent)
          if (rootSessionID === undefined) rootSessionID = info.sessionID
          await attestOnce(info.sessionID, info.agent)
        }
        return
      }
      if (event.type === "message.part.updated") {
        // The callID<->child-sessionID binding for a concurrent dispatch
        // travels here, on the DISPATCHING session's own part, the moment
        // the child session is named -- well before session.idle or
        // tool.execute.after report the same call complete (measured:
        // project_gaia_opencode_lifecycle_medido_2026_08_26). Filtered to
        // this exact shape so the frequent non-tool part-stream is never
        // forwarded.
        const part = event.properties?.part
        if (
          part?.type === "tool"
          && part.tool === "task"
          && typeof part.sessionID === "string"
          && typeof part.callID === "string"
          && typeof part.state?.metadata?.sessionId === "string"
        ) {
          await bindChild(part.sessionID, part.callID, part.state.metadata.sessionId, true)
        }
        return
      }
      if (LIFECYCLE_EVENT_TYPES.has(event.type)) {
        const sessionID = event.properties?.sessionID
        if (typeof sessionID === "string") {
          const control = controlBySession.get(sessionID)
          if (control) {
            closeControl(control)
            controlBySession.delete(sessionID)
            return
          }
          shellIdentities.clearSession(sessionID)
          await send({ event: event.type, sessionID })
        }
        return
      }
      const lane = permissionDecisionLane(event.type)
      if (!lane) return
      const requestID = event.properties.permissionID
      const sessionID = event.properties.sessionID
      if (typeof requestID !== "string" || typeof sessionID !== "string") return
      const approval = pending.get(requestID)
      if (!approval) return
      if (approval.sessionID !== sessionID) return
      const reply = normalizePermissionReply(event.properties.response ?? event.properties.reply)
      const control = [...controlBySession.values()].find(
        (candidate) => candidate.approval.approvalID === approval.approvalID,
      )
      if (!reply || !control || control.closed) {
        clearPendingApproval(approval.approvalID)
        return
      }
      await applyDecision(control, reply, lane)
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
      const control = controlBySession.get(call.sessionID)
      if (control) {
        if (control.closed) {
          throw new Error("Gaia consent control plane is already closed")
        }
        if (
          canonicalBridgeToolName(call.tool) !== "AskUserQuestion"
          || JSON.stringify(output.args?.questions) !== JSON.stringify([control.request.question])
          || (control.questionCallID !== undefined && control.questionCallID !== call.callID)
        ) {
          closeControl(control)
          throw new Error("Gaia consent control plane permits one exact binary question")
        }
        control.questionCallID = call.callID
        return
      }
      shellIdentities.forget(call.sessionID, call.callID)
      const dispatching = call.tool === "task" ? dispatchRole(output.args) : undefined
      const requestedChildSessionID = call.tool === "task" ? output.args?.task_id : undefined
      if (requestedChildSessionID !== undefined
        && (typeof requestedChildSessionID !== "string" || !requestedChildSessionID)) {
        throw new Error("Gaia dispatch has an invalid task_id")
      }
      const agent = await identify(call.sessionID, dispatching)
      const dispatchKey = JSON.stringify([call.sessionID, call.callID])
      const priorDispatch = authorizedDispatches.get(dispatchKey)
      if (call.tool === "task" && priorDispatch
        && (priorDispatch.role !== dispatching || priorDispatch.requestedChildSessionID !== requestedChildSessionID || priorDispatch.completed
          || priorDispatch.childSessionID || priorDispatch.reservedChildSessionID)) {
        throw new Error("Gaia refused a reused or relabelled dispatch call")
      }
      const normalized = normalizeBridgeToolRequest(call.tool, output.args, input)
      const retry = retryBySession.get(call.sessionID)
      const retryProof = consentRetry(retry, call, agent, normalized.tool, normalized.args)
      if (retry && !retryProof) {
        throw new Error("Gaia refused a drifted or replayed consent retry")
      }
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
        consentRetry: retryProof,
      })
      if (response.action === "allow") {
        if (retry && retryProof) {
          retry.usedCallIDs.add(call.callID)
          retryByCall.set(`${call.sessionID}:${call.callID}`, retry)
        }
        applyUpdatedInput(output, response.updated_input)
        if (dispatching) {
          const context = roleContext(call.sessionID)
          const appliedRole = dispatchRole(output.args)
          if (!call.callID || !isPrimarySession(call.sessionID)
            || context?.role !== "gaia-orchestrator" || !context.attestation
            || appliedRole !== dispatching || output.args?.task_id !== requestedChildSessionID) {
            throw new Error("Gaia dispatch lacks an authenticated unchanged parent request")
          }
          const current = authorizedDispatches.get(dispatchKey)
          if (current && (current.role !== dispatching || current.requestedChildSessionID !== requestedChildSessionID || current.completed
            || current.childSessionID || current.reservedChildSessionID)) {
            throw new Error("Gaia refused a concurrently reused dispatch call")
          }
          if (!current) authorizedDispatches.set(dispatchKey, { role: dispatching, requestedChildSessionID, completed: false })
        }
        if (normalized.tool === "Bash" || normalized.tool === "bash") {
          const context = roleContext(call.sessionID)
          const delivery = response.shell_env
          if (!delivery || delivery.session_id !== call.sessionID || delivery.call_id !== call.callID
            || delivery.agent_type !== agent || !context?.attestation) {
            throw new Error("Gaia bridge did not confirm authenticated shell environment delivery")
          }
          const directory = output.args?.workdir ?? input.directory
          if (typeof directory !== "string" || !isAbsolute(directory)) {
            throw new Error("Gaia shell environment lacks absolute invocation directory")
          }
          shellIdentities.remember({
            sessionID: call.sessionID, callID: call.callID,
            agent: delivery.agent_type, attestation: context.attestation,
            cwd: resolve(directory), args: output.args,
          })
        }
        return
      }
      if (approvalID(response)) {
        await requestApproval(response, call.sessionID, call.callID)
        throw new Error(response.reason ?? "Gaia requires approval before retrying this tool call")
      }
      throw new Error(response.reason ?? "Gaia denied this tool call without a persisted approval")
    },
    "tool.execute.after": async (call, output) => {
      const control = controlBySession.get(call.sessionID)
      if (control) {
        if (
          canonicalBridgeToolName(call.tool) !== "AskUserQuestion"
          || control.questionCallID !== call.callID
        ) {
          closeControl(control)
          throw new Error("Gaia refused a drifted control-plane tool result")
        }
        return
      }
      shellIdentities.forget(call.sessionID, call.callID)
      const agent = agentBySession.get(call.sessionID)
      if (call.tool === "task") {
        const sessionID = output.metadata?.sessionId
        const dispatch = authorizedDispatches.get(JSON.stringify([call.sessionID, call.callID]))
        if (typeof sessionID === "string") {
          await bindChild(call.sessionID, call.callID, sessionID)
          if (dispatch && output.metadata?.background !== true) {
            dispatch.completed = true
            shellIdentities.clearSession(sessionID)
          }
        }
      }
      const result = toolResult(output)
      if (canonicalBridgeToolName(call.tool) === "AskUserQuestion") {
        const answers = questionAnswers(output)
        if (answers) result.answers = answers
      }
      await send({
        event: "tool.execute.after",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: dispatchHandle(call.sessionID),
        agent,
        roleContext: roleContext(call.sessionID),
        tool: canonicalBridgeToolName(call.tool),
        args: call.args,
        result,
      })
      const retryKey = `${call.sessionID}:${call.callID}`
      const retried = retryByCall.get(retryKey)
      if (retried) {
        retryByCall.delete(retryKey)
        if (result.exit_code === 0) {
          retried.expectedIndex += 1
          if (retried.expectedIndex >= retried.commands.length) {
            if (retryBySession.get(call.sessionID) === retried) retryBySession.delete(call.sessionID)
          }
        } else if (retryBySession.get(call.sessionID) === retried) {
          retryBySession.delete(call.sessionID)
        }
      }
    },
    "shell.env": async (call, output) => {
      if (!call.sessionID) throw new Error("Gaia shell environment lacks session identity")
      const context = roleContext(call.sessionID)
      if (!call.callID && isPrimarySession(call.sessionID)
        && context?.attestation && context.role === "gaia-orchestrator") return
      if (!call.callID) throw new Error("Gaia dispatched shell environment lacks call identity")
      output.env.GAIA_DISPATCH_AGENT = shellIdentities.take({
        sessionID: call.sessionID, callID: call.callID, cwd: resolve(call.cwd),
        agent: agentBySession.get(call.sessionID),
        attestation: context?.attestation,
      })
    },
    // The installed OpenCode host fires this hook mid-compaction, before the
    // summary completes, with a mutable {context, prompt} output -- unlike
    // "session.compacted" (forwarded above, LIFECYCLE_EVENT_TYPES), which
    // fires only after and cannot inject anything. This is the one point
    // that can put a dispatched child's contract kernel back into context
    // before OpenCode's own compaction discards the messages it lived in.
    "experimental.session.compacting": async (compacting: any, output: any) => {
      const sessionID = compacting?.sessionID
      if (typeof sessionID !== "string") return
      const response = await send({
        event: "session.compacting",
        sessionID,
        agentID: dispatchHandle(sessionID),
        agent: agentBySession.get(sessionID),
        roleContext: roleContext(sessionID),
      })
      const injected = response.action === "allow" ? response.updated_input?.context : undefined
      // Mutated in place, never reassigned: output.context = [...] replaces
      // the reference the host already captured, the same measured no-op
      // applyUpdatedInput's own comment documents for output.args.
      if (Array.isArray(injected) && output && Array.isArray(output.context)) {
        output.context.push(...injected)
      }
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
