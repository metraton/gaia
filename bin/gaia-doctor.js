#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Health Check CLI
 *
 * Verifies the complete Gaia-Ops installation is healthy.
 * Run after install, update, or when things seem broken.
 *
 * Severity levels:
 *   pass    - check passed
 *   info    - informational, not an issue (e.g., prerequisites, auto-created dirs)
 *   warning - actual issue that degrades functionality
 *   error   - critical issue, Gaia won't work
 *
 * Usage:
 *   npx gaia-doctor              # Full health check
 *   npx gaia-doctor --fix        # Attempt auto-fix for common issues
 *   npx gaia-doctor --json       # Output as JSON (for CI)
 */

import { fileURLToPath } from 'url';
import { dirname, join, relative } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import { exec } from 'child_process';
import { promisify } from 'util';
import { findPython } from './python-detect.js';
import chalk from 'chalk';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.cwd();

// ============================================================================
// Severity helpers
// ============================================================================

/** Create a check result with explicit severity. */
function result(name, severity, detail, fix = null) {
  // Backward-compatible `ok` field: pass and info are considered ok
  const ok = severity === 'pass' || severity === 'info';
  return { name, severity, ok, detail, ...(fix ? { fix } : {}) };
}

// ============================================================================
// Health Checks
// ============================================================================

async function checkGaiaVersion() {
  try {
    const pkg = JSON.parse(await fs.readFile(join(__dirname, '..', 'package.json'), 'utf-8'));
    return result('Gaia-Ops', 'pass', `v${pkg.version}`);
  } catch {
    return result('Gaia-Ops', 'error', 'Version unknown', 'Reinstall @jaguilar87/gaia-ops');
  }
}

async function checkSymlinks() {
  const names = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'speckit', 'skills', 'CHANGELOG.md'];
  // Critical symlinks that break core functionality if missing
  const critical = new Set(['agents', 'hooks', 'skills']);
  const sub = [];
  let valid = 0;
  let hasCriticalMissing = false;

  for (const name of names) {
    const linkPath = join(CWD, '.claude', name);
    const exists = existsSync(linkPath);

    if (exists) {
      try {
        await fs.realpath(linkPath);
        valid++;
        sub.push({ name, status: 'ok' });
      } catch {
        if (critical.has(name)) hasCriticalMissing = true;
        sub.push({ name, status: 'broken', fix: `rm .claude/${name} && gaia-scan` });
      }
    } else {
      if (critical.has(name)) hasCriticalMissing = true;
      sub.push({ name, status: 'missing', fix: 'Run gaia-scan to recreate' });
    }
  }

  if (valid === names.length) {
    return { ...result('Symlinks', 'pass', `${valid}/${names.length} valid`), sub };
  }

  const severity = hasCriticalMissing ? 'error' : 'warning';
  return {
    ...result('Symlinks', severity, `${valid}/${names.length} valid`, 'Run gaia-scan to recreate symlinks'),
    sub
  };
}

async function checkClaudeMd() {
  // CLAUDE.md is no longer generated -- identity is injected by UserPromptSubmit hook.
  const path = join(CWD, 'CLAUDE.md');
  if (existsSync(path)) {
    return result('CLAUDE.md', 'pass', 'Present (legacy -- identity now injected by hook)');
  }
  return result('CLAUDE.md', 'pass', 'Not present (identity injected by UserPromptSubmit hook)');
}

async function checkSettingsJson() {
  const path = join(CWD, '.claude', 'settings.json');

  if (!existsSync(path)) {
    return result('settings.json', 'error', 'Missing', 'Run gaia-scan');
  }

  try {
    const data = JSON.parse(await fs.readFile(path, 'utf-8'));
    const hookIssues = [];
    const infoItems = [];

    // Check hooks -- may live in settings.json OR settings.local.json
    let hooksConfig = data.hooks || null;
    const localPath = join(CWD, '.claude', 'settings.local.json');
    if (!hooksConfig && existsSync(localPath)) {
      try {
        const localData = JSON.parse(await fs.readFile(localPath, 'utf-8'));
        if (localData.hooks) hooksConfig = localData.hooks;
      } catch { /* ignore parse errors */ }
    }

    if (!hooksConfig) {
      hookIssues.push('No hooks configured (check settings.json and settings.local.json)');
    } else {
      const hookTypes = Object.keys(hooksConfig);
      if (!hookTypes.includes('PreToolUse')) hookIssues.push('Missing PreToolUse hook');
      if (!hookTypes.includes('PostToolUse')) hookIssues.push('Missing PostToolUse hook');
    }

    // Check permissions -- live in settings.local.json (not settings.json)
    let permCount = 0;
    if (existsSync(localPath)) {
      try {
        const localData = JSON.parse(await fs.readFile(localPath, 'utf-8'));
        if (localData.permissions) {
          permCount = Object.values(localData.permissions).flat().length;
        }
      } catch { /* ignore parse errors */ }
    }
    // Also count permissions in settings.json (legacy installs)
    if (data.permissions) {
      permCount += Object.values(data.permissions).flat().length;
    }
    if (permCount === 0) {
      // Zero permissions is not an error -- just means no auto-allowed tools
      infoItems.push('No permissions configured (tools will prompt for approval)');
    }

    // Hook issues are errors (injection breaks without hooks)
    if (hookIssues.length > 0) {
      return result('settings.json', 'error', hookIssues.join('; '), 'Run gaia-scan or npx gaia-update');
    }

    // Info items are informational, not problems
    if (infoItems.length > 0) {
      const hookCount = hooksConfig ? Object.keys(hooksConfig).length : 0;
      return result('settings.json', 'info', `${hookCount} hook types, ${permCount} rules -- ${infoItems.join('; ')}`);
    }

    const hookCount = hooksConfig ? Object.keys(hooksConfig).length : 0;
    return result('settings.json', 'pass', `${hookCount} hook types, ${permCount} rules`);
  } catch {
    return result('settings.json', 'error', 'Invalid JSON', 'Delete and run gaia-scan');
  }
}

