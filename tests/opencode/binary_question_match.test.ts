import { describe, expect, test } from "bun:test"
import {
  matchesHostSignatureQuestionEvent,
  matchesSignatureQuestion,
  readSignatureAnswer,
} from "../../opencode/plugin"

// One D37 signature question: one line, the machine format, Gaia's three options.
const asked = {
  header: "Firma 1/1",
  question: "[ GAIA-SECURITY ] [ AGENT-REQUEST ] [ developer ] [ COMMAND ] [ git push origin main ]",
  options: [
    { label: "Approve", description: "Approve this request" },
    { label: "Reject", description: "Reject this request" },
    { label: "Details", description: "Show the details" },
  ],
  multiple: false as const,
  custom: false as const,
}
const expected = [asked]

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
    expect(matchesSignatureQuestion([reencoded], expected)).toBe(true)
  })

  test("accepts unrelated host keys", () => {
    expect(matchesSignatureQuestion([{ ...asked, tool: "question" }], expected)).toBe(true)
  })

  test("refuses a question whose consent-bearing fields differ", () => {
    expect(matchesSignatureQuestion([{ ...asked, question: `${asked.question} ` }], expected)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, header: "Other" }], expected)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, multiple: undefined }], expected)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, multiple: true }], expected)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, custom: true }], expected)).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, custom: undefined }], expected)).toBe(false)
    expect(matchesSignatureQuestion(
      [{ ...asked, options: [{ ...asked.options[0], label: "Approve once" }, ...asked.options.slice(1)] }], expected,
    )).toBe(false)
    expect(matchesSignatureQuestion([{ ...asked, options: [...asked.options, asked.options[0]] }], expected)).toBe(false)
    expect(matchesSignatureQuestion(
      [{ ...asked, options: [asked.options[1], asked.options[0], asked.options[2]] }], expected,
    )).toBe(false)
  })

  test("correlates the literal event 60148 shape only after Gaia wrote the question call", () => {
    expect(matchesSignatureQuestion(event60148Questions, expected)).toBe(false)
    expect(matchesHostSignatureQuestionEvent(event60148Questions, expected, false)).toBe(false)
    expect(matchesHostSignatureQuestionEvent(event60148Questions, expected, true)).toBe(true)
  })

  test("event normalization refuses malformed, additional, reordered, custom, and drifted questions", () => {
    const match = (questions: unknown) => matchesHostSignatureQuestionEvent(questions, expected, true)
    expect(match([{ ...event60148Questions[0], multiple: undefined }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: true }])).toBe(false)
    expect(match([{ ...event60148Questions[0], custom: "false" }])).toBe(false)
    expect(match([{ ...event60148Questions[0], options: [asked.options[1], asked.options[0], asked.options[2]] }]))
      .toBe(false)
    expect(match([{ ...event60148Questions[0], question: `${asked.question} ` }])).toBe(false)
    expect(match([...event60148Questions, event60148Questions[0]])).toBe(false)
    expect(match(event60148Questions[0])).toBe(false)
  })

  test("refuses anything but exactly the questions Gaia wrote, in order", () => {
    const second = { ...asked, header: "Firma 1/2", question: asked.question.replace("git push origin main", "docker push app:1") }
    expect(matchesSignatureQuestion([asked, second], [asked, second])).toBe(true)
    expect(matchesSignatureQuestion([second, asked], [asked, second])).toBe(false)
    expect(matchesSignatureQuestion([asked, asked], expected)).toBe(false)
    expect(matchesSignatureQuestion([], expected)).toBe(false)
    expect(matchesSignatureQuestion(asked, expected)).toBe(false)
    expect(matchesSignatureQuestion([null], expected)).toBe(false)
    expect(matchesSignatureQuestion(undefined, expected)).toBe(false)
  })
})

describe("readSignatureAnswer", () => {
  const request = { questions: [asked] } as any
  const twoCommands = { questions: [asked, asked] } as any

  test("maps exactly one option label to its answer by position", () => {
    expect(readSignatureAnswer(request, [["Approve"]])).toBe("once")
    expect(readSignatureAnswer(request, [["Reject"]])).toBe("reject")
    expect(readSignatureAnswer(request, [["Details"]])).toBe("details")
  })

  test("folds one answer per command: any Reject rejects, every Approve approves, else Details (D33)", () => {
    expect(readSignatureAnswer(twoCommands, [["Approve"], ["Approve"]])).toBe("once")
    expect(readSignatureAnswer(twoCommands, [["Approve"], ["Reject"]])).toBe("reject")
    expect(readSignatureAnswer(twoCommands, [["Details"], ["Reject"]])).toBe("reject")
    expect(readSignatureAnswer(twoCommands, [["Approve"], ["Details"]])).toBe("details")
  })

  test("decides nothing for typed text, several labels, or no answer", () => {
    expect(readSignatureAnswer(request, [["yes"]])).toBeUndefined()
    expect(readSignatureAnswer(request, [["approve"]])).toBeUndefined()
    expect(readSignatureAnswer(request, [["Approve", "Reject"]])).toBeUndefined()
    expect(readSignatureAnswer(request, [[]])).toBeUndefined()
    expect(readSignatureAnswer(request, [["Approve"], ["Approve"]])).toBeUndefined()
    expect(readSignatureAnswer(twoCommands, [["Approve"], ["typed"]])).toBeUndefined()
    expect(readSignatureAnswer(request, undefined)).toBeUndefined()
  })
})
