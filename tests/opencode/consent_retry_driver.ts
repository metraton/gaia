/**
 * Drives the real GaiaOpenCodePlugin through a consent retry and reports every
 * boundary it crossed.
 *
 * The claim this driver exists to support is about a SEQUENCE of tool calls
 * sharing one identity, so nothing in the sequence may be hand-written: the
 * plugin closure runs, its own `bridge()` is reached through a recorder that
 * forwards verbatim to the real `opencode/bridge.py`, and the orchestrator's
 * question call executes the real `gaia approvals show` / `opencode-present` /
 * `opencode-decide` CLIs against the database in GAIA_DB (D39).
 *
 * Normal scenarios double two host seams because no
 * OpenCode host runs here: the host's question events and the host's
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
const gaiaPath = new URL("../../bin/gaia", import.meta.url).pathname

/** Bridge events that carry an audit record, never a policy verdict. */
const AUDIT_TRACE_EVENTS = new Set([
  "permission.uncorrelated", "control.opened", "control.closed", "decision.applied", "retry.refused",
])

/**
 * The host's normalized copy of the question the model asked. OpenCode may use
 * it for the pre-execution input or question.asked event, re-order keys, add
 * keys the plugin never sent, or omit optional `custom`; `mismatch` is not the
 * question Gaia asked.
 */
function hostNormalizedQuestion(question: any, encoding: string | undefined) {
  if (encoding === "reordered") {
    return {
      question: question.question,
      header: question.header,
      options: question.options.map((option: any) => ({ description: option.description, label: option.label })),
      multiple: question.multiple,
      custom: question.custom,
    }
  }
  if (encoding === "extra-keys") return { ...question, custom: false, tool: "question" }
  if (encoding === "event-60148") {
    return {
      question: question.question,
      header: question.header,
      options: question.options.map((option: any) => ({ ...option })),
      multiple: false,
    }
  }
  if (encoding === "mismatch") {
    return { ...question, options: [{ ...question.options[0], label: `${question.options[0].label} ` }, question.options[1]] }
  }
  return question
}

type Exchange = {
  sent: Record<string, unknown>
  received: unknown
  /** JSON.stringify of the args the plugin forwarded, for byte comparison. */
  sentArgsJSON: string
}

const exchanges: Exchange[] = []
/** Each approval a blocked step handed to the plugin's question, in order. */
const presentations: Record<string, unknown>[] = []
/** Every prompt the plugin sent into any session; D39 leaves it empty. */
const controlPrompts: Record<string, any>[] = []
/** Messages posted with session.prompt and question.asked deliveries, in the order the host saw them. */
const hostEvents: Record<string, unknown>[] = []
const stepResults: Record<string, unknown>[] = []
let lastBridgeAction: string | undefined
let questionCalls = 0

const OPTION_BY_ANSWER: Record<string, number> = { approve: 0, reject: 1, details: 2 }

/**
 * The question-tool input `gaia approvals question` prints in an OpenCode
 * shell (`_opencode_question` in bin/cli/approvals.py): one placeholder per
 * approval, carrying only its id, plus " details" for the Details re-ask.
 */
function placeholderArgs(approvalIDs: string[], details: boolean) {
  return {
    questions: approvalIDs.map((approvalID) => ({
      question: details ? `${approvalID} details` : approvalID,
      header: "Gaia",
      options: [{ label: "OK", description: "Gaia fills this question in" }],
    })),
  }
}

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
  // Audit traces the plugin sends mid-verdict (a denial it could not correlate,
  // a control the host accepted) are acknowledged, never ruled on; only a
  // policy answer says whether the host must now ask.
  const isAuditTrace = AUDIT_TRACE_EVENTS.has(String(event.event))
  if (!isAuditTrace) {
    lastBridgeAction = (received as any)?.action
  }
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
let plugin: any

// create/promptAsync/prompt only record: under D39 the plugin opens no session,
// sends no prompt and posts no message, so any entry here is a regression.
const client = {
  session: {
    async messages({ sessionID }: { sessionID: string }) {
      return { data: scenario.messages?.[sessionID] ?? [] }
    },
    async create(request: any) {
      controlPrompts.push({ create: request })
      return { data: { id: `control-${controlPrompts.length}` } }
    },
    async promptAsync(request: any) {
      controlPrompts.push(request)
      return { data: undefined, response: { ok: true, status: 204 } }
    },
    async prompt(request: any) {
      hostEvents.push({ type: "message", sessionID: request?.path?.id, text: request?.body?.parts?.[0]?.text })
      return { data: { info: { role: "user" } }, response: { ok: true, status: 200 } }
    },
  },
}

