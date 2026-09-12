/**
 * Drives the real GaiaOpenCodePlugin through a consent retry and reports every
 * boundary it crossed.
 *
 * The claim this driver exists to support is about a SEQUENCE of tool calls
 * sharing one identity, so nothing in the sequence may be hand-written: the
 * plugin closure runs, its own `bridge()` is reached through a recorder that
 * forwards verbatim to the real `opencode/bridge.py`, and `requestApproval`
 * executes the real `gaia approvals opencode-present` / `opencode-decide`
 * CLIs against the database in GAIA_DB.
 *
 * Normal scenarios double two host seams because no
 * OpenCode host runs here: the host-created permission request and the host's
 * decision to invoke a tool at all. The second is
 * why a `before` step in this scenario proves what the PLUGIN does with an
 * invocation carrying a given session/call identity, and never that OpenCode
 * would deliver that invocation -- an invocation this driver issues is this
 * driver's, and the Python side states that limit rather than asserting past
 * it. Controlled-race scenarios additionally hold or fail a bridge request;
 * every successful request still uses the real bridge, ledger and database.
 *
 * Usage: bun consent_retry_driver.ts '<scenario json>'
 */

import { isAbsolute } from "node:path"
import { pathToFileURL } from "node:url"

const bridgePath = new URL("./isolated_bridge.py", import.meta.url).pathname

type Exchange = {
  sent: Record<string, unknown>
  received: unknown
  /** JSON.stringify of the args the plugin forwarded, for byte comparison. */
  sentArgsJSON: string
}

const exchanges: Exchange[] = []
const permissionAsks: Record<string, unknown>[] = []
const controlPrompts: Record<string, any>[] = []
const stepResults: Record<string, unknown>[] = []
let lastBridgeAction: string | undefined
let lastBridgeRequiresApproval = false

/** A manually released barrier, independent of bridge subprocess timing. */
function barrier() {
  let release!: () => void
  const promise = new Promise<void>((resolve) => { release = resolve })
  return { promise, release }
}

type HeldRequest = {
  sessionID: string; outcome: string; used: boolean
  entered: ReturnType<typeof barrier>; release: ReturnType<typeof barrier>
}
let heldStart: HeldRequest | undefined
let heldIssuance: HeldRequest | undefined
const startDelivered = new Map<string, ReturnType<typeof barrier>>()
const identityAttempts: string[] = []
const observations: Record<string, unknown>[] = []
let activeIssuers = 0
let maxActiveIssuers = 0

/** Hold exactly one selected request, optionally failing before any backend execution. */
async function hold(request: HeldRequest): Promise<boolean> {
  request.used = true
  request.entered.release()
  await request.release.promise
  if (request.outcome === "throw") throw new Error("driver injected bridge failure")
  return request.outcome !== "deny"
}

/** Instrument issuer overlap and start readiness without fabricating a successful claim. */
async function gaiaBridge(event: Record<string, unknown>) {
  if (event.event === "identity.attest") {
    identityAttempts.push(String(event.sessionID))
    activeIssuers++
    maxActiveIssuers = Math.max(maxActiveIssuers, activeIssuers)
    try {
      if (heldIssuance && !heldIssuance.used && event.sessionID === heldIssuance.sessionID
        && !(await hold(heldIssuance))) return { action: "deny" as const }
      return await policyBridge(event)
    } finally {
      activeIssuers--
    }
  }
  if (event.event === "message.part.updated") {
    const child = (event.state as any)?.metadata?.sessionId
    if (heldStart && !heldStart.used && child === heldStart.sessionID
      && !(await hold(heldStart))) return { action: "deny" as const }
    const response = await policyBridge(event)
    startDelivered.get(child)?.release()
    return response
  }
  return policyBridge(event)
}

async function policyBridge(event: Record<string, unknown>) {
  if (scenario.missingAttestation && event.event === "tool.execute.before" && event.tool === "bash") {
    event = { ...event, roleContext: undefined }
  }
  if (scenario.injectCarrierClaim) {
    event = { ...event, shell_env_transport: true, _dispatch_identity_in_env: true }
  }
  const child = Bun.spawn(["python3", "-B", bridgePath, ...(scenario.legacyBridge ? [] : ["--shell-env-v1"])], {
    cwd: process.env.WORKSPACE,
    env: { ...process.env, GAIA_HOST: "opencode" },
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
  })
  child.stdin.write(JSON.stringify(event))
  child.stdin.end()
  const [code, stdout, stderr] = await Promise.all([
    child.exited,
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
  ])
  if (code !== 0) {
    throw new Error(`Gaia policy bridge exited ${code}: ${stderr}`)
  }
  const received = JSON.parse(stdout.trim().split("\n").pop() ?? "null")
  exchanges.push({
    sent: event,
    received,
    sentArgsJSON: JSON.stringify(event.args ?? null),
  })
  lastBridgeAction = (received as any)?.action
  lastBridgeRequiresApproval = Boolean(
    (received as any)?.approval_id
    || String((received as any)?.reason ?? "").match(/approval_id:\s*P-[A-Za-z0-9-]+/),
  )
  return received
}

