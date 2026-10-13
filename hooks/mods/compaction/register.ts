import type { Register } from 'claude-code'

export const STEERING = [
  'Preserve continuity for the next turn:',
  '- ACTIVE OBJECTIVE: the goal being worked on',
  '- RESUME POINT: the exact next action',
  '- DURABLE REFERENCES: memory slugs and brief/plan/task ids, never their bodies',
  '- UNSAVED TRANSIENT CONTEXT: facts that exist only in this conversation',
  '- BLOCKERS / APPROVALS: current state',
  '- ACTIVE FILES: only the files needed to resume',
  'Compress tool output and intermediate reasoning. Do not duplicate durable',
  'memory or structured work; retain identifiers and the next decision.',
].join('\n')

export const COMPACT_TOOL = 'compact_when_idle'

export const RETRY_DELAY_MS = 500
export const MAX_ATTEMPTS = 6
export const REQUEST_EXPIRY_MS = 15 * 60_000

/** What follows one compaction attempt: started, try again after a wait, wait for the next turn end, or give up. */
export type Step =
  | { kind: 'started' }
  | { kind: 'retry'; waitMs: number }
  | { kind: 'next-turn-end' }
  | { kind: 'failed'; reason: string }

/**
 * Decides the step after attempt *attemptNumber* resolved with *outcome* or
 * rejected with it. Only the host's "a turn is running" (Stop hooks still
 * finishing) is worth retrying; every other refusal, DISABLE_COMPACT and
 * turnHeld included, is final.
 */
export function afterAttempt(outcome: { skip?: unknown } | undefined | Error, attemptNumber: number): Step {
  if (!(outcome instanceof Error)) {
    return outcome?.skip ? { kind: 'failed', reason: `a hook vetoed it (${String(outcome.skip)})` } : { kind: 'started' }
  }
  if (!/a turn is running/i.test(outcome.message)) return { kind: 'failed', reason: outcome.message }
  if (attemptNumber < MAX_ATTEMPTS) return { kind: 'retry', waitMs: RETRY_DELAY_MS * 2 ** (attemptNumber - 1) }
  return { kind: 'next-turn-end' }
}

export function isExpired(requestedAt: number, now: number): boolean {
  return now - requestedAt > REQUEST_EXPIRY_MS
}

function steer(instructions: string | undefined): string {
  return instructions ? `${instructions}\n\n${STEERING}` : STEERING
}

/**
 * Steers every compaction and lets the main session ask for one.
 *
 * A request waits for the main turn to end, since `session.compact` rejects
 * while a turn runs, and is retried only while the host still says so; past
 * the retries it waits for the next turn end, until REQUEST_EXPIRY_MS. A final
 * refusal or an expiry is told to the user (a toast) and to the orchestrator
 * (context on the next prompt), both naming /compact. Nothing is appended to
 * the conversation: the SessionStart(compact) refresh delivers the snapshot.
 */
export const register: Register = on => {
  let compactTool: string | undefined
  let pendingSince: number | undefined
  let attempting = false
  let undelivered: string | undefined

  on('session.start', async ($, e, next) => {
    compactTool = (
      await $.tool.register({
        name: COMPACT_TOOL,
        description:
          'Schedule a compaction of this session for when the current turn ends. Call it once, write the resume point it names, then end the turn; a refusal by the host is reported.',
        isDeferred: false,
      })
    ).tool
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool !== compactTool) return next(e)
    if (e.agentId) return { result: 'Compaction was not requested: only the main session can be compacted this way.' }
    pendingSince = await $.clock.now()
    const sessionId = await $.session.id()
    return {
      result:
        `Compaction of session ${sessionId} is scheduled for when this turn ends; if the host refuses it, ` +
        'the refusal will be reported and the user must type /compact. Before ending the turn, record the resume point: ' +
        `gaia session resume-point set --session-id ${sessionId} --text "<where the work stands and what comes next>"`,
    }
  })

  on('turn.complete', async ($, e, next) => {
    const completed = await next(e)
    if (pendingSince === undefined || e.agentId || e.isAborted || attempting) return completed

    const fail = (reason: string): void => {
      pendingSince = undefined
      undelivered = reason
      $.ui.toast(`Gaia could not start the compaction: ${reason}. Type /compact to compact the session.`)
    }
    if (isExpired(pendingSince, await $.clock.now())) {
      fail('the request expired before the session was idle')
      return completed
    }

    const attempt = (attemptNumber: number, waitMs: number): void => {
      $.clock.after(waitMs, async () => {
        let outcome: { skip?: unknown } | undefined | Error
        try {
          outcome = (await $.session.compact()) as { skip?: unknown } | undefined
        } catch (error) {
          outcome = error instanceof Error ? error : new Error(String(error))
        }
        const step = afterAttempt(outcome, attemptNumber)
        if (step.kind === 'retry') return attempt(attemptNumber + 1, step.waitMs)
        attempting = false
        if (step.kind === 'started') pendingSince = undefined
        if (step.kind === 'failed') fail(step.reason)
      })
    }
    attempting = true
    attempt(1, 0)
    return completed
  })

  on('prompt.submit', ($, e, next) => {
    if (undelivered === undefined) return next(e)
    const note =
      `[gaia] The compaction you requested did not start: ${undelivered}. ` +
      'Tell the user it did not happen and ask them to type /compact.'
    undelivered = undefined
    return next({ ...e, context: [...(e.context ?? []), note] })
  })

  on('session.compact', ($, e, next) => next({ ...e, instructions: steer(e.instructions) }))
}
