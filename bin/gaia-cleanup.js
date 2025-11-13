#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Cleanup script
 *
 * Purpose:
 * - Remove CLAUDE.md
 * - Remove settings.json
 * - Remove all symlinks (agents, tools, hooks, commands, config, templates, speckit, CHANGELOG.md)
 * - Preserve project-specific data (logs, tests, project-context, session, metrics)
 *
 * Usage:
 *   Manual: npx gaia-cleanup
 *   Automatic: Runs via preuninstall hook when uninstalling from npm registry
 *
 * IMPORTANT FOR LOCAL DEVELOPMENT:
 *   When testing with local installs (npm install ./gaia-ops), the preuninstall hook
 *   will NOT execute automatically due to npm's file: protocol behavior.
 *   You must run 'npx gaia-cleanup' manually before uninstalling:
 *
 *   npx gaia-cleanup && npm uninstall @jaguilar87/gaia-ops
 *
 *   When installed from npm registry, cleanup happens automatically on uninstall.
 */

import { join, dirname, resolve } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import chalk from 'chalk';
import ora from 'ora';

/**
 * Find the project root directory by looking for .claude/ directory
 * Searches upward from the current working directory
 */
function findProjectRoot() {
  // First try INIT_CWD (available during npm install/uninstall)
  if (process.env.INIT_CWD) {
    const claudeDir = join(process.env.INIT_CWD, '.claude');
    if (existsSync(claudeDir)) {
      return process.env.INIT_CWD;
    }
  }

  // Search upward from current directory
  let currentDir = process.cwd();
  const root = resolve('/');

  while (currentDir !== root) {
    const claudeDir = join(currentDir, '.claude');
    if (existsSync(claudeDir)) {
      return currentDir;
    }
    currentDir = dirname(currentDir);
  }

  // Fallback to current directory
  return process.env.INIT_CWD || process.cwd();
}

const CWD = findProjectRoot();

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
