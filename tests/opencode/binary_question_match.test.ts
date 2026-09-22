import { describe, expect, test } from "bun:test"
import {
  matchesBinaryQuestion,
  matchesHostBinaryQuestionCall,
  matchesHostBinaryQuestionEvent,
} from "../../opencode/plugin"

const asked = {
  header: "Gaia approval",
  question: "surface\n\nDECISION: P-1 / C-1",
  options: [
    { label: "Approve once [P-1]", description: "Activate this exact request once" },
    { label: "Reject [P-1]", description: "Create no grant and perform no operation" },
  ],
  multiple: false as const,
  custom: false as const,
}

const event60148Questions = [{
  question: asked.question,
  header: asked.header,
  options: asked.options.map((option) => ({ ...option })),
  multiple: false,
}]

describe("matchesBinaryQuestion", () => {
  test("accepts the question re-encoded in the host's key order", () => {
    const reencoded = {
      question: asked.question,
      header: asked.header,
      options: asked.options.map((option) => ({ description: option.description, label: option.label })),
      multiple: false,
      custom: false,
    }
    expect(JSON.stringify([reencoded])).not.toBe(JSON.stringify([asked]))
    expect(matchesBinaryQuestion([reencoded], asked)).toBe(true)
  })

  test("accepts unrelated host keys", () => {
    expect(matchesBinaryQuestion([{ ...asked, tool: "question" }], asked)).toBe(true)
  })

  test("refuses a question whose consent-bearing fields differ", () => {
    expect(matchesBinaryQuestion([{ ...asked, question: `${asked.question} ` }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, header: "Other" }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, multiple: undefined }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, multiple: true }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, custom: true }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, custom: undefined }], asked)).toBe(false)
    expect(matchesBinaryQuestion(
      [{ ...asked, options: [{ ...asked.options[0], label: "Approve" }, asked.options[1]] }], asked,
    )).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, options: [...asked.options, asked.options[0]] }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, options: [asked.options[1], asked.options[0]] }], asked)).toBe(false)
  })

  test("correlates the literal event 60148 shape only after the exact call was validated", () => {
    expect(matchesBinaryQuestion(event60148Questions, asked)).toBe(false)
    expect(matchesHostBinaryQuestionEvent(event60148Questions, asked, false)).toBe(false)
    expect(matchesHostBinaryQuestionEvent(event60148Questions, asked, true)).toBe(true)
  })

  test("event normalization refuses malformed, additional, reordered, custom, and drifted questions", () => {
    const match = (questions: unknown) => matchesHostBinaryQuestionEvent(questions, asked, true)
    expect(match([{ ...event60148Questions[0], multiple: undefined }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: true }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: "false" }])).toBe(false)
    expect(match([{ ...event60148Questions[0], options: [asked.options[1], asked.options[0]] }])).toBe(false)
    expect(match([{ ...event60148Questions[0], question: `${asked.question} ` }])).toBe(false)
    expect(match([...event60148Questions, event60148Questions[0]])).toBe(false)
    expect(match(event60148Questions[0])).toBe(false)
  })

  test("refuses anything but exactly one question object", () => {
    expect(matchesBinaryQuestion([asked, asked], asked)).toBe(false)
    expect(matchesBinaryQuestion([], asked)).toBe(false)
    expect(matchesBinaryQuestion(asked, asked)).toBe(false)
    expect(matchesBinaryQuestion([null], asked)).toBe(false)
    expect(matchesBinaryQuestion(undefined, asked)).toBe(false)
  })
})

describe("matchesHostBinaryQuestionCall", () => {
  test("accepts the measured pre-hook shape with optional custom omitted", () => {
    expect(matchesHostBinaryQuestionCall(event60148Questions, asked)).toBe(true)
    expect(matchesHostBinaryQuestionCall([asked], asked)).toBe(true)
  })

  test("does not widen any consent-bearing field or the single-question boundary", () => {
    const match = (questions: unknown) => matchesHostBinaryQuestionCall(questions, asked)
    expect(match([{ ...event60148Questions[0], custom: true }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: "false" }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: undefined }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: null }])).toBe(false)
    expect(match([{ ...event60148Questions[0], multiple: undefined }])).toBe(false)
    expect(match([{ ...event60148Questions[0], multiple: true }])).toBe(false)
    expect(match([{ ...event60148Questions[0], header: "Other" }])).toBe(false)
    expect(match([{ ...event60148Questions[0], question: `${asked.question} ` }])).toBe(false)
    expect(match([{
      ...event60148Questions[0],
      options: [{ ...asked.options[0], label: "Approve" }, asked.options[1]],
    }])).toBe(false)
    expect(match([{
      ...event60148Questions[0],
      options: [{ ...asked.options[0], description: "Other" }, asked.options[1]],
    }])).toBe(false)
    expect(match([{ ...event60148Questions[0], options: [asked.options[1], asked.options[0]] }])).toBe(false)
    expect(match([...event60148Questions, event60148Questions[0]])).toBe(false)
    expect(match(event60148Questions[0])).toBe(false)
  })
})
