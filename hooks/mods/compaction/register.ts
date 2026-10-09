import type { Engine, Register } from 'claude-code'

const LAUNCHER = 'hooks/launch.sh'
const GAIA_CLI = 'bin/gaia'
const SNAPSHOT_ARGS = ['session', 'snapshot', '--session-id'] as const
const SNAPSHOT_TIMEOUT_MS = 10_000
const RETRY_DELAY_MS = 2_000
const MAX_ATTEMPTS = 30

/**
 * First line of the message appended to one compaction. The token count binds
 * it to that compaction's boundary entry, which the SessionStart(compact)
 * refresh compares against; without a count nothing is marked, and the refresh
 * delivers its own copy.
 */
export function withMarker(snapshot: string, tokensBefore: number | undefined): string {
  return tokensBefore === undefined
    ? snapshot
    : `[gaia:session-snapshot tokens-before=${tokensBefore}]\n${snapshot}`
}

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
 * The one process this mod starts: a read-only `gaia session snapshot` through
 * the plugin's own launcher and CLI. The host's process call skips the
 * permission prompt and Gaia's PreToolUse, so the argv holds constants, the
 * plugin root and the session id, never conversation text. Undefined when the
 * snapshot is unavailable; the SessionStart(compact) hook then delivers alone.
 */
async function readSnapshot($: Engine): Promise<string | undefined> {
  const root = $.plugin.root
  const argv = [
    'sh',
    `${root}/${LAUNCHER}`,
    `${root}/${GAIA_CLI}`,
    ...SNAPSHOT_ARGS,
    await $.session.id(),
  ]
  try {
    const { exitCode, stdout } = await $.process.run(argv, { timeoutMs: SNAPSHOT_TIMEOUT_MS })
    const text = stdout.trim()
    return exitCode === 0 && text ? text : undefined
  } catch {
    return undefined
  }
}

/**
 * Compacts the session at the next idle tick: the entry point for a plugin
 * trigger. The host rejects a compaction while a turn runs, so a rejection
 * schedules another attempt. The engine skips the calling plugin's own
 * session.compact hook, so the steering and the snapshot ride in the
 * instructions here. The engine follows `$` only into functions declared in
 * the file that received it, so a trigger calls this from this file.
 */
export function scheduleCompact($: Engine, instructions?: string, attempt = 1): void {
  $.clock.after(RETRY_DELAY_MS, async () => {
    const snapshot = await readSnapshot($)
    const steered = steer(instructions)
    try {
      await $.session.compact({
        instructions: snapshot ? `${steered}\n\nSession state to keep:\n${snapshot}` : steered,
      })
    } catch {
      if (attempt < MAX_ATTEMPTS) scheduleCompact($, instructions, attempt + 1)
    }
  })
}

export const register: Register = on => {
  on('session.compact', async ($, e, next) => {
    const compacted = await next({ ...e, instructions: steer(e.instructions) })
    // A precompute installs nothing and the compaction that uses its summary
    // dispatches this hook again, so the snapshot is read then, not now.
    if (compacted.skip !== undefined || e.trigger === 'precompute') return compacted

    const snapshot = await readSnapshot($)
    if (!snapshot) return compacted
    const text = withMarker(snapshot, compacted.tokensBefore)
    return { ...compacted, messages: [...compacted.messages, { role: 'user', text, toolUses: [] }] }
  })
}
