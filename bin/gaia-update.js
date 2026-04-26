#!/usr/bin/env node

/**
 * @jaguilar87/gaia - Update script
 *
 * Runs automatically on npm install/update (postinstall hook).
 * Also available as: npx gaia-update
 *
 * Behavior:
 * - First-time install (.claude/ doesn't exist):
 *   1. Check Python 3 is available
 *   2. Run gaia-scan --npm-postinstall to create .claude/, symlinks, settings, project-context
 *   3. Create plugin-registry.json
 *   4. Merge permissions into settings.local.json
 *   5. Merge hooks into settings.local.json
 *   6. Fall through to verification
 * - Update (.claude/ exists):
 *   1. Show version transition (previous → current)
 *   2. settings.json: create only if missing (non-invasive, never overwrites)
 *   3. Merge permissions, env vars, and agent key into settings.local.json (union, preserves user config)
 *   4. Merge hooks from hooks.json into settings.local.json (npm mode requires this)
 *   5. Symlinks: recreate if missing, fix broken ones
 *   5. Verify: hooks, python, project-context, config files
 *   6. Report: summary with any issues found
 *
 * Usage:
 *   npm update @jaguilar87/gaia   # Automatic via postinstall
 *   npx gaia-update                   # Manual trigger
 *   npx gaia-update --verbose         # Show all checks
 */

import { fileURLToPath } from 'url';
import { dirname, join, relative, isAbsolute, resolve as resolvePath } from 'path';
import fs from 'fs/promises';
import { existsSync, realpathSync, readdirSync, readlinkSync } from 'fs';
import { exec } from 'child_process';
import { promisify } from 'util';
import chalk from 'chalk';
import ora from 'ora';
import { findPython } from './python-detect.js';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.env.INIT_CWD || process.cwd();
const VERBOSE = process.argv.includes('--verbose') || process.argv.includes('-v');

// Use junctions on Windows (no admin required), regular symlinks elsewhere
const LINK_TYPE = process.platform === 'win32' ? 'junction' : 'dir';

// ============================================================================
// Dynamic package resolution
// ============================================================================
//
// The gaia package was renamed from `@jaguilar87/gaia-ops` to `@jaguilar87/gaia`
// in v5. Hardcoding either name breaks the postinstall on the other variant.
// Resolve dynamically by scanning `node_modules/@jaguilar87/` for the first
// `gaia*` package installed in the consumer project.
//
// Returns the package name (e.g. `gaia` or `gaia-ops`) or `null` when the
// scope directory is missing / has no gaia package. Callers should fall back
// or report not-found rather than assume a default.

function resolveGaiaPackageName(cwd = CWD) {
  const scopeDir = join(cwd, 'node_modules', '@jaguilar87');
  if (!existsSync(scopeDir)) return null;
  let entries;
  try {
    entries = readdirSync(scopeDir);
  } catch {
    return null;
  }
  // Prefer exact canonical name when present, else first gaia* match.
  if (entries.includes('gaia')) return 'gaia';
  const legacy = entries.find((name) => name.startsWith('gaia'));
  return legacy || null;
}

// ============================================================================
// Version Detection
// ============================================================================

async function detectVersions() {
  const current = await readPackageVersion(join(__dirname, '..', 'package.json'));

  // Try to find previous version from the installed package.json backup or lock.
  // Use the dynamically-resolved package name so both `@jaguilar87/gaia` (v5+)
  // and the legacy `@jaguilar87/gaia-ops` are supported on upgrade paths.
  let previous = null;
  try {
    const lockPath = join(CWD, 'package-lock.json');
    if (existsSync(lockPath)) {
      const lock = JSON.parse(await fs.readFile(lockPath, 'utf-8'));
      const pkgName = resolveGaiaPackageName() || 'gaia';
      const dep = lock.packages?.[`node_modules/@jaguilar87/${pkgName}`]
        || lock.dependencies?.[`@jaguilar87/${pkgName}`];
      if (dep) previous = dep.version;
    }
  } catch { /* ignore */ }

  return { previous, current };
}

async function readPackageVersion(path) {
  try {
    const pkg = JSON.parse(await fs.readFile(path, 'utf-8'));
    return pkg.version;
  } catch {
    return null;
  }
}

// ============================================================================
// Update Steps
// ============================================================================

