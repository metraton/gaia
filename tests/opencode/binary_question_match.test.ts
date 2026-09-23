import { describe, expect, test } from "bun:test"
import {
  matchesHostSignatureQuestionEvent,
  matchesSignatureQuestion,
  readSignatureAnswer,
} from "../../opencode/plugin"

const asked = {
  header: "Aprobacion",
  question: "surface\n\n¿Publico la rama?",
  options: [
    { label: "Approve", description: "Approve this request" },
    { label: "Reject", description: "Reject this request" },
    { label: "Details", description: "Show the details" },
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

describe("matchesSignatureQuestion", () => {
  test("accepts the question re-encoded in the host's key order", () => {
    const reencoded = {
      question: asked.question,
      header: asked.header,
      options: asked.options.map((option) => ({ description: option.description, label: option.label })),
      multiple: false,
      custom: false,
    }
    expect(JSON.stringify([reencoded])).not.toBe(JSON.stringify([asked]))
    expect(matchesSignatureQuestion([reencoded], asked)).toBe(true)
  })

  test("accepts unrelated host keys", () => {
    expect(matchesSignatureQuestion([{ ...asked, tool: "question" }], asked)).toBe(true)
  })

  test("refuses a question whose consent-bearing fields differ", () => {
    expect(matchesSignatureQuestion([{ ...asked, question: `${asked.question} ` }], asked)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, header: "Other" }], asked)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, multiple: undefined }], asked)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, multiple: true }], asked)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, custom: true }], asked)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, custom: undefined }], asked)).toBe(false)
    expect(matchesSignatureQuestion(
      [{ ...asked, options: [{ ...asked.options[0], label: "Approve once" }, ...asked.options.slice(1)] }], asked,
    )).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, options: [...asked.options, asked.options[0]] }], asked)).toBe(false)
    expect(matchesSignatureQuestion(
      [{ ...asked, options: [asked.options[1], asked.options[0], asked.options[2]] }], asked,
    )).toBe(false)
  })

  test("correlates the literal event 60148 shape only after Gaia wrote the question call", () => {
    expect(matchesSignatureQuestion(event60148Questions, asked)).toBe(false)
    expect(matchesHostSignatureQuestionEvent(event60148Questions, asked, false)).toBe(false)
    expect(matchesHostSignatureQuestionEvent(event60148Questions, asked, true)).toBe(true)
  })

  test("event normalization refuses malformed, additional, reordered, custom, and drifted questions", () => {
    const match = (questions: unknown) => matchesHostSignatureQuestionEvent(questions, asked, true)
    expect(match([{ ...event60148Questions[0], multiple: undefined }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: true }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: "false" }])).toBe(false)
    expect(match([{ ...event60148Questions[0], options: [asked.options[1], asked.options[0], asked.options[2]] }]))
      .toBe(false)
    expect(match([{ ...event60148Questions[0], question: `${asked.question} ` }])).toBe(false)
    expect(match([...event60148Questions, event60148Questions[0]])).toBe(false)
    expect(match(event60148Questions[0])).toBe(false)
  })

  test("refuses anything but exactly one question object", () => {
    expect(matchesSignatureQuestion([asked, asked], asked)).toBe(false)
    expect(matchesSignatureQuestion([], asked)).toBe(false)
    expect(matchesSignatureQuestion(asked, asked)).toBe(false)
    expect(matchesSignatureQuestion([null], asked)).toBe(false)
    expect(matchesSignatureQuestion(undefined, asked)).toBe(false)
  })
})

describe("readSignatureAnswer", () => {
  const request = { question: asked } as any

  test("maps exactly one option label to its answer by position", () => {
    expect(readSignatureAnswer(request, [["Approve"]])).toBe("once")
    expect(readSignatureAnswer(request, [["Reject"]])).toBe("reject")
    expect(readSignatureAnswer(request, [["Details"]])).toBe("details")
  })

  test("decides nothing for typed text, several labels, or no answer", () => {
    expect(readSignatureAnswer(request, [["yes"]])).toBeUndefined()
    expect(readSignatureAnswer(request, [["approve"]])).toBeUndefined()
    expect(readSignatureAnswer(request, [["Approve", "Reject"]])).toBeUndefined()
    expect(readSignatureAnswer(request, [[]])).toBeUndefined()
    expect(readSignatureAnswer(request, [["Approve"], ["Approve"]])).toBeUndefined()
    expect(readSignatureAnswer(request, undefined)).toBeUndefined()
  })
})
