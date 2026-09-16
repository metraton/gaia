/**
 * The request shapes the installed OpenCode SDK accepts for the session calls
 * the plugin's consent control plane makes.
 *
 * `@opencode-ai/sdk` 1.18.18 (`dist/gen/types.gen.d.ts`) declares
 * `SessionCreateData.body` as `{ parentID?: string; title?: string }` and
 * `SessionPromptAsyncData.body` as `{ agent?: string; noReply?: boolean;
 * system?: string; tools?: Record<string, boolean>; parts: Array<TextPartInput
 * | FilePartInput | AgentPartInput | SubtaskPartInput> }`. The real host
 * rejects a body outside those shapes while the SDK client resolves `{ error }`
 * without throwing, so a stub that accepts anything hides exactly the defect
 * that left every production control session empty. Every driver standing in
 * for the host validates through here and throws on a mismatch.
 */

const PART_TYPES = new Set(["text", "file", "agent", "subtask"])

function describe(value: unknown): string {
  if (Array.isArray(value)) return "array"
  if (value === null) return "null"
  return typeof value
}

function reject(call: string, field: string, expected: string, value: unknown): never {
  throw new Error(`SDK contract: ${call} ${field} must be ${expected}, got ${describe(value)}`)
}

export function assertSessionCreateBody(request: any): void {
  const body = request?.body
  if (typeof body !== "object" || body === null) reject("session.create", "body", "an object", body)
  if (body.parentID !== undefined && typeof body.parentID !== "string") {
    reject("session.create", "body.parentID", "a string", body.parentID)
  }
  if (body.title !== undefined && typeof body.title !== "string") {
    reject("session.create", "body.title", "a string", body.title)
  }
}

export function assertPromptAsyncBody(request: any): void {
  if (typeof request?.path?.id !== "string" || !request.path.id) {
    reject("promptAsync", "path.id", "a non-empty string", request?.path?.id)
  }
  const body = request?.body
  if (typeof body !== "object" || body === null) reject("promptAsync", "body", "an object", body)
  if (body.agent !== undefined && typeof body.agent !== "string") {
    reject("promptAsync", "body.agent", "a string", body.agent)
  }
  if (body.system !== undefined && typeof body.system !== "string") {
    reject("promptAsync", "body.system", "a string", body.system)
  }
  if (body.noReply !== undefined && typeof body.noReply !== "boolean") {
    reject("promptAsync", "body.noReply", "a boolean", body.noReply)
  }
  if (body.tools !== undefined) {
    if (typeof body.tools !== "object" || body.tools === null || Array.isArray(body.tools)) {
      reject("promptAsync", "body.tools", "an object", body.tools)
    }
    for (const [name, enabled] of Object.entries(body.tools)) {
      if (typeof enabled !== "boolean") reject("promptAsync", `body.tools.${name}`, "a boolean", enabled)
    }
  }
  if (!Array.isArray(body.parts) || body.parts.length === 0) {
    reject("promptAsync", "body.parts", "a non-empty array", body.parts)
  }
  body.parts.forEach((part: any, index: number) => {
    if (!PART_TYPES.has(part?.type)) {
      reject("promptAsync", `body.parts[${index}].type`, "text|file|agent|subtask", part?.type)
    }
    if (part.type === "text" && typeof part.text !== "string") {
      reject("promptAsync", `body.parts[${index}].text`, "a string", part.text)
    }
  })
}
