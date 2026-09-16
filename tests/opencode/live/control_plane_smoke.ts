/**
 * Live control-plane smoke against a RUNNING `opencode serve`.
 *
 * Measures the literal `question.asked` / `question.replied` events the host
 * emits when a child control session asks Gaia's consent question, so the
 * correlation in `opencode/plugin.ts` (event handler for `question.asked`,
 * which compares `JSON.stringify(event.properties.questions)` against
 * `JSON.stringify([control.request.question])`) can be judged against the real
 * host instead of the stubs in `tests/opencode/*_driver.ts`.
 *
 * Opt-in: exits 0 with `SKIP` unless `OPENCODE_SMOKE_BASE_URL` is set. It talks
 * to a real LLM through the host's `gaia-orchestrator` agent, creates two
 * sessions (root + child) and always deletes them at the end.
 *
 *   OPENCODE_SMOKE_BASE_URL=http://127.0.0.1:10788 \
 *   OPENCODE_SMOKE_DIRECTORY=/home/jorge/ws/me \
 *   OPENCODE_SMOKE_PASSWORD=<OPENCODE_SERVER_PASSWORD of the serve process> \
 *   bun tests/opencode/live/control_plane_smoke.ts
 *
 * Environment:
 *   OPENCODE_SMOKE_BASE_URL   required; the smoke is skipped without it.
 *   OPENCODE_SMOKE_DIRECTORY  project directory the host resolves agents for;
 *                             must expose the smoke agent. Default: cwd.
 *   OPENCODE_SMOKE_AGENT      agent named in the prompt body. Default is the
 *                             plugin's `gaia-orchestrator`; when the Gaia plugin
 *                             is loaded in the serve it refuses that role for a
 *                             session it did not open itself ("control-plane
 *                             role was declared without an attested runtime
 *                             context"), so the host never runs `question`. A
 *                             native agent such as `build` measures the host's
 *                             event shape without claiming a Gaia role.
 *   OPENCODE_SMOKE_ROOT_ONLY  `1` prompts the root session instead of a child.
 *                             The Gaia adapter denies tool calls of any child
 *                             session not bound to a Task dispatch
 *                             (`child-session-binding-backstop`), so a child
 *                             created from outside the plugin never reaches the
 *                             host's `question` tool while the plugin is loaded.
 *   OPENCODE_SMOKE_USERNAME / OPENCODE_SMOKE_PASSWORD
 *                             HTTP basic auth when the serve runs with
 *                             OPENCODE_SERVER_PASSWORD (falls back to that
 *                             variable). Username defaults to `opencode`.
 *   OPENCODE_SMOKE_SDK_DIR    `@opencode-ai/sdk` package dir. Default: the copy
 *                             the host itself ships, ~/.opencode/node_modules.
 *
 * The prompt body is the one `openBinaryDecision` sends (`agent`, `system`
 * string, `tools: {"*": false, question: true}`, instruction carrying
 * `JSON.stringify({ questions: [request.question] })`), and the question mirrors
 * `binaryDecisionRequest`: keys header, question, options[].label/description,
 * multiple, in that order. Both must be updated together with plugin.ts or the
 * smoke stops measuring what the plugin does.
 */

import { randomBytes } from "node:crypto"
import { writeFileSync } from "node:fs"

const baseUrl = process.env.OPENCODE_SMOKE_BASE_URL
if (!baseUrl) {
  console.log("SKIP: OPENCODE_SMOKE_BASE_URL not set; live control-plane smoke is opt-in")
  process.exit(0)
}

const directory = process.env.OPENCODE_SMOKE_DIRECTORY ?? process.cwd()
const agent = process.env.OPENCODE_SMOKE_AGENT ?? "gaia-orchestrator"
const rootOnly = process.env.OPENCODE_SMOKE_ROOT_ONLY === "1"
const sdkDir = process.env.OPENCODE_SMOKE_SDK_DIR
  ?? `${process.env.HOME}/.opencode/node_modules/@opencode-ai/sdk`
const username = process.env.OPENCODE_SMOKE_USERNAME ?? "opencode"
const password = process.env.OPENCODE_SMOKE_PASSWORD ?? process.env.OPENCODE_SERVER_PASSWORD
const headers: Record<string, string> = password
  ? { Authorization: `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}` }
  : {}

const QUESTION_ASKED_TIMEOUT_MS = 90_000
const QUESTION_REPLIED_TIMEOUT_MS = 30_000
const SETTLE_AFTER_REPLY_MS = 20_000

type Captured = { at: string; event: any }

function now(): string {
  return new Date().toISOString()
}

const transcript: string[] = []

function log(line: string) {
  const stamped = `[${now()}] ${line}`
  transcript.push(stamped)
  console.log(stamped)
}

/** Full transcript on disk when OPENCODE_SMOKE_DUMP names a file; terminals truncate the SSE dump. */
function flushTranscript() {
  const dump = process.env.OPENCODE_SMOKE_DUMP
  if (dump) writeFileSync(dump, `${transcript.join("\n")}\n`)
}

