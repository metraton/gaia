"""Read-only: what each Gaia channel on this machine declares, registers, installs and runs (``read``), and the host steps to a version (``plan``)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "@jaguilar87/gaia"
# Claude Code applies settings in this order: the first scope naming a key decides it.
SCOPES = ("local", "project", "user")
_VERSION_REF = re.compile(r"v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)")
_CACHE_ENTRY = re.compile(r"/plugins/cache/([^/]+)/gaia/([^/]+)(?:/|$)", re.IGNORECASE)
_PLAIN_ARG = re.compile(r"[\w@%+=:,./#~-]+")
_OPENCODE_SUFFIX = "/opencode/plugin.ts"


def claude_plugins_dir(default: "Path | None" = None) -> Path:
    """Claude Code's plugins root: ``CLAUDE_CODE_PLUGIN_CACHE_DIR`` when set, else *default*, else ``~/.claude/plugins``."""
    moved = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR", "").strip()
    if moved:
        return Path(moved).expanduser()
    return Path(default) if default is not None else Path.home() / ".claude" / "plugins"


@dataclass(frozen=True)
class Machine:
    """Where this machine keeps each record the report reads."""

    workspace: Path
    user_settings: Path
    plugins_dir: Path
    opencode_global: Path
    npm_prefix: "Path | None"
    path_gaia: "Path | None"
    plugin_root: "Path | None"
    windows: bool
    db_path: "Path | None" = None

    @classmethod
    def current(cls, workspace: Path, user_settings: Path, plugins_dir: Path) -> "Machine":
        """This machine, with ``CLAUDE_CODE_PLUGIN_CACHE_DIR`` moving the plugins root when set."""
        from cli.install import _npm_config_prefix_posix  # noqa: PLC0415
        from gaia.paths.resolver import db_path  # noqa: PLC0415

        windows = sys.platform == "win32"
        prefix = _npm_config_prefix_posix()
        if prefix is None and windows and os.environ.get("APPDATA"):
            prefix = Path(os.environ["APPDATA"]) / "npm"
        config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
        which = shutil.which("gaia")
        return cls(
            workspace=Path(workspace),
            user_settings=Path(user_settings),
            plugins_dir=claude_plugins_dir(plugins_dir),
            opencode_global=(Path(config_home) if config_home else Path.home() / ".config") / "opencode",
            npm_prefix=prefix,
            path_gaia=Path(which) if which else None,
            plugin_root=Path(plugin_root) if plugin_root else None,
            windows=windows,
            db_path=db_path(),
        )


def _slashes(path) -> str:
    return str(path).replace("\\", "/")


def same_path(a, b, *, windows: bool) -> bool:
    """True when *a* and *b* spell one path; separators always fold, case only on Windows."""
    def key(path) -> str:
        text = _slashes(path).rstrip("/")
        return text.casefold() if windows else text
    return key(a) == key(b)


def cache_entry(path, plugins_dir: "Path | None" = None) -> "tuple[str, str] | None":
    """``(marketplace, version)`` when *path* lies in a Claude Code plugin-cache folder of gaia, in either separator form."""
    text = _slashes(path)
    if plugins_dir is not None:
        base = _slashes(plugins_dir).rstrip("/") + "/cache/"
        if text.casefold().startswith(base.casefold()):
            parts = text[len(base):].split("/")
            if len(parts) >= 3 and parts[1] == "gaia":
                return parts[0], parts[2]
    found = _CACHE_ENTRY.search(text)
    return (found.group(1), found.group(2)) if found else None


def spelled_source(source: dict, ref: "str | None") -> "str | None":
    """The ``claude plugin marketplace add`` argument for *source* pinned to *ref*, in its declared form.

    Claude Code refuses an add whose form differs from the declaration, so a git URL never becomes ``owner/repo``.
    """
    kind = source.get("source") if isinstance(source, dict) else None
    if kind in ("directory", "file", "url"):
        return source.get("path") or source.get("url")
    base = source.get("repo") if kind == "github" else source.get("url") if kind == "git" else None
    if not base:
        return None
    return f"{base}#{ref}" if ref else base


def describe_source(source) -> str:
    """One line naming a marketplace source: its type, where it points, and its ref."""
    if not isinstance(source, dict):
        return "nothing"
    where = source.get("repo") or source.get("url") or source.get("path") or "?"
    line = f"{source.get('source', '?')} {where}"
    if source.get("ref"):
        line += f" ref {source['ref']}"
    if source.get("sha"):
        line += f" sha {source['sha']}"
    return line


def _source_key(source) -> tuple:
    source = source if isinstance(source, dict) else {}
    return tuple(source.get(field) for field in ("source", "repo", "url", "path", "ref", "sha"))


def ref_version(ref) -> "str | None":
    """The version a tag ref names (``v5.8.0-rc.1`` -> ``5.8.0-rc.1``), None for a branch or no ref."""
    found = _VERSION_REF.fullmatch(ref) if isinstance(ref, str) else None
    return found.group(1) if found else None