async function updateSettingsJson() {
  const spinner = ora('Checking settings.json...').start();
  try {
    const settingsPath = join(CWD, '.claude', 'settings.json');

    if (!existsSync(join(CWD, '.claude'))) {
      spinner.info('Skipped (.claude/ not found)');
      return false;
    }

    // Non-invasive: only create if missing. Never overwrite.
    // Hooks come from hooks.json (auto-discovered via symlink).
    // Env vars and permissions live in settings.local.json.
    if (existsSync(settingsPath)) {
      spinner.succeed('settings.json already exists (not overwriting)');
      return false;
    }

    await fs.writeFile(settingsPath, '{}\n');
    spinner.succeed('settings.json created (minimal — hooks from hooks.json)');
    return true;
  } catch (error) {
    spinner.fail(`settings.json: ${error.message}`);
    return false;
  }
}

async function updateLocalPermissions() {
  const spinner = ora('Merging permissions into settings.local.json...').start();
  try {
    const claudeDir = join(CWD, '.claude');
    const localPath = join(claudeDir, 'settings.local.json');

    if (!existsSync(claudeDir)) {
      spinner.info('Skipped (.claude/ not found)');
      return false;
    }

    // Load existing settings.local.json — preserve everything (enabledPlugins, MCP servers, etc.)
    let existing = {};
    if (existsSync(localPath)) {
      try {
        existing = JSON.parse(await fs.readFile(localPath, 'utf-8'));
      } catch {
        existing = {};
      }
    }

    // Track what changed
    let changed = false;

    // Set the orchestrator agent identity (always, even if Python extraction fails)
    if (existing.agent !== 'gaia-orchestrator') {
      existing.agent = 'gaia-orchestrator';
      changed = true;
    }

    // Add env vars (smart merge: add if not present, don't overwrite)
    existing.env = existing.env || {};
    if (!('CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS' in existing.env)) {
      existing.env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = '1';
      changed = true;
    }

    // Load permissions from plugin_setup.py — the single source of truth.
    // We use ast.literal_eval to extract the constants without importing
    // the module (which has relative imports that fail standalone).
    let gaiaPerms;
    try {
      const setupPath = join(__dirname, '..', 'hooks', 'modules', 'core', 'plugin_setup.py');
      const pythonCmd = findPython() || 'python3';

      // Write the extraction script to a temp file instead of using -c with
      // inline code.  This avoids shell quoting issues on Windows where
      // backslash paths and nested quotes break the inline Python string.
      const tempScript = join(claudeDir, '.gaia-extract-perms.py');
      const scriptContent = `
import ast, json, re, sys

setup_path = sys.argv[1]
source = open(setup_path, encoding="utf-8").read()

# Extract _DENY_RULES list
deny_match = re.search(r'^_DENY_RULES\\s*=\\s*\\[', source, re.MULTILINE)
if deny_match:
    bracket_start = deny_match.start() + source[deny_match.start():].index('[')
    depth, i = 0, bracket_start
    for i, ch in enumerate(source[bracket_start:], bracket_start):
        if ch == '[': depth += 1
        elif ch == ']': depth -= 1
        if depth == 0: break
    deny_rules = ast.literal_eval(source[bracket_start:i+1])
else:
    deny_rules = []

# Extract OPS_PERMISSIONS allow list
ops_match = re.search(r'^OPS_PERMISSIONS\\s*=', source, re.MULTILINE)
if ops_match:
    bracket_start = source.index('{', ops_match.start())
    depth, i = 0, bracket_start
    for i, ch in enumerate(source[bracket_start:], bracket_start):
        if ch == '{': depth += 1
        elif ch == '}': depth -= 1
        if depth == 0: break
    # Replace _DENY_RULES reference with actual list for eval
    ops_str = source[bracket_start:i+1].replace('_DENY_RULES', json.dumps(deny_rules))
    ops_perms = ast.literal_eval(ops_str)
else:
    ops_perms = {'permissions': {'allow': [], 'deny': deny_rules, 'ask': []}}

print(json.dumps(ops_perms))
`;
      await fs.writeFile(tempScript, scriptContent);
      try {
        const { stdout } = await execAsync(
          `${pythonCmd} "${tempScript}" "${setupPath}"`,
          { timeout: 10000 }
        );
        gaiaPerms = JSON.parse(stdout.trim());
      } finally {
        // Clean up temp script
        try { await fs.unlink(tempScript); } catch { /* ignore */ }
      }
    } catch (pyError) {
      spinner.warn(`Could not load permissions from Python — ${pyError.message || 'unknown error'}`);
      // Still write agent and env changes even if permissions extraction fails
      if (changed) {
        await fs.writeFile(localPath, JSON.stringify(existing, null, 2) + '\n');
        spinner.succeed('settings.local.json agent and env merged (permissions skipped)');
        return true;
      }
      return false;
    }

    const ourAllow = new Set(gaiaPerms.permissions.allow || []);
    const ourDeny = new Set(gaiaPerms.permissions.deny || []);

    const perms = existing.permissions || {};
    const currentAllow = new Set(perms.allow || []);
    const currentDeny = new Set(perms.deny || []);

    // Union merge — add ours without removing user's
    const mergedAllow = [...new Set([...currentAllow, ...ourAllow])].sort();
    const mergedDeny = [...new Set([...currentDeny, ...ourDeny])].sort();

    // Check if permissions changed
    const allowChanged = mergedAllow.length !== currentAllow.size
      || mergedAllow.some(r => !currentAllow.has(r));
    const denyChanged = mergedDeny.length !== currentDeny.size
      || mergedDeny.some(r => !currentDeny.has(r));

    if (allowChanged || denyChanged) {
      existing.permissions = existing.permissions || {};
      existing.permissions.allow = mergedAllow;
      existing.permissions.deny = mergedDeny;
      existing.permissions.ask = existing.permissions.ask || [];
      changed = true;
    }

    if (!changed) {
      spinner.succeed('settings.local.json permissions already up to date');
      return false;
    }

    await fs.writeFile(localPath, JSON.stringify(existing, null, 2) + '\n');
    spinner.succeed('settings.local.json permissions, env, and agent merged');
    return true;
  } catch (error) {
    spinner.fail(`settings.local.json: ${error.message}`);
    return false;
  }
}

