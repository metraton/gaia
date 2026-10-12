import type { Hook, Register } from 'claude-code'

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
export const MAX_ATTEMPTS = 5

function steer(instructions: string | undefined): string {
  return instructions ? `${instructions}\n\n${STEERING}` : STEERING
}

/**
 * Steers every compaction and lets the main session ask for one.
 *
 * The compaction tool only starts the compaction once the requesting turn has
 * ended, since `session.compact` rejects while a turn runs. Nothing is appended
 * here: the SessionStart(compact) refresh delivers the session snapshot, once.
 */
export const register: Register = on => {
  let compactTool: string | undefined
  let requested = false

  on('session.start', async ($, e, next) => {
    compactTool = (
      await $.tool.register({
        name: COMPACT_TOOL,
        description:
          'Start compacting this session when the current turn ends. Call it once, write the resume point it names, then end the turn.',
        isDeferred: false,
      })
    ).tool
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    if (e.tool !== compactTool) return next(e)
    if (e.agentId) return { result: 'Compaction was not requested: only the main session can be compacted this way.' }
    requested = true
    const sessionId = await $.session.id()
    return {
      result:
        `Compaction of session ${sessionId} starts when this turn ends. Before ending it, record the resume point: ` +
        `gaia session resume-point set --session-id ${sessionId} --text "<where the work stands and what comes next>"`,
    }
  })

  on('turn.complete', async ($, e, next) => {
    const completed = await next(e)
    if (requested && !e.agentId) {
      requested = false
      if (!e.isAborted) compactAfterTurn($)
    }
    return completed
  })

  on('session.compact', ($, e, next) => next({ ...e, instructions: steer(e.instructions) }))
}

function compactAfterTurn($: Parameters<Hook<'turn.complete'>>[0], attempt = 1): void {
  $.clock.after(attempt === 1 ? 0 : RETRY_DELAY_MS * 2 ** (attempt - 2), () => {
    $.session.compact().catch(() => {
      if (attempt < MAX_ATTEMPTS) compactAfterTurn($, attempt + 1)
    })
  })
}
