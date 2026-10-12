import { expect, mock, test } from 'claude-code/testing'

import { COMPACT_TOOL, MAX_ATTEMPTS, STEERING } from './register'

const SUMMARY = { role: 'assistant', text: 'summary', toolUses: [] }
const TRANSCRIPT = { role: 'user', text: 'work so far', toolUses: [] }
const TURN = { answer: 'done', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' }
const SESSION_ID = 'ses-893'

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

test('a requested compaction starts only once the requesting turn has ended', async ($, on) => {
  const clock = mock.clock(on)
  const compactions = countCompactions(on)
  const tool = await startSession($, on)

  const reply = await $.tool.call({ tool } as never)
  await clock.settle()
  expect(compactions.count).toBe(0)
  expect(String(reply.result)).toContain(`gaia session resume-point set --session-id ${SESSION_ID}`)

  await $.turn.complete(TURN as never)
  await clock.settle()
  expect(compactions.count).toBe(1)

  await $.turn.complete(TURN as never)
  await clock.settle()
  expect(compactions.count).toBe(1)
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

test('a compaction the host rejects is retried a bounded number of times', async ($, on) => {
  const clock = mock.clock(on)
  const compactions = countCompactions(on, () => {
    throw new Error('a turn is running')
  })
  const tool = await startSession($, on)

  await $.tool.call({ tool } as never)
  await $.turn.complete(TURN as never)
  await clock.advance(60_000)

  expect(compactions.count).toBe(MAX_ATTEMPTS)
})