async function updateLocalHooks() {
  const spinner = ora('Merging hooks into settings.local.json...').start();
  try {
    const claudeDir = join(CWD, '.claude');
    const localPath = join(claudeDir, 'settings.local.json');

    if (!existsSync(claudeDir)) {
      spinner.info('Skipped (.claude/ not found)');
      return false;
    }

    // Read hooks.json from the installed package
    const hooksJsonPath = join(__dirname, '..', 'hooks', 'hooks.json');
    if (!existsSync(hooksJsonPath)) {
      spinner.warn('hooks.json not found in package');
      return false;
    }

    let hooksData;
    try {
      hooksData = JSON.parse(await fs.readFile(hooksJsonPath, 'utf-8'));
    } catch {
      spinner.warn('hooks.json is invalid JSON');
      return false;
    }

    // Unwrap outer "hooks" key if present
    const sourceHooks = hooksData.hooks || hooksData;

    // Resolve absolute path to hooks directory so hooks work regardless of
    // CWD at execution time (Stop/PostCompact hooks may run from unknown CWD)
    const hooksSymlink = join(claudeDir, 'hooks');
    let hooksAbs;
    try {
      hooksAbs = realpathSync(hooksSymlink);
    } catch {
      hooksAbs = hooksSymlink; // Fallback if symlink not yet created
    }
    const convertCommand = (cmd) => {
      return cmd.replace(/\$\{CLAUDE_PLUGIN_ROOT\}\/hooks\//g, `${hooksAbs}/`);
    };

    const convertedHooks = {};
    for (const [event, entries] of Object.entries(sourceHooks)) {
      convertedHooks[event] = entries.map(entry => {
        const converted = { ...entry };
        if (converted.hooks) {
          converted.hooks = converted.hooks.map(h => ({
            ...h,
            command: h.command ? convertCommand(h.command) : h.command,
          }));
        }
        return converted;
      });
    }

    // Load existing settings.local.json
    let existing = {};
    if (existsSync(localPath)) {
      try {
        existing = JSON.parse(await fs.readFile(localPath, 'utf-8'));
      } catch {
        existing = {};
      }
    }

    // Migrate existing relative .claude/hooks/ paths to absolute
    const existingHooks = existing.hooks || {};
    let changed = false;

    for (const [event, entries] of Object.entries(existingHooks)) {
      for (const entry of entries) {
        for (const h of (entry.hooks || [])) {
          if (h.command && h.command.startsWith('.claude/hooks/')) {
            h.command = h.command.replace('.claude/hooks/', `${hooksAbs}/`);
            changed = true;
          }
        }
      }
    }

    // Smart merge: for each hook event, deduplicate by command string
    for (const [event, newEntries] of Object.entries(convertedHooks)) {
      if (!existingHooks[event]) {
        existingHooks[event] = newEntries;
        changed = true;
        continue;
      }

      // Collect existing command strings for deduplication
      const existingCommands = new Set();
      for (const entry of existingHooks[event]) {
        for (const h of (entry.hooks || [])) {
          if (h.command) existingCommands.add(h.command);
        }
      }

      // Add new entries whose commands are not already present
      for (const newEntry of newEntries) {
        const newCommands = (newEntry.hooks || []).map(h => h.command).filter(Boolean);
        const allPresent = newCommands.length > 0 && newCommands.every(c => existingCommands.has(c));
        if (!allPresent) {
          existingHooks[event].push(newEntry);
          changed = true;
        }
      }
    }

    if (!changed) {
      spinner.succeed('settings.local.json hooks already up to date');
      return false;
    }

    existing.hooks = existingHooks;
    await fs.writeFile(localPath, JSON.stringify(existing, null, 2) + '\n');
    spinner.succeed('settings.local.json hooks merged');
    return true;
  } catch (error) {
    spinner.fail(`hooks merge: ${error.message}`);
    return false;
  }
}

async function updateSymlinks() {
  const spinner = ora('Checking symlinks...').start();
  try {
    const claudeDir = join(CWD, '.claude');
    if (!existsSync(claudeDir)) {
      spinner.info('Skipped (.claude/ not found)');
      return { updated: false, fixed: 0, total: 0 };
    }

    // Resolve the installed package name dynamically so this works for both
    // `@jaguilar87/gaia` (v5+) and the legacy `@jaguilar87/gaia-ops` when the
    // consumer happens to have the old name on disk.
    const pkgName = resolveGaiaPackageName();
    if (!pkgName) {
      spinner.fail('Package not found in node_modules/@jaguilar87/');
      return { updated: false, fixed: 0, total: 0 };
    }
    const packagePath = join(CWD, 'node_modules', '@jaguilar87', pkgName);
    if (!existsSync(packagePath)) {
      spinner.fail(`Package not found at ${packagePath}`);
      return { updated: false, fixed: 0, total: 0 };
    }

    const relativePath = relative(claudeDir, packagePath);
    const symlinks = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'skills'];
    let fixed = 0;

    // Helpers: detect a stale symlink by reading the raw link target and
    // checking whether the (absolute-resolved) target exists. Also detect
    // symlinks that still point at the legacy `gaia-ops` path after the
    // rename and repair them to the current package path.
    const isStaleOrLegacy = (link) => {
      let raw;
      try {
        raw = readlinkSync(link);
      } catch {
        return { stale: false, reason: null };
      }
      const absTarget = isAbsolute(raw) ? raw : resolvePath(dirname(link), raw);
      if (!existsSync(absTarget)) {
        return { stale: true, reason: `target missing: ${raw}` };
      }
      // Legacy name detection: if the installed package is `gaia` but the
      // symlink still references `@jaguilar87/gaia-ops`, repair it.
      if (pkgName === 'gaia' && raw.includes('@jaguilar87/gaia-ops')) {
        return { stale: true, reason: `legacy target: ${raw}` };
      }
      return { stale: false, reason: null };
    };

    // Detect whether a path exists as a symlink (broken or not). Plain
    // existsSync returns false for a broken symlink, so we need lstat to
    // distinguish "no entry" from "entry whose target is missing".
    const symlinkEntryExists = (p) => {
      try {
        return readlinkSync(p) !== undefined;
      } catch {
        return false;
      }
    };

    for (const name of symlinks) {
      const link = join(claudeDir, name);
      // Junctions on Windows require absolute targets; symlinks on Unix use relative
      const target = process.platform === 'win32'
        ? join(packagePath, name)
        : join(relativePath, name);

      if (!existsSync(link) && !symlinkEntryExists(link)) {
        try {
          await fs.symlink(target, link, LINK_TYPE);
          console.log(chalk.gray(`[gaia-update] Created symlink: ${link}`));
          fixed++;
        } catch { /* skip */ }
      } else {
        const { stale, reason } = isStaleOrLegacy(link);
        if (stale) {
          try {
            await fs.unlink(link);
            await fs.symlink(target, link, LINK_TYPE);
            console.log(chalk.gray(`[gaia-update] Repaired stale symlink: ${link} (${reason})`));
            fixed++;
          } catch { /* skip */ }
        }
      }
    }

    // CHANGELOG.md — same stale-detection contract, but file-copy on Windows
    const changelogLink = join(claudeDir, 'CHANGELOG.md');
    const changelogSrc = join(packagePath, 'CHANGELOG.md');
    if (!existsSync(changelogLink)) {
      try {
        if (process.platform === 'win32') {
          // Junctions only work for directories; copy the file on Windows
          await fs.copyFile(changelogSrc, changelogLink);
        } else {
          await fs.symlink(join(relativePath, 'CHANGELOG.md'), changelogLink);
        }
        console.log(chalk.gray(`[gaia-update] Created CHANGELOG link: ${changelogLink}`));
        fixed++;
      } catch { /* skip */ }
    } else if (process.platform !== 'win32') {
      const { stale, reason } = isStaleOrLegacy(changelogLink);
      if (stale) {
        try {
          await fs.unlink(changelogLink);
          await fs.symlink(join(relativePath, 'CHANGELOG.md'), changelogLink);
          console.log(chalk.gray(`[gaia-update] Repaired stale CHANGELOG link (${reason})`));
          fixed++;
        } catch { /* skip */ }
      }
    }

    const total = symlinks.length + 1;
    if (fixed > 0) {
      spinner.succeed(`Symlinks: fixed ${fixed}/${total}`);
    } else {
      spinner.succeed(`Symlinks: ${total}/${total} valid`);
    }

    return { updated: fixed > 0, fixed, total };
  } catch (error) {
    spinner.fail(`Symlinks: ${error.message}`);
    return { updated: false, fixed: 0, total: 0 };
  }
}

