#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Update script
 *
 * Runs automatically on npm install/update (postinstall hook).
 * Also available as: npx gaia-update
 *
 * Behavior:
 * - First-time install (.claude/ doesn't exist): skip silently (gaia-init handles it)
 * - Update (.claude/ exists):
 *   1. Show version transition (previous → current)
 *   2. CLAUDE.md: overwrite safely (template is static)
 *   3. settings.json: MERGE new rules, preserve user additions
 *   4. Symlinks: recreate if missing, fix broken ones
 *   5. Verify: hooks, python, project-context, config files
 *   6. Report: summary with any issues found
 *
 * Usage:
 *   npm update @jaguilar87/gaia-ops   # Automatic via postinstall
 *   npx gaia-update                   # Manual trigger
 *   npx gaia-update --verbose         # Show all checks
 */

import { fileURLToPath } from 'url';
import { dirname, join, relative } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import { exec } from 'child_process';
import { promisify } from 'util';
import chalk from 'chalk';
import ora from 'ora';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.env.INIT_CWD || process.cwd();
const VERBOSE = process.argv.includes('--verbose') || process.argv.includes('-v');

// ============================================================================
// Version Detection
// ============================================================================

async function detectVersions() {
  const current = await readPackageVersion(join(__dirname, '..', 'package.json'));

  // Try to find previous version from the installed package.json backup or lock
  let previous = null;
  try {
    const lockPath = join(CWD, 'package-lock.json');
    if (existsSync(lockPath)) {
      const lock = JSON.parse(await fs.readFile(lockPath, 'utf-8'));
      const dep = lock.packages?.['node_modules/@jaguilar87/gaia-ops']
        || lock.dependencies?.['@jaguilar87/gaia-ops'];
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

async function updateClaudeMd() {
  const spinner = ora('Updating CLAUDE.md...').start();
  try {
    const templatePath = join(__dirname, '../templates/CLAUDE.template.md');
    const claudeDir = join(CWD, '.claude');

    if (!existsSync(templatePath) || !existsSync(claudeDir)) {
      spinner.info('Skipped (template or .claude/ not found)');
      return false;
    }

    const claudeMdPath = join(CWD, 'CLAUDE.md');
    await fs.copyFile(templatePath, claudeMdPath);
    spinner.succeed('CLAUDE.md updated');
    return true;
  } catch (error) {
    spinner.fail(`CLAUDE.md: ${error.message}`);
    return false;
  }
}

async function updateSettingsJson() {
  const spinner = ora('Merging settings.json...').start();
  try {
    const templatePath = join(__dirname, '../templates/settings.template.json');
    const settingsPath = join(CWD, '.claude', 'settings.json');

    if (!existsSync(templatePath) || !existsSync(join(CWD, '.claude'))) {
      spinner.info('Skipped');
      return false;
    }

    const template = JSON.parse(await fs.readFile(templatePath, 'utf-8'));

    if (!existsSync(settingsPath)) {
      await fs.writeFile(settingsPath, JSON.stringify(template, null, 2), 'utf-8');
      spinner.succeed('settings.json created from template');
      return true;
    }

    let existing;
    try {
      existing = JSON.parse(await fs.readFile(settingsPath, 'utf-8'));
    } catch {
      await fs.writeFile(settingsPath, JSON.stringify(template, null, 2), 'utf-8');
      spinner.succeed('settings.json replaced (was invalid)');
      return true;
    }

    // Merge: hooks from template, permissions union
    const merged = { ...template };
    merged.hooks = template.hooks;

    if (existing.permissions || template.permissions) {
      merged.permissions = mergePermissions(
        template.permissions || {},
        existing.permissions || {}
      );
    }

    await fs.writeFile(settingsPath, JSON.stringify(merged, null, 2), 'utf-8');
    spinner.succeed('settings.json merged (custom rules preserved)');
    return true;
  } catch (error) {
    spinner.fail(`settings.json: ${error.message}`);
    return false;
  }
}

function mergePermissions(template, existing) {
  const result = {};
  const keys = new Set([...Object.keys(template), ...Object.keys(existing)]);

  for (const key of keys) {
    const tVal = template[key];
    const eVal = existing[key];

    if (Array.isArray(tVal) && Array.isArray(eVal)) {
      const templateSet = new Set(tVal);
      const userAdditions = eVal.filter(rule => !templateSet.has(rule));
      result[key] = [...tVal, ...userAdditions];
    } else if (tVal !== undefined) {
      result[key] = tVal;
    } else {
      result[key] = eVal;
    }
  }

  return result;
}

async function updateSymlinks() {
  const spinner = ora('Checking symlinks...').start();
  try {
    const claudeDir = join(CWD, '.claude');
    if (!existsSync(claudeDir)) {
      spinner.info('Skipped (.claude/ not found)');
      return { updated: false, fixed: 0, total: 0 };
    }

    const packagePath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops');
    if (!existsSync(packagePath)) {
      spinner.fail('Package not found in node_modules');
      return { updated: false, fixed: 0, total: 0 };
    }

    const relativePath = relative(claudeDir, packagePath);
    const symlinks = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'speckit'];
    let fixed = 0;

    for (const name of symlinks) {
      const link = join(claudeDir, name);
      const target = join(relativePath, name);

      if (!existsSync(link)) {
        try {
          await fs.symlink(target, link);
          fixed++;
        } catch { /* skip */ }
      } else {
        // Check if symlink is broken (target doesn't resolve)
        try {
          await fs.realpath(link);
        } catch {
          // Broken symlink — remove and recreate
          try {
            await fs.unlink(link);
            await fs.symlink(target, link);
            fixed++;
          } catch { /* skip */ }
        }
      }
    }

    // CHANGELOG.md
    const changelogLink = join(claudeDir, 'CHANGELOG.md');
    if (!existsSync(changelogLink)) {
      try {
        await fs.symlink(join(relativePath, 'CHANGELOG.md'), changelogLink);
        fixed++;
      } catch { /* skip */ }
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

  // 2. Python available
  try {
    const { stdout } = await execAsync('python3 --version', { timeout: 5000 });
    checks.push({ name: 'python3', ok: true, detail: stdout.trim() });
  } catch {
    checks.push({ name: 'python3', ok: false });
    issues.push('Python 3 not found (required for hooks)');
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
    issues.push('project-context.json not found (run gaia-init)');
  }

  // 4. Config files accessible
  const configFiles = ['classification-rules.json', 'git_standards.json', 'universal-rules.json'];
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
  const agentFiles = ['terraform-architect.md', 'gitops-operator.md', 'cloud-troubleshooter.md', 'devops-developer.md', 'gaia.md'];
  let agentsOk = 0;
  for (const agent of agentFiles) {
    if (existsSync(join(CWD, '.claude', 'agents', agent))) agentsOk++;
  }
  checks.push({ name: 'agent definitions', ok: agentsOk === agentFiles.length, detail: `${agentsOk}/${agentFiles.length}` });
  if (agentsOk < agentFiles.length) issues.push(`${agentFiles.length - agentsOk} agent definition(s) missing`);

  // 6. settings.json has hooks configured
  const settingsPath = join(CWD, '.claude', 'settings.json');
  if (existsSync(settingsPath)) {
    try {
      const settings = JSON.parse(await fs.readFile(settingsPath, 'utf-8'));
      const hasHooks = settings.hooks && Object.keys(settings.hooks).length > 0;
      checks.push({ name: 'hooks config', ok: hasHooks });
      if (!hasHooks) issues.push('settings.json has no hooks configured');
    } catch {
      checks.push({ name: 'hooks config', ok: false });
      issues.push('settings.json is invalid');
    }
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

async function main() {
  const claudeDir = join(CWD, '.claude');
  const isUpdate = existsSync(claudeDir);

  if (!isUpdate) {
    // First-time install — gaia-init handles everything
    process.exit(0);
  }

  // Version info
  const { previous, current } = await detectVersions();
  const versionLine = previous && previous !== current
    ? `${chalk.gray(previous)} → ${chalk.green(current)}`
    : chalk.green(current);

  console.log(chalk.cyan(`\n  gaia-ops update ${versionLine}\n`));

  // Step 1-3: Update files
  const claudeUpdated = await updateClaudeMd();
  const settingsUpdated = await updateSettingsJson();
  const { updated: symlinksUpdated, fixed: symlinksFix } = await updateSymlinks();

  // Step 4: Verify
  const { issues, passed, total } = await runVerification();

  // Summary
  const changes = [claudeUpdated, settingsUpdated, symlinksUpdated].filter(Boolean).length;

  console.log('');
  if (changes > 0 || issues.length > 0) {
    // Changes summary
    if (changes > 0) {
      console.log(chalk.green(`  ${changes} file(s) updated`));
      if (settingsUpdated) console.log(chalk.gray('    settings.json: new rules merged, custom rules preserved'));
      if (symlinksFix > 0) console.log(chalk.gray(`    ${symlinksFix} symlink(s) fixed`));
    }

    // Issues
    if (issues.length > 0) {
      console.log(chalk.yellow(`\n  ${issues.length} issue(s) found:`));
      for (const issue of issues) {
        console.log(chalk.yellow(`    - ${issue}`));
      }
    }
  } else {
    console.log(chalk.green('  Everything up to date'));
  }

  console.log(chalk.gray(`\n  Health: ${passed}/${total} checks passed\n`));
}

main().catch(error => {
  console.error(chalk.red(`\n  Update failed: ${error.message}\n`));
  process.exit(0); // Never fail npm install
});
