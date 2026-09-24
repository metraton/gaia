import { fileURLToPath } from "node:url"
import { lstatSync, mkdirSync, realpathSync, statSync } from "node:fs"
import { createHash } from "node:crypto"
import { homedir } from "node:os"
import { delimiter, dirname, isAbsolute, join, parse, relative, resolve, sep } from "node:path"
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
  role: string
  surface: SignatureSurface
}

type RetryOperation =
  | {
    version: 1
    kind: "COMMAND_SET"
    commands: string[]
    fingerprints: string[]
    requestFingerprint: string
    expectedIndex: number
  }
  | {
    version: 1
    kind: "SCOPE_SEMANTIC_SIGNATURE"
    command: string
    commandFingerprint: string
  }
  | {
    version: 1
    kind: "SCOPE_FILE_PATH"
    canonicalPath: string
    pathFingerprint: string
    toolFamily: ["Write", "Edit"]
  }

type BoundRetry = PendingApproval & {
  agentID: string
  correlationID: string
  commands: string[]
  fingerprints: string[]
  expectedIndex: number
  usedCallIDs: Set<string>
  operation: RetryOperation
}

/** Which of the renderer's strings a signature question carries. */
export type SignatureMode = "signature" | "details"

export type SignatureRequest = {
  sessionID: string
  approvalID: string
  correlationID: string
  mode: SignatureMode
  metadata: Record<string, unknown>
  question: {
    header: string
    question: string
    options: Array<{ label: string; description: string }>
    multiple: false
    custom: false
  }
}

/** A user's answer to a signature question, by the option's position. */
export type SignatureAnswer = "once" | "reject" | "details"

type ControlDecision = {
  approval: PendingApproval
  request: SignatureRequest
  retry: BoundRetry
  questionCallID?: string
  questionID?: string
  awaitingSafeIdle: boolean
  presenting: boolean
  presented: boolean
  closed: boolean
}

type DeferredActivation = {
  control: ControlDecision
  lane: DecisionLane
  retry: BoundRetry
}

type RoleCapabilityContext = {
  role: string
  capabilities: string[]
  issuer: "opencode-runtime"
  attestation: string
  verified: true
}

/** The two answers that reach Gaia as a decision; Details only re-asks. */
type DecisionReply = Exclude<SignatureAnswer, "details">

type HostParentRecord = "none" | "present" | "unavailable"

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

/**
 * The directory a bash call runs in: its `workdir` argument, resolved against
 * the session directory, or the session directory itself. A signed command is
 * sealed to one directory, so the policy must compare where it will actually
 * run, not where the bridge process happens to start.
 */
function bashDirectory(args: Record<string, unknown>, input: any): string | undefined {
  const base = typeof input?.directory === "string" ? input.directory : undefined
  const workdir = typeof args.workdir === "string" && args.workdir ? args.workdir : undefined
  if (workdir) return base ? resolve(base, workdir) : isAbsolute(workdir) ? resolve(workdir) : undefined
  return base
}

