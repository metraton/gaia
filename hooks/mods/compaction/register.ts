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

function steer(instructions: string | undefined): string {
  return instructions ? `${instructions}\n\n${STEERING}` : STEERING
}

/**
 * Adds Gaia's steering to every compaction. The session snapshot is not
 * appended here: the SessionStart(compact) refresh delivers it, once.
 */
export const register: Register = on => {
  on('session.compact', ($, e, next) => next({ ...e, instructions: steer(e.instructions) }))
}
