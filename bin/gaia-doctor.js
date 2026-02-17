#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Health Check CLI
 *
 * Verifies the complete Gaia-Ops installation is healthy.
 * Run after install, update, or when things seem broken.
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
import chalk from 'chalk';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.cwd();

// ============================================================================
// Health Checks
// ============================================================================

async function checkGaiaVersion() {
  try {
    const pkg = JSON.parse(await fs.readFile(join(__dirname, '..', 'package.json'), 'utf-8'));
    return { name: 'Gaia-Ops', ok: true, detail: `v${pkg.version}` };
  } catch {
    return { name: 'Gaia-Ops', ok: false, detail: 'Version unknown', fix: 'Reinstall @jaguilar87/gaia-ops' };
  }
}

async function checkSymlinks() {
  const names = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'speckit', 'skills', 'CHANGELOG.md'];
  const results = [];
  let valid = 0;

  for (const name of names) {
    const linkPath = join(CWD, '.claude', name);
    const exists = existsSync(linkPath);

    if (exists) {
      // Verify symlink target actually resolves
      try {
        await fs.realpath(linkPath);
        valid++;
        results.push({ name, status: 'ok' });
      } catch {
        results.push({ name, status: 'broken', fix: `rm .claude/${name} && gaia-init` });
      }
    } else {
      results.push({ name, status: 'missing', fix: 'Run gaia-init to recreate' });
    }
  }

  return {
    name: 'Symlinks',
    ok: valid === names.length,
    detail: `${valid}/${names.length} valid`,
    fix: valid < names.length ? 'Run gaia-init to recreate symlinks' : null,
    sub: results
  };
}

async function checkClaudeMd() {
  const path = join(CWD, 'CLAUDE.md');

  if (!existsSync(path)) {
    return { name: 'CLAUDE.md', ok: false, detail: 'Missing', fix: 'Run gaia-init' };
  }

  const content = await fs.readFile(path, 'utf-8');
  const issues = [];

  if (content.includes('{{')) {
    issues.push('Contains raw {{placeholders}} - template was not processed');
  }

  if (content.length < 100) {
    issues.push('File is suspiciously short');
  }

  if (!content.includes('Binary Delegation') && !content.includes('orchestrator')) {
    issues.push('Missing core orchestrator instructions');
  }

  if (issues.length > 0) {
    return { name: 'CLAUDE.md', ok: false, detail: issues.join('; '), fix: 'Run gaia-init to regenerate' };
  }

  const lines = content.split('\n').length;
  return { name: 'CLAUDE.md', ok: true, detail: `Valid (${lines} lines)` };
}

async function checkSettingsJson() {
  const path = join(CWD, '.claude', 'settings.json');

  if (!existsSync(path)) {
    return { name: 'settings.json', ok: false, detail: 'Missing', fix: 'Run gaia-init' };
  }

  try {
    const data = JSON.parse(await fs.readFile(path, 'utf-8'));
    const issues = [];

    // Check hooks are configured
    if (!data.hooks) {
      issues.push('No hooks configured');
    } else {
      const hookTypes = Object.keys(data.hooks);
      if (!hookTypes.includes('PreToolUse')) issues.push('Missing PreToolUse hook');
      if (!hookTypes.includes('PostToolUse')) issues.push('Missing PostToolUse hook');
    }

    // Check permissions exist
    if (!data.permissions) {
      issues.push('No permissions configured');
    }

    if (issues.length > 0) {
      return { name: 'settings.json', ok: false, detail: issues.join('; '), fix: 'Run gaia-init' };
    }

    const hookCount = data.hooks ? Object.keys(data.hooks).length : 0;
    const permCount = data.permissions ? Object.values(data.permissions).flat().length : 0;
    return { name: 'settings.json', ok: true, detail: `${hookCount} hook types, ${permCount} rules` };
  } catch {
    return { name: 'settings.json', ok: false, detail: 'Invalid JSON', fix: 'Delete and run gaia-init' };
  }
}

async function checkProjectContext() {
  const path = join(CWD, '.claude', 'project-context', 'project-context.json');

  if (!existsSync(path)) {
    return { name: 'project-context', ok: false, detail: 'Missing', fix: 'Run gaia-init or /speckit.init' };
  }

  try {
    const data = JSON.parse(await fs.readFile(path, 'utf-8'));
    const issues = [];

    if (!data.metadata) issues.push('Missing metadata section');
    if (!data.paths) issues.push('Missing paths section');
    if (!data.sections) issues.push('Missing sections');

    if (data.metadata) {
      if (!data.metadata.cloud_provider) issues.push('No cloud provider set');
      if (!data.metadata.primary_region) issues.push('No region set');
    }

    if (data.sections) {
      const sectionCount = Object.keys(data.sections).length;
      if (sectionCount < 3) issues.push(`Only ${sectionCount} sections (expected >=3)`);
    }

    if (issues.length > 0) {
      return { name: 'project-context', ok: false, detail: issues.join('; '), fix: 'Run /speckit.init to enrich' };
    }

    const sectionCount = Object.keys(data.sections).length;
    const cloud = data.metadata.cloud_provider?.toUpperCase() || '?';
    return { name: 'project-context', ok: true, detail: `${sectionCount} sections, ${cloud}` };
  } catch {
    return { name: 'project-context', ok: false, detail: 'Invalid JSON', fix: 'Regenerate with /speckit.init' };
  }
}

