/** Load and initialize the packaged plugin in a real Bun process.
 *
 * This is deliberately closer to OpenCode's loader than a direct helper import:
 * the module is dynamically imported, its loader-compatible export is selected,
 * and the plugin factory is invoked with the host-shaped context.
 */

const modulePath = new URL("../../opencode/plugin.ts", import.meta.url).href
const pluginModule = await import(modulePath)

function selectPlugin(module: typeof pluginModule) {
  if (module.default && typeof module.default.server === "function") {
    return module.default.server
  }
  if (typeof module.GaiaOpenCodePlugin === "function") {
    return module.GaiaOpenCodePlugin
  }
  throw new Error("OpenCode loader found no usable Gaia plugin export")
}

const plugin = selectPlugin(pluginModule)

if (process.argv[2] === "import-only") {
  console.log(JSON.stringify({ imported: true }))
  process.exit(0)
}

if (process.argv[2] === "fail-log") {
  await plugin({
    client: { app: { log: async () => { throw new Error("host logger unavailable") } } },
  })
  process.exit(0)
}

if (process.argv[2] === "context-log") {
  const app = {
    marker: "host-app",
    async log(this: { marker: string }, _payload: unknown) {
      if (this.marker !== "host-app") throw new Error("host logger receiver was lost")
    },
  }
  const instance = await plugin({ client: { app } })
  if (!instance || typeof instance["tool.execute.before"] !== "function") {
    throw new Error("OpenCode loader initialized no Gaia tool hook")
  }
  console.log(JSON.stringify({ context: "preserved" }))
  process.exit(0)
}

const instance = await plugin({ client: {} })
if (!instance || typeof instance["tool.execute.before"] !== "function") {
  throw new Error("OpenCode loader initialized no Gaia tool hook")
}

console.log(JSON.stringify({
  event: "gaia-opencode-plugin-live",
  pid: process.pid,
  export: "GaiaOpenCodePlugin",
  hook: "tool.execute.before",
}))
