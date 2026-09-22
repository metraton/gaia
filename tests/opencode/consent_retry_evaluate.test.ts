import { createHash } from "node:crypto"
import { describe, expect, test } from "bun:test"
import { evaluateConsentRetry } from "../../opencode/plugin"

// The shape measured on 2026-09-16: the approval's agent_id is the specialist's
// Gaia contract identity, the session's host agent is its role.
const GAIA_AGENT_ID = "a69d869dc02031f54"
const ROLE = "gaia-operator"
const SESSION = "ses_f53ee3ac2ffeLzO3PSaVXUxFYy"
const COMMANDS = [
  "cp /dev/null /home/jorge/.gaia/scratch/oc-lote-probe1.txt",
  "mv /home/jorge/.gaia/scratch/oc-lote-probe1.txt /home/jorge/.gaia/scratch/oc-lote-probe1.moved",
]
const sha256 = (text: string) => createHash("sha256").update(text).digest("hex")

function retry(overrides: Record<string, unknown> = {}) {
  return {
    approvalID: "P-a4e54958238e4428b49e5623ab4f527a",
    sessionID: SESSION,
    callID: "call_fwv2msrC7uZKlZDftj64alRO",
    token: "t",
    role: ROLE,
    surface: { visibleLines: [], metadata: {} },
    agentID: GAIA_AGENT_ID,
    correlationID: "C-1",
    commands: COMMANDS,
    fingerprints: COMMANDS.map(sha256),
    expectedIndex: 0,
    usedCallIDs: new Set(["call_fwv2msrC7uZKlZDftj64alRO"]),
    operation: {
      version: 1,
      kind: "COMMAND_SET",
      commands: COMMANDS,
      fingerprints: COMMANDS.map(sha256),
      requestFingerprint: "request-fingerprint",
      expectedIndex: 0,
    },
    ...overrides,
  } as any
}

const call = { sessionID: SESSION, callID: "call_RPws80NkLnr3VjRiriMk6QSx" }

describe("evaluateConsentRetry", () => {
  test("a byte-identical retry by the presented role yields the proof even though agentID is a Gaia hash", () => {
    const verdict = evaluateConsentRetry(retry(), call, ROLE, "bash", { command: COMMANDS[0] })
    expect(verdict?.refusal).toBeUndefined()
    expect(verdict?.proof).toMatchObject({
      agent_id: GAIA_AGENT_ID,
      retry_call_id: call.callID,
      expected_index: 0,
      command_fingerprint: sha256(COMMANDS[0]),
    })
  })

  test("a call that claims none of the approved commands is unrelated, not refused", () => {
    expect(evaluateConsentRetry(retry(), call, ROLE, "skill", { name: "agent-protocol" })).toBeUndefined()
    expect(evaluateConsentRetry(retry(), call, ROLE, "bash", { command: "gaia approvals show P-1" })).toBeUndefined()
  })

  test("a different role executing the approved bytes is refused by name, expected against received", () => {
    const verdict = evaluateConsentRetry(retry(), call, "developer", "bash", { command: COMMANDS[0] })
    expect(verdict?.refusal).toEqual({ reason: "role_mismatch", expected: ROLE, received: "developer" })
  })

  test("a spent call id is a replay whatever it carries", () => {
    const verdict = evaluateConsentRetry(retry(), { sessionID: SESSION, callID: "call_fwv2msrC7uZKlZDftj64alRO" }, ROLE, "skill", {})
    expect(verdict?.refusal?.reason).toBe("replayed_call_id")
  })

  test("the approved command with whitespace drift claims the retry and is refused on exact bytes", () => {
    const verdict = evaluateConsentRetry(retry(), call, ROLE, "bash", { command: COMMANDS[0] + " " })
    expect(verdict?.refusal).toEqual({
      reason: "fingerprint_mismatch",
      expected: sha256(COMMANDS[0]),
      received: sha256(COMMANDS[0] + " "),
    })
  })

  test("an approved command claimed ahead of its turn is out of order", () => {
    const verdict = evaluateConsentRetry(retry(), call, ROLE, "bash", { command: COMMANDS[1] })
    expect(verdict?.refusal).toEqual({ reason: "out_of_order", expected: "command [0]", received: "command [1]" })
  })

  test("a reactive singular retry produces a typed proof for one identical fresh Bash call", () => {
    const singular = retry({
      operation: {
        version: 1,
        kind: "SCOPE_SEMANTIC_SIGNATURE",
        command: COMMANDS[0],
        commandFingerprint: sha256(COMMANDS[0]),
      },
    })

    const verdict = evaluateConsentRetry(singular, call, ROLE, "bash", { command: COMMANDS[0] })

    expect(verdict?.proof).toMatchObject({
      version: 1,
      kind: "SCOPE_SEMANTIC_SIGNATURE",
      command: COMMANDS[0],
      command_fingerprint: sha256(COMMANDS[0]),
    })
  })

  test("a protected file retry binds the exact canonical target and Edit family", () => {
    const path = "/home/jorge/ws/me/gaia/hooks/example.py"
    const fileRetry = retry({
      operation: {
        version: 1,
        kind: "SCOPE_FILE_PATH",
        canonicalPath: path,
        pathFingerprint: sha256(path),
        toolFamily: ["Write", "Edit"],
      },
    })

    const valid = evaluateConsentRetry(fileRetry, call, ROLE, "edit", { file_path: path })
    const wrongPath = evaluateConsentRetry(fileRetry, call, ROLE, "write", { file_path: `${path}.other` })

    expect(valid?.proof).toMatchObject({
      version: 1,
      kind: "SCOPE_FILE_PATH",
      canonical_path: path,
      path_fingerprint: sha256(path),
      tool_family: "Edit",
    })
    expect(wrongPath?.refusal?.reason).toBe("path_mismatch")
  })

  test("apply_patch maps to Edit only for one exact granted target", () => {
    const path = "/home/jorge/ws/me/gaia/hooks/example.py"
    const fileRetry = retry({
      operation: {
        version: 1,
        kind: "SCOPE_FILE_PATH",
        canonicalPath: path,
        pathFingerprint: sha256(path),
        toolFamily: ["Write", "Edit"],
      },
    })

    const valid = evaluateConsentRetry(fileRetry, call, ROLE, "apply_patch", { file_paths: [path] })
    const extraTarget = evaluateConsentRetry(fileRetry, call, ROLE, "apply_patch", {
      file_paths: [path, `${path}.other`],
    })
    const unrelated = evaluateConsentRetry(fileRetry, call, ROLE, "skill", { name: "agent-protocol" })

    expect(valid?.proof?.tool_family).toBe("Edit")
    expect(extraTarget?.refusal?.reason).toBe("path_mismatch")
    expect(unrelated).toBeUndefined()
  })
})
