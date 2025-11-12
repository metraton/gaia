#!/usr/bin/env node

/**
 * Cleanup script to remove redundant Claude Code installations
 * Ensures only ONE installation exists (native or local)
 */

import { exec } from 'child_process';
import { promisify } from 'util';
import chalk from 'chalk';
import ora from 'ora';

const execAsync = promisify(exec);

async function cleanupClaudeInstalls() {
  console.log(chalk.cyan('\n🧹 Claude Code Installation Cleanup\n'));

  try {
    // Check if npm global installation exists
    const spinner = ora('Checking for redundant Claude Code installations...').start();

    try {
      const { stdout } = await execAsync('npm list -g @anthropic-ai/claude-code 2>/dev/null || true');

      if (stdout.includes('@anthropic-ai/claude-code')) {
        spinner.warn('Found npm global installation of @anthropic-ai/claude-code');

        console.log(chalk.yellow('\nRemoving npm global installation...\n'));

        try {
          await execAsync('npm uninstall -g @anthropic-ai/claude-code');
          console.log(chalk.green('✅ Removed: @anthropic-ai/claude-code (global)\n'));
        } catch (error) {
          console.error(chalk.red('❌ Failed to uninstall global package:'), error.message);
          console.log(chalk.yellow('\nTry manually: npm -g uninstall @anthropic-ai/claude-code\n'));
        }
      } else {
        spinner.succeed('No redundant global installations found');
      }
    } catch (error) {
      spinner.fail('Could not check npm global packages');
    }

    console.log(chalk.cyan('\n✅ Cleanup complete\n'));
    console.log(chalk.gray('Current Claude Code installation:'));

    try {
      const { stdout } = await execAsync('which claude');
      console.log(chalk.gray(`  Location: ${stdout.trim()}`));
    } catch {
      console.log(chalk.yellow('  ⚠️  Claude Code not in PATH'));
    }

    console.log('');
  } catch (error) {
    console.error(chalk.red('\n❌ Cleanup error:'), error.message);
    process.exit(1);
  }
}

cleanupClaudeInstalls();