// ============================================================================
// FTS5 Backfill Safety-Net
// ============================================================================
//
// On upgrade paths, a project may already have episodes in
// `.claude/project-context/episodic-memory/index.json` but an empty FTS5
// `search.db`. This happens when episodes were produced before FTS5 was wired
// in, or when `search.db` was deleted for any reason. Fresh installs have
// zero episodes and will no-op through this check.
//
// Opt-out: pass `--no-fts5-backfill` (or set GAIA_SKIP_FTS5_BACKFILL=1).

async function maybeBackfillFts5() {
  if (process.argv.includes('--no-fts5-backfill')
      || process.env.GAIA_SKIP_FTS5_BACKFILL === '1') {
    return;
  }

  const claudeDir = join(CWD, '.claude');
  if (!existsSync(claudeDir)) return;

  const memoryDir = join(claudeDir, 'project-context', 'episodic-memory');
  const indexPath = join(memoryDir, 'index.json');
  const dbPath = join(memoryDir, 'search.db');

  if (!existsSync(indexPath)) return; // Fresh install — nothing to backfill

  let total = 0;
  try {
    const idx = JSON.parse(await fs.readFile(indexPath, 'utf-8'));
    total = Array.isArray(idx.episodes) ? idx.episodes.length : 0;
  } catch {
    return; // Unreadable index — let doctor handle it
  }
  if (total === 0) return; // No episodes to index

  // If search.db doesn't exist yet but episodes do, fall through to the
  // backfill step below. doctor --fix creates the db AND populates it.
  // (Previously this returned early "doctor --fix will create it on first
  // use", but nothing in the install flow runs doctor --fix automatically,
  // so the index would stay empty until the user manually invoked it.)

  // Query FTS5 count via python3 subprocess (sqlite3 binary may not be on PATH
  // on Windows; python3 is already a hard requirement for gaia-ops hooks).
  const pyCmd = findPython();
  if (!pyCmd) return; // Python missing — the health check will report it

  const spinner = ora('Checking FTS5 backfill status...').start();
  let indexed = 0;
  if (existsSync(dbPath)) {
    try {
      const probeScript = join(memoryDir, '.gaia-fts5-probe.py');
      const probeContent = `
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    cur = con.execute("SELECT COUNT(*) FROM episodes_fts")
    print(cur.fetchone()[0])
except Exception:
    print(-1)
`;
      await fs.writeFile(probeScript, probeContent);
      try {
        const { stdout } = await execAsync(
          `${pyCmd} "${probeScript}" "${dbPath}"`,
          { timeout: 10000 }
        );
        indexed = parseInt(stdout.trim(), 10);
      } finally {
        try { await fs.unlink(probeScript); } catch { /* ignore */ }
      }
    } catch {
      spinner.info('FTS5 probe skipped (sqlite3/python issue)');
      return;
    }

    if (!Number.isFinite(indexed) || indexed < 0) {
      // Table doesn't exist yet — treat as zero indexed, fall through to backfill.
      indexed = 0;
    }

    // If indexed is already >=90% of total, no backfill needed — matches doctor
    // threshold exactly so we don't loop users through unnecessary work.
    if (indexed > 0 && indexed / total >= 0.9) {
      spinner.succeed(`FTS5 backfill: ${indexed}/${total} episodes indexed (ok)`);
      return;
    }
  }
  // else: search.db missing entirely — doctor --fix will create + backfill.

  spinner.text = existsSync(dbPath)
    ? `FTS5 backfill: rebuilding ${total} episodes (had ${indexed})...`
    : `FTS5 backfill: creating index for ${total} episodes...`;

  // Invoke backfill via the gaia CLI doctor --fix. We call `python3 bin/gaia
  // doctor --fix` from the installed package directory, with CWD set to the
  // consumer project so doctor locates the right .claude/ tree.
  const packageDir = join(__dirname, '..');
  const gaiaEntry = join(packageDir, 'bin', 'gaia');

  if (!existsSync(gaiaEntry)) {
    spinner.warn('FTS5 backfill skipped (bin/gaia not found)');
    return;
  }

  try {
    const { stdout, stderr } = await execAsync(
      `${pyCmd} "${gaiaEntry}" doctor --fix`,
      { timeout: 120000, cwd: CWD }
    );
    if (VERBOSE) {
      if (stdout) console.log(chalk.gray(stdout));
      if (stderr) console.log(chalk.yellow(stderr));
    }

    // Re-probe to report outcome.
    const probeScript = join(memoryDir, '.gaia-fts5-probe.py');
    const probeContent = `
import sqlite3, sys
try:
    con = sqlite3.connect(sys.argv[1])
    cur = con.execute("SELECT COUNT(*) FROM episodes_fts")
    print(cur.fetchone()[0])
except Exception:
    print(-1)
`;
    await fs.writeFile(probeScript, probeContent);
    let newIndexed = -1;
    try {
      const { stdout: newOut } = await execAsync(
        `${pyCmd} "${probeScript}" "${dbPath}"`,
        { timeout: 10000 }
      );
      newIndexed = parseInt(newOut.trim(), 10);
    } finally {
      try { await fs.unlink(probeScript); } catch { /* ignore */ }
    }

    if (Number.isFinite(newIndexed) && newIndexed > indexed) {
      spinner.succeed(`FTS5 backfill: rebuilt ${newIndexed}/${total} episodes`);
    } else {
      spinner.warn(`FTS5 backfill completed but index still at ${newIndexed}/${total}`);
    }
  } catch (err) {
    spinner.warn(`FTS5 backfill skipped: ${err.message || 'unknown error'}`);
  }
}

