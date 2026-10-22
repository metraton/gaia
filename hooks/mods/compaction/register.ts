import type { Register } from 'claude-code'

// Generated: OpenCode sends build_summary_instructions; run compact_context_builder.py to rewrite this line.
export const STEERING = "# Compaction Instructions\n\nWrite the summary so the next turn can resume without re-deriving anything:\n- Do not restate what the next turn's birth block already delivers: its resume point carries the active plan and task, open contracts, pending approvals and the resume note, or the command that lists them.\n- State the active objective and the exact next action.\n- Carry durable work as coordinates, never its body: task and plan ids, contract ids, approval ids, memory slugs, commit SHAs, brief names.\n- Keep facts that exist only in this conversation, labeled as not durable.\n- Compress tool output and intermediate reasoning to what the next decision needs."

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
    // A headless session (-p, or the SDK the desktop app drives) cannot be
    // compacted from a mod: the host refuses $.session.compact() there, so the
    // tool is not offered and the orchestrator asks the user for /compact.
    if (!e.isInteractive) return next(e)
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