async function checkProjectContext() {
  const path = join(CWD, '.claude', 'project-context', 'project-context.json');

  if (!existsSync(path)) {
    return result('project-context', 'warning', 'Missing', 'Run gaia-scan or /speckit.init');
  }

  try {
    const data = JSON.parse(await fs.readFile(path, 'utf-8'));
    const warnings = [];
    const infos = [];

    if (!data.metadata) warnings.push('Missing metadata section');
    if (!data.sections) warnings.push('Missing sections');

    // Detect schema version: v2.0 uses metadata.version, v1.0 does not
    const isV2 = data.metadata?.version === '2.0' || data.metadata?.created_by === 'gaia-scan';

    // Check paths: v2.0 uses sections.infrastructure.paths, v1.0 uses top-level paths
    const hasPaths = isV2
      ? !!data.sections?.infrastructure?.paths
      : !!data.paths;
    if (!hasPaths) infos.push('No paths section');

    // Cloud provider and region are informational -- not all projects use cloud
    if (data.metadata) {
      const cloudProvider = isV2
        ? data.sections?.infrastructure?.cloud_providers?.[0]?.name
        : data.metadata.cloud_provider;
      if (!cloudProvider) infos.push('No cloud provider set');

      const region = isV2
        ? data.sections?.terraform_infrastructure?.provider_credentials?.gcp?.region
        : data.metadata.primary_region;
      if (!isV2 && !region) infos.push('No region set');
    }

    if (data.sections) {
      const sectionCount = Object.keys(data.sections).length;
      if (sectionCount < 3) infos.push(`Only ${sectionCount} sections (expected >=3)`);
    }

    // Warnings are real problems
    if (warnings.length > 0) {
      const detail = [...warnings, ...infos].join('; ');
      return result('project-context', 'warning', detail, 'Run /speckit.init to enrich');
    }

    // Info-only items are not problems
    if (infos.length > 0) {
      const sectionCount = data.sections ? Object.keys(data.sections).length : 0;
      return result('project-context', 'info', `${sectionCount} sections -- ${infos.join('; ')}`);
    }

    const sectionCount = Object.keys(data.sections).length;
    const cloud = isV2
      ? (data.sections?.infrastructure?.cloud_providers?.[0]?.name?.toUpperCase() || '?')
      : (data.metadata.cloud_provider?.toUpperCase() || '?');
    return result('project-context', 'pass', `${sectionCount} sections, ${cloud}`);
  } catch {
    return result('project-context', 'warning', 'Invalid JSON', 'Regenerate with /speckit.init');
  }
}

async function checkPython() {
  const pyCmd = findPython();
  if (!pyCmd) {
    return result('Python', 'error', 'Not found (hooks require Python)', 'Install Python 3.9+');
  }

  try {
    const { stdout } = await execAsync(`${pyCmd} --version`);
    const version = stdout.trim();
    const match = version.match(/(\d+)\.(\d+)/);

    if (match) {
      const major = parseInt(match[1]);
      const minor = parseInt(match[2]);
      if (major < 3 || (major === 3 && minor < 9)) {
        return result('Python', 'error', `${version} (need >=3.9)`, 'Upgrade Python to 3.9+');
      }
    }

    return result('Python', 'pass', version);
  } catch {
    return result('Python', 'error', 'Not found (hooks require Python)', 'Install Python 3.9+');
  }
}