// ============================================================================
// Post-Update Verification
// ============================================================================

async function runVerification() {
  const spinner = ora('Verifying installation health...').start();
  const checks = [];
  const issues = [];

  // 1. Hooks exist and are reachable
  const hookFiles = ['pre_tool_use.py', 'post_tool_use.py', 'subagent_stop.py'];
  for (const hook of hookFiles) {
    const path = join(CWD, '.claude', 'hooks', hook);
    if (existsSync(path)) {
      checks.push({ name: hook, ok: true });
    } else {
      checks.push({ name: hook, ok: false });
      issues.push(`Hook missing: .claude/hooks/${hook}`);
    }
  }

  // 2. Python available (try python3 first, fall back to python on Windows)
  {
    const pyCmd = findPython();
    if (pyCmd) {
      const { stdout } = await execAsync(`${pyCmd} --version`, { timeout: 5000 });
      checks.push({ name: 'python3', ok: true, detail: stdout.trim() });
    } else {
      checks.push({ name: 'python3', ok: false });
      issues.push('Python 3 not found (required for hooks)');
    }
  }

  // 3. project-context.json exists and is valid
  const ctxPath = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (existsSync(ctxPath)) {
    try {
      const ctx = JSON.parse(await fs.readFile(ctxPath, 'utf-8'));
      const sections = Object.keys(ctx.sections || {}).length;
      checks.push({ name: 'project-context.json', ok: sections >= 3, detail: `${sections} sections` });
      if (sections < 3) issues.push('project-context.json has fewer than 3 sections');
    } catch {
      checks.push({ name: 'project-context.json', ok: false });
      issues.push('project-context.json is invalid JSON');
    }
  } else {
    checks.push({ name: 'project-context.json', ok: false });
    issues.push('project-context.json not found (run gaia-scan)');
  }

  // 4. Config files accessible
  const configFiles = ['git_standards.json', 'universal-rules.json', 'surface-routing.json'];
  for (const cfg of configFiles) {
    const path = join(CWD, '.claude', 'config', cfg);
    if (existsSync(path)) {
      checks.push({ name: cfg, ok: true });
    } else {
      checks.push({ name: cfg, ok: false });
      if (VERBOSE) issues.push(`Config missing: .claude/config/${cfg}`);
    }
  }

  // 5. Agent definitions accessible
  const agentFiles = ['gaia-orchestrator.md', 'gaia-operator.md', 'terraform-architect.md', 'gitops-operator.md', 'cloud-troubleshooter.md', 'developer.md', 'gaia-system.md', 'gaia-planner.md'];
  let agentsOk = 0;
  for (const agent of agentFiles) {
    if (existsSync(join(CWD, '.claude', 'agents', agent))) agentsOk++;
  }
  checks.push({ name: 'agent definitions', ok: agentsOk === agentFiles.length, detail: `${agentsOk}/${agentFiles.length}` });
  if (agentsOk < agentFiles.length) issues.push(`${agentFiles.length - agentsOk} agent definition(s) missing`);

  // 6. hooks.json exists (hooks are auto-discovered from hooks directory)
  const hooksJsonPath = join(CWD, '.claude', 'hooks', 'hooks.json');
  if (existsSync(hooksJsonPath)) {
    try {
      const hooksData = JSON.parse(await fs.readFile(hooksJsonPath, 'utf-8'));
      const hasHooks = hooksData.hooks && Object.keys(hooksData.hooks).length > 0;
      checks.push({ name: 'hooks.json', ok: hasHooks });
      if (!hasHooks) issues.push('hooks.json has no hooks configured');
    } catch {
      checks.push({ name: 'hooks.json', ok: false });
      issues.push('hooks.json is invalid');
    }
  } else {
    checks.push({ name: 'hooks.json', ok: false });
    issues.push('hooks.json not found (hooks symlink may be broken)');
  }

  const passed = checks.filter(c => c.ok).length;
  const total = checks.length;

  if (issues.length === 0) {
    spinner.succeed(`Health check: ${passed}/${total} passed`);
  } else {
    spinner.warn(`Health check: ${passed}/${total} passed, ${issues.length} issue(s)`);
  }

  return { checks, issues, passed, total };
}