def _json(path) -> "dict | None":
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _jsonc(path: Path) -> "dict | None":
    """A JSON or JSONC config: whole-line ``//`` comments are dropped before parsing."""
    data = _json(path)
    if data is not None:
        return data
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _version(root) -> "str | None":
    """The Gaia version the tree at *root* holds, from package.json or the plugin manifest."""
    if root is None:
        return None
    root = Path(root)
    pkg = _json(root / "package.json")
    if pkg and pkg.get("name") == PACKAGE and isinstance(pkg.get("version"), str):
        return pkg["version"]
    manifest = _json(root / ".claude-plugin" / "plugin.json")
    return manifest.get("version") if manifest and isinstance(manifest.get("version"), str) else None


def _rev_parse(location: str, ref: str) -> "str | None":
    """The commit *ref* names in the git clone at *location*, or None when it does not resolve there."""
    git = shutil.which("git")
    if not git or not Path(location).is_dir():
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        result = subprocess.run([git, "-C", str(location), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                                capture_output=True, text=True, timeout=10, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _finding(findings: list, severity: str, channel: str, detail: str) -> None:
    findings.append({"severity": severity, "channel": channel, "detail": detail})


def _settings(m: Machine) -> "list[tuple[str, Path]]":
    claude = m.workspace / ".claude"
    return [("local", claude / "settings.local.json"), ("project", claude / "settings.json"), ("user", m.user_settings)]


def _plugin_channels(m: Machine, findings: list) -> list:
    declared: dict = {}
    enabled: dict = {}
    for scope, path in _settings(m):
        data = _json(path) or {}
        markets = data.get("extraKnownMarketplaces")
        for name, entry in (markets.items() if isinstance(markets, dict) else ()):
            if isinstance(entry, dict):
                declared.setdefault(name, []).append({"scope": scope, "file": str(path), "source": entry.get("source")})
        plugins = data.get("enabledPlugins")
        for key, on in (plugins.items() if isinstance(plugins, dict) else ()):
            if key.split("@", 1)[0] == "gaia":
                enabled.setdefault(key, {"value": on is True, "scope": scope, "file": str(path)})
    known = _json(m.plugins_dir / "known_marketplaces.json") or {}
    records = (_json(m.plugins_dir / "installed_plugins.json") or {}).get("plugins")
    records = records if isinstance(records, dict) else {}
    keys = sorted({key for key in records if key.split("@", 1)[0] == "gaia"} | set(enabled))
    return [_plugin_channel(m, key, declared, enabled.get(key), known, records, findings) for key in keys]


def _plugin_channel(m, key, declared, enabled, known, records, findings) -> dict:
    name = key.partition("@")[2]
    declarations = declared.get(name, [])
    decl = declarations[0] if declarations else None
    entry = known.get(name) if isinstance(known.get(name), dict) else None
    registered = {"source": entry.get("source"), "location": entry.get("installLocation")} if entry else None
    source = (registered or decl or {}).get("source")
    directory = isinstance(source, dict) and source.get("source") in ("directory", "file")
    served_in_place = Path(registered["location"]) if directory and registered and registered.get("location") else None

    installs = []
    for record in records.get(key) or []:
        if not isinstance(record, dict) or not isinstance(record.get("installPath"), str):
            continue
        path = Path(record["installPath"])
        scope = record.get("scope", "user")
        installs.append({
            "scope": scope, "project": record.get("projectPath"), "path": str(path),
            "recorded_version": record.get("version"), "version": _version(served_in_place or path),
            "commit": record.get("gitCommitSha"), "exists": path.is_dir(),
            "orphaned": (path / ".orphaned_at").exists(),
            "serves_workspace": scope == "user" or same_path(record.get("projectPath") or "", m.workspace,
                                                             windows=m.windows),
        })
    serving = sorted((i for i in installs if i["serves_workspace"]),
                     key=lambda i: SCOPES.index(i["scope"]) if i["scope"] in SCOPES else len(SCOPES))
    serving = serving[0] if serving else None

    running = None
    if m.plugin_root is not None and (cache_entry(m.plugin_root, m.plugins_dir) or ("",))[0] == name:
        running = {"version": _version(m.plugin_root), "path": str(m.plugin_root),
                   "origin": "CLAUDE_PLUGIN_ROOT of the session running this doctor"}
    elif enabled and enabled["value"] and serving:
        running = {"version": serving["version"], "path": str(served_in_place or serving["path"]),
                   "origin": f"what the next Claude Code session in {m.workspace} loads ({serving['scope']} scope)"}

    channel = {
        "channel": "plugin", "key": key, "marketplace": name, "directory": directory,
        "declared": None, "registered": None, "installed": installs, "enabled": enabled,
        "running": running, "provenance": None,
    }
    if decl:
        channel["declared"] = {"version": ref_version((decl["source"] or {}).get("ref")) if not directory else None,
                               "origin": f"{decl['scope']} settings {decl['file']}", **decl}
    if registered:
        channel["registered"] = {"version": ref_version((registered["source"] or {}).get("ref")) if not directory else None,
                                 "origin": f"known_marketplaces.json, clone at {registered['location']}", **registered}
    _plugin_findings(channel, declarations, serving, findings)
    if not directory and registered and serving:
        channel["provenance"] = _plugin_provenance(key, registered, serving, findings)
    return channel


def _plugin_findings(channel: dict, declarations: list, serving, findings: list) -> None:
    key, decl, registered = channel["key"], channel["declared"], channel["registered"]
    for other in declarations[1:]:
        if _source_key(other["source"]) != _source_key(declarations[0]["source"]):
            _finding(findings, "error", "plugin",
                     f"{channel['marketplace']} is declared as {describe_source(declarations[0]['source'])} in "
                     f"{declarations[0]['scope']} settings ({declarations[0]['file']}) and as "
                     f"{describe_source(other['source'])} in {other['scope']} settings ({other['file']})")
    if decl and registered and _source_key(decl["source"]) != _source_key(registered["source"]):
        _finding(findings, "error", "plugin",
                 f"{key}: declared {describe_source(decl['source'])} in {decl['scope']} settings ({decl['file']}) "
                 f"but registered {describe_source(registered['source'])} in known_marketplaces.json -- "
                 "Claude Code ignores a marketplace whose declared and registered sources differ")
    elif decl and not registered:
        _finding(findings, "warning", "plugin",
                 f"{key}: declared {describe_source(decl['source'])} in {decl['scope']} settings but not registered "
                 "in known_marketplaces.json; Claude Code fetches it at the next session start")
    for install in channel["installed"]:
        where = f"{install['scope']} install{' for ' + install['project'] if install['project'] else ''}"
        if not install["exists"]:
            _finding(findings, "error", "plugin",
                     f"{key}: installed_plugins.json records the {where} at {install['path']}, which is gone from disk")
        elif install["orphaned"]:
            _finding(findings, "error", "plugin",
                     f"{key}: the {where} at {install['path']} is marked orphaned (.orphaned_at); "
                     "Claude Code deletes it 14 days after the marker")
        elif (not channel["directory"] and install["version"] and install["recorded_version"]
              and install["version"] != install["recorded_version"]):
            _finding(findings, "error", "plugin",
                     f"{key}: installed_plugins.json records {install['recorded_version']} for the {where} but "
                     f"{install['path']} holds {install['version']}")
    claimed = registered and registered["version"]
    if claimed and serving and serving["version"] and serving["version"] != claimed:
        _finding(findings, "error", "plugin",
                 f"{key}: registered at {registered['source'].get('ref')} but the {serving['scope']} install "
                 f"serving this workspace is {serving['version']} ({serving['path']})")
    enabled = channel["enabled"]
    if enabled and enabled["value"] and not serving:
        _finding(findings, "error", "plugin",
                 f"{key} is enabled in {enabled['scope']} settings ({enabled['file']}) but no install serves "
                 "this workspace")
    if serving and not (enabled and enabled["value"]):
        state = f"disabled in {enabled['scope']} settings" if enabled else "enabled in no settings file"
        _finding(findings, "info", "plugin",
                 f"{key} {serving['version'] or '?'} is installed ({serving['scope']} scope, {serving['path']}) but "
                 f"{state}: residue that an enable would revive")


def _plugin_provenance(key: str, registered: dict, serving: dict, findings: list) -> dict:
    ref = (registered["source"] or {}).get("ref")
    installed = serving.get("commit")
    location = registered.get("location") or ""
    record = {"claimed": ref, "installed_commit": installed, "tag_commit": None, "verdict": "undecided"}
    if not ref:
        record["detail"] = "the registered source pins no ref, so no tag to compare"
    elif not installed:
        record["detail"] = "installed_plugins.json records no gitCommitSha for the install"
    else:
        tag = _rev_parse(location, ref)
        record["tag_commit"] = tag
        if tag is None:
            record["detail"] = f"{ref} does not resolve in the marketplace clone at {location}; undecidable here"
        elif tag == installed:
            record.update(verdict="match", detail=f"commit {installed[:12]} is {ref}")
        else:
            record.update(verdict="mismatch",
                          detail=f"the installed copy is commit {installed[:12]} but {ref} is {tag[:12]} "
                                 f"in the marketplace clone ({location})")
            _finding(findings, "error", "plugin", f"{key}: {record['detail']}")
    return record


def _lock_entry(path: Path) -> "dict | None":
    packages = (_json(path) or {}).get("packages")
    entry = packages.get(f"node_modules/{PACKAGE}") if isinstance(packages, dict) else None
    return entry if isinstance(entry, dict) else None


def _exact(spec) -> bool:
    return isinstance(spec, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", spec) is not None


def _npm_local(m: Machine, findings: list) -> "dict | None":
    from gaia.install_provenance import declared_dependency_spec  # noqa: PLC0415

    ws = m.workspace
    spec = declared_dependency_spec(ws)
    root = ws / "node_modules" / "@jaguilar87" / "gaia"
    version = _version(root)
    if spec is None and version is None:
        return None
    lock, hidden = _lock_entry(ws / "package-lock.json"), _lock_entry(ws / "node_modules" / ".package-lock.json")
    channel = {
        "channel": "npm-local", "path": str(root),
        "declared": {"version": spec, "spec": spec,
                     "origin": f"package.json {ws / 'package.json'}"} if spec else None,
        "registered": {"version": lock.get("version"), "integrity": lock.get("integrity"),
                       "resolved": lock.get("resolved"), "origin": f"package-lock.json, {lock.get('resolved')}"}
        if lock else None,
        "installed": {"version": version, "path": str(root), "integrity": (hidden or {}).get("integrity"),
                      "resolved": (hidden or {}).get("resolved"),
                      "origin": f"{root}, npm recorded {(hidden or {}).get('resolved') or 'nothing'}"} if version else None,
        "running": None,
    }
    if spec and version is None:
        _finding(findings, "warning", "npm-local", f"{ws / 'package.json'} declares {PACKAGE} {spec} but "
                 f"{root} holds no Gaia package (npm install has not run)")
    if _exact(spec) and version and spec != version:
        _finding(findings, "error", "npm-local", f"{ws / 'package.json'} pins {spec} but {root} holds {version}")
    if lock and version and lock.get("version") != version:
        _finding(findings, "error", "npm-local",
                 f"{ws / 'package-lock.json'} records {lock.get('version')} but {root} holds {version}")
    if hidden and version and hidden.get("version") != version:
        _finding(findings, "error", "npm-local",
                 f"npm recorded {hidden.get('version')} in node_modules/.package-lock.json but {root} holds "
                 f"{version}: something other than npm replaced the copy")
    channel["provenance"] = _npm_provenance(lock, hidden, version, ws, findings)
    return channel


def _npm_provenance(lock, hidden, version, ws: Path, findings: list) -> dict:
    record = {"verdict": "undecided", "integrity": (hidden or {}).get("integrity"),
              "resolved": (hidden or {}).get("resolved")}
    if not (lock and hidden and lock.get("integrity") and hidden.get("integrity")):
        record["detail"] = "package-lock.json or node_modules/.package-lock.json records no integrity to compare"
    elif lock["integrity"] != hidden["integrity"]:
        record.update(verdict="mismatch", detail=f"{ws / 'package-lock.json'} expects {lock['integrity'][:22]}... but "
                      f"npm installed {hidden['integrity'][:22]}... ({hidden.get('resolved')})")
        _finding(findings, "error", "npm-local", record["detail"])
    elif version and hidden.get("version") == version:
        record.update(verdict="match", detail=f"{version} from {hidden.get('resolved')}, integrity "
                      f"{hidden['integrity'][:22]}... in both npm records")
    return record


def _global_root(m: Machine) -> "Path | None":
    if m.npm_prefix is None:
        return None
    return (m.npm_prefix if m.windows else m.npm_prefix / "lib") / "node_modules" / "@jaguilar87" / "gaia"


def _npm_global(m: Machine) -> "dict | None":
    root = _global_root(m)
    version = _version(root)
    if version is None:
        return None
    return {"channel": "npm-global", "path": str(root), "declared": None, "registered": None,
            "installed": {"version": version, "path": str(root), "origin": f"npm global prefix {m.npm_prefix}"},
            "running": None}


def _origin_kind(m: Machine, root) -> str:
    if root is None:
        return "package"
    if cache_entry(root, m.plugins_dir):
        return "plugin-cache"
    if str(_slashes(root)).casefold().startswith(_slashes(m.workspace / "node_modules").casefold()):
        return "npm-local"
    global_root = _global_root(m)
    if global_root is not None and same_path(root, global_root, windows=m.windows):
        return "npm-global"
    return "path"


def _recorded_installs(plugins: list) -> list:
    return [install for channel in plugins for install in channel["installed"]]


def _opencode(m: Machine, plugins: list, findings: list) -> "dict | None":
    configs = [m.workspace / "opencode.json", m.workspace / "opencode.jsonc",
               m.workspace / ".opencode" / "opencode.json", m.workspace / ".opencode" / "opencode.jsonc",
               m.opencode_global / "opencode.json", m.opencode_global / "opencode.jsonc"]
    entries = []
    for config in (c for c in configs if c.is_file()):
        data = _jsonc(config)
        if data is None:
            _finding(findings, "warning", "opencode", f"{config} is not readable JSON; its plugins are not checked")
            continue
        for field in ("plugin", "plugins"):
            items = data.get(field)
            for item in items if isinstance(items, list) else ():
                entry = _opencode_entry(m, item, config, plugins) if isinstance(item, str) else None
                if entry:
                    entries.append(entry)
    if not entries:
        return None
    for entry in entries:
        _opencode_findings(entry, findings)
    if len(entries) > 1:
        _finding(findings, "error", "opencode", f"OpenCode loads {len(entries)} Gaia plugins: " + "; ".join(
            f"{e['entry']} ({e['version'] or '?'}) in {e['config']}" for e in entries))
    return {"channel": "opencode", "entries": entries, "declared": None, "registered": None, "running": None,
            "installed": [{"version": e["version"], "path": e["root"], "origin": e["origin"]} for e in entries]}


def _opencode_entry(m: Machine, item: str, config: Path, plugins: list) -> "dict | None":
    pinned = re.fullmatch(r"@jaguilar87/gaia(?:@(\S+))?", item.strip())
    if pinned:
        return {"entry": item, "config": str(config), "kind": "package", "version": pinned.group(1), "root": None,
                "exists": None, "orphaned": False, "recorded": None,
                "origin": "npm package name: OpenCode installs it into its own cache"}
    if not _slashes(item).endswith(_OPENCODE_SUFFIX):
        return None
    root = Path(item[: -len(_OPENCODE_SUFFIX)])
    if not root.is_absolute() and not re.match(r"[A-Za-z]:[\\/]", item):
        root = config.parent / root
    cache = cache_entry(root, m.plugins_dir)
    version = _version(root) or (cache[1] if cache else None)
    if not (cache or f"/{PACKAGE}/" in _slashes(item) + "/" or version):
        return None
    kind = _origin_kind(m, root)
    recorded = any(same_path(i["path"], root, windows=m.windows) for i in _recorded_installs(plugins)) if cache else None
    return {"entry": item, "config": str(config), "kind": kind, "version": version, "root": str(root),
            "exists": (root / "opencode" / "plugin.ts").is_file(), "orphaned": bool(cache) and (root / ".orphaned_at").exists(),
            "recorded": recorded, "origin": {"plugin-cache": f"Claude Code plugin cache {cache and cache[0]}",
                                             "npm-local": "the workspace's npm copy",
                                             "npm-global": "the npm global copy"}.get(kind, "a path")}


def _opencode_findings(entry: dict, findings: list) -> None:
    where = f"{entry['config']} loads Gaia {entry['version'] or '?'} from {entry['root']}"
    if entry["kind"] == "package":
        return
    if not entry["exists"]:
        _finding(findings, "error", "opencode", f"{where}, which does not exist: OpenCode fails at startup")
    elif entry["orphaned"]:
        _finding(findings, "error", "opencode", f"{where}, a plugin-cache folder marked orphaned: Claude Code deletes "
                 "it 14 days after the marker and OpenCode then fails at startup")
    elif entry["kind"] == "plugin-cache" and not entry["recorded"]:
        _finding(findings, "error", "opencode", f"{where}, a plugin-cache folder Claude Code no longer records as "
                 "installed (stale)")
    elif entry["kind"] == "plugin-cache":
        _finding(findings, "warning", "opencode", f"{where}, a versioned plugin-cache folder: the next plugin update "
                 "orphans it")


def _launcher(m: Machine, plugins: list, findings: list) -> "dict | None":
    if m.path_gaia is None:
        return None
    from cli._converge import launcher_package  # noqa: PLC0415

    package = launcher_package(m.path_gaia)
    version = _version(package)
    kind = _origin_kind(m, package) if package else "unknown"
    channel = {"channel": "launcher", "declared": None, "registered": None, "installed": None, "kind": kind,
               "running": {"version": version, "path": str(m.path_gaia), "root": str(package) if package else None,
                           "origin": f"`gaia` on PATH runs {package or 'a package Gaia cannot identify'}"}}
    if kind == "plugin-cache":
        recorded = any(same_path(i["path"], package, windows=m.windows) for i in _recorded_installs(plugins))
        if not recorded or (Path(package) / ".orphaned_at").exists():
            _finding(findings, "error", "launcher", f"`gaia` on PATH ({m.path_gaia}) runs {package}, a plugin-cache "
                     "folder Claude Code no longer records as installed (stale)")
    return channel


def _live_versions(channels: list) -> "list[tuple[str, str, str, str | None]]":
    """(label, version, where, package root) for every copy a host or the shell runs on this machine."""
    live = []
    for channel in channels:
        kind = channel["channel"]
        if kind == "plugin" and channel["running"]:
            path = channel["running"]["path"]
            live.append((f"plugin {channel['key']}", channel["running"]["version"], path, path))
        elif kind in ("npm-local", "npm-global") and channel["installed"]:
            path = channel["installed"]["path"]
            live.append((kind, channel["installed"]["version"], path, path))
        elif kind == "opencode":
            live += [(f"opencode ({e['config']})", e["version"], e["root"] or e["entry"], e["root"])
                     for e in channel["entries"] if e["exists"] is not False]
        elif kind == "launcher":
            running = channel["running"]
            live.append(("launcher", running["version"], running["path"], running.get("root")))
    return [entry for entry in live if entry[1]]


def _schema_expected(root) -> "int | None":
    """The store schema the build at *root* writes: its doctor's EXPECTED_SCHEMA_VERSION, the engine's own source."""
    try:
        text = (Path(root) / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    except (OSError, TypeError):
        return None
    found = re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", text, re.MULTILINE)
    return int(found.group(1)) if found else None


def _store_verdict(m: Machine, label, version, root, expects, ledger, minimum, findings) -> str:
    if expects == ledger:
        return "current"
    if expects > ledger:
        _finding(findings, "warning", "store", f"{label} {version} ({root}) expects schema v{expects}: its next "
                 f"install or session start migrates {m.db_path} from v{ledger}")
        return "migrates"
    if minimum is not None and minimum <= expects:
        return "writes-ahead"
    _finding(findings, "error", "store", f"{m.db_path} is at schema v{ledger}, writable by builds of schema "
             f"v{minimum if minimum is not None else ledger} or later; {label} {version} ({root}) writes "
             f"v{expects} and every write it makes is refused")
    return "refused"


def _store(m: Machine, live: list, findings: list, store_plan) -> "dict | None":
    if m.db_path is None:
        return None
    try:
        report = store_plan(str(m.db_path))
    except (OSError, RuntimeError) as exc:
        _finding(findings, "warning", "store", f"`gaia migrate plan` could not read {m.db_path}: {exc}")
        return None
    ledger, minimum = report.get("ledger") or 0, report.get("min_code_version")
    fresh = report.get("direction") == "fresh"
    copies, seen = [], set()
    for label, version, _where, root in live:
        expects = _schema_expected(root) if root else None
        if expects is None or (root, label) in seen:
            continue
        seen.add((root, label))
        verdict = "creates" if fresh else _store_verdict(m, label, version, root, expects, ledger, minimum, findings)
        copies.append({"label": label, "version": version, "root": root, "expects": expects, "verdict": verdict})
    if report.get("direction") == "missing-migrations":
        _finding(findings, "warning", "store", f"this doctor's build lacks migrations {', '.join(report['missing'])} "
                 f"for {m.db_path}")
    floor = minimum if minimum is not None else "?"
    return {"channel": "store", "path": str(m.db_path), "schema": None if fresh else ledger,
            "min_code_version": minimum, "direction": report.get("direction"), "chain": report.get("chain"),
            "consent_command": report.get("consent_command"), "backups": report.get("backups") or [],
            "copies": copies, "declared": None, "registered": None, "running": None,
            "installed": {"version": "absent (fresh)" if fresh else f"schema v{ledger}",
                          "origin": f"{m.db_path}, writable by builds of schema v{floor}+"}}


def _plan_report(db_path: str) -> dict:
    from cli.migrate import plan_report  # noqa: PLC0415

    return plan_report(db_path)


def read(m: Machine, store_plan=None) -> dict:
    """The install state of *m* (channels and the store), its splits as findings, and an install-scoped verdict; *store_plan* defaults to ``cli.migrate.plan_report``."""
    findings: list = []
    plugins = _plugin_channels(m, findings)
    enabled = [c for c in plugins if c["enabled"] and c["enabled"]["value"]]
    if len(enabled) > 1:
        _finding(findings, "error", "plugin", "two Gaia plugins are enabled for this workspace: " + ", ".join(
            f"{c['key']} ({c['enabled']['scope']} settings)" for c in enabled)
            + " -- Claude Code registers the hooks of one plugin per name")
    channels = [*plugins, _npm_local(m, findings), _npm_global(m), _opencode(m, plugins, findings),
                _launcher(m, plugins, findings)]
    channels = [channel for channel in channels if channel]
    live = _live_versions(channels)
    versions = sorted({version for _, version, _, _ in live})
    if len(versions) > 1:
        _finding(findings, "error", "machine", f"Gaia runs {len(versions)} versions on this machine: "
                 + "; ".join(f"{label} {version} ({where})" for label, version, where, _ in live))
    store = _store(m, live, findings, store_plan or _plan_report)
    if store:
        channels.append(store)
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    here = Path(__file__).resolve().parent.parent.parent
    return {
        "workspace": str(m.workspace), "windows": m.windows,
        "doctor": {"path": str(here), "version": _version(here)},
        "channels": channels, "versions": versions, "findings": findings,
        "errors": errors, "warnings": warnings,
        "status": "split" if errors else "residue" if warnings else "aligned",
        "exit_code": 2 if errors else 1 if warnings else 0,
    }


def _arg(value) -> str:
    from gaia.subprocess_text import command_arg  # noqa: PLC0415

    text = str(value)
    return text if _PLAIN_ARG.fullmatch(text) else command_arg(text)


def _step(steps: list, channel: str, kind: str, gap: str, *, command=None, cwd=None, file=None, detail=None) -> None:
    steps.append({"channel": channel, "kind": kind, "gap": gap, "command": command, "cwd": cwd,
                  "file": file, "detail": detail})


def _plugin_steps(channel: dict, target: str, ref: str, steps: list) -> None:
    key, decl, registered = channel["key"], channel["declared"], channel["registered"]
    source = (decl or registered or {}).get("source")
    if not isinstance(source, dict):
        return
    if decl and source.get("ref") != ref:
        _step(steps, "plugin", "edit", f"{key} is declared at {source.get('ref') or 'no ref'}, not {ref}",
              file=decl["file"],
              detail=f'set extraKnownMarketplaces["{channel["marketplace"]}"].source.ref to "{ref}" and keep every '
                     "other field as written; the add below must match the declaration exactly")
    pinned = dict(source, ref=ref)
    if not registered or _source_key(registered["source"]) != _source_key(pinned):
        scope = decl["scope"] if decl else "user"
        _step(steps, "plugin", "command",
              f"{key} is registered as {describe_source(registered and registered['source'])}",
              command=f"claude plugin marketplace add {_arg(spelled_source(source, ref))} --scope {scope}",
              detail="spelled as declared: a source typed in another form is refused or leaves the marketplace ignored")
    for install in channel["installed"]:
        if install["version"] == target and install["exists"] and not install["orphaned"]:
            continue
        cwd = install["project"] if install["scope"] in ("local", "project") else None
        _step(steps, "plugin", "command", f"the {install['scope']} install is {install['version'] or 'missing'}",
              command=f"claude plugin update {key} --scope {install['scope']}", cwd=cwd,
              detail="run from the install's project folder; a later session or /reload-plugins loads it")


def _npm_steps(report: dict, channel, target: str, steps: list, windows: bool) -> None:
    ws = report["workspace"]
    installed = (channel or {}).get("installed") or {}
    declared = (channel or {}).get("declared") or {}
    if installed.get("version") == target and declared.get("spec") in (None, target):
        return
    gap = f"the workspace copy is {installed.get('version') or 'absent'}"
    _step(steps, "npm-local", "check", f"{target} must be served by the registry first",
          command=f"npm view {PACKAGE}@{target} version", detail=f"prints {target} once the registry serves it")
    _step(steps, "npm-local", "command", gap, command=f"npm install {PACKAGE}@{target} --save-exact", cwd=ws)
    gaia = (f"{ws}\\node_modules\\.bin\\gaia.cmd" if windows else f"{ws}/node_modules/.bin/gaia")
    _step(steps, "npm-local", "command", "the new copy re-wires the channels this workspace recorded",
          command=f"{_arg(gaia)} update --workspace {_arg(ws)}", cwd=ws,
          detail="it also regenerates opencode.json's agent block (each agent's permissions and scratch path), "
                 "so a diff of that file shows more than the plugin entry; `--dry-run` previews only the "
                 "Claude Code wiring, so read the result with `gaia doctor --channels` instead")


def plan(report: dict, target: str) -> dict:
    """The gaps between *report*'s machine and *target*, each with the host command or edit that closes it; runs nothing."""
    target = target[1:] if target.startswith("v") else target
    ref = f"v{target}"
    windows = report.get("windows", False)
    steps: list = []
    channels = report["channels"]
    plugins = [c for c in channels if c["channel"] == "plugin"]
    real = [c for c in plugins if not c["directory"]]
    for channel in real:
        _plugin_steps(channel, target, ref, steps)
    for channel in plugins:
        if channel["directory"] and channel["enabled"] and channel["enabled"]["value"]:
            scope = channel["enabled"]["scope"]
            _step(steps, "plugin", "command", f"{channel['key']} (a dev build) is enabled",
                  command=f"claude plugin disable {channel['key']} --scope {scope}")
            for other in real:
                if not (other["enabled"] and other["enabled"]["value"]):
                    _step(steps, "plugin", "command", f"{other['key']} is not enabled",
                          command=f"claude plugin enable {other['key']} --scope {scope}")
    by_kind = {c["channel"]: c for c in channels if c["channel"] != "plugin"}
    opencode = by_kind.get("opencode")
    if "npm-local" in by_kind or (opencode and any(e["kind"] == "npm-local" for e in opencode["entries"])):
        _npm_steps(report, by_kind.get("npm-local"), target, steps, windows)
    global_copy = by_kind.get("npm-global")
    if global_copy and global_copy["installed"]["version"] != target:
        _step(steps, "npm-global", "check", f"{target} must be served by the registry first",
              command=f"npm view {PACKAGE}@{target} version")
        _step(steps, "npm-global", "command", f"the global copy is {global_copy['installed']['version']}",
              command=f"npm install -g {PACKAGE}@{target}")
    for entry in (opencode or {}).get("entries", []):
        if entry["kind"] == "package" and entry["version"] != target:
            _step(steps, "opencode", "edit", f"OpenCode pins {entry['entry']}", file=entry["config"],
                  detail=f'replace "{entry["entry"]}" with "{PACKAGE}@{target}" in plugin')
        elif entry["kind"] in ("plugin-cache", "path") or not entry["exists"]:
            _step(steps, "opencode", "edit", f"OpenCode loads Gaia from {entry['origin']}", file=entry["config"],
                  detail=f'remove "{entry["entry"]}" from plugin; the workspace npm copy is the one OpenCode loads')
    launcher = by_kind.get("launcher")
    if launcher and launcher["running"]["version"] != target and launcher["kind"] not in ("npm-global", "plugin-cache"):
        _step(steps, "launcher", "command", f"`gaia` on PATH runs {launcher['running']['version']}",
              command="gaia install --path", cwd=report["workspace"],
              detail="run it with the gaia of the new copy, from the workspace")
    store = by_kind.get("store")
    if store and steps:
        _step(steps, "store", "check",
              f"the store {store['path']} is at {store['installed']['version']}, writable by builds of schema "
              f"v{store['min_code_version'] if store['min_code_version'] is not None else '?'}+",
              command="gaia migrate plan",
              detail="run it with the gaia of the new copy once installed: it shows the chain the data goes through, "
                     "whether apply needs --consent-chain, and the backups on hand")
    if any(s["channel"] == "plugin" for s in steps):
        _step(steps, "plugin", "session", "a running session keeps the version it loaded",
              detail="start a new Claude Code session in each install's project, or run /reload-plugins")
    if any(s["channel"] in ("npm-local", "opencode") for s in steps):
        _step(steps, "opencode", "session", "a running OpenCode keeps the plugin it loaded",
              detail=f"restart OpenCode in {report['workspace']}")
    return {"target": target, "ref": ref, "steps": steps,
            "verify": f"gaia doctor --channels --workspace {_arg(report['workspace'])}",
            "note": "Gaia describes these steps; it runs none of them."}


def _stage(label: str, stage) -> "list[str]":
    if not stage:
        return [f"      {label:<11} -"]
    return [f"      {label:<11} {stage.get('version') or '?'}  {stage.get('origin', '')}"]


def format_report(report: dict) -> "list[str]":
    """The report as the lines `gaia doctor --channels` prints."""
    lines = [f"  Install state  (workspace {report['workspace']}; this doctor "
             f"{report['doctor']['version'] or '?'} at {report['doctor']['path']})", ""]
    for channel in report["channels"]:
        kind = channel["channel"]
        if kind == "plugin":
            lines.append(f"    plugin {channel['key']}{' (dev, directory)' if channel['directory'] else ''}")
            for label in ("declared", "registered"):
                stage = channel[label]
                if stage:
                    stage = {**stage, "origin": f"{describe_source(stage['source'])}  ({stage['origin']})"}
                lines += _stage(label, stage)
            for install in channel["installed"] or [None]:
                lines += _stage("installed", install and {
                    "version": install["version"],
                    "origin": f"{install['scope']}{' ' + install['project'] if install['project'] else ''}  "
                              f"commit {(install['commit'] or '?')[:12]}  {install['path']}"})
            enabled = channel["enabled"]
            lines.append(f"      {'enabled':<11} " + (f"{str(enabled['value']).lower()}  ({enabled['scope']} settings)"
                                                     if enabled else "-"))
            lines += _stage("running", channel["running"])
            if channel["provenance"]:
                lines.append(f"      {'provenance':<11} {channel['provenance']['verdict']}: "
                             f"{channel['provenance']['detail']}")
        elif kind == "opencode":
            lines.append("    opencode")
            for entry in channel["entries"]:
                lines.append(f"      {'declared':<11} {entry['entry']}  ({entry['config']})")
                lines.append(f"      {'installed':<11} {entry['version'] or '?'}  {entry['origin']}")
            lines.append(f"      {'running':<11} not observable: OpenCode logs no plugin load")
        elif kind == "store":
            lines.append("    store")
            lines += _stage("installed", channel["installed"])
            lines.append(f"      {'direction':<11} {channel['direction']} against this doctor's build"
                         + (f", chain {channel['chain']}" if channel["chain"] else "")
                         + f"; {len(channel['backups'])} backup(s) on hand")
            for copy in channel["copies"]:
                lines.append(f"      {'copy':<11} {copy['label']} {copy['version']} writes v{copy['expects']}: "
                             f"{copy['verdict']}")
        else:
            lines.append(f"    {kind}")
            labels = ("running",) if kind == "launcher" else ("declared", "registered", "installed")
            for label in labels:
                lines += _stage(label, channel[label])
            if channel.get("provenance"):
                lines.append(f"      {'provenance':<11} {channel['provenance']['verdict']}: "
                             f"{channel['provenance']['detail']}")
        lines.append("")
    tags = {"error": "FAIL", "warning": "WARN", "info": "INFO"}
    for finding in report["findings"]:
        lines.append(f"    [{tags[finding['severity']]}] {finding['channel']}: {finding['detail']}")
    lines += ["", f"  Versions running: {', '.join(report['versions']) or 'none'}",
              f"  Install verdict: {report['status'].upper()} ({report['errors']} error(s), "
              f"{report['warnings']} warning(s)) -- exit {report['exit_code']}", ""]
    return lines


def format_plan(target_plan: dict) -> "list[str]":
    """The plan as the lines `gaia doctor --target` prints."""
    lines = [f"  To reach {target_plan['target']} on this machine ({target_plan['note']})", ""]
    if not target_plan["steps"]:
        lines.append(f"    Every channel present is at {target_plan['target']}: nothing to change.")
    for number, step in enumerate(target_plan["steps"], 1):
        lines.append(f"    {number}. [{step['channel']}] {step['gap']}")
        if step["command"]:
            lines.append(f"       {step['kind']}: {step['command']}" + (f"   (in {step['cwd']})" if step["cwd"] else ""))
        if step["file"]:
            lines.append(f"       edit {step['file']}")
        if step["detail"]:
            lines.append(f"       {step['detail']}")
    lines += ["", f"  Then read: {target_plan['verify']}", ""]
    return lines