/** Canonicalize governed file tools before any request reaches Gaia's bridge. */
export function normalizeBridgeToolRequest(
  tool: unknown,
  args: unknown,
  input: any,
): NormalizedBridgeToolRequest {
  const canonicalTool = canonicalFileTool(tool)
  if (!canonicalTool) {
    const plainArgs = args && typeof args === "object" && !Array.isArray(args)
      ? args as Record<string, unknown>
      : {}
    const bridgeTool = canonicalBridgeToolName(tool)
    return {
      tool: bridgeTool,
      args: plainArgs,
      ...(bridgeTool.toLowerCase() === "bash" ? { cwd: bashDirectory(plainArgs, input) } : {}),
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

/**
 * The bridge event that records a host permission request matching no Gaia
 * verdict. Must stay equal to bridge.py's UNCORRELATED_PERMISSION_EVENT: the
 * two halves of this adapter exchange the name by value, and a rename on one
 * side silently stops the audit rather than failing.
 */
export const UNCORRELATED_PERMISSION_EVENT = "permission.uncorrelated"
export const CONTROL_OPENED_EVENT = "control.opened"
export const CONTROL_CLOSED_EVENT = "control.closed"
export const DECISION_APPLIED_EVENT = "decision.applied"
export const CONSENT_RETRY_REFUSED_EVENT = "retry.refused"

/** The schema-valid arguments the control-plane model is asked to pass; Gaia overwrites them. */
export const CONTROL_PLACEHOLDER_QUESTION = {
  question: "Gaia",
  header: "Gaia",
  options: [{ label: "OK", description: "Gaia fills this question in" }],
}

/**
 * The one message the plugin puts in the orchestrator's session once the user
 * activated an approval. Deterministic so the orchestrator can match it, and
 * addressed to the ROOT session because the specialist's turn is already over:
 * its blocked attempt was a tool error that ended the turn, so nobody is left
 * in that session to retry, and a prompt sent there would run the specialist
 * with no dispatch, no contract row and no coordinator reading the result.
 * The orchestrator re-dispatches with task_id = the specialist session, which
 * is the only shape the plugin's retry accounting accepts.
 */
export function activationNotice(
  approvalID: string,
  specialistSessionID: string,
  operation: RetryOperation,
): string {
  const retry = operation.kind === "COMMAND_SET"
    ? `command [${operation.expectedIndex}]`
    : operation.kind === "SCOPE_SEMANTIC_SIGNATURE"
      ? "the exact approved Bash command"
      : `the exact granted file target with ${operation.toolFamily.join("/")}`
  return `Gaia: approval ${approvalID} has an executable ${operation.kind} grant and typed retry descriptor. `
    + `Resume the specialist session ${specialistSessionID} (task_id) so it retries ${retry} now.`
}

/**
 * Why a consent control was released. Recorded verbatim by the bridge, so a
 * reader of harness_events can tell which exit the control took; `decided` is
 * the one exit where the user's answer reached Gaia.
 */
export type ControlCloseReason =
  | "decided"
  | "decide_failed"
  | "question_mismatch"
  | "question_rejected"
  | "reply_unreadable"
  | "details_requested"
  | "decision_duplicate"
  | "retry_conflict"
  | "prompt_rejected"
  | "session_ended"
  | "drifted_tool_call"
  | "drifted_tool_result"

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
const gaiaBinDirectory = dirname(gaiaPath)

/**
 * Create and return a dispatched child's own TMPDIR, or undefined when it
 * cannot exist. Mirrors gaia/paths/resolver.py::dispatch_tmp_dir, which owns
 * the rule; tests/integration/test_opencode_shell_env.py compares the two.
 */
function dispatchTmpDir(owner: string): string | undefined {
  const override = process.env.GAIA_DATA_DIR
  const dataDir = override ? resolve(override) : join(homedir(), ".gaia")
  const name = createHash("sha256").update(owner).digest("hex").slice(0, 12)
  try {
    mkdirSync(join(dataDir, "tmp", name), { recursive: true })
    // Python resolves symlinks in an overridden data dir, never in ~/.gaia.
    return join(override ? realpathSync(dataDir) : dataDir, "tmp", name)
  } catch {
    return undefined
  }
}

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

/** The directory Gaia's own processes run from, so their writes are attributed
 * to the session's workspace: `resolve_workspace` derives the workspace from
 * the cwd, and `opencode serve` may run from a directory that is not the
 * project (measured: events from a /home/jorge serve landed in workspace
 * 'jorge' instead of 'me'). Undefined when the host handed no absolute
 * directory, which leaves the spawn inheriting this process's cwd. */
function gaiaDirectory(input: any): string | undefined {
  const directory = input?.directory
  return typeof directory === "string" && isAbsolute(directory) ? directory : undefined
}

async function bridge(event: Record<string, unknown>, cwd: string | undefined): Promise<BridgeResponse> {
  if (process.env.GAIA_DEBUG) {
    console.error(`[gaia-opencode-bridge:request] ${JSON.stringify(traceableBridgeRequest(event))}`)
  }
  const child = Bun.spawn(["python3", bridgePath, "--shell-env-v1"], {
    cwd,
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

async function gaiaCapture(
  args: string[],
  cwd: string | undefined,
): Promise<{ ok: boolean; stdout: string; stderr: string }> {
  const child = Bun.spawn(["python3", gaiaPath, ...args], {
    cwd,
    env: { ...process.env, GAIA_HOST: "opencode" },
    stdout: "pipe",
    stderr: "pipe",
  })
  const [code, stdout, stderr] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ])
  return { ok: code === 0, stdout, stderr }
}

/** The cause Gaia gave for a failed CLI call: its `--json` error line, else stderr. */
export function gaiaFailureCause(result: { stdout: string; stderr: string }): string {
  const lastLine = result.stdout.trim().split("\n").pop() ?? ""
  try {
    const emitted = JSON.parse(lastLine)
    if (typeof emitted?.error === "string" && emitted.error) return emitted.error
  } catch {
    // Not a JSON line; fall through to stderr.
  }
  return result.stderr.trim() || "gaia exited non-zero without reporting a cause"
}

/** The renderer's signature for one approval, exactly as `opencode-present` emitted it. */
export type SignatureSurface = {
  question: string
  details: string
  header: string
  options: Array<{ label: string; description: string }>
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
    operation: {
      version: 1,
      kind: "COMMAND_SET",
      commands: commands.map(String),
      fingerprints: fingerprints.map(String),
      requestFingerprint: String(metadata.request_fingerprint ?? ""),
      expectedIndex: 0,
    },
  }
}

function boundRetryFromActivation(
  approval: PendingApproval,
  correlationID: string,
  raw: unknown,
): BoundRetry {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("Gaia activation returned no typed retry descriptor")
  }
  const descriptor = raw as Record<string, unknown>
  const binding = approval.surface.metadata.binding as Record<string, unknown> | undefined
  if (descriptor.version !== 1) throw new Error("Gaia activation retry descriptor has an unsupported version")
  if (descriptor.approval_id !== approval.approvalID) throw new Error("Gaia activation retry descriptor approval drifted")
  if (descriptor.agent_id !== binding?.agent_id) throw new Error("Gaia activation retry descriptor agent drifted")
  if (descriptor.session_id !== approval.sessionID) throw new Error("Gaia activation retry descriptor session drifted")
  if (descriptor.original_call_id !== approval.callID) throw new Error("Gaia activation retry descriptor original call drifted")
  let operation: RetryOperation
  let commands: string[]
  let fingerprints: string[]
  let expectedIndex = 0
  if (descriptor.kind === "COMMAND_SET") {
    commands = Array.isArray(descriptor.commands) ? descriptor.commands.map(String) : []
    fingerprints = Array.isArray(descriptor.fingerprints) ? descriptor.fingerprints.map(String) : []
    expectedIndex = Number(descriptor.expected_index)
    if (
      commands.length === 0
      || commands.length !== fingerprints.length
      || !Number.isInteger(expectedIndex)
      || expectedIndex < 0
      || expectedIndex >= commands.length
      || typeof descriptor.request_fingerprint !== "string"
      || commands.some((command, index) => commandFingerprint(command) !== fingerprints[index])
    ) throw new Error("Gaia activation returned an invalid COMMAND_SET retry descriptor")
    operation = {
      version: 1,
      kind: "COMMAND_SET",
      commands,
      fingerprints,
      requestFingerprint: descriptor.request_fingerprint,
      expectedIndex,
    }
  } else if (descriptor.kind === "SCOPE_SEMANTIC_SIGNATURE") {
    const command = descriptor.command
    const fingerprint = descriptor.command_fingerprint
    if (
      typeof command !== "string"
      || !command
      || typeof fingerprint !== "string"
      || commandFingerprint(command) !== fingerprint
    ) throw new Error("Gaia activation returned an invalid semantic retry descriptor")
    commands = [command]
    fingerprints = [fingerprint]
    operation = {
      version: 1,
      kind: "SCOPE_SEMANTIC_SIGNATURE",
      command,
      commandFingerprint: fingerprint,
    }
  } else if (descriptor.kind === "SCOPE_FILE_PATH") {
    const canonicalPath = descriptor.canonical_path
    const pathFingerprint = descriptor.path_fingerprint
    const family = descriptor.tool_family
    if (
      typeof canonicalPath !== "string"
      || !isAbsolute(canonicalPath)
      || typeof pathFingerprint !== "string"
      || commandFingerprint(canonicalPath) !== pathFingerprint
      || !Array.isArray(family)
      || family.join(":") !== "Write:Edit"
    ) throw new Error("Gaia activation returned an invalid file retry descriptor")
    commands = [canonicalPath]
    fingerprints = [pathFingerprint]
    operation = {
      version: 1,
      kind: "SCOPE_FILE_PATH",
      canonicalPath,
      pathFingerprint,
      toolFamily: ["Write", "Edit"],
    }
  } else {
    throw new Error("Gaia activation returned an unsupported retry descriptor kind")
  }
  return {
    ...approval,
    agentID: String(binding?.agent_id),
    correlationID,
    commands,
    fingerprints,
    expectedIndex,
    usedCallIDs: new Set([approval.callID]),
    operation,
  }
}

export type ConsentRetryRefusal = {
  reason:
    | "replayed_call_id" | "session_mismatch" | "role_mismatch" | "out_of_order" | "fingerprint_mismatch"
    | "path_mismatch" | "tool_family_mismatch" | "host_gate_refused"
  expected: string
  received: string
}

export type ConsentRetryVerdict =
  | { proof: Record<string, unknown>; refusal?: undefined }
  | { proof?: undefined; refusal: ConsentRetryRefusal }

/** Decide whether one tool call is the bound retry, a drifted claim to it, or unrelated.
 *
 * Unrelated returns `undefined`: a call that names none of the approved
 * commands and reuses no retry call id claims nothing, so it travels with no
 * proof and the host-neutral policy rules on it (the specialist loads skills,
 * reads, and runs the Gaia CLI while a retry is bound). Only a call that does
 * claim the retry -- an approved command's exact bytes, or a call id already
 * spent on one -- is judged, and a judged call either yields the proof or the
 * one comparison that refused it, expected against received.
 *
 * The role compared is the host role of the session the approval was
 * presented to, never `agentID`: that field is the approval's Gaia contract
 * identity (`a69d869dc02031f54`), and the session's agent is `gaia-operator`
 * (measured 2026-09-16: comparing the two refused a byte-identical retry).
 */
export function evaluateConsentRetry(
  retry: BoundRetry,
  call: { sessionID?: string; callID?: string },
  agent: string | undefined,
  tool: string,
  args: Record<string, unknown>,
): ConsentRetryVerdict | undefined {
  const callID = typeof call.callID === "string" && call.callID ? call.callID : undefined
  const replayed = callID !== undefined && retry.usedCallIDs.has(callID)
  const refuse = (reason: ConsentRetryRefusal["reason"], expected: string, received: string) => (
    { refusal: { reason, expected, received } }
  )
  const operation = retry.operation
  const command = tool.toLowerCase() === "bash" && typeof args.command === "string" ? args.command : undefined
  const collapse = (text: string) => text.trim().replace(/\s+/g, " ")
  let claimedIndex = -1
  let fileTool: "Write" | "Edit" | undefined
  let filePaths: string[] = []
  if (operation.kind === "COMMAND_SET") {
    claimedIndex = command === undefined
      ? -1
      : operation.commands.findIndex((approved) => collapse(approved) === collapse(command))
    if (claimedIndex < 0 && !replayed) return undefined
  } else if (operation.kind === "SCOPE_SEMANTIC_SIGNATURE") {
    const claimed = command !== undefined && collapse(command) === collapse(operation.command)
    if (!claimed && !replayed) return undefined
  } else {
    const canonical = canonicalFileTool(tool)
    if (!canonical && !replayed) return undefined
    fileTool = canonical === "apply_patch" ? "Edit" : canonical
    filePaths = canonical === "apply_patch"
      ? (Array.isArray(args.file_paths) ? args.file_paths.filter((path): path is string => typeof path === "string") : [])
      : (typeof args.file_path === "string" ? [args.file_path] : [])
  }
  if (replayed || callID === undefined) return refuse("replayed_call_id", "an unused call id", String(callID))
  if (call.sessionID !== retry.sessionID) return refuse("session_mismatch", retry.sessionID, String(call.sessionID))
  if (agent !== retry.role) return refuse("role_mismatch", retry.role, String(agent))
  if (operation.kind === "SCOPE_FILE_PATH") {
    if (!fileTool || !operation.toolFamily.includes(fileTool)) {
      return refuse("tool_family_mismatch", operation.toolFamily.join("/"), String(fileTool))
    }
    if (filePaths.length !== 1 || filePaths[0] !== operation.canonicalPath) {
      return refuse("path_mismatch", operation.canonicalPath, filePaths.join(","))
    }
    return {
      proof: {
        version: operation.version,
        kind: operation.kind,
        approval_id: retry.approvalID,
        correlation_id: retry.correlationID,
        agent_id: retry.agentID,
        role: retry.role,
        session_id: retry.sessionID,
        original_call_id: retry.callID,
        retry_call_id: callID,
        canonical_path: operation.canonicalPath,
        path_fingerprint: operation.pathFingerprint,
        tool_family: fileTool,
      },
    }
  }
  const expectedCommand = operation.kind === "COMMAND_SET"
    ? operation.commands[operation.expectedIndex]
    : operation.command
  const expectedFingerprint = operation.kind === "COMMAND_SET"
    ? operation.fingerprints[operation.expectedIndex]
    : operation.commandFingerprint
  if (operation.kind === "COMMAND_SET" && claimedIndex !== operation.expectedIndex) {
    return refuse("out_of_order", `command [${operation.expectedIndex}]`, `command [${claimedIndex}]`)
  }
  const fingerprint = commandFingerprint(command!)
  if (command !== expectedCommand || expectedFingerprint !== fingerprint) {
    return refuse("fingerprint_mismatch", expectedFingerprint, fingerprint)
  }
  return {
    proof: {
      version: operation.version,
      kind: operation.kind,
      approval_id: retry.approvalID,
      correlation_id: retry.correlationID,
      agent_id: retry.agentID,
      role: retry.role,
      session_id: retry.sessionID,
      original_call_id: retry.callID,
      retry_call_id: callID,
      command,
      command_fingerprint: fingerprint,
      ...(operation.kind === "COMMAND_SET" ? {
        expected_index: operation.expectedIndex,
        request_fingerprint: operation.requestFingerprint,
      } : {}),
    },
  }
}

/**
 * The question that asks one signature: the renderer's string for `mode`
 * placed as-is, with the renderer's header and options. The plugin composes no
 * text of its own; it only chooses which of Gaia's two strings is asked.
 */
export function signatureRequest(
  approval: PendingApproval,
  controlSessionID: string,
  mode: SignatureMode = "signature",
): SignatureRequest {
  const retry = boundRetry(approval)
  const surface = approval.surface
  return {
    sessionID: controlSessionID,
    approvalID: approval.approvalID,
    correlationID: retry.correlationID,
    mode,
    metadata: { ...surface.metadata },
    question: {
      header: surface.header,
      question: mode === "details" ? surface.details : surface.question,
      options: surface.options.map((option) => ({ ...option })),
      multiple: false,
      custom: false,
    },
  }
}

/**
 * Whether the host's copy of the question is the signature question Gaia
 * wrote into the call, compared by the fields that carry consent: header,
 * question text, the ordered option labels and descriptions, single choice,
 * and no custom answer.
 *
 * Compared structurally, never as serialized bytes: the host re-encodes the
 * question (QuestionInfo declares question, header, options, multiple, custom
 * in that order) and may add keys of its own (`tool`). A byte comparison
 * closed the control on every such re-encoding, silently.
 */
export function matchesSignatureQuestion(
  questions: unknown,
  expected: SignatureRequest["question"],
): boolean {
  if (!Array.isArray(questions) || questions.length !== 1) return false
  const question = questions[0] as Record<string, unknown> | null
  if (!question || typeof question !== "object") return false
  if (question.header !== expected.header || question.question !== expected.question) return false
  if (question.multiple !== expected.multiple || question.custom !== expected.custom) return false
  const options = question.options
  if (!Array.isArray(options) || options.length !== expected.options.length) return false
  return expected.options.every((option, index) => {
    const candidate = options[index] as Record<string, unknown> | null
    return Boolean(candidate) && typeof candidate === "object"
      && candidate.label === option.label
      && candidate.description === option.description
  })
}

/** Match OpenCode's event copy of the question Gaia wrote into the validated call. */
export function matchesHostSignatureQuestionEvent(
  questions: unknown,
  expected: SignatureRequest["question"],
  questionCallWritten: boolean,
): boolean {
  if (!questionCallWritten || !Array.isArray(questions) || questions.length !== 1) return false
  const question = questions[0] as Record<string, unknown> | null
  if (!question || typeof question !== "object" || question.multiple !== false) return false
  if (question.custom !== undefined && question.custom !== false) return false
  return matchesSignatureQuestion([{ ...question, custom: false }], expected)
}

const SIGNATURE_ANSWERS: SignatureAnswer[] = ["once", "reject", "details"]

/**
 * Map the one selected label to the option it names, by position: Approve,
 * Reject, Details. Anything else -- no answer, several, or text typed in the
 * host's free-text row -- decides nothing.
 */
export function readSignatureAnswer(
  request: SignatureRequest,
  answers: unknown,
): SignatureAnswer | undefined {
  if (!Array.isArray(answers) || answers.length !== 1 || !Array.isArray(answers[0])) {
    return undefined
  }
  if (answers[0].length !== 1) return undefined
  const index = request.question.options.findIndex((option) => option.label === answers[0][0])
  return index === -1 ? undefined : SIGNATURE_ANSWERS[index]
}

function isOption(value: unknown): value is { label: string; description: string } {
  const option = value as Record<string, unknown> | null
  return Boolean(option) && typeof option?.label === "string" && Boolean(option.label)
    && typeof option?.description === "string"
}

/**
 * Read the signature Gaia rendered for one presented approval.
 *
 * The strings and the metadata are both Gaia's, never this edge's: a question
 * this plugin composed would be a description of a consent request written by
 * the party asking for it. A response missing any part is refused rather than
 * asked, because the alternative is a question whose content the user cannot
 * trust.
 */
export function readConsentPresentation(stdout: string): SignatureSurface {
  let emitted: any
  try {
    emitted = JSON.parse(stdout.trim().split("\n").pop() ?? "")
  } catch {
    throw new Error("Gaia did not emit a consent presentation to render")
  }
  const signature = emitted?.signature
  const metadata = emitted?.metadata
  const options = signature?.options
  if (
    typeof signature?.question !== "string" || !signature.question
    || typeof signature?.details !== "string" || !signature.details
    || typeof signature?.header !== "string" || !signature.header
    || !Array.isArray(options) || options.length !== SIGNATURE_ANSWERS.length || !options.every(isOption)
    || !metadata || typeof metadata !== "object"
  ) {
    throw new Error(
      emitted?.presentation_error
        ? `Gaia could not render this signature: ${emitted.presentation_error}`
        : "Gaia returned no complete signature for this approval",
    )
  }
  return {
    question: signature.question,
    details: signature.details,
    header: signature.header,
    options: options.map((option: { label: string; description: string }) => ({
      label: option.label, description: option.description,
    })),
    metadata,
  }
}

function approvalID(response: BridgeResponse): string | undefined {
  return response.approval_id || undefined
}

const APPROVAL_REQUEST_COMMAND = /^\s*(?:\S*\/)?gaia\s+approvals\s+(?:request-set|request-file-write)(?:\s|$)/
const CANONICAL_APPROVAL_ID = /\bP-[0-9a-f]{32}\b/g

/**
 * The approval a successful `gaia approvals request-set` or `request-file-write`
 * call printed, or undefined for any other command, or output naming no single id.
 *
 * Only a hint of which approval to present: `gaia approvals opencode-present`
 * still refuses one this session and agent did not request.
 */
export function requestedApprovalID(command: unknown, output: unknown): string | undefined {
  if (typeof command !== "string" || !APPROVAL_REQUEST_COMMAND.test(command)) return undefined
  if (typeof output !== "string") return undefined
  const ids = new Set(output.match(CANONICAL_APPROVAL_ID) ?? [])
  return ids.size === 1 ? [...ids][0] : undefined
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
 * by field, never by whole-object reassignment. OpenCode 1.18.18 hands this
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
  const controlsBySession = new Map<string, ControlDecision[]>()
  const controlByQuestion = new Map<string, ControlDecision>()
  const releasedControlCalls = new Set<string>()
  const controlsAwaitingIdle = new Set<string>()
  const deferredRetryBySession = new Map<string, DeferredActivation>()
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
  const workspaceDirectory = gaiaDirectory(input)
  const send: (event: Record<string, unknown>) => Promise<BridgeResponse> =
    typeof input?.gaiaBridge === "function"
      ? input.gaiaBridge
      : (event) => bridge(event, workspaceDirectory)
  const gaia = (args: string[]) => gaiaCapture(args, workspaceDirectory)
  // A claim the host process was granted, never one this edge composed: the
  // plugin receives caller-supplied names and cannot be the issuer of the
  // authority they would otherwise assert.
  const attestationBySession = new Map<string, string>()
  // The sessions this run's parentless claim may be issued to. The first
  // session seen is one: no child can exist before a primary has taken a turn.
  // Every later session inherits a grant instead, unless the host's own record
  // shows it has no parent -- a root the user opened after the first one, which
  // the serve process hosts for its whole life (measured 2026-09-17: every root
  // after the first was refused the control plane until the host restarted).
  const primarySessions = new Set<string>()
  // The host's last answer about a later session's parent, read back by the
  // refusal trace so an unadmitted session says why.
  const hostParentBySession = new Map<string, HostParentRecord>()
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
   * asks. A session other than a primary one exists only because a dispatch
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
    return primarySessions.has(sessionID)
  }

  function dispatchHandle(sessionID: string): string | undefined {
    // Both arms fail closed on an unknown primary: no handle is issued, so an
    // unidentifiable session is never handed the unrestricted subagent lane.
    if (primarySessions.size === 0 || isPrimarySession(sessionID)) return undefined
    return dispatchBySession.get(sessionID) ?? sessionID
  }

  /** The primary session an approval's specialist answers to, or undefined when none is known. */
  function primaryFor(sessionID: string): string | undefined {
    if (isPrimarySession(sessionID)) return sessionID
    const parent = childBindings.get(sessionID)?.parentSessionID
    return parent !== undefined && isPrimarySession(parent) ? parent : undefined
  }

  /** The host's own record of the session's parent; the host, not this edge, says what is a root. */
  async function hostParentRecord(sessionID: string): Promise<HostParentRecord> {
    try {
      const fetched = await input.client?.session?.get?.({ path: { id: sessionID } })
      const record = fetched?.data as { id?: unknown; parentID?: unknown } | undefined
      if (hostRejection(fetched) || record?.id !== sessionID) return "unavailable"
      return typeof record.parentID === "string" && record.parentID ? "present" : "none"
    } catch {
      return "unavailable"
    }
  }

  /** Admit the session to the parentless lane when it is the first seen or the host records no parent.
   *
   * Only the host's record can vouch for a later session: a child named by
   * message.updated before its Task binding lands looks exactly like a fresh
   * root from this edge. A host that cannot answer admits nothing, and is
   * asked again on the next sighting; a recorded parent is final.
   */
  async function adoptPrimary(sessionID: string): Promise<void> {
    if (primarySessions.has(sessionID) || childBindings.has(sessionID) || provisionalBindings.has(sessionID)) return
    if (primarySessions.size === 0) {
      primarySessions.add(sessionID)
      return
    }
    if (hostParentBySession.get(sessionID) === "present") return
    const parent = await hostParentRecord(sessionID)
    hostParentBySession.set(sessionID, parent)
    if (parent === "none") primarySessions.add(sessionID)
  }

  /** Why a named session presents no claim, for the refusal trace; a diagnosis, never an authority. */
  function identityGap(sessionID: string): string | undefined {
    if (!agentBySession.has(sessionID) || roleContext(sessionID)) return undefined
    if (isPrimarySession(sessionID)) return "primary session was refused issuance"
    if (childBindings.has(sessionID) || provisionalBindings.has(sessionID)) return "bound child was refused issuance"
    switch (hostParentBySession.get(sessionID)) {
      case "present": return "host records a parent and no dispatch bound the session"
      case "none": return "host records no parent but issuance did not complete"
      default: return "host session record unavailable, later session not admitted as primary"
    }
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
    // OpenCode 1.18.18 passes tool.execute.before exactly {tool, sessionID,
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
    }
    await adoptPrimary(sessionID)
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

  /** Hand the user's reply to Gaia; a refusal is logged and traced with Gaia's cause. */
  async function decide(
    approval: PendingApproval,
    reply: DecisionReply,
    lane: DecisionLane = "preferred",
  ): Promise<{ ok: true; retry?: BoundRetry } | { ok: false; cause: string }> {
    const decided = await gaia([
      "approvals", "opencode-decide", approval.approvalID,
      "--session-id", approval.sessionID,
      "--call-id", approval.callID,
      "--token", approval.token,
      "--reply", reply,
      "--decision-lane", lane === "compatibility" ? "compatibility" : "preferred",
      "--json",
    ])
    if (decided.ok) {
      const lastLine = decided.stdout.trim().split("\n").pop() ?? ""
      try {
        const emitted = JSON.parse(lastLine)
        if (
          emitted.approval_id !== approval.approvalID
          || emitted.correlation_id !== boundRetry(approval).correlationID
        ) throw new Error("decision output drifted from the presented approval")
        if (reply === "once") {
          return {
            ok: true,
            retry: boundRetryFromActivation(
              approval,
              emitted.correlation_id,
              emitted.retry_descriptor,
            ),
          }
        }
        return { ok: true }
      } catch (error) {
        const cause = error instanceof Error ? error.message : String(error)
        console.error(`[gaia-opencode:control] decision '${reply}' for ${approval.approvalID} returned unusable activation: ${cause}`)
        return { ok: false, cause }
      }
    }
    const cause = gaiaFailureCause(decided)
    console.error(`[gaia-opencode:control] decision '${reply}' for ${approval.approvalID} was refused by Gaia: ${cause}`)
    await reportUncorrelatedDenial(approval.sessionID, approval.callID, {
      approvalID: approval.approvalID, cause, stage: "decide",
    })
    return { ok: false, cause }
  }

  /** Release a control and leave the reason where a reader can find it.
   *
   * Every exit is logged AND traced through the bridge: a control that closes
   * without a trace is indistinguishable from one the host never answered,
   * which is how a user's answer came to vanish with nothing to query
   * (measured 2026-09-16). A control already closed is left alone, so the
   * clear that follows a decision does not record a second exit.
   */
  async function closeControl(
    control: ControlDecision,
    reason: ControlCloseReason,
    detail?: string,
  ): Promise<void> {
    if (control.closed) return
    control.closed = true
    if (control.questionID && controlByQuestion.get(control.questionID) === control) {
      controlByQuestion.delete(control.questionID)
    }
    const { approvalID, sessionID, callID } = control.approval
    const controlSessionID = control.request.sessionID
    console.error(
      `[gaia-opencode:control] closed control for ${approvalID} in ${controlSessionID}: ${reason}`
      + (detail ? ` -- ${detail}` : ""),
    )
    try {
      await send({
        event: CONTROL_CLOSED_EVENT,
        sessionID,
        callID,
        approvalID,
        controlSessionID,
        reason,
        ...(detail ? { detail } : {}),
      })
    } catch (error) {
      console.error(`[gaia-opencode:control] closed control ${controlSessionID} went unaudited: ${error}`)
    }
  }

  function activeControl(sessionID: string): ControlDecision | undefined {
    return controlsBySession.get(sessionID)?.[0]
  }

  function owningControl(sessionID: string): ControlDecision | undefined {
    const control = activeControl(sessionID)
    return control && (control.presenting || control.presented) ? control : undefined
  }

  async function correlateQuestionEvent(
    control: ControlDecision,
    requestID: string,
    questions: unknown,
  ): Promise<void> {
    if (control.questionCallID === undefined) {
      await clearControl(control, "question_mismatch", `question ${requestID} preceded the validated Gaia call`)
      return
    }
    if (control.questionID || controlByQuestion.has(requestID)) {
      await clearControl(control, "question_mismatch", `second question ${requestID} for one control`)
      return
    }
    if (!matchesHostSignatureQuestionEvent(questions, control.request.question, control.questionCallID !== undefined)) {
      await clearControl(control, "question_mismatch", `host asked ${JSON.stringify(questions)}`)
      return
    }
    control.questionID = requestID
    controlByQuestion.set(requestID, control)
  }

  async function presentNextControl(sessionID: string, propagateError = false): Promise<void> {
    if (
      retryBySession.has(sessionID)
      || deferredRetryBySession.has(sessionID)
      || controlsAwaitingIdle.has(sessionID)
    ) return
    const control = activeControl(sessionID)
    if (!control || control.closed || control.awaitingSafeIdle || control.presenting || control.presented) return
    try {
      await presentControl(control)
    } catch (error) {
      if (propagateError) throw error
      console.error(`[gaia-opencode:control] queued control for ${control.approval.approvalID} failed: ${error}`)
    }
  }

  async function releaseControl(
    control: ControlDecision,
    reason: ControlCloseReason,
    detail?: string,
    advance = true,
  ): Promise<void> {
    await closeControl(control, reason, detail)
    const sessionID = control.request.sessionID
    const controls = controlsBySession.get(sessionID)
    const wasActive = controls?.[0] === control
    if (controls) {
      const index = controls.indexOf(control)
      if (index !== -1) controls.splice(index, 1)
      if (controls.length === 0) controlsBySession.delete(sessionID)
    }
    if (control.questionCallID) releasedControlCalls.add(`${sessionID}:${control.questionCallID}`)
    if (advance && wasActive) {
      if (control.presented) controlsAwaitingIdle.add(sessionID)
      else await presentNextControl(sessionID)
    }
  }

  async function clearControl(
    control: ControlDecision,
    reason: ControlCloseReason,
    detail?: string,
    advance = true,
  ): Promise<void> {
    await releaseControl(control, reason, detail, advance)
  }

  /** Ask the same signature again, next in its session, carrying the renderer's Details.
   *
   * Details decides nothing: the approval, its presentation token and its
   * bound retry carry over unchanged, so the answer to the re-asked question
   * reaches Gaia exactly as an answer to the first one would have.
   */
  async function reaskWithDetails(control: ControlDecision): Promise<void> {
    const sessionID = control.request.sessionID
    const details: ControlDecision = {
      approval: control.approval,
      request: signatureRequest(control.approval, sessionID, "details"),
      retry: control.retry,
      awaitingSafeIdle: false,
      presenting: false,
      presented: false,
      closed: false,
    }
    const controls = controlsBySession.get(sessionID) ?? []
    const position = controls.indexOf(control)
    controls.splice(position === -1 ? 0 : position + 1, 0, details)
    controlsBySession.set(sessionID, controls)
    await clearControl(control, "details_requested")
  }

  async function applyDecision(
    control: ControlDecision,
    reply: DecisionReply,
    lane: DecisionLane,
  ): Promise<boolean> {
    const existing = retryBySession.get(control.approval.sessionID)
    if (reply === "once" && existing && existing.correlationID !== control.retry.correlationID) {
      await clearControl(control, "retry_conflict", `session already bound to ${existing.approvalID}`)
      return false
    }
    const admission = decisions.admit(control.request.correlationID, lane)
    if (!admission.accepted) {
      await clearControl(control, "decision_duplicate", `lane ${lane} after ${admission.lane}`)
      return false
    }
    const decided = await decide(control.approval, reply, lane)
    if (decided.ok && reply === "once") {
      if (!decided.retry) {
        await clearControl(control, "decide_failed", "activation returned no typed retry descriptor")
        return false
      }
      deferredRetryBySession.set(control.approval.sessionID, { control, lane, retry: decided.retry })
    }
    // Cleared on a refused decision too: the question is consumed and the
    // lane admitted, so nothing left here could carry a second reply to Gaia.
    // A control kept open would refuse every later call on its session and
    // hold the specialist's original call pending, while a fresh attempt
    // re-blocks on the same approval and presents a new question.
    if (decided.ok) {
      await clearControl(control, "decided", reply)
    } else {
      await clearControl(control, "decide_failed", decided.cause)
    }
    return decided.ok
  }

  /** Give the user's "yes" an actor: tell the orchestrator, and record that it was told.
   *
   * The grant is armed by then; neither the notice nor its trace can undo it,
   * so both failures are logged and the activation stands. The trace carries
   * which session was notified (or none), because a grant nobody resumes is
   * exactly the state that used to look like a command never attempted.
   */
  async function announceActivation(
    control: ControlDecision,
    lane: DecisionLane,
    retry: BoundRetry,
  ): Promise<void> {
    const { approvalID, sessionID, callID } = control.approval
    if (!retry.operation) throw new Error("Gaia cannot announce activation without a typed retry descriptor")
    let notifiedSessionID: string | undefined
    let notifyFailure: string | undefined
    const session = input?.client?.session
    const primarySessionID = primaryFor(sessionID)
    if (typeof session?.promptAsync !== "function" || !primarySessionID) {
      notifyFailure = "no primary session or host prompt API to notify"
    } else {
      try {
        const prompted = await session.promptAsync({
          path: { id: primarySessionID },
          body: { parts: [{ type: "text", text: activationNotice(approvalID, sessionID, retry.operation) }] },
        })
        notifyFailure = hostRejection(prompted)
        if (!notifyFailure) notifiedSessionID = primarySessionID
      } catch (error) {
        notifyFailure = error instanceof Error ? error.message : String(error)
      }
    }
    if (notifyFailure) {
      console.error(`[gaia-opencode:control] activation of ${approvalID} was not announced to the orchestrator: ${notifyFailure}`)
    }
    try {
      await send({
        event: DECISION_APPLIED_EVENT,
        sessionID,
        callID,
        approvalID,
        controlSessionID: control.request.sessionID,
        reply: "once",
        lane,
        nextIndex: retry.expectedIndex,
        ...(notifiedSessionID ? { notifiedSessionID } : {}),
        ...(notifyFailure ? { notifyFailure } : {}),
      })
    } catch (error) {
      console.error(`[gaia-opencode:control] activation of ${approvalID} went unaudited: ${error}`)
    }
  }

  /** The host's account of an SDK call that resolved with an error instead of throwing.
   *
   * The generated client returns `{ error, response }` for a rejected request
   * unless it was built with throwOnError, which OpenCode's plugin client is
   * not. Reading the outcome is therefore the only way to learn the host
   * refused; an awaited call that "succeeded" proves nothing on its own.
   */
  function hostRejection(result: unknown): string | undefined {
    const outcome = result as { error?: unknown; response?: { ok?: boolean; status?: number } } | undefined
    if (outcome?.error === undefined && outcome?.response?.ok !== false) return undefined
    const status = outcome?.response?.status
    const error = outcome?.error
    const detail = typeof error === "string" ? error : error === undefined ? "" : JSON.stringify(error)
    return [status === undefined ? "" : `HTTP ${status}`, detail].filter(Boolean).join(" ")
      || "host resolved with an error carrying no detail"
  }

  /** Release a control whose question never reached the host. */
  async function abandonControl(control: ControlDecision, cause: string): Promise<void> {
    await releaseControl(control, "prompt_rejected", cause)
  }

  async function presentControl(control: ControlDecision): Promise<void> {
    const approval = control.approval
    const session = input?.client?.session
    if (typeof session?.promptAsync !== "function") {
      throw new Error("OpenCode control plane cannot open a signature question")
    }
    const failClosed = async (cause: string): Promise<Error> => {
      await reportUncorrelatedDenial(approval.sessionID, approval.callID, {
        approvalID: approval.approvalID, cause, stage: "control-plane",
      })
      return new Error(`Gaia could not open the consent control plane for ${approval.approvalID}: ${cause}`)
    }
    const controlSessionID = control.request.sessionID
    control.presenting = true
    // The model only opens the question: OpenCode offers a plugin no route to
    // open one itself (1.18.32 exposes list/reply/reject), so a model's
    // question-tool call is the opener, and tool.execute.before replaces its
    // arguments with Gaia's signature before the host asks anything.
    const instruction = [
      "You are a mechanical consent control plane.",
      "Invoke the question tool exactly once with the placeholder JSON below and do nothing else.",
      "Gaia fills the question in before the user sees it. Do not answer it, infer consent, or emit approval prose.",
      JSON.stringify({ questions: [CONTROL_PLACEHOLDER_QUESTION] }),
    ].join("\n")
    let prompted: unknown
    try {
      prompted = await session.promptAsync({
        path: { id: controlSessionID },
        body: {
          system: "Only the question tool is available. Free text has no decision authority.",
          parts: [{ type: "text", text: instruction }],
        },
      })
    } catch (error) {
      const cause = `control-plane prompt rejected: ${error instanceof Error ? error.message : String(error)}`
      control.presenting = false
      await abandonControl(control, cause)
      throw await failClosed(cause)
    }
    const promptRejected = hostRejection(prompted)
    if (promptRejected) {
      const cause = `control-plane prompt rejected: ${promptRejected}`
      control.presenting = false
      await abandonControl(control, cause)
      throw await failClosed(cause)
    }
    control.presenting = false
    if (control.closed) {
      throw new Error(`Gaia consent control ${approval.approvalID} closed before promptAsync resolved`)
    }
    control.presented = true
    await reportControlOpened(approval, controlSessionID)
  }

  async function openSignatureQuestion(approval: PendingApproval): Promise<ControlDecision> {
    const primarySessionID = primaryFor(approval.sessionID)
    if (!primarySessionID || approval.sessionID === primarySessionID) {
      throw new Error("OpenCode control plane requires an approval session bound to an active primary")
    }
    const controlSessionID = approval.sessionID
    const control: ControlDecision = {
      approval,
      request: signatureRequest(approval, controlSessionID),
      retry: boundRetry(approval),
      awaitingSafeIdle: false,
      presenting: false,
      presented: false,
      closed: false,
    }
    const controls = controlsBySession.get(controlSessionID) ?? []
    control.awaitingSafeIdle = controls.length === 0
      && !controlsAwaitingIdle.has(controlSessionID)
      && !deferredRetryBySession.has(controlSessionID)
      && !retryBySession.has(controlSessionID)
    controls.push(control)
    controlsBySession.set(controlSessionID, controls)
    return control
  }

  /** Trace that the host accepted the consent question for this approval.
   *
   * `gaia approvals opencode-present` writes SHOWN before the prompt is
   * attempted, so SHOWN alone only says the presentation was registered. This
   * record is what says the question reached the host, and in which session.
   * The opened control never depends on this call succeeding.
   */
  async function reportControlOpened(approval: PendingApproval, controlSessionID: string) {
    try {
      await send({
        event: CONTROL_OPENED_EVENT,
        sessionID: approval.sessionID,
        callID: approval.callID,
        approvalID: approval.approvalID,
        controlSessionID,
      })
    } catch (error) {
      console.error(`[gaia-opencode:control] opened control ${controlSessionID} went unaudited: ${error}`)
    }
  }

  /** Trace a refused claim to the bound retry, with the comparison that refused it.
   *
   * The specialist receives the same cause in the thrown error; this record is
   * what lets the orchestrator read it once that turn has ended. The refusal
   * never depends on this call succeeding.
   */
  async function reportConsentRetryRefused(
    retry: BoundRetry,
    call: { sessionID?: string; callID?: string },
    refusal: ConsentRetryRefusal,
  ) {
    try {
      await send({
        event: CONSENT_RETRY_REFUSED_EVENT,
        sessionID: call.sessionID,
        callID: call.callID,
        approvalID: retry.approvalID,
        reason: refusal.reason,
        expected: refusal.expected,
        received: refusal.received,
      })
    } catch (error) {
      console.error(`[gaia-opencode:consent] refused retry of ${retry.approvalID} went unaudited: ${error}`)
    }
  }

  async function requestApproval(id: string, sessionID: string, callID: string, role: string) {
    const approval = { approvalID: id, sessionID, callID, role, token: crypto.randomUUID() }
    const presented = await gaia([
      "approvals", "opencode-present", id,
      "--session-id", sessionID,
      "--agent-id", role,
      "--call-id", callID,
      "--token", approval.token,
      "--json",
    ])
    if (!presented.ok) {
      const cause = gaiaFailureCause(presented)
      await reportUncorrelatedDenial(sessionID, callID, { approvalID: id, cause })
      throw new Error(`Gaia could not present approval ${id}: ${cause}`)
    }
    const surface = readConsentPresentation(presented.stdout)
    await openSignatureQuestion({ ...approval, surface })
  }

  /** Queue Gaia's question for an approval the specialist just requested, with no attempt needed.
   *
   * The question opens in the specialist's session once its turn ends, exactly
   * as after a blocked attempt, so no model has to remember to attempt the
   * sealed command for the user to be asked. A request made from the primary
   * session, or one Gaia refuses to present, leaves the request as it is: the
   * cause is traced and told to the requester in its tool result.
   */
  async function presentRequested(
    id: string,
    sessionID: string,
    callID: string,
    output: { output?: unknown },
  ): Promise<void> {
    let notice: string
    try {
      await requestApproval(id, sessionID, callID, (await identify(sessionID)) ?? "")
      notice = `Gaia asks ${id} as a question in this session when your turn ends: close APPROVAL_REQUEST with it and do not ask it yourself.`
    } catch (error) {
      const cause = error instanceof Error ? error.message : String(error)
      console.error(`[gaia-opencode:control] requested ${id} was not presented: ${cause}`)
      notice = `Gaia could not queue the question for ${id}: ${cause}`
    }
    if (typeof output.output === "string") output.output = `${output.output.trimEnd()}\n${notice}\n`
  }

  /** Leave a durable trace of a denial that otherwise only reached stderr.
   *
   * A request denied here and a request never made are indistinguishable to the
   * user: that is how a signed approval came to look like a command nobody ever
   * attempted. The bridge routes this onto Gaia's existing non-activation audit
   * channel, so the denial becomes queryable without a new record shape. The
   * denial itself never depends on this call succeeding.
   *
   * A presentation Gaia refused travels on the same channel with the approval
   * it named and the cause Gaia returned, so the bridge records it under its
   * own reason instead of as an uncorrelated request. A control plane the HOST
   * refused after the presentation carries `stage: "control-plane"`, and a
   * reply Gaia refused after the user gave it carries `stage: "decide"`, so
   * the bridge can tell the three refusals apart.
   */
  async function reportUncorrelatedDenial(
    sessionID: unknown,
    callID: unknown,
    failure?: { approvalID: string; cause: string; stage?: "control-plane" | "decide" },
  ) {
    try {
      await send({
        event: UNCORRELATED_PERMISSION_EVENT,
        sessionID: typeof sessionID === "string" ? sessionID : "",
        callID: typeof callID === "string" ? callID : "",
        ...(failure ? { approvalID: failure.approvalID, cause: failure.cause } : {}),
        ...(failure?.stage ? { stage: failure.stage } : {}),
      })
    } catch (error) {
      console.error(`[gaia-opencode:permission] uncorrelated denial went unaudited: ${error}`)
    }
  }

  return {
    dispose: async () => {
      shellIdentities.clear()
      controlByQuestion.clear()
      controlsBySession.clear()
      releasedControlCalls.clear()
      controlsAwaitingIdle.clear()
      deferredRetryBySession.clear()
      retryByCall.clear()
      retryBySession.clear()
    },
    event: async ({ event }) => {
      if (event.type === "question.asked") {
        const sessionID = event.properties?.sessionID
        const requestID = event.properties?.id
        const control = typeof sessionID === "string" ? owningControl(sessionID) : undefined
        if (!control || control.closed || typeof requestID !== "string" || !requestID) return
        await correlateQuestionEvent(control, requestID, event.properties?.questions)
        return
      }
      if (event.type === "question.rejected") {
        const requestID = event.properties?.requestID ?? event.properties?.id
        const control = typeof requestID === "string" ? controlByQuestion.get(requestID) : undefined
        if (control) await clearControl(control, "question_rejected")
        return
      }
      if (event.type === "question.replied") {
        const requestID = event.properties?.requestID
        const sessionID = event.properties?.sessionID
        const control = typeof requestID === "string" ? controlByQuestion.get(requestID) : undefined
        if (!control || control.closed || control.request.sessionID !== sessionID) return
        const reply = readSignatureAnswer(control.request, event.properties?.answers)
        if (!reply) {
          await clearControl(control, "reply_unreadable", JSON.stringify(event.properties?.answers))
          return
        }
        if (reply === "details") {
          await reaskWithDetails(control)
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
          await adoptPrimary(info.sessionID)
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
        // The host's own gate (ruleset deny, rejected prompt) throws inside
        // tool.execute, and OpenCode fires tool.execute.after only on a result;
        // this errored part is the only signal that a retry Gaia allowed never
        // ran (measured 2026-09-17: cp /dev/null -> external_directory deny,
        // reservation held, no trace). The reservation is left to its TTL:
        // a host refusal is neither a failed run nor withdrawn consent.
        if (
          part?.type === "tool"
          && part.state?.status === "error"
          && typeof part.sessionID === "string"
          && typeof part.callID === "string"
        ) {
          const key = `${part.sessionID}:${part.callID}`
          const retried = retryByCall.get(key)
          if (retried) {
            retryByCall.delete(key)
            if (retryBySession.get(part.sessionID) === retried) {
              retryBySession.delete(part.sessionID)
              await presentNextControl(part.sessionID)
            }
            await reportConsentRetryRefused(retried, { sessionID: part.sessionID, callID: part.callID }, {
              reason: "host_gate_refused",
              expected: `host execution of allowed command [${retried.expectedIndex}]`,
              received: String(part.state.error ?? "tool part errored without a message"),
            })
          }
        }
        return
      }
      if (LIFECYCLE_EVENT_TYPES.has(event.type)) {
        const sessionID = event.properties?.sessionID
        if (typeof sessionID === "string") {
          const controlWaitingForIdle = activeControl(sessionID)
          if (event.type === "session.idle" && controlWaitingForIdle?.awaitingSafeIdle) {
            controlWaitingForIdle.awaitingSafeIdle = false
            await presentNextControl(sessionID, true)
            shellIdentities.clearSession(sessionID)
            await send({ event: event.type, sessionID })
            return
          }
          if (event.type === "session.idle" && controlsAwaitingIdle.delete(sessionID)) {
            const activation = deferredRetryBySession.get(sessionID)
            if (activation) {
              deferredRetryBySession.delete(sessionID)
              retryBySession.set(sessionID, {
                ...activation.retry,
                usedCallIDs: new Set(activation.retry.usedCallIDs),
              })
              await announceActivation(activation.control, activation.lane, activation.retry)
            } else {
              await presentNextControl(sessionID)
            }
            shellIdentities.clearSession(sessionID)
            await send({ event: event.type, sessionID })
            return
          }
          if (event.type === "session.idle") {
            const control = activeControl(sessionID)
            if (control) {
              await clearControl(control, "session_ended", event.type, false)
              await presentNextControl(sessionID)
            }
            shellIdentities.clearSession(sessionID)
            await send({ event: event.type, sessionID })
            return
          }
          const controls = [...(controlsBySession.get(sessionID) ?? [])]
          if (controls.length > 0) {
            for (const control of controls) await clearControl(control, "session_ended", event.type, false)
            controlsAwaitingIdle.delete(sessionID)
            deferredRetryBySession.delete(sessionID)
            return
          }
          shellIdentities.clearSession(sessionID)
          await send({ event: event.type, sessionID })
        }
        return
      }
    },
    // No "permission.ask" hook: the installed OpenCode (1.18.32) never
    // triggers one -- its bundle calls no plugin hook named permission, so a
    // host permission prompt is the host's alone and a signature is asked
    // only through the question tool above.
    "tool.execute.before": async (call, output) => {
      const control = owningControl(call.sessionID)
      if (control) {
        if (control.closed) {
          throw new Error("Gaia consent control plane is already closed")
        }
        const tool = canonicalBridgeToolName(call.tool)
        if (tool !== "AskUserQuestion" || control.questionCallID !== undefined) {
          await clearControl(control, "drifted_tool_call", `${tool} ${call.callID}`)
          throw new Error("Gaia consent control plane permits one signature question")
        }
        // Whatever the model passed is replaced: the user is asked Gaia's
        // signature, never a copy a model typed.
        applyUpdatedInput(output, { questions: [structuredClone(control.request.question)] })
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
      const verdict = retry ? evaluateConsentRetry(retry, call, agent, normalized.tool, normalized.args) : undefined
      if (retry && verdict?.refusal) {
        await reportConsentRetryRefused(retry, call, verdict.refusal)
        if (retryBySession.get(call.sessionID) === retry) retryBySession.delete(call.sessionID)
        await presentNextControl(call.sessionID)
        const { reason, expected, received } = verdict.refusal
        throw new Error(
          `Gaia refused consent retry for ${retry.approvalID}: ${reason} (expected ${expected}, received ${received})`,
        )
      }
      const retryProof = verdict?.proof
      const response = await send({
        event: "tool.execute.before",
        sessionID: call.sessionID,
        callID: call.callID,
        agentID: dispatchHandle(call.sessionID),
        agent,
        roleContext: roleContext(call.sessionID),
        identityGap: identityGap(call.sessionID),
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
      if (retry && retryProof && retryBySession.get(call.sessionID) === retry) {
        retryBySession.delete(call.sessionID)
        await presentNextControl(call.sessionID)
      }
      const blockedApprovalID = approvalID(response)
      if (blockedApprovalID) {
        await requestApproval(blockedApprovalID, call.sessionID, call.callID, agent ?? "")
        throw new Error(response.reason ?? "Gaia requires approval before retrying this tool call")
      }
      throw new Error(response.reason ?? "Gaia denied this tool call without a persisted approval")
    },
    "tool.execute.after": async (call, output) => {
      if (releasedControlCalls.delete(`${call.sessionID}:${call.callID}`)) return
      const control = owningControl(call.sessionID)
      if (control) {
        const tool = canonicalBridgeToolName(call.tool)
        if (tool === "AskUserQuestion" && control.questionCallID === call.callID) return
        await clearControl(control, "drifted_tool_result", `${tool} ${call.callID}`)
        throw new Error("Gaia refused a drifted control-plane tool result")
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
      const requested = normalizedToken(call.tool) === "bash" && result.exit_code === 0
        ? requestedApprovalID(call.args?.command, result.output)
        : undefined
      if (requested) await presentRequested(requested, call.sessionID, call.callID, output)
      const retryKey = `${call.sessionID}:${call.callID}`
      const retried = retryByCall.get(retryKey)
      if (retried) {
        retryByCall.delete(retryKey)
        if (result.exit_code === 0) {
          retried.expectedIndex += 1
          if (retried.operation?.kind === "COMMAND_SET") {
            retried.operation.expectedIndex = retried.expectedIndex
          }
          if (retried.expectedIndex >= retried.commands.length) {
            if (retryBySession.get(call.sessionID) === retried) {
              retryBySession.delete(call.sessionID)
              await presentNextControl(call.sessionID)
            }
          }
        } else if (retryBySession.get(call.sessionID) === retried) {
          retryBySession.delete(call.sessionID)
          await presentNextControl(call.sessionID)
        }
      }
    },
    "shell.env": async (call, output) => {
      if (!call.sessionID) throw new Error("Gaia shell environment lacks session identity")
      // The `gaia` the kernel names is the one shipped beside this plugin, and
      // OpenCode's bash inherits the serve process's PATH, which need not
      // carry it (measured: a specialist fell back to ./bin/gaia).
      output.env.PATH = [gaiaBinDirectory, output.env.PATH ?? process.env.PATH]
        .filter(Boolean).join(delimiter)
      // `gaia approvals request-set` seals the requesting session from here,
      // under a host-neutral name read ahead of Claude Code's variables.
      output.env.GAIA_HOST_SESSION_ID = call.sessionID
      const context = roleContext(call.sessionID)
      if (!call.callID && isPrimarySession(call.sessionID)
        && context?.attestation && context.role === "gaia-orchestrator") return
      if (!call.callID) throw new Error("Gaia dispatched shell environment lacks call identity")
      output.env.GAIA_DISPATCH_AGENT = shellIdentities.take({
        sessionID: call.sessionID, callID: call.callID, cwd: resolve(call.cwd),
        agent: agentBySession.get(call.sessionID),
        attestation: context?.attestation,
      })
      // Tool temporaries (pytest's tmp_path above all) otherwise fill the
      // host's /tmp, a small tmpfs where this runs.
      const tmpdir = dispatchTmpDir(call.sessionID)
      if (tmpdir) output.env.TMPDIR = tmpdir
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