const scenario = JSON.parse(process.argv[2])
if (scenario.pluginModulePath !== undefined
  && (typeof scenario.pluginModulePath !== "string" || !isAbsolute(scenario.pluginModulePath))) {
  throw new Error("pluginModulePath must be an exact absolute module path")
}
const { GaiaOpenCodePlugin } = await import(scenario.pluginModulePath === undefined
  ? new URL("../../opencode/plugin.ts", import.meta.url).href
  : pathToFileURL(scenario.pluginModulePath).href)

const client = {
  session: {
    async messages({ sessionID }: { sessionID: string }) {
      return { data: scenario.messages?.[sessionID] ?? [] }
    },
    async create({ body }: any) {
      return { data: { id: `control-${controlPrompts.length + 1}`, title: body.title } }
    },
    async promptAsync(request: any) {
      controlPrompts.push(request)
    },
  },
}

const directory = process.env.WORKSPACE ?? process.cwd()
const plugin: any = await GaiaOpenCodePlugin({ gaiaBridge, client, directory })
const argsByCall = new Map<string, any>()

async function presentPermission(step: any) {
  const permission = {
    id: scenario.permissionID ?? "perm-1",
    sessionID: step.sessionID,
    callID: step.callID,
    title: "host permission",
    metadata: {},
  }
  const permissionOutput = { status: "ask" as const }
  await plugin["permission.ask"](permission, permissionOutput)
  permissionAsks.push({ permission, status: permissionOutput.status })
  return permissionOutput.status
}

