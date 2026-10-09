import { expect, test } from 'claude-code/testing'

import { STEERING } from './register'

test('a compaction is told Gaia\'s instructions after the user\'s own', async ($, on) => {
  let told: string | undefined
  on('session.compact', (_$, e) => {
    told = e.instructions
    return { messages: [{ role: 'assistant', text: 'summary', toolUses: [] }] }
  })

  const transcript = { role: 'user', text: 'work so far', toolUses: [] }
  await $.session.compact({ instructions: 'keep the plan', messages: [transcript] } as never)

  expect(told).toBe(`keep the plan\n\n${STEERING}`)
})
