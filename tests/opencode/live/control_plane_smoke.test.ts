import { describe, expect, test } from "bun:test"
import { matchesBinaryQuestion } from "../../../opencode/plugin"
import { EventCursor, selectedBinaryLabel, signedQuestion } from "./control_plane_smoke"

const approvalID = `P-${"a".repeat(32)}`
const correlationID = "correlation-1"
const question = {
  header: "Gaia approval",
  question: `surface\n\nDECISION: ${approvalID} / ${correlationID}`,
  options: [
    { label: `Approve once [${approvalID}]`, description: "Activate this exact request once" },
    { label: `Reject [${approvalID}]`, description: "Create no grant and perform no operation" },
  ],
  multiple: false as const,
  custom: false as const,
}

describe("signedQuestion", () => {
  test("accepts only false or omitted custom in the host event copy", () => {
    expect(signedQuestion([question])).toEqual({
      approvalID,
      correlationID,
      approveLabel: question.options[0].label,
      rejectLabel: question.options[1].label,
    })
    const { custom: _, ...withoutCustom } = question
    expect(signedQuestion([withoutCustom])).toEqual(signedQuestion([question]))

    for (const custom of [true, "false", null, 0]) {
      expect(signedQuestion([{ ...question, custom }])).toBeUndefined()
    }
  })

  test("keeps the plugin-emitted question strict at creation", () => {
    const { custom: _, ...withoutCustom } = question
    const { multiple: __, ...withoutMultiple } = question
    expect(matchesBinaryQuestion([question], question)).toBe(true)
    expect(matchesBinaryQuestion([withoutCustom], question)).toBe(false)
    expect(matchesBinaryQuestion([withoutMultiple], question)).toBe(false)
  })

  test("rejects malformed, additional, and consent-bearing drift", () => {
    expect(signedQuestion(question)).toBeUndefined()
    expect(signedQuestion([])).toBeUndefined()
    expect(signedQuestion([null])).toBeUndefined()
    expect(signedQuestion([question, question])).toBeUndefined()
    const { multiple: _, ...withoutMultiple } = question
    for (const multiple of [undefined, true, "false", null, 0]) {
      const candidate = multiple === undefined ? withoutMultiple : { ...question, multiple }
      expect(signedQuestion([candidate])).toBeUndefined()
    }
    expect(signedQuestion([{ ...question, question: `${question.question} ` }])).toBeUndefined()
    expect(signedQuestion([{ ...question, options: question.options.toReversed() }])).toBeUndefined()
    expect(signedQuestion([{ ...question, options: [...question.options, question.options[0]] }])).toBeUndefined()
  })

  test("accepts only one exact signed reply label", () => {
    const signed = signedQuestion([question])!
    expect(selectedBinaryLabel([[question.options[0].label]], signed)).toBe(question.options[0].label)
    expect(selectedBinaryLabel([[question.options[1].label]], signed)).toBe(question.options[1].label)
    expect(selectedBinaryLabel([["Approve"]], signed)).toBeUndefined()
    expect(selectedBinaryLabel([[question.options[0].label, question.options[1].label]], signed)).toBeUndefined()
    expect(selectedBinaryLabel([question.options[0].label], signed)).toBeUndefined()
    expect(selectedBinaryLabel(null, signed)).toBeUndefined()
  })
})

describe("EventCursor", () => {
  test("ignores a stale same-session idle captured before the correlated reply", async () => {
    const cursor = new EventCursor()
    cursor.push({ type: "question.asked", properties: { id: "question-1", sessionID: "specialist-1" } }, "t0")
    cursor.push({ type: "session.idle", properties: { sessionID: "specialist-1" } }, "t1")
    cursor.push({
      type: "question.replied",
      properties: { requestID: "question-1", sessionID: "specialist-1" },
    }, "t2")

    const asked = await cursor.waitFor((event) => event.type === "question.asked", 50, "asked")
    const replied = await cursor.waitFor(
      (event) => event.type === "question.replied"
        && event.properties.requestID === "question-1"
        && event.properties.sessionID === "specialist-1",
      50,
      "exact reply",
    )
    const idlePromise = cursor.waitFor(
      (event) => event.type === "session.idle" && event.properties.sessionID === "specialist-1",
      50,
      "post-reply idle",
    )
    cursor.push({ type: "session.idle", properties: { sessionID: "specialist-1" } }, "t3")
    const idle = await idlePromise

    expect([asked.index, replied.index, idle.index]).toEqual([0, 2, 3])
    expect(asked.index).toBeLessThan(replied.index)
    expect(replied.index).toBeLessThan(idle.index)
  })

  test("consumes stale events and preserves exact request and session correlation", async () => {
    const cursor = new EventCursor()
    cursor.push({
      type: "question.replied",
      properties: { requestID: "question-1", sessionID: "specialist-1" },
    }, "stale-before-asked")
    cursor.push({
      type: "question.asked",
      properties: { id: "question-1", sessionID: "specialist-1", questions: [question] },
    }, "asked")
    cursor.push({
      type: "question.replied",
      properties: { requestID: "question-2", sessionID: "specialist-1" },
    }, "drifted-request")
    cursor.push({
      type: "question.replied",
      properties: { requestID: "question-1", sessionID: "specialist-2" },
    }, "drifted-session")
    cursor.push({
      type: "question.replied",
      properties: {
        requestID: "question-1",
        sessionID: "specialist-1",
        answers: [[question.options[0].label]],
      },
    }, "exact-reply")
    cursor.push({ type: "session.idle", properties: { sessionID: "specialist-2" } }, "drifted-idle")
    cursor.push({ type: "session.idle", properties: { sessionID: "specialist-1" } }, "exact-idle")

    const asked = await cursor.waitFor(
      (event) => event.type === "question.asked" && signedQuestion(event.properties.questions) !== undefined,
      50,
      "signed question",
    )
    const replied = await cursor.waitFor(
      (event) => event.type === "question.replied"
        && event.properties.requestID === asked.event.properties.id
        && event.properties.sessionID === asked.event.properties.sessionID,
      50,
      "correlated reply",
    )
    const idle = await cursor.waitFor(
      (event) => event.type === "session.idle"
        && event.properties.sessionID === asked.event.properties.sessionID,
      50,
      "correlated idle",
    )

    expect([asked.index, replied.index, idle.index]).toEqual([1, 4, 6])
    expect(replied.event.properties.answers).toEqual([[question.options[0].label]])
  })
})
