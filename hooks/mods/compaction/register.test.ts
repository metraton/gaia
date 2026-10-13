import { expect, mock, test } from 'claude-code/testing'

import {
  COMPACT_TOOL,
  MAX_ATTEMPTS,
  REQUEST_EXPIRY_MS,
  RETRY_DELAY_MS,
  STEERING,
  afterAttempt,
  isExpired,
} from './register'

const SUMMARY = { role: 'assistant', text: 'summary', toolUses: [] }
const TRANSCRIPT = { role: 'user', text: 'work so far', toolUses: [] }
const TURN = { answer: 'done', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' }
const SESSION_ID = 'ses-893'

// The host's own refusals of $.session.compact().
const TURN_RUNNING = 'a turn is running (t1)'
const SWITCHED_OFF = 'compaction is switched off (DISABLE_COMPACT)'
const TURN_HELD = 'the turn is held by another client (turnHeld)'

async function startSession($, on): Promise<string> {
  const registered: string[] = []
  on('tool.register', (_$, e) => {
    registered.push(`mcp__gaia__${e.name}`)
    return { value: { tool: registered.at(-1) } }
  })
  on('session.id', () => ({ value: SESSION_ID }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  await $.session.start({ cwd: '/work', surface: null, isInteractive: true })
  expect(registered).toEqual([`mcp__gaia__${COMPACT_TOOL}`])
  return registered[0]
}

function countCompactions(on, outcome: () => object = () => ({ messages: [SUMMARY] })): { count: number } {
  const seen = { count: 0 }
  on('session.compact', () => {
    seen.count += 1
    return outcome()
  })
  return seen
}

function watchReports(on): { toasts: string[]; context: string[][] } {
  const seen = { toasts: [] as string[], context: [] as string[][] }
  on('ui.toast', (_$, e) => {
    seen.toasts.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', (_$, e) => {
    seen.context.push([...(e.context ?? [])])
    return { text: e.text, context: e.context }
  })
  return seen
}

test('a compaction is told Gaia\'s instructions after the user\'s own', async ($, on) => {
  let told: string | undefined
  on('session.compact', (_$, e) => {
    told = e.instructions
    return { messages: [SUMMARY] }
  })

  await $.session.compact({ instructions: 'keep the plan', messages: [TRANSCRIPT] } as never)

  expect(told).toBe(`keep the plan\n\n${STEERING}`)
})

test('a compaction keeps exactly what core installed: the mod appends nothing', async ($, on) => {
  on('session.compact', () => ({ messages: [SUMMARY], tokensBefore: 158950 }))
  on('process.run', () => ({ exitCode: 0, stdout: '## Session Snapshot\nstate\n', stderr: '' }) as never)

  const compacted = await $.session.compact({ instructions: 'x', messages: [TRANSCRIPT] } as never)

  expect(compacted.messages).toHaveLength(1)
})

test('the tool reply says the compaction is scheduled and that a refusal will be reported', async ($, on) => {
  mock.clock(on)
  const tool = await startSession($, on)

  const reply = String((await $.tool.call({ tool } as never)).result)

  expect(reply).toContain('scheduled')
  expect(reply).toContain('reported')
  expect(reply).toContain(`gaia session resume-point set --session-id ${SESSION_ID}`)
})

test('a requested compaction starts only once the requesting turn has ended', async ($, on) => {
  const clock = mock.clock(on)
  const compactions = countCompactions(on)
  const reports = watchReports(on)
  const tool = await startSession($, on)

  await $.tool.call({ tool } as never)
  await clock.settle()
  expect(compactions.count).toBe(0)

  await $.turn.complete(TURN as never)
  await clock.settle()
  expect(compactions.count).toBe(1)

  await $.turn.complete(TURN as never)
  await clock.settle()
  expect(compactions.count).toBe(1)
  expect(reports.toasts).toEqual([])
})

test('a turn that ends with no request starts no compaction', async ($, on) => {
  const clock = mock.clock(on)
  const compactions = countCompactions(on)
  await startSession($, on)

  await $.turn.complete(TURN as never)
  await clock.advance(60_000)

  expect(compactions.count).toBe(0)
})

test('a subagent\'s turn ending does not start the main session\'s compaction', async ($, on) => {
  const clock = mock.clock(on)
  const compactions = countCompactions(on)
  const tool = await startSession($, on)

  await $.tool.call({ tool } as never)
  await $.turn.complete({ ...TURN, agentId: 'sub-1' } as never)
  await clock.advance(60_000)
  expect(compactions.count).toBe(0)

  await $.turn.complete(TURN as never)
  await clock.settle()
  expect(compactions.count).toBe(1)
})

test('a refused compaction is reported once to the user and once to the orchestrator, naming /compact', async ($, on) => {
  const clock = mock.clock(on)
  const compactions = countCompactions(on, () => {
    throw new Error(SWITCHED_OFF)
  })
  const reports = watchReports(on)
  const tool = await startSession($, on)

  await $.tool.call({ tool } as never)
  await $.turn.complete(TURN as never)
  await clock.advance(120_000)
  await $.turn.complete(TURN as never)
  await clock.advance(120_000)

  expect(compactions.count).toBe(1)
  expect(reports.toasts).toHaveLength(1)
  expect(reports.toasts[0]).toContain('/compact')

  await $.prompt.submit({ text: 'next', asUser: true } as never)
  await $.prompt.submit({ text: 'later', asUser: true } as never)
  expect(reports.context[0].join('\n')).toContain('did not start')
  expect(reports.context[0].join('\n')).toContain('/compact')
  expect(reports.context[1]).toEqual([])
})

test('only a running turn is retried, with growing waits, then left for the next turn end', () => {
  expect(afterAttempt(new Error(TURN_RUNNING), 1)).toEqual({ kind: 'retry', waitMs: RETRY_DELAY_MS })
  expect(afterAttempt(new Error(TURN_RUNNING), 2)).toEqual({ kind: 'retry', waitMs: RETRY_DELAY_MS * 2 })
  expect(afterAttempt(new Error(TURN_RUNNING), MAX_ATTEMPTS)).toEqual({ kind: 'next-turn-end' })
})

test('DISABLE_COMPACT, turnHeld and a vetoing hook are final, with the host\'s reason kept', () => {
  expect(afterAttempt(new Error(SWITCHED_OFF), 1)).toEqual({ kind: 'failed', reason: SWITCHED_OFF })
  expect(afterAttempt(new Error(TURN_HELD), 1)).toEqual({ kind: 'failed', reason: TURN_HELD })
  expect(afterAttempt({ skip: 'policy' }, 1)).toEqual({ kind: 'failed', reason: 'a hook vetoed it (policy)' })
  expect(afterAttempt({}, 1)).toEqual({ kind: 'started' })
})

test('a request older than REQUEST_EXPIRY_MS is expired', () => {
  expect(isExpired(0, REQUEST_EXPIRY_MS)).toBe(false)
  expect(isExpired(0, REQUEST_EXPIRY_MS + 1)).toBe(true)
})
