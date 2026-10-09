import { expect, test } from 'claude-code/testing'

import { STEERING } from './register'

const SUMMARY = { role: 'assistant', text: 'summary', toolUses: [] }
const TRANSCRIPT = { role: 'user', text: 'work so far', toolUses: [] }

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