/**
 * The orchestrator's own question-tool call (D39): the host asks whatever the
 * plugin left in `args` after tool.execute.before, delivers question.asked and
 * question.replied, and only then tool.execute.after, whose output carries
 * Gaia's notice back to the orchestrator.
 */
async function askAsOrchestrator(step: any, record: Record<string, unknown>, args: any): Promise<void> {
  const sessionID = step.sessionID ?? scenario.rootSessionID
  const callID = step.callID ?? `question-call-${++questionCalls}`
  const requestID = step.requestID ?? `question-${callID}`
  record.controlSessionID = sessionID
  record.callID = callID
  record.modelQuestion = structuredClone(args.questions[0])
  await plugin["tool.execute.before"]({ sessionID, callID, tool: "question" }, { args })
  const questions = args.questions
  record.questions = structuredClone(questions)
  record.question = structuredClone(questions[0])
  if (step.secondQuestionCall === true) {
    await plugin["tool.execute.before"](
      { sessionID, callID: `${callID}-second`, tool: "question" },
      { args: { questions: structuredClone(questions) } },
    )
  }
  const asked = {
    type: "question.asked",
    properties: {
      sessionID, id: requestID,
      questions: questions.map((question: any) => hostNormalizedQuestion(question, step.questionEncoding)),
    },
  }
  await plugin.event({ event: asked })
  if (step.duplicateQuestionEvent === true) {
    await plugin.event({ event: { ...asked, properties: { ...asked.properties, id: `${requestID}-duplicate` } } })
  }
  if (Array.isArray(step.gaiaBeforeReply)) {
    // A Gaia CLI call made while the user reads the question, as another
    // session would make it.
    const child = Bun.spawn(["python3", "-B", gaiaPath, ...step.gaiaBeforeReply], {
      cwd: directory, env: { ...process.env, GAIA_HOST: "opencode" }, stdout: "pipe", stderr: "pipe",
    })
    record.gaiaBeforeReplyExitCode = await child.exited
  }
  if (step.answer === undefined && step.answers === undefined) return
  const answers = step.answers ?? questions.map((question: any) => [
    step.answer in OPTION_BY_ANSWER ? question.options[OPTION_BY_ANSWER[step.answer]].label : step.answer,
  ])
  record.selected = answers
  await plugin.event({ event: { type: "question.replied", properties: { sessionID, requestID, answers } } })
  const output = { output: "User has answered your questions.", metadata: { answers } }
  await plugin["tool.execute.after"]({ sessionID, callID, tool: "question", args }, output)
  record.output = output.output
}

const directory = process.env.WORKSPACE ?? process.cwd()
plugin = await GaiaOpenCodePlugin({ gaiaBridge, client, directory })
const deliverEvent = plugin.event
plugin.event = async (input: any) => {
  if (input?.event?.type === "question.asked") {
    hostEvents.push({ type: "question.asked", sessionID: input.event.properties?.sessionID })
  }
  return deliverEvent(input)
}
const argsByCall = new Map<string, any>()

/** The approval the policy bridge named for this call; concurrent steps share no global. */
function bridgeApprovalIDFor(callID: string): string | undefined {
  const exchange = exchanges.findLast((item: any) =>
    item.sent?.event === "tool.execute.before" && item.sent?.callID === callID && item.received?.approval_id)
  return (exchange as any)?.received?.approval_id
}