// ============================================================================
// Main
// ============================================================================

async function runFreshInstall() {
  const packageDir = join(__dirname, '..');
  const scanScript = join(packageDir, 'bin', 'gaia-scan.py');
  const { current } = await detectVersions();

  console.log(chalk.cyan(`\n  gaia-ops ${chalk.green(current)} — fresh install\n`));

  // 1. Check Python 3 is available (try python3, then python)
  const spinner = ora('Checking Python 3...').start();
  const pyCmd = findPython();
  if (pyCmd) {
    spinner.succeed(`Python 3 found (${pyCmd})`);
  } else {
    spinner.warn('Python 3 not found — skipping project setup');
    console.log(chalk.gray('  Install Python 3.9+ and run: npx gaia-scan\n'));
    return;
  }

  // 2. Run gaia-scan --npm-postinstall
  const scanSpinner = ora('Running gaia-scan...').start();
  try {
    const { stdout, stderr } = await execAsync(
      `${pyCmd} "${scanScript}" --npm-postinstall --root "${CWD}"`,
      { timeout: 60000 }
    );
    scanSpinner.succeed('Project scanned and configured');
    if (VERBOSE && stdout) console.log(chalk.gray(stdout));
    if (VERBOSE && stderr) console.log(chalk.yellow(stderr));
  } catch (error) {
    scanSpinner.warn('gaia-scan encountered issues (non-fatal)');
    if (VERBOSE && error.stderr) console.log(chalk.gray(error.stderr));
  }

  // 3. Create plugin-registry.json (in .claude/, same path Python hooks expect)
  try {
    const claudeDirPath = join(CWD, '.claude');
    if (!existsSync(claudeDirPath)) {
      await fs.mkdir(claudeDirPath, { recursive: true });
    }
    const registryPath = join(claudeDirPath, 'plugin-registry.json');
    const registry = {
      installed: [{ name: 'gaia-ops', version: current || 'unknown' }],
      source: 'npm-postinstall',
    };
    await fs.writeFile(registryPath, JSON.stringify(registry, null, 2) + '\n');
  } catch {
    // Non-fatal — plugin-registry is a convenience, not critical
  }

  // 4. Merge permissions into settings.local.json (same approach as plugin mode)
  await updateLocalPermissions();

  // 5. Merge hooks into settings.local.json (npm mode — Claude Code reads hooks from settings, not hooks.json)
  await updateLocalHooks();
}

