import { describe, expect, test } from "bun:test"
import { matchesBinaryQuestion } from "../../opencode/plugin"

const asked = {
  header: "Gaia approval",
  question: "surface\n\nDECISION: P-1 / C-1",
  options: [
    { label: "Approve once [P-1]", description: "Activate this exact request once" },
    { label: "Reject [P-1]", description: "Create no grant and perform no operation" },
  ],
  multiple: false as const,
}

describe("matchesBinaryQuestion", () => {
  test("accepts the question re-encoded in the host's key order", () => {
    const reencoded = {
      question: asked.question,
      header: asked.header,
      options: asked.options.map((option) => ({ description: option.description, label: option.label })),
      multiple: false,
    }
    expect(JSON.stringify([reencoded])).not.toBe(JSON.stringify([asked]))
    expect(matchesBinaryQuestion([reencoded], asked)).toBe(true)
  })

  test("accepts keys the host adds and a multiple the host omits", () => {
    const { multiple, ...withoutMultiple } = asked
    expect(matchesBinaryQuestion([{ ...asked, custom: false, tool: "question" }], asked)).toBe(true)
    expect(matchesBinaryQuestion([withoutMultiple], asked)).toBe(true)
  })

  test("refuses a question whose consent-bearing fields differ", () => {
    expect(matchesBinaryQuestion([{ ...asked, question: `${asked.question} ` }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, header: "Other" }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, multiple: true }], asked)).toBe(false)
    expect(matchesBinaryQuestion(
      [{ ...asked, options: [{ ...asked.options[0], label: "Approve" }, asked.options[1]] }], asked,
    )).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, options: [...asked.options, asked.options[0]] }], asked)).toBe(false)
    expect(matchesBinaryQuestion([{ ...asked, options: [asked.options[1], asked.options[0]] }], asked)).toBe(false)
  })

  test("refuses anything but exactly one question object", () => {
    expect(matchesBinaryQuestion([asked, asked], asked)).toBe(false)
    expect(matchesBinaryQuestion([], asked)).toBe(false)
    expect(matchesBinaryQuestion(asked, asked)).toBe(false)
    expect(matchesBinaryQuestion([null], asked)).toBe(false)
    expect(matchesBinaryQuestion(undefined, asked)).toBe(false)
  })
})