function literal(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

/** Mirror of plugin.ts `binaryDecisionRequest` for a fictitious approval. */
function smokeQuestion(approvalID: string, correlationID: string) {
  const visibleText = [
    `APPROVAL: ${approvalID}`,
    "COMMAND: cp ~/.gaia/scratch/smoke-src ~/.gaia/scratch/smoke-dst",
    "RATIONALE: live control-plane smoke; no real command is executed",
  ].join("\n")
  return {
    header: "Gaia approval",
    question: `${visibleText}\n\nDECISION: ${approvalID} / ${correlationID}`,
    options: [
      { label: `Approve once [${approvalID}]`, description: "Activate this exact request once" },
      { label: `Reject [${approvalID}]`, description: "Create no grant and perform no operation" },
    ],
    multiple: false,
  }
}

function hostRejection(result: any): string | undefined {
  if (!result) return "empty SDK result"
  if (result.error) return literal(result.error)
  return undefined
}

function structurallyEqual(actual: any, expected: any): boolean {
  if (!Array.isArray(actual) || actual.length !== 1) return false
  const q = actual[0]
  return q?.header === expected.header
    && q?.question === expected.question
    && q?.multiple === expected.multiple
    && Array.isArray(q?.options)
    && q.options.length === expected.options.length
    && q.options.every((option: any, index: number) =>
      option?.label === expected.options[index].label
      && option?.description === expected.options[index].description)
}

async function main() {
  const v1 = await import(`${sdkDir}/dist/index.js`)
  const v2 = await import(`${sdkDir}/dist/v2/index.js`)
  const client = v1.createOpencodeClient({ baseUrl, directory, headers })
  const clientV2 = v2.createOpencodeClient({ baseUrl, directory, headers })

  const agents = await fetch(`${baseUrl}/agent?directory=${encodeURIComponent(directory)}`, { headers })
  if (!agents.ok) throw new Error(`GET /agent failed: HTTP ${agents.status}`)
  const agentNames = ((await agents.json()) as Array<{ name: string }>).map((a) => a.name)
  if (!agentNames.includes(agent)) {
    throw new Error(`host does not expose ${agent} for ${directory}; agents: ${agentNames.join(", ")}`)
  }
  log(`host agents for ${directory}: ${agentNames.join(", ")}`)

  const events: Captured[] = []
  const waiters: Array<() => void> = []
  const sse = await client.event.subscribe()
  const pump = (async () => {
    for await (const event of sse.stream) {
      events.push({ at: now(), event })
      for (const wake of waiters.splice(0)) wake()
    }
  })()
  pump.catch((error: unknown) => log(`SSE stream ended with error: ${String(error)}`))

  async function waitFor(predicate: (event: any) => boolean, timeoutMs: number, label: string): Promise<Captured> {
    const deadline = Date.now() + timeoutMs
    let scanned = 0
    for (;;) {
      for (; scanned < events.length; scanned++) {
        if (predicate(events[scanned].event)) return events[scanned]
      }
      const remaining = deadline - Date.now()
      if (remaining <= 0) throw new Error(`timeout after ${timeoutMs} ms waiting for ${label}`)
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, remaining)
        waiters.push(() => { clearTimeout(timer); resolve() })
      })
    }
  }

  const ts = now()
  const approvalID = `P-${randomBytes(16).toString("hex")}`
  const correlationID = `smoke-${randomBytes(6).toString("hex")}`
  const question = smokeQuestion(approvalID, correlationID)
  const approveLabel = question.options[0].label

  let rootID: string | undefined
  let childID: string | undefined
  const sessionIDs = () => [rootID, childID].filter((id): id is string => typeof id === "string")
  const mentionsOurSessions = (event: any) => {
    const p = event?.properties
    const candidates = [p?.sessionID, p?.info?.sessionID, p?.part?.sessionID, p?.info?.id, p?.id]
    return candidates.some((id) => typeof id === "string" && sessionIDs().includes(id))
  }

  try {
    const root = await client.session.create({ body: { title: `Gaia smoke ${ts}` } })
    const rootRejected = hostRejection(root)
    if (rootRejected) throw new Error(`root session.create rejected: ${rootRejected}`)
    rootID = root.data.id
    log(`root session: ${rootID}`)

    if (rootOnly) {
      childID = rootID
      log("OPENCODE_SMOKE_ROOT_ONLY set: prompting the root session itself, no child")
    } else {
      const child = await client.session.create({ body: { parentID: rootID, title: `Gaia smoke control ${ts}` } })
      const childRejected = hostRejection(child)
      if (childRejected) throw new Error(`child session.create rejected: ${childRejected}`)
      childID = child.data.id
      log(`child session: ${childID} (parentID ${child.data.parentID})`)
    }

    const instruction = [
      "You are a mechanical consent control plane.",
      "Invoke the question tool exactly once with the JSON below and do nothing else.",
      "Do not answer the question, infer consent, rewrite any text, or emit approval prose.",
      JSON.stringify({ questions: [question] }),
    ].join("\n")
    const body = {
      agent,
      system: "Only the question tool is available. Free text has no decision authority.",
      tools: { "*": false, question: true },
      parts: [{ type: "text", text: instruction }],
    }
    log(`promptAsync body:\n${literal(body)}`)
    const prompted = await client.session.promptAsync({ path: { id: childID }, body })
    const promptRejected = hostRejection(prompted)
    if (promptRejected) throw new Error(`promptAsync rejected: ${promptRejected}`)
    log(`promptAsync accepted: HTTP ${prompted.response?.status} data=${literal(prompted.data)}`)

    const asked = await waitFor(
      (event) => event?.type === "question.asked" && event?.properties?.sessionID === childID,
      QUESTION_ASKED_TIMEOUT_MS,
      `question.asked for ${childID}`,
    )
    log(`question.asked captured at ${asked.at}:\n${literal(asked.event)}`)
    log(`question.asked properties keys: ${Object.keys(asked.event.properties).join(", ")}`)
    log(`question.asked questions[0] keys: ${Object.keys(asked.event.properties.questions?.[0] ?? {}).join(", ")}`)

    const byteEqual = JSON.stringify(asked.event.properties.questions) === JSON.stringify([question])
    const structEqual = structurallyEqual(asked.event.properties.questions, question)
    log(`VERDICT byte-equal (plugin.ts question.asked correlation): ${byteEqual ? "MATCH" : "MISMATCH"}`)
    log(`VERDICT structural-equal (header, question, options[].label/description, multiple): ${structEqual ? "MATCH" : "MISMATCH"}`)
    if (!byteEqual) {
      log(`expected JSON: ${JSON.stringify([question])}`)
      log(`actual   JSON: ${JSON.stringify(asked.event.properties.questions)}`)
    }

    const requestID = asked.event.properties.id
    const replied = await clientV2.question.reply({ requestID, directory, answers: [[approveLabel]] })
    const replyRejected = hostRejection(replied)
    if (replyRejected) throw new Error(`question.reply rejected: ${replyRejected}`)
    log(`question.reply accepted: HTTP ${replied.response?.status} data=${literal(replied.data)}`)

    const repliedEvent = await waitFor(
      (event) => event?.type === "question.replied" && event?.properties?.requestID === requestID,
      QUESTION_REPLIED_TIMEOUT_MS,
      `question.replied for ${requestID}`,
    )
    log(`question.replied captured at ${repliedEvent.at}:\n${literal(repliedEvent.event)}`)
    log(`question.replied properties keys: ${Object.keys(repliedEvent.event.properties).join(", ")}`)

    try {
      await waitFor(
        (event) => event?.type === "session.idle" && event?.properties?.sessionID === childID,
        SETTLE_AFTER_REPLY_MS,
        `session.idle for ${childID}`,
      )
    } catch (error) {
      log(String(error))
    }
  } finally {
    if (childID) {
      const messages = await client.session.messages({ path: { id: childID } })
      log(`child session messages (${messages.data?.length ?? 0}):`)
      for (const message of messages.data ?? []) {
        const parts = (message.parts ?? []).map((part: any) => ({
          type: part.type,
          ...(part.type === "text" ? { text: part.text } : {}),
          ...(part.type === "reasoning" ? { text: part.text } : {}),
          ...(part.type === "tool" ? { tool: part.tool, callID: part.callID, status: part.state?.status, input: part.state?.input, output: part.state?.output, error: part.state?.error, metadata: part.state?.metadata } : {}),
        }))
        log(`  ${message.info.role} ${message.info.id} agent=${message.info.agent ?? "-"} finish=${message.info.finish ?? "-"}:\n${literal(parts)}`)
      }
    }

    const histogram: Record<string, number> = {}
    for (const { event } of events) histogram[event?.type ?? "?"] = (histogram[event?.type ?? "?"] ?? 0) + 1
    log(`SSE events captured: ${events.length}; by type: ${literal(histogram)}`)
    const ours = events.filter(({ event }) => mentionsOurSessions(event) && event?.type !== "message.part.updated")
    log(`events touching the smoke sessions (message.part.updated excluded): ${ours.length}`)
    for (const { at, event } of ours) log(`  ${at} ${event.type} ${JSON.stringify(event.properties)}`)

    for (const id of new Set(sessionIDs())) {
      const deleted = await client.session.delete({ path: { id } })
      log(`session.delete ${id}: ${hostRejection(deleted) ?? `ok (HTTP ${deleted.response?.status})`}`)
    }
  }
}

main().then(
  () => {
    flushTranscript()
    process.exit(0)
  },
  (error) => {
    log(`FAILED: ${error instanceof Error ? error.stack ?? error.message : String(error)}`)
    flushTranscript()
    process.exit(1)
  },
)