async function main() {
  process.stderr.write('[DEPRECATED] gaia-update.js is deprecated. Use: python3 bin/gaia update\n[DEPRECATED] Migration guide: see CHANGELOG.md\n');

  const claudeDir = join(CWD, '.claude');
  const isUpdate = existsSync(claudeDir);

  if (!isUpdate) {
    // First-time install — run gaia-scan to bootstrap everything
    await runFreshInstall();
  } else {
    // Version info
    const { previous, current } = await detectVersions();
    const versionLine = previous && previous !== current
      ? `${chalk.gray(previous)} → ${chalk.green(current)}`
      : chalk.green(current);

    console.log(chalk.cyan(`\n  gaia-ops update ${versionLine}\n`));

    // Step 1-4: Update files
    await updateSettingsJson();
    await updateLocalPermissions();
    await updateLocalHooks();
    await updateSymlinks();
  }

  // Ensure plugin-registry.json exists in .claude/ (both fresh and update)
  try {
    const registryPath = join(CWD, '.claude', 'plugin-registry.json');
    if (!existsSync(registryPath)) {
      const { current } = await detectVersions();
      const registry = {
        installed: [{ name: 'gaia-ops', version: current || 'unknown' }],
        source: 'npm-postinstall',
      };
      await fs.writeFile(registryPath, JSON.stringify(registry, null, 2) + '\n');
    }
  } catch { /* non-fatal */ }

  // FTS5 backfill safety-net (no-op on fresh install; only fires when
  // episodes exist in index.json but search.db is under-indexed)
  await maybeBackfillFts5();

  // Verify (runs for both fresh install and update)
  const { issues, passed, total } = await runVerification();

  console.log('');
  if (issues.length > 0) {
    console.log(chalk.yellow(`  ${issues.length} issue(s) found:`));
    for (const issue of issues) {
      console.log(chalk.yellow(`    - ${issue}`));
    }
  } else {
    console.log(chalk.green('  Everything up to date'));
  }

  console.log(chalk.gray(`\n  Health: ${passed}/${total} checks passed\n`));
}

// Only execute main() when this file is invoked directly (not when imported
// for testing). This lets unit tests import internal helpers without
// triggering the postinstall side effects.
const _invokedDirectly = process.argv[1]
  && fileURLToPath(import.meta.url) === resolvePath(process.argv[1]);

if (_invokedDirectly) {
  main().catch(error => {
    console.error(chalk.red(`\n  Update failed: ${error.message}\n`));
    process.exit(0); // Never fail npm install
  });
}

export { resolveGaiaPackageName };
