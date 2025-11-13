#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Uninstall wrapper
 *
 * Safely uninstalls gaia-ops by:
 * 1. Running gaia-cleanup to remove all generated files
 * 2. Running npm uninstall to remove the package
 *
 * Usage:
 *   npx gaia-uninstall
 *   OR
 *   npm exec gaia-uninstall
 *
 * This ensures a clean uninstallation with no leftover files.
 */

import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { existsSync } from 'fs';
import chalk from 'chalk';
import ora from 'ora';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const CWD = process.env.INIT_CWD || process.cwd();

/**
 * Run gaia-cleanup to remove generated files
 */
async function runCleanup() {
  const spinner = ora('Running cleanup...').start();

  try {
    // Import and execute gaia-cleanup
    const cleanupScript = join(__dirname, 'gaia-cleanup.js');

    if (!existsSync(cleanupScript)) {
      spinner.fail('Cleanup script not found');
      return false;
    }

    // Execute cleanup by importing it
    await import(`file://${cleanupScript}`);

    spinner.succeed('Cleanup completed');
    return true;
  } catch (error) {
    spinner.fail(`Cleanup failed: ${error.message}`);
    return false;
  }
}

/**
 * Run npm uninstall
 */
function runUninstall() {
  const spinner = ora('Uninstalling @jaguilar87/gaia-ops...').start();

  try {
    execSync('npm uninstall @jaguilar87/gaia-ops', {
      cwd: CWD,
      stdio: 'inherit'
    });

    spinner.succeed('Package uninstalled');
    return true;
  } catch (error) {
    spinner.fail(`Uninstall failed: ${error.message}`);
    return false;
  }
}

/**
 * Main function
 */
async function main() {
  console.log(chalk.cyan('\n🗑️  @jaguilar87/gaia-ops uninstaller\n'));

  try {
    // Step 1: Run cleanup
    const cleanupSuccess = await runCleanup();

    if (!cleanupSuccess) {
      console.log(chalk.yellow('\n⚠️  Cleanup had issues, but continuing with uninstall...\n'));
    }

    console.log('');

    // Step 2: Run uninstall
    const uninstallSuccess = runUninstall();

    if (uninstallSuccess) {
      console.log(chalk.green('\n✅ Uninstall complete!\n'));
      console.log(chalk.gray('All gaia-ops files have been removed.'));
      console.log(chalk.gray('Your project data (logs, tests, project-context) was preserved.\n'));
    } else {
      console.log(chalk.red('\n❌ Uninstall failed\n'));
      console.log(chalk.yellow('You can try manually:'));
      console.log(chalk.gray('  1. npx gaia-cleanup'));
      console.log(chalk.gray('  2. npm uninstall @jaguilar87/gaia-ops\n'));
      process.exit(1);
    }
  } catch (error) {
    console.error(chalk.red(`\n❌ Uninstall error: ${error.message}\n`));
    process.exit(1);
  }
}

main();