/** Deliver one host event and record its observable result, including refusals. */
async function runStep(step: any): Promise<void> {
  const record: Record<string, unknown> = { kind: step.kind, label: step.label }
  try {
    if (step.kind === "before") {
      // One args object per step, built here so the Python side can compare the
      // bytes the plugin forwarded across two steps that declare the same input.
      const args = step.reuseArgs ? argsByCall.get(step.callID) : step.args ?? { command: step.command }
      argsByCall.set(step.callID, args)
      record.commandBefore = args.command
      await plugin["tool.execute.before"](
        { sessionID: step.sessionID, callID: step.callID, tool: step.tool ?? "bash" },
        { args },
      )
      record.commandAfter = args.command
      if (lastBridgeAction === "allow") {
        record.allowed = true
        stepResults.push(record)
        return
      }
      record.allowed = await presentPermission(step) === "allow"
    } else if (step.kind === "shell-env") {
      const output = { env: {} as Record<string, string> }
      await plugin["shell.env"]({ sessionID: step.sessionID, callID: step.callID, cwd: directory }, output)
      record.env = output.env
      // Never execute the signed publication text: only observe the delivered child environment.
      const child = Bun.spawn(["python3", "-B", "-c", "import os; print(os.environ.get('GAIA_DISPATCH_AGENT', '<unset>'))"], {
        env: { ...process.env, ...output.env }, stdout: "pipe", stderr: "pipe",
      })
      record.childIdentity = (await new Response(child.stdout).text()).trim()
      if (await child.exited !== 0) throw new Error("identity observation child failed")
      record.allowed = true
    } else if (step.kind === "after") {
      const args = argsByCall.get(step.callID) ?? step.args ?? { command: step.command }
      record.commandAfter = args.command
      await plugin["tool.execute.after"](
        {
          sessionID: step.sessionID,
          callID: step.callID,
          tool: step.tool ?? "bash",
          args,
        },
        { output: step.output ?? "", metadata: step.metadata ?? {} },
      )
      record.allowed = true
    } else if (step.kind === "compact") {
      const output = { context: [] as string[] }
      await plugin["experimental.session.compacting"]({ sessionID: step.sessionID }, output)
      record.context = output.context
      record.allowed = true
    } else if (step.kind === "lifecycle") {
      await plugin.event({ event: { type: step.eventType, properties: { sessionID: step.sessionID } } })
      record.allowed = true
    } else if (step.kind === "message") {
      await plugin.event({
        event: {
          type: "message.updated",
          properties: {
            info: { role: "assistant", sessionID: step.sessionID, agent: step.agent },
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "after-task") {
      await plugin["tool.execute.after"](
        { sessionID: step.sessionID, callID: step.callID, tool: "task", args: step.args ?? {} },
        { metadata: { sessionId: step.childSessionID }, output: "" },
      )
      record.allowed = true
    } else if (step.kind === "task-part") {
      // The real host's callID<->child-session binding signal (measured:
      // project_gaia_opencode_lifecycle_medido_2026_08_26), on the
      // DISPATCHING session's own part -- see lifecycle_transport_driver.ts
      // for the identical shape exercised against a recording stub.
      await plugin.event({
        event: {
          type: "message.part.updated",
          properties: {
            part: {
              type: "tool",
              tool: "task",
              sessionID: step.sessionID,
              callID: step.callID,
              state: { metadata: { sessionId: step.childSessionID } },
            },
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "replied") {
      await plugin.event({
        event: {
          type: step.eventType ?? "permission.replied",
          properties: {
            sessionID: step.sessionID ?? scenario.sessionID,
            permissionID: step.requestID,
            response: step.reply,
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "control-decision") {
      const prompt = controlPrompts.at(-1)
      const controlSessionID = prompt?.path?.id
      const instruction = prompt?.body?.parts?.[0]?.text
      const encoded = typeof instruction === "string" ? instruction.split("\n").at(-1) : undefined
      const questions = encoded ? JSON.parse(encoded).questions : undefined
      if (typeof controlSessionID !== "string" || !Array.isArray(questions)) {
        throw new Error("driver observed no structured Gaia control question")
      }
      const question = questions[0]
      const requestID = step.requestID ?? `question-${controlPrompts.length}`
      const callID = step.callID ?? `question-call-${controlPrompts.length}`
      await plugin["tool.execute.before"](
        { sessionID: controlSessionID, callID, tool: "question" },
        { args: { questions } },
      )
      await plugin.event({ event: {
        type: "question.asked",
        properties: { sessionID: controlSessionID, id: requestID, questions },
      } })
      const selected = step.answer === "approve"
        ? question.options[0].label
        : step.answer === "reject"
          ? question.options[1].label
          : step.answer
      await plugin.event({ event: {
        type: "question.replied",
        properties: {
          sessionID: controlSessionID,
          requestID,
          answers: step.answers ?? [[selected]],
        },
      } })
      await plugin["tool.execute.after"](
        { sessionID: controlSessionID, callID, tool: "question", args: { questions } },
        { output: "User has answered your questions.", metadata: { answers: [[selected]] } },
      )
      record.controlSessionID = controlSessionID
      record.question = question
      record.selected = selected
      record.allowed = true
    } else {
      throw new Error(`unknown scenario step: ${step.kind}`)
    }
  } catch (thrown: any) {
    // tool.execute.before ends every non-allow decision by throwing. The throw
    // IS the observation for a blocked step, so it is recorded rather than
    // propagated -- a driver that died here would report nothing.
    if (step.kind === "before" && lastBridgeRequiresApproval) {
      await presentPermission(step)
    }
    record.allowed = false
    record.error = String(thrown?.message ?? thrown)
  }
  stepResults.push(record)
}

/** Run competing callbacks while a selected real bridge boundary is held open. */
async function runHeld(step: any) {
  const gate: HeldRequest = {
    sessionID: step.first.childSessionID, outcome: step.outcome, used: false,
    entered: barrier(), release: barrier(),
  }
  if (step.kind === "held-start") heldStart = gate
  else heldIssuance = gate
  const first = runStep(step.first)
  await gate.entered.promise
  const delivered = barrier()
  if (step.second) startDelivered.set(step.second.childSessionID, delivered)
  const second = step.second ? runStep(step.second) : undefined
  const waiting = (step.during ?? []).map(runStep)
  if (step.second) await delivered.promise
  // Drain callbacks scheduled by bridge completion, not a wall-clock race or a sleep.
  await new Promise<void>((resolve) => setImmediate(resolve))
  observations.push({
    label: step.label,
    identityAttempts: [...identityAttempts], maxActiveIssuers,
    settledWhileHeld: stepResults.filter((result) =>
      (step.during ?? []).some((item: any) => item.label === result.label)).map((result) => result.label),
  })
  gate.release.release()
  await Promise.all([first, second, ...waiting])
  if (step.second) startDelivered.delete(step.second.childSessionID)
  heldStart = undefined
  heldIssuance = undefined
}

for (const step of scenario.steps) {
  if (step.kind === "concurrent") {
    await Promise.all(step.steps.map(runStep))
  } else if (step.kind === "held-start" || step.kind === "held-issuance") {
    await runHeld(step)
  } else if (step.kind === "observe") {
    observations.push({ label: step.label, identityAttempts: [...identityAttempts], maxActiveIssuers })
  } else {
    await runStep(step)
  }
}

/** Keep identity regression reports useful without emitting issued credentials. */
function redactIdentity(exchange: Exchange) {
  const sent: any = { ...exchange.sent }
  const received: any = { ...(exchange.received as object) }
  if (sent.parentAttestation) {
    sent.parentAttestationPresent = true
    delete sent.parentAttestation
  }
  if (sent.roleContext) {
    sent.roleContext = { ...sent.roleContext, attestationPresent: Boolean(sent.roleContext.attestation) }
    delete sent.roleContext.attestation
  }
  if (received.attestation) {
    received.attestationPresent = true
    delete received.attestation
  }
  return { ...exchange, sent, received }
}

console.log(JSON.stringify({
  steps: stepResults,
  exchanges: scenario.redactIdentityRecords ? exchanges.map(redactIdentity) : exchanges,
  permissionAsks,
  controlPrompts,
  observations,
  maxActiveIssuers,
}))