async function checkClaudeCode() {
  try {
    const { stdout } = await execAsync('claude --version 2>/dev/null || claude-code --version 2>/dev/null');
    return result('Claude Code', 'pass', stdout.trim().split('\n')[0]);
  } catch {
    // Claude Code is a prerequisite, not a Gaia issue -- informational only
    return result('Claude Code', 'info', 'Not installed', 'npm install -g @anthropic-ai/claude-code');
  }
}

async function checkHooks() {
  const hooks = [
    { file: 'pre_tool_use.py', required: true },
    { file: 'post_tool_use.py', required: true },
    { file: 'subagent_stop.py', required: false }
  ];

  const errors = [];
  const infos = [];
  let valid = 0;

  for (const { file, required } of hooks) {
    const hookPath = join(CWD, '.claude', 'hooks', file);
    if (existsSync(hookPath)) {
      valid++;
    } else if (required) {
      errors.push(`${file} missing`);
    } else {
      infos.push(`${file} optional, not present`);
    }
  }

  if (errors.length > 0) {
    return result('Hooks', 'error', errors.join('; '), 'Recreate symlinks: gaia-scan');
  }

  if (infos.length > 0) {
    return result('Hooks', 'pass', `${valid}/${hooks.length} found (${infos.join('; ')})`);
  }

  return result('Hooks', 'pass', `${valid}/${hooks.length} found`);
}

async function checkMemoryDirs() {
  // Each memory dir has its own severity -- some are auto-created, some need manual setup
  const checks = [
    {
      path: join(CWD, '.claude', 'project-context', 'speckit-project-specs'),
      label: 'speckit-project-specs',
      severity: 'warning',
      fix: 'Run gaia-scan or /speckit.init'
    },
    {
      path: join(CWD, '.claude', 'project-context', 'speckit-project-specs', 'governance.md'),
      label: 'governance.md',
      severity: 'warning',
      fix: 'Run /speckit.init to generate governance.md'
    },
    {
      path: join(CWD, '.claude', 'project-context', 'workflow-episodic-memory'),
      label: 'workflow-episodic-memory',
      severity: 'warning',
      fix: 'Run gaia-scan to create workflow memory directory'
    },
    {
      path: join(CWD, '.claude', 'project-context', 'episodic-memory'),
      label: 'episodic-memory',
      severity: 'info',  // Auto-created on first agent run
      fix: 'Created automatically on first agent run'
    },
  ];

  const warnings = [];
  const infos = [];
  let found = 0;

  for (const { path, label, severity, fix } of checks) {
    if (existsSync(path)) {
      found++;
    } else if (severity === 'info') {
      infos.push({ label, fix });
    } else {
      warnings.push({ label, fix });
    }
  }

  if (warnings.length > 0) {
    const detail = warnings.map(i => `${i.label} missing`).join('; ');
    return result('Memory dirs', 'warning', detail, warnings[0].fix);
  }

  if (infos.length > 0) {
    const detail = `${found}/${checks.length} present (${infos.map(i => `${i.label}: ${i.fix}`).join('; ')})`;
    return result('Memory dirs', 'info', detail);
  }

  return result('Memory dirs', 'pass', `${found}/${checks.length} present`);
}

async function checkProjectDirs() {
  const contextPath = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (!existsSync(contextPath)) {
    return result('Project dirs', 'pass', 'Skipped (no context)');
  }

  try {
    const data = JSON.parse(await fs.readFile(contextPath, 'utf-8'));
    // v2.0 stores paths in sections.infrastructure.paths; v1.0 uses top-level paths
    const paths = data.sections?.infrastructure?.paths || data.paths || {};
    const issues = [];

    for (const [key, dirPath] of Object.entries(paths)) {
      if (dirPath && !existsSync(join(CWD, dirPath))) {
        issues.push(`${key}: ${dirPath} not found`);
      }
    }

    if (issues.length > 0) {
      return result('Project dirs', 'warning', issues.join('; '), 'Create missing directories or update paths');
    }

    return result('Project dirs', 'pass', `${Object.keys(paths).length} paths verified`);
  } catch {
    return result('Project dirs', 'pass', 'Skipped (parse error)');
  }
}

// ============================================================================
// Auto-fix
// ============================================================================

