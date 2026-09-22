/**
 * Live control-plane smoke against a RUNNING `opencode serve`.
 *
 * Observes the literal `question.asked` / `question.replied` / `session.idle`
 * lifecycle the host emits when Gaia presents consent in an already-known
 * specialist child. The smoke creates no session, sends no control prompt, and
 * records no decision; the normal Gaia dispatch and native host UI own those
 * actions.
 *
 * Opt-in: exits 0 with `SKIP` unless `OPENCODE_SMOKE_BASE_URL` is set. It talks
 * to a real OpenCode serve with the Gaia plugin loaded. Start it, exercise a
 * normal Gaia flow that reaches native consent, and answer the binary question
 * in the host UI. The watcher derives the specialist child from the signed
 * event; nobody has to locate or open an internal session.
 *
 *   OPENCODE_SMOKE_BASE_URL=http://127.0.0.1:10788 \
 *   OPENCODE_SMOKE_DIRECTORY=/home/jorge/ws/me \
 *   OPENCODE_SMOKE_PASSWORD=<OPENCODE_SERVER_PASSWORD of the serve process> \
 *   bun tests/opencode/live/control_plane_smoke.ts
 *
 * Environment:
 *   OPENCODE_SMOKE_BASE_URL   required; the smoke is skipped without it.
 *   OPENCODE_SMOKE_DIRECTORY  project directory whose serve event stream is
 *                             observed. Default: cwd.
 *   OPENCODE_SMOKE_USERNAME / OPENCODE_SMOKE_PASSWORD
 *                             HTTP basic auth when the serve runs with
 *                             OPENCODE_SERVER_PASSWORD (falls back to that
 *                             variable). Username defaults to `opencode`.
 *   OPENCODE_SMOKE_SDK_DIR    `@opencode-ai/sdk` package dir. Default: the copy
 *                             the host itself ships, ~/.opencode/node_modules.
 *
 * The question call from `binaryDecisionRequest` carries canonical visible
 * text, exact approval/correlation ids, two ordered labels, `multiple: false`,
 * and `custom: false`. OpenCode may omit optional `custom` from its normalized
 * `tool.execute.before` input and `question.asked` copy; Gaia permits only that
 * omission at those boundaries in the exclusive control-owned lifecycle. The prompt targets the known
 * specialist session without replacing its agent or tools.
 */

import { writeFileSync } from "node:fs"

const QUESTION_ASKED_TIMEOUT_MS = 90_000
const QUESTION_REPLIED_TIMEOUT_MS = 30_000
const SESSION_IDLE_TIMEOUT_MS = 30_000

export type Captured = { at: string; event: any }

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

/** Consume streamed events once, so each match is strictly later than the prior match. */
export class EventCursor {
  readonly events: Captured[] = []
  private readonly waiters: Array<() => void> = []
  private nextIndex = 0

  push(event: any, at = now()): void {
    this.events.push({ at, event })
    for (const wake of this.waiters.splice(0)) wake()
  }

  async waitFor(
    predicate: (event: any) => boolean,
    timeoutMs: number,
    label: string,
  ): Promise<Captured & { index: number }> {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      for (; this.nextIndex < this.events.length; this.nextIndex++) {
        const index = this.nextIndex
        const captured = this.events[index]
        if (predicate(captured.event)) {
          this.nextIndex = index + 1
          return { ...captured, index }
        }
      }
      const remaining = deadline - Date.now()
      if (remaining <= 0) {
        const recent = this.events.slice(-5).map(({ at, event }, offset) => {
          const index = this.events.length - Math.min(5, this.events.length) + offset
          return `${index}:${String(event?.type ?? "?")}@${at}`
        }).join(", ")
        throw new Error(
          `timeout after ${timeoutMs} ms waiting for ${label}; `
          + `cursor=${this.nextIndex}, buffered=${this.events.length}, recent=[${recent}]`,
        )
      }
      await new Promise<void>((resolve) => {
        let settled = false
        let timer: ReturnType<typeof setTimeout>
        const wake = () => {
          if (settled) return
          settled = true
          clearTimeout(timer)
          resolve()
        }
        timer = setTimeout(() => {
          if (settled) return
          settled = true
          const index = this.waiters.indexOf(wake)
          if (index !== -1) this.waiters.splice(index, 1)
          resolve()
        }, remaining)
        this.waiters.push(wake)
      })
    }
  }
}

export type SignedQuestion = {
  approvalID: string
  correlationID: string
  approveLabel: string
  rejectLabel: string
}

/** Validate and decode one signed Gaia question from the host event copy. */
export function signedQuestion(questions: unknown): SignedQuestion | undefined {
  if (!Array.isArray(questions) || questions.length !== 1) return undefined
  const question = questions[0] as any
  if (
    question?.header !== "Gaia approval"
    || typeof question?.question !== "string"
    || question?.multiple !== false
    || (question?.custom !== undefined && question.custom !== false)
    || !Array.isArray(question?.options)
    || question.options.length !== 2
  ) return undefined
  const match = /\n\nDECISION: (P-[0-9a-f]{32}) \/ (\S+)$/.exec(question.question)
  if (!match) return undefined
  const [, approvalID, correlationID] = match
  const approveLabel = `Approve once [${approvalID}]`
  const rejectLabel = `Reject [${approvalID}]`
  if (
    question.options[0]?.label !== approveLabel
    || question.options[0]?.description !== "Activate this exact request once"
    || question.options[1]?.label !== rejectLabel
    || question.options[1]?.description !== "Create no grant and perform no operation"
  ) return undefined
  return { approvalID, correlationID, approveLabel, rejectLabel }
}

