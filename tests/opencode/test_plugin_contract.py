"""Static contract checks for the packaged OpenCode plugin."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def test_plugin_learns_identity_from_real_message_events():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert 'event.type === "message.updated"' in source
    assert "agentBySession.set(info.sessionID, info.agent)" in source
    assert "call.agent" not in source


def test_plugin_uses_opencode_default_export_fast_path_contract():
    """Mirrors the install guard's own predicate (_install_helpers.py::
    configure_opencode_plugin): the installed OpenCode loader (decompiled:
    dk()/lk()/pk() in the opencode-ai binary) takes a fast path ONLY when the
    module's default export matches {id, server: <function>} and calls
    ONLY default.server -- its fallback otherwise scans every module export
    and invokes each function/{server:fn}-shaped one as its own plugin entry
    point. plugin.ts carries helper functions, a class, and constants past
    the plugin function itself, so the default export is required to keep
    the loader from invoking all of them; the official docs
    (opencode.ai/docs/plugins/) document only the named-export form, but the
    decompiled loader is ground truth over the docs (measured regression:
    4daa9bd removed this export and broke live loading, restored here)."""
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert "export const GaiaOpenCodePlugin = async" in source
    assert "export default {" in source
    assert "server: GaiaOpenCodePlugin" in source


def test_opencode_loader_takes_the_default_export_fast_path():
    import json
    import subprocess

    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'''
      const module = await import({json.dumps(str(plugin))})
      console.log(JSON.stringify({{
        hasDefault: "default" in module,
        defaultId: module.default?.id,
        defaultServerIsNamedExport: module.default?.server === module.GaiaOpenCodePlugin,
      }}))
    '''
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {
        "hasDefault": True,
        "defaultId": "gaia",
        "defaultServerIsNamedExport": True,
    }


def test_opencode_loader_fallback_throws_on_the_non_function_exports():
    """Reproduces the exact measured incident (handoff 15919, runs
    08e400d8/94a00d8b): with no default export, the decompiled fallback
    (dk()/lk()) throws on the first non-function/non-{server:fn} export it
    finds in Object.values(mod) -- here, the plain-string/array constants
    (PREFERRED_PERMISSION_EVENT et al.) precede GaiaOpenCodePlugin in
    declaration order. This is why the fast path (the default export) is
    required, not merely a function-shaped GaiaOpenCodePlugin export."""
    import json
    import subprocess

    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'''
      const mod = await import({json.dumps(str(plugin))})
      const withoutDefault = {{}}
      for (const [k, v] of Object.entries(mod)) {{
        if (k !== "default") withoutDefault[k] = v
      }}
      function toCallable(v) {{
        if (typeof v === "function") return v
        if (v && typeof v === "object" && "server" in v && typeof v.server === "function") return v.server
        return undefined
      }}
      function dk(mod) {{
        const seen = new Set(), callables = []
        for (const v of Object.values(mod)) {{
          if (seen.has(v)) continue
          seen.add(v)
          const callable = toCallable(v)
          if (!callable) throw new TypeError("Plugin export is not a function")
          callables.push(callable)
        }}
        return callables
      }}
      dk(withoutDefault)
    '''
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True)
    assert result.returncode != 0
    assert "Plugin export is not a function" in result.stderr


def test_opencode_loader_fallback_invokes_every_function_export_not_just_validates():
    """Resolves the open gap this task was dispatched to close: given ONLY
    the function-shaped exports (isolating the question from the
    string/array-constant symptom above), does the fallback merely validate
    each export's shape, or does it CALL each one as its own plugin? This
    reproduces dk()/lk() verbatim against plugin.ts's real function exports
    (GaiaOpenCodePlugin, the three helpers, and the PermissionDecisionRouter
    class). The answer is INVOKE: every one is called with (app, options),
    including the class -- called without `new`, which JS itself rejects
    (Bun/JSC: "Cannot call a class constructor ... without |new|"). That a
    constructor-without-`new` TypeError surfaces here, rather than a
    shape-validation message, is the proof the loader calls rather than
    inspects. This is why direction (a) ("exports need only be
    function-shaped") is insufficient on its own: multiple function exports
    are each treated as a separate plugin, and the default export's fast
    path is the only way to keep dk() from ever running over them."""
    import json
    import subprocess

    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'''
      const mod = await import({json.dumps(str(plugin))})
      const functionsOnly = {{}}
      for (const [k, v] of Object.entries(mod)) {{
        if (k !== "default" && typeof v === "function") functionsOnly[k] = v
      }}
      function toCallable(v) {{
        if (typeof v === "function") return v
        if (v && typeof v === "object" && "server" in v && typeof v.server === "function") return v.server
        return undefined
      }}
      function dk(mod) {{
        const seen = new Set(), callables = []
        for (const v of Object.values(mod)) {{
          if (seen.has(v)) continue
          seen.add(v)
          const callable = toCallable(v)
          if (!callable) throw new TypeError("Plugin export is not a function")
          callables.push(callable)
        }}
        return callables
      }}
      const invoked = []
      for (const fn of dk(functionsOnly)) {{
        try {{ await fn({{}}, {{}}); invoked.push({{ ok: true }}) }}
        catch (error) {{ invoked.push({{ ok: false, error: String(error) }}) }}
      }}
      console.log(JSON.stringify({{
        exportCount: Object.keys(functionsOnly).length,
        invokedCount: invoked.length,
        anyConstructorRejection: invoked.some((r) => !r.ok && r.error.includes("without |new|")),
      }}))
    '''
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["exportCount"] > 1
    assert payload["invokedCount"] == payload["exportCount"]
    assert payload["anyConstructorRejection"] is True


def test_plugin_has_a_positive_process_liveness_signal():
    source = (PACKAGE_ROOT / "opencode" / "plugin.ts").read_text()

    assert 'const LIVENESS_PREFIX = "[gaia-opencode:liveness]"' in source
    assert "process.pid" in source
    assert "loaded_at" in source
    assert "host log failed" in source


def test_orchestrator_bash_is_restricted_to_gaia_cli():
    import json

    policy = json.loads((PACKAGE_ROOT / "opencode" / "agent-policy.json").read_text())

    assert policy["gaia-orchestrator"]["permission"]["bash"] == {
        "*": "deny",
        "gaia *": "allow",
    }


def test_plugin_preserves_bash_failure_signals_for_post_tool_policy():
    import json
    import subprocess
    plugin = PACKAGE_ROOT / "opencode" / "plugin.ts"
    script = f'import {{ toolResult }} from {json.dumps(str(plugin))}; console.log(JSON.stringify(toolResult({{output:"", metadata:{{exitCode:9}}}})))'
    result = subprocess.run(["bun", "-e", script], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)["exit_code"] == 9
