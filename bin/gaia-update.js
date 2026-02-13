#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Auto-update script
 *
 * Runs automatically on npm install/update (postinstall hook).
 *
 * Behavior:
 * - First-time install (.claude/ doesn't exist): skip silently (gaia-init handles it)
 * - Update (.claude/ exists):
 *   - CLAUDE.md: overwrite safely (template is static, no project data to lose)
 *   - settings.json: MERGE new rules from template, preserve user additions
 *   - Symlinks: recreate if missing
 *
 * Usage: Automatic (npm postinstall hook)
 */

import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import chalk from 'chalk';
import ora from 'ora';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.env.INIT_CWD || process.cwd();

/**
 * Update CLAUDE.md from template (safe overwrite - template is static).
 */
async function updateClaudeMd() {
  const spinner = ora('Updating CLAUDE.md...').start();

  try {
    const templatePath = join(__dirname, '../templates/CLAUDE.template.md');
    const claudeDir = join(CWD, '.claude');

    if (!existsSync(templatePath)) {
      spinner.warn('Template not found, skipping CLAUDE.md update');
      return false;
    }

    if (!existsSync(claudeDir)) {
      spinner.info('First-time installation detected - skipping');
      return false;
    }

    const claudeMdPath = join(CWD, 'CLAUDE.md');
    await fs.copyFile(templatePath, claudeMdPath);

    spinner.succeed('CLAUDE.md updated (static template)');
    return true;
  } catch (error) {
    spinner.fail(`Failed to update CLAUDE.md: ${error.message}`);
    return false;
  }
}

/**
 * Merge settings.json: add new template rules, preserve user additions.
 *
 * Strategy:
 * - hooks: always replace from template (hooks are code, not config)
 * - permissions.allow: union (template + user custom rules)
 * - permissions.deny: union (template + user custom rules)
 * - permissions.ask: union (template + user custom rules)
 * - Other top-level keys: template wins (new features)
 */
async function updateSettingsJson() {
  const spinner = ora('Updating settings.json...').start();

  try {
    const templatePath = join(__dirname, '../templates/settings.template.json');
    const settingsPath = join(CWD, '.claude', 'settings.json');
    const claudeDir = join(CWD, '.claude');

    if (!existsSync(templatePath)) {
      spinner.warn('Settings template not found, skipping');
      return false;
    }

    if (!existsSync(claudeDir)) {
      spinner.info('First-time installation detected - skipping');
      return false;
    }

    const template = JSON.parse(await fs.readFile(templatePath, 'utf-8'));

    // If no existing settings, just write the template
    if (!existsSync(settingsPath)) {
      await fs.writeFile(settingsPath, JSON.stringify(template, null, 2), 'utf-8');
      spinner.succeed('settings.json created from template');
      return true;
    }

    // Read existing settings
    let existing;
    try {
      existing = JSON.parse(await fs.readFile(settingsPath, 'utf-8'));
    } catch {
      // Invalid JSON - replace with template
      await fs.writeFile(settingsPath, JSON.stringify(template, null, 2), 'utf-8');
      spinner.succeed('settings.json replaced (was invalid JSON)');
      return true;
    }

    // Merge strategy
    const merged = { ...template };

    // Hooks: always from template (these are code references, not user config)
    merged.hooks = template.hooks;

    // Permissions: union of template + user custom rules
    if (existing.permissions || template.permissions) {
      merged.permissions = mergePermissions(
        template.permissions || {},
        existing.permissions || {}
      );
    }

    await fs.writeFile(settingsPath, JSON.stringify(merged, null, 2), 'utf-8');
    spinner.succeed('settings.json merged (new rules added, custom preserved)');
    return true;
  } catch (error) {
    spinner.fail(`Failed to update settings.json: ${error.message}`);
    return false;
  }
}

/**
 * Merge permission arrays: union of template + user, template first.
 */
function mergePermissions(template, existing) {
  const result = {};

  const keys = new Set([...Object.keys(template), ...Object.keys(existing)]);

  for (const key of keys) {
    const tVal = template[key];
    const eVal = existing[key];

    if (Array.isArray(tVal) && Array.isArray(eVal)) {
      // Union: template rules first, then user additions not in template
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

/**
 * Recreate missing symlinks in .claude/ directory.
 */
async function recreateSymlinks() {
  const spinner = ora('Checking symlinks...').start();

  try {
    const claudeDir = join(CWD, '.claude');

    if (!existsSync(claudeDir)) {
      spinner.info('First-time installation detected - skipping');
      return false;
    }

    const packagePath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops');
    if (!existsSync(packagePath)) {
      spinner.fail('Package not found in node_modules');
      return false;
    }

    const { relative } = await import('path');
    const relativePath = relative(claudeDir, packagePath);

    const symlinks = [
      'agents', 'tools', 'hooks', 'commands',
      'templates', 'config', 'speckit'
    ];

    let recreated = 0;

    for (const name of symlinks) {
      const link = join(claudeDir, name);
      if (!existsSync(link)) {
        try {
          await fs.symlink(join(relativePath, name), link);
          recreated++;
        } catch {
          // Skip
        }
      }
    }

    // CHANGELOG.md
    const changelogLink = join(claudeDir, 'CHANGELOG.md');
    if (!existsSync(changelogLink)) {
      try {
        await fs.symlink(join(relativePath, 'CHANGELOG.md'), changelogLink);
        recreated++;
      } catch {
        // Skip
      }
    }

    if (recreated > 0) {
      spinner.succeed(`Recreated ${recreated} missing symlink(s)`);
      return true;
    }

    spinner.succeed('All symlinks valid');
    return false;
  } catch (error) {
    spinner.fail(`Failed to check symlinks: ${error.message}`);
    return false;
  }
}

async function main() {
  console.log(chalk.cyan('\n  @jaguilar87/gaia-ops auto-update\n'));

  try {
    const claudeDir = join(CWD, '.claude');
    const isUpdate = existsSync(claudeDir);

    if (!isUpdate) {
      // First-time install - gaia-init will handle everything
      process.exit(0);
    }

    const claudeUpdated = await updateClaudeMd();
    const settingsUpdated = await updateSettingsJson();
    const symlinksRecreated = await recreateSymlinks();

    if (claudeUpdated || settingsUpdated || symlinksRecreated) {
      console.log(chalk.green('\n  Auto-update completed\n'));
      if (settingsUpdated) {
        console.log(chalk.gray('  settings.json: new rules merged, your custom rules preserved'));
      }
      console.log(chalk.gray('  Run `npx gaia-doctor` to verify installation health\n'));
    }
  } catch (error) {
    console.error(chalk.red(`\n  Auto-update failed: ${error.message}\n`));
    process.exit(0); // Don't fail npm install
  }
}

main();