async function autoFix() {
  console.log(chalk.cyan('\n  Attempting auto-fix...\n'));

  let fixed = 0;

  // Fix broken symlinks
  const claudeDir = join(CWD, '.claude');
  if (existsSync(claudeDir)) {
    const packagePath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops');
    if (existsSync(packagePath)) {
      const relPath = relative(claudeDir, packagePath);
      const names = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'speckit', 'skills'];
      // Use junctions on Windows (no admin required), regular symlinks elsewhere
      const linkType = process.platform === 'win32' ? 'junction' : 'dir';

      for (const name of names) {
        const link = join(claudeDir, name);
        if (!existsSync(link)) {
          try {
            // Junctions on Windows require absolute targets; symlinks on Unix use relative
            const target = process.platform === 'win32'
              ? join(packagePath, name)
              : join(relPath, name);
            await fs.symlink(target, link, linkType);
            console.log(chalk.green(`    Fixed: .claude/${name} symlink`));
            fixed++;
          } catch {
            console.log(chalk.red(`    Failed: .claude/${name}`));
          }
        }
      }
    }
  }

  // Create missing project dirs
  const contextPath = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (existsSync(contextPath)) {
    try {
      const data = JSON.parse(await fs.readFile(contextPath, 'utf-8'));
      const paths = data.sections?.infrastructure?.paths || data.paths || {};
      for (const dirPath of Object.values(paths)) {
        const abs = join(CWD, dirPath);
        if (!existsSync(abs)) {
          await fs.mkdir(abs, { recursive: true });
          console.log(chalk.green(`    Fixed: Created ${dirPath}`));
          fixed++;
        }
      }
    } catch {
      // Skip
    }
  }

  console.log(chalk.cyan(`\n  ${fixed} issue(s) fixed\n`));
  return fixed;
}

// ============================================================================
// Display helpers
// ============================================================================

const SEVERITY_ICONS = {
  pass:    { icon: '\u2713', color: chalk.green },     // check mark
  info:    { icon: '\u2139', color: chalk.cyan },      // info symbol
  warning: { icon: '\u26A0', color: chalk.yellow },    // warning triangle
  error:   { icon: '\u2717', color: chalk.red },       // X mark
};

function severityIcon(severity) {
  const entry = SEVERITY_ICONS[severity] || SEVERITY_ICONS.warning;
  return entry.color(entry.icon);
}

function severityDetail(severity, detail) {
  switch (severity) {
    case 'pass': return chalk.gray(detail || '');
    case 'info': return chalk.cyan(detail);
    case 'warning': return chalk.yellow(detail);
    case 'error': return chalk.red(detail);
    default: return detail;
  }
}

// ============================================================================
// Main
// ============================================================================

async function main() {
  const args = yargs(hideBin(process.argv))
    .usage('Usage: $0 [options]')
    .option('fix', { type: 'boolean', description: 'Attempt auto-fix for common issues', default: false })
    .option('json', { type: 'boolean', description: 'Output as JSON', default: false })
    .help('h').alias('h', 'help')
    .version(false)
    .parse();

  // Run all checks
  const checks = [
    checkGaiaVersion,
    checkClaudeCode,
    checkSymlinks,
    checkClaudeMd,
    checkSettingsJson,
    checkProjectContext,
    checkPython,
    checkHooks,
    checkProjectDirs,
    checkMemoryDirs
  ];

  const results = [];
  for (const check of checks) {
    try {
      results.push(await check());
    } catch (error) {
      results.push(result(check.name, 'error', `Error: ${error.message}`));
    }
  }

  // Compute overall status from severity levels
  const hasErrors = results.some(r => r.severity === 'error');
  const hasWarnings = results.some(r => r.severity === 'warning');

  // JSON output mode
  if (args.json) {
    const status = hasErrors ? 'critical' : hasWarnings ? 'degraded' : 'healthy';
    console.log(JSON.stringify({ healthy: !hasErrors && !hasWarnings, status, checks: results }, null, 2));
    process.exit(hasErrors ? 2 : hasWarnings ? 1 : 0);
  }

  // Human-readable output
  const gaiaCheck = results.find(r => r.name === 'Gaia-Ops');
  const versionTag = gaiaCheck?.severity === 'pass' ? chalk.gray(` (${gaiaCheck.detail})`) : '';
  console.log(chalk.cyan(`\n  Gaia-Ops Health Check${versionTag}\n`));

  for (const r of results) {
    const icon = severityIcon(r.severity);
    const detail = severityDetail(r.severity, r.detail || '');
    console.log(`    ${icon} ${r.name.padEnd(18)} ${detail}`);

    if ((r.severity === 'warning' || r.severity === 'error') && r.fix) {
      console.log(chalk.gray(`      Fix: ${r.fix}`));
    }
  }

  console.log('');

  if (hasErrors) {
    console.log(chalk.red.bold('  Status: CRITICAL\n'));
    if (args.fix) {
      await autoFix();
    } else {
      console.log(chalk.gray('  Run with --fix to attempt auto-repair\n'));
    }
  } else if (hasWarnings) {
    console.log(chalk.yellow.bold('  Status: ISSUES FOUND\n'));
    if (args.fix) {
      await autoFix();
    } else {
      console.log(chalk.gray('  Run with --fix to attempt auto-repair\n'));
    }
  } else {
    console.log(chalk.green.bold('  Status: HEALTHY\n'));
  }

  process.exit(hasErrors ? 2 : hasWarnings ? 1 : 0);
}

main();
