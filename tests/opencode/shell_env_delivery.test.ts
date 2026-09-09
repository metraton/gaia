import { expect, test } from "bun:test"
import { ShellEnvDelivery } from "../../opencode/shell-env"

/** Supply host-owned fixture values; this suite tests identity delivery, not approval policy. */
function fixture() {
  const delivery = new ShellEnvDelivery()
  const args = { command: "GHX_ACCOUNT=metraton ghx pr list", workdir: "/fixture" }
  const identity = { sessionID: "child", callID: "call", agent: "gaia-system", attestation: "host-token", cwd: "/fixture" }
  delivery.remember({ ...identity, args })
  return { delivery, args, identity }
}

test("identity delivered once without changing exact command or account", () => {
  const { delivery, args, identity } = fixture()
  const original = JSON.stringify(args)
  expect(delivery.take(identity)).toBe("gaia-system")
  expect(JSON.stringify(args)).toBe(original)
  expect(() => delivery.take(identity)).toThrow("correlation missing or changed")
})

test("each changed binding fails closed", () => {
  for (const changes of [
    { sessionID: "other" }, { callID: "other" }, { agent: "gaia-orchestrator" },
    { attestation: "forged" }, { cwd: "/other" },
  ]) {
    const { delivery, identity } = fixture()
    expect(() => delivery.take({ ...identity, ...changes })).toThrow("correlation missing or changed")
  }
})

test("post-policy argument mutation cannot acquire identity", () => {
  const { delivery, args, identity } = fixture()
  args.command = "export GAIA_DISPATCH_AGENT=gaia-orchestrator; ghx pr list"
  expect(() => delivery.take(identity)).toThrow("correlation missing or changed")
})

test("no duplicate delivery record or missing attestation", () => {
  const { delivery, args, identity } = fixture()
  expect(() => delivery.remember({ ...identity, args })).toThrow("already pending")
  expect(() => new ShellEnvDelivery().remember({ ...identity, args, attestation: "" })).toThrow("authenticated correlation")
})

test("after, session failure and disposal invalidate identity", () => {
  for (const clear of [
    (d: ShellEnvDelivery) => d.forget("child", "call"),
    (d: ShellEnvDelivery) => d.clearSession("child"),
    (d: ShellEnvDelivery) => d.clear(),
  ]) {
    const { delivery, identity } = fixture()
    clear(delivery)
    expect(() => delivery.take(identity)).toThrow("correlation missing or changed")
  }
})

test("plugin instances do not share identity or permission state", () => {
  const { identity } = fixture()
  expect(() => new ShellEnvDelivery().take(identity)).toThrow("correlation missing or changed")
})
