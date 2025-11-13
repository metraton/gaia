#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Cleanup script
 *
 * Runs automatically on npm uninstall (preuninstall hook)
 *
 * Purpose:
 * - Remove CLAUDE.md
 * - Remove settings.json
 * - Remove all symlinks (agents, tools, hooks, commands, config, templates, speckit, CHANGELOG.md)
 * - Preserve project-specific data (logs, tests, project-context, session, metrics)
 *
 * Usage: Automatic (npm preuninstall hook)
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
 * Remove CLAUDE.md if it exists
 */
async function removeClaudeMd() {
  const spinner = ora('Removing CLAUDE.md...').start();

  try {
    const claudeMdPath = join(CWD, 'CLAUDE.md');

    if (!existsSync(claudeMdPath)) {
      spinner.info('CLAUDE.md not found, skipping');
      return false;
    }

    await fs.unlink(claudeMdPath);
    spinner.succeed('CLAUDE.md removed');
    return true;
  } catch (error) {
    spinner.fail(`Failed to remove CLAUDE.md: ${error.message}`);
    return false;
  }
}

/**
 * Remove settings.json if it exists
 */
async function removeSettingsJson() {
  const spinner = ora('Removing settings.json...').start();

  try {
    const settingsPath = join(CWD, '.claude', 'settings.json');

    if (!existsSync(settingsPath)) {
      spinner.info('settings.json not found, skipping');
      return false;
    }

    await fs.unlink(settingsPath);
    spinner.succeed('settings.json removed');
    return true;
  } catch (error) {
    spinner.fail(`Failed to remove settings.json: ${error.message}`);
    return false;
  }
}

/**
 * Remove all symlinks in .claude/ directory
 */
async function removeSymlinks() {
  const spinner = ora('Removing symlinks...').start();

  try {
    const claudeDir = join(CWD, '.claude');

    if (!existsSync(claudeDir)) {
      spinner.info('.claude/ directory not found, skipping');
      return false;
    }

    // Define all symlinks that should be removed
    const symlinks = [
      join(claudeDir, 'agents'),
      join(claudeDir, 'tools'),
      join(claudeDir, 'hooks'),
      join(claudeDir, 'commands'),
      join(claudeDir, 'templates'),
      join(claudeDir, 'config'),
      join(claudeDir, 'speckit'),
      join(claudeDir, 'CHANGELOG.md'),
      join(claudeDir, 'README.en.md'),
      join(claudeDir, 'README.md')
    ];

    let removed = 0;
    for (const symlinkPath of symlinks) {
      if (existsSync(symlinkPath)) {
        try {
          await fs.unlink(symlinkPath);
          removed++;
        } catch (error) {
          // Ignore errors
        }
      }
    }

    if (removed > 0) {
      spinner.succeed(`Removed ${removed} symlink(s)`);
      return true;
    } else {
      spinner.info('No symlinks found to remove');
      return false;
    }
  } catch (error) {
    spinner.fail(`Failed to remove symlinks: ${error.message}`);
    return false;
  }
}

/**
 * Main function
 */
async function main() {
  console.log(chalk.cyan('\n🧹 @jaguilar87/gaia-ops cleanup\n'));

  try {
    const claudeRemoved = await removeClaudeMd();
    const settingsRemoved = await removeSettingsJson();
    const symlinksRemoved = await removeSymlinks();

    if (claudeRemoved || settingsRemoved || symlinksRemoved) {
      console.log(chalk.green('\n✅ Cleanup completed\n'));
      console.log(chalk.gray('Preserved data:'));
      console.log(chalk.gray('  • .claude/logs/'));
      console.log(chalk.gray('  • .claude/tests/'));
      console.log(chalk.gray('  • .claude/project-context/'));
      console.log(chalk.gray('  • .claude/session/'));
      console.log(chalk.gray('  • .claude/metrics/\n'));
    } else {
      console.log(chalk.gray('\n✓ Nothing to clean up\n'));
    }
  } catch (error) {
    console.error(chalk.red(`\n❌ Cleanup failed: ${error.message}\n`));
    // Don't fail npm uninstall, just warn
    process.exit(0);
  }
}

main();