async function checkPython() {
  try {
    const { stdout } = await execAsync('python3 --version');
    const version = stdout.trim();
    const match = version.match(/(\d+)\.(\d+)/);

    if (match) {
      const major = parseInt(match[1]);
      const minor = parseInt(match[2]);
      if (major < 3 || (major === 3 && minor < 9)) {
        return { name: 'Python', ok: false, detail: `${version} (need >=3.9)`, fix: 'Upgrade Python to 3.9+' };
      }
    }

    return { name: 'Python', ok: true, detail: version };
  } catch {
    return { name: 'Python', ok: false, detail: 'Not found', fix: 'Install Python 3.9+' };
  }
}

async function checkClaudeCode() {
  try {
    const { stdout } = await execAsync('claude --version 2>/dev/null || claude-code --version 2>/dev/null');
    return { name: 'Claude Code', ok: true, detail: stdout.trim().split('\n')[0] };
  } catch {
    return { name: 'Claude Code', ok: false, detail: 'Not installed', fix: 'npm install -g @anthropic-ai/claude-code' };
  }
}

async function checkHooks() {
  const hooks = [
    { file: 'pre_tool_use.py', required: true },
    { file: 'post_tool_use.py', required: true },
    { file: 'subagent_stop.py', required: false }
  ];

  const issues = [];
  let valid = 0;

  for (const { file, required } of hooks) {
    const hookPath = join(CWD, '.claude', 'hooks', file);
    if (existsSync(hookPath)) {
      valid++;
    } else if (required) {
      issues.push(`${file} missing`);
    }
  }

  if (issues.length > 0) {
    return { name: 'Hooks', ok: false, detail: issues.join('; '), fix: 'Recreate symlinks: gaia-init' };
  }

  return { name: 'Hooks', ok: true, detail: `${valid}/${hooks.length} found` };
}

async function checkProjectDirs() {
  const contextPath = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (!existsSync(contextPath)) {
    return { name: 'Project dirs', ok: true, detail: 'Skipped (no context)' };
  }

  try {
    const data = JSON.parse(await fs.readFile(contextPath, 'utf-8'));
    const paths = data.paths || {};
    const issues = [];

    for (const [key, dirPath] of Object.entries(paths)) {
      if (dirPath && !existsSync(join(CWD, dirPath))) {
        issues.push(`${key}: ${dirPath} not found`);
      }
    }

    if (issues.length > 0) {
      return { name: 'Project dirs', ok: false, detail: issues.join('; '), fix: 'Create missing directories or update paths' };
    }

    return { name: 'Project dirs', ok: true, detail: `${Object.keys(paths).length} paths verified` };
  } catch {
    return { name: 'Project dirs', ok: true, detail: 'Skipped (parse error)' };
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

      for (const name of names) {
        const link = join(claudeDir, name);
        if (!existsSync(link)) {
          try {
            await fs.symlink(join(relPath, name), link);
            console.log(chalk.green(`    Fixed: .claude/${name} symlink`));
            fixed++;
          } catch {
            console.log(chalk.red(`    Failed: .claude/${name}`));
          }
        }
      }
    }
  }

  // Fix CLAUDE.md with raw placeholders
  const claudeMdPath = join(CWD, 'CLAUDE.md');
  if (existsSync(claudeMdPath)) {
    const content = await fs.readFile(claudeMdPath, 'utf-8');
    if (content.includes('{{')) {
      const templatePath = join(CWD, '.claude', 'templates', 'CLAUDE.template.md');
      if (existsSync(templatePath)) {
        await fs.copyFile(templatePath, claudeMdPath);
        console.log(chalk.green('    Fixed: CLAUDE.md regenerated from template'));
        fixed++;
      }
    }
  }

  // Create missing project dirs
  const contextPath = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (existsSync(contextPath)) {
    try {
      const data = JSON.parse(await fs.readFile(contextPath, 'utf-8'));
      for (const dirPath of Object.values(data.paths || {})) {
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
    checkProjectDirs
  ];

  const results = [];
  for (const check of checks) {
    try {
      results.push(await check());
    } catch (error) {
      results.push({ name: check.name, ok: false, detail: `Error: ${error.message}` });
    }
  }

  // JSON output mode
  if (args.json) {
    const allOk = results.every(r => r.ok);
    console.log(JSON.stringify({ healthy: allOk, checks: results }, null, 2));
    process.exit(allOk ? 0 : 1);
  }

  // Human-readable output
  // Extract version for header
  const gaiaCheck = results.find(r => r.name === 'Gaia-Ops');
  const versionTag = gaiaCheck?.ok ? chalk.gray(` (${gaiaCheck.detail})`) : '';
  console.log(chalk.cyan(`\n  Gaia-Ops Health Check${versionTag}\n`));

  let allOk = true;

  for (const result of results) {
    const icon = result.ok ? chalk.green('✓') : chalk.yellow('⚠');
    const detail = result.ok ? chalk.gray(result.detail || '') : chalk.yellow(result.detail);
    console.log(`    ${icon} ${result.name.padEnd(18)} ${detail}`);

    if (!result.ok && result.fix) {
      console.log(chalk.gray(`      Fix: ${result.fix}`));
    }

    if (!result.ok) allOk = false;
  }

  console.log('');

  if (allOk) {
    console.log(chalk.green.bold('  Status: HEALTHY\n'));
  } else {
    console.log(chalk.yellow.bold('  Status: ISSUES FOUND\n'));

    if (args.fix) {
      await autoFix();
    } else {
      console.log(chalk.gray('  Run with --fix to attempt auto-repair\n'));
    }
  }

  process.exit(allOk ? 0 : 1);
}

main();