/** Return the one exact signed label selected by a correlated host reply. */
export function selectedBinaryLabel(answers: unknown, signed: SignedQuestion): string | undefined {
  const selected = Array.isArray(answers) && Array.isArray(answers[0]) && answers[0].length === 1
    ? answers[0][0]
    : undefined
  return selected === signed.approveLabel || selected === signed.rejectLabel ? selected : undefined
}

async function main() {
  const baseUrl = process.env.OPENCODE_SMOKE_BASE_URL
  if (!baseUrl) {
    console.log("SKIP: OPENCODE_SMOKE_BASE_URL not set; live control-plane smoke is opt-in")
    return
  }
  const directory = process.env.OPENCODE_SMOKE_DIRECTORY ?? process.cwd()
  const sdkDir = process.env.OPENCODE_SMOKE_SDK_DIR
    ?? `${process.env.HOME}/.opencode/node_modules/@opencode-ai/sdk`
  const username = process.env.OPENCODE_SMOKE_USERNAME ?? "opencode"
  const password = process.env.OPENCODE_SMOKE_PASSWORD ?? process.env.OPENCODE_SERVER_PASSWORD
  const headers: Record<string, string> = password
    ? { Authorization: `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}` }
    : {}
  const v1 = await import(`${sdkDir}/dist/index.js`)
  const client = v1.createOpencodeClient({ baseUrl, directory, headers })

  const cursor = new EventCursor()
  const sse = await client.event.subscribe()
  const pump = (async () => {
    for await (const event of sse.stream) {
      cursor.push(event)
    }
  })()
  pump.catch((error: unknown) => log(`SSE stream ended with error: ${String(error)}`))

  log("waiting for Gaia's next native approval question; use the normal root chat and answer in the host UI")
  const asked = await cursor.waitFor(
    (event) => event?.type === "question.asked" && signedQuestion(event?.properties?.questions) !== undefined,
    QUESTION_ASKED_TIMEOUT_MS,
    "a signed Gaia question.asked event",
  )
  const requestID = asked.event.properties.id
  const specialistSessionID = asked.event.properties.sessionID
  const signed = signedQuestion(asked.event.properties.questions)!
  if (typeof requestID !== "string" || typeof specialistSessionID !== "string") {
    throw new Error("signed question event lacks request or specialist session identity")
  }
  const specialist = await client.session.get({ path: { id: specialistSessionID } })
  if (specialist?.error || specialist?.data?.id !== specialistSessionID || !specialist?.data?.parentID) {
    throw new Error("signed Gaia question did not originate in a readable specialist child")
  }
  log(`question.asked captured at ${asked.at} for known specialist ${specialistSessionID}:\n${literal(asked.event)}`)
  log(`VERDICT signed native binary question: MATCH (${signed.approvalID} / ${signed.correlationID})`)

  const replied = await cursor.waitFor(
    (event) => event?.type === "question.replied"
      && event?.properties?.requestID === requestID
      && event?.properties?.sessionID === specialistSessionID,
    QUESTION_REPLIED_TIMEOUT_MS,
    `question.replied for ${requestID}`,
  )
  const selected = selectedBinaryLabel(replied.event.properties?.answers, signed)
  if (selected === undefined) {
    throw new Error(`question.replied was not one exact signed label: ${literal(replied.event.properties?.answers)}`)
  }
  log(`question.replied captured at ${replied.at}; exact binary label: ${selected}`)

  const idle = await cursor.waitFor(
    (event) => event?.type === "session.idle" && event?.properties?.sessionID === specialistSessionID,
    SESSION_IDLE_TIMEOUT_MS,
    `safe session.idle for ${specialistSessionID}`,
  )
  if (!(asked.index < replied.index && replied.index < idle.index)) {
    throw new Error(
      `invalid consent lifecycle order: asked=${asked.index}, replied=${replied.index}, idle=${idle.index}`,
    )
  }
  log(
    `session.idle captured at ${idle.at}; strict lifecycle indexes `
    + `${asked.index} < ${replied.index} < ${idle.index}`,
  )

  const messages = await client.session.messages({ path: { id: specialistSessionID } })
  log(`known specialist messages after decision (${messages.data?.length ?? 0})`)
  const histogram: Record<string, number> = {}
  for (const { event } of cursor.events) histogram[event?.type ?? "?"] = (histogram[event?.type ?? "?"] ?? 0) + 1
  log(`SSE events captured: ${cursor.events.length}; by type: ${literal(histogram)}`)
}

if (import.meta.main) {
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
}