function recordPresentation(step: any, approvalID: string) {
  presentations.push({ approvalID, sessionID: step.sessionID, callID: step.callID })
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
      lastBridgeAction = undefined
      record.commandBefore = args.command
      await plugin["tool.execute.before"](
        { sessionID: step.sessionID, callID: step.callID, tool: step.tool ?? "bash" },
        { args },
      )
      record.commandAfter = args.command
      record.allowed = true
    } else if (step.kind === "shell-env") {
      const output = { env: {} as Record<string, string> }
      await plugin["shell.env"]({ sessionID: step.sessionID, callID: step.callID, cwd: directory }, output)
      record.env = output.env
      // Never execute the signed publication text: only observe the delivered
      // child environment -- the identity it carries, which `gaia` it resolves,
      // and where its temporary files go.
      const child = Bun.spawn([
        "python3", "-B", "-c",
        "import os, shutil, tempfile; print(os.environ.get('GAIA_DISPATCH_AGENT', '<unset>')); "
          + "print(shutil.which('gaia') or '<none>'); print(os.environ.get('TMPDIR', '<unset>')); "
          + "print(tempfile.gettempdir())",
      ], {
        env: { ...process.env, TMPDIR: "/tmp", ...output.env }, stdout: "pipe", stderr: "pipe",
      })
      const [childIdentity, gaiaOnPath, childTmpdir, childTempdir] =
        (await new Response(child.stdout).text()).trim().split("\n")
      record.childIdentity = childIdentity
      record.gaiaOnPath = gaiaOnPath
      record.childTmpdir = childTmpdir
      record.childTempdir = childTempdir
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
    } else if (step.kind === "gaia") {
      // A Gaia CLI call made between host events, as the user or another
      // session would make it; never a command from the set under test.
      const child = Bun.spawn(["python3", "-B", gaiaPath, ...step.args], {
        cwd: directory, env: { ...process.env, GAIA_HOST: "opencode" }, stdout: "pipe", stderr: "pipe",
      })
      record.stdout = (await new Response(child.stdout).text()).trim()
      record.exitCode = await child.exited
      record.allowed = record.exitCode === 0
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
    } else if (step.kind === "tool-error-part") {
      // The host's own gate (ruleset deny, rejected prompt) throws inside
      // tool.execute, so the real host fires no tool.execute.after; the errored
      // part is the only signal the plugin receives for that call.
      await plugin.event({
        event: {
          type: "message.part.updated",
          properties: {
            part: {
              type: "tool",
              tool: step.tool ?? "bash",
              sessionID: step.sessionID,
              callID: step.callID,
              state: { status: "error", error: step.error, input: argsByCall.get(step.callID) },
            },
          },
        },
      })
      record.allowed = true
    } else if (step.kind === "question-event") {
      // A question the host asked in a session with no Gaia question call
      // behind it, answered with Gaia's own labels: never a decision.
      await plugin.event({ event: {
        type: "question.asked",
        properties: { sessionID: step.sessionID, id: step.requestID, questions: step.questions },
      } })
      await plugin.event({ event: {
        type: "question.replied",
        properties: { sessionID: step.sessionID, requestID: step.requestID, answers: step.answers },
      } })
      record.allowed = true
    } else if (step.kind === "control-decision") {
      // The orchestrator asks the approvals the blocked steps named, by default
      // the last one, with exactly what `gaia approvals question` prints.
      const approvalIDs = step.approvalIDs ?? [presentations.at(-1)?.approvalID]
      if (approvalIDs.some((approvalID: unknown) => typeof approvalID !== "string")) {
        throw new Error("driver has no approval for the orchestrator to ask")
      }
      await askAsOrchestrator(step, record, placeholderArgs(approvalIDs, step.mode === "details"))
      record.allowed = true
    } else if (step.kind === "orchestrator-question") {
      // The orchestrator's own question-tool call, with the arguments the test
      // captured from `gaia approvals question`.
      await askAsOrchestrator(step, record, structuredClone(step.args))
      record.allowed = true
    } else {
      throw new Error(`unknown scenario step: ${step.kind}`)
    }
  } catch (thrown: any) {
    // tool.execute.before ends every non-allow decision by throwing. The throw
    // IS the observation for a blocked step, so it is recorded rather than
    // propagated -- a driver that died here would report nothing.
    const approvalID = step.kind === "before" ? bridgeApprovalIDFor(step.callID) : undefined
    if (approvalID) {
      recordPresentation(step, approvalID)
      record.requiresApproval = true
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
  const labels = step.kind === "concurrent" ? step.steps.map((item: any) => item.label) : [step.label]
  const blockedControlNeedsIdle = stepResults.some(
    (result) => labels.includes(result.label) && result.requiresApproval === true,
  )
  if (scenario.autoSafeIdle !== false && blockedControlNeedsIdle) {
    await plugin.event({ event: { type: "session.idle", properties: { sessionID: scenario.sessionID } } })
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
  presentations,
  controlPrompts,
  hostEvents,
  observations,
  maxActiveIssuers,
}))
