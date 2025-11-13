#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Auto-update script
 *
 * Runs automatically on npm install/update (postinstall hook)
 *
 * Purpose:
 * - Regenerate CLAUDE.md from template (OVERWRITES existing file)
 * - Regenerate settings.json from template (OVERWRITES existing file)
 * - Only runs if CLAUDE.md already exists (skip first-time install, let gaia-init handle it)
 *
 * ⚠️  WARNING: All customizations in CLAUDE.md and settings.json will be lost
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
// Use INIT_CWD (npm's original working directory) when available,
// otherwise fall back to process.cwd()
const CWD = process.env.INIT_CWD || process.cwd();

/**
 * Check if CLAUDE.md exists (to determine if this is first-time install)
 */
async function claudeMdExists() {
  const claudeMdPath = join(CWD, 'CLAUDE.md');
  return existsSync(claudeMdPath);
}

/**
 * Regenerate CLAUDE.md from template (OVERWRITES existing file)
 */
async function updateClaudeMd() {
  const spinner = ora('Updating CLAUDE.md...').start();

  try {
    // Get template path from gaia-ops package
    const templatePath = join(__dirname, '../templates/CLAUDE.template.md');

    if (!existsSync(templatePath)) {
      spinner.warn('Template not found, skipping CLAUDE.md update');
      return false;
    }

    const claudeMdPath = join(CWD, 'CLAUDE.md');

    // Check if this is first-time install
    if (!existsSync(claudeMdPath)) {
      // First time install - don't auto-generate, gaia-init handles it
      spinner.info('First-time installation detected - skipping auto-update');
      return false;
    }

    // Read template and copy it directly (no placeholder replacement)
    // The template will be copied as-is with placeholders intact
    const template = await fs.readFile(templatePath, 'utf-8');

    // Write updated CLAUDE.md (OVERWRITES existing file)
    await fs.writeFile(claudeMdPath, template, 'utf-8');

    spinner.succeed('CLAUDE.md updated successfully (existing file overwritten)');
    return true;
  } catch (error) {
    spinner.fail(`Failed to update CLAUDE.md: ${error.message}`);
    return false;
  }
}

/**
 * Regenerate settings.json from template (OVERWRITES existing file)
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

    // Only skip if .claude/ directory doesn't exist (true first-time install)
    if (!existsSync(claudeDir)) {
      spinner.info('First-time installation detected - skipping auto-update');
      return false;
    }

    // If .claude/ exists, ALWAYS regenerate settings.json
    // (even if settings.json was manually deleted)
    const template = await fs.readFile(templatePath, 'utf-8');
    await fs.writeFile(settingsPath, template, 'utf-8');

    spinner.succeed('settings.json updated successfully (existing file overwritten)');
    return true;
  } catch (error) {
    spinner.fail(`Failed to update settings.json: ${error.message}`);
    return false;
  }
}

/**
 * Main function
 */
async function main() {
  console.log(chalk.cyan('\n🔄 @jaguilar87/gaia-ops auto-update\n'));

  try {
    // Check if this is an update (not first-time install)
    const isUpdate = await claudeMdExists();

    if (isUpdate) {
      // Show warning before overwriting files
      console.log(chalk.yellow('⚠️  WARNING: The following files will be OVERWRITTEN:'));
      console.log(chalk.yellow('  • CLAUDE.md (all customizations will be lost)'));
      console.log(chalk.yellow('  • .claude/settings.json (all customizations will be lost)'));
      console.log(chalk.gray('\n  Files will be regenerated from templates...\n'));
    }

    const claudeUpdated = await updateClaudeMd();
    const settingsUpdated = await updateSettingsJson();

    if (claudeUpdated || settingsUpdated) {
      console.log(chalk.green('\n✅ Auto-update completed\n'));
      console.log(chalk.yellow('⚠️  IMPORTANT: Files have been overwritten from templates'));
      console.log(chalk.gray('\nNext steps:'));
      if (claudeUpdated) {
        console.log(chalk.gray('  • Configure CLAUDE.md with your project paths'));
      }
      if (settingsUpdated) {
        console.log(chalk.gray('  • Review .claude/settings.json for security rules'));
      }
      console.log(chalk.gray('\n  Tip: Run "gaia-init" to reconfigure from scratch\n'));
    } else {
      // Silent exit on first-time install (gaia-init will handle it)
      process.exit(0);
    }
  } catch (error) {
    console.error(chalk.red(`\n❌ Auto-update failed: ${error.message}\n`));
    // Don't fail npm install, just warn
    process.exit(0);
  }
}

main();
