#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Pending Context Updates Review CLI
 *
 * Interactive tool for reviewing, approving, and rejecting
 * pending context update suggestions from agents.
 *
 * Usage:
 *   npx gaia-review              # Interactive review mode
 *   npx gaia-review --list       # List pending updates
 *   npx gaia-review --approve ID # Approve specific update
 *   npx gaia-review --reject ID  # Reject specific update
 *   npx gaia-review --stats      # Show statistics
 *   npx gaia-review --json       # Output as JSON
 */

import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { join } from 'path';
import chalk from 'chalk';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import prompts from 'prompts';

const CWD = process.cwd();

// ============================================================================
// Python Backend Calls
// ============================================================================

function findReviewEngine() {
  const candidates = [
    join(CWD, '.claude', 'tools', 'review', 'review_engine.py'),
    join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops', 'tools', 'review', 'review_engine.py'),
    join(import.meta.dirname, '..', 'tools', 'review', 'review_engine.py'),
  ];

  for (const path of candidates) {
    if (existsSync(path)) return path;
  }
  return null;
}

function callReviewEngine(action, opts = {}) {
  const enginePath = findReviewEngine();
  if (!enginePath) {
    console.error(chalk.red('Error: review_engine.py not found'));
    process.exit(1);
  }

  let cmd = `python3 "${enginePath}" ${action}`;
  if (opts.updateId) cmd += ` --update-id "${opts.updateId}"`;
  if (opts.contextPath) cmd += ` --context-path "${opts.contextPath}"`;
  cmd += ' --json';

  try {
    const stdout = execSync(cmd, { encoding: 'utf-8', cwd: CWD, timeout: 30000 });
    return JSON.parse(stdout.trim());
  } catch (err) {
    if (err.stdout) {
      try { return JSON.parse(err.stdout.trim()); } catch { /* fall through */ }
    }
    return { error: err.message };
  }
}

// ============================================================================
// Display Helpers
// ============================================================================

function statusColor(status) {
  switch (status) {
    case 'pending':  return chalk.yellow(status);
    case 'approved': return chalk.green(status);
    case 'rejected': return chalk.red(status);
    case 'applied':  return chalk.blue(status);
    default:         return status;
  }
}

function categoryIcon(category) {
  const icons = {
    new_resource: '🆕',
    configuration_issue: '⚙️',
    drift_detected: '📐',
    dependency_discovered: '🔗',
    topology_change: '🗺️',
  };
  return icons[category] || '📋';
}

function displayTable(updates) {
  if (!updates || updates.length === 0) {
    console.log(chalk.dim('  No pending updates found.'));
    return;
  }

  // Header
  const header = `  ${'ID'.padEnd(14)} ${'Category'.padEnd(22)} ${'Summary'.padEnd(50)} ${'Seen'.padEnd(5)} ${'Status'.padEnd(10)}`;
  console.log(chalk.bold.underline(header));

  for (const u of updates) {
    const icon = categoryIcon(u.category);
    const id = (u.update_id || '').substring(0, 12);
    const cat = `${icon} ${u.category}`.padEnd(22);
    const summary = (u.summary || '').substring(0, 48).padEnd(50);
    const seen = String(u.seen_count || 1).padEnd(5);
    const status = statusColor(u.status || 'pending');
    console.log(`  ${chalk.cyan(id.padEnd(14))} ${cat} ${summary} ${seen} ${status}`);
  }
}

function displayStats(stats) {
  console.log(chalk.bold('\n  Pending Update Statistics\n'));
  console.log(`  Total:    ${chalk.bold(stats.total || 0)}`);
  console.log(`  Pending:  ${chalk.yellow(stats.pending || 0)}`);
  console.log(`  Approved: ${chalk.green(stats.approved || 0)}`);
  console.log(`  Rejected: ${chalk.red(stats.rejected || 0)}`);
  console.log(`  Applied:  ${chalk.blue(stats.applied || 0)}`);

  if (stats.by_category) {
    console.log(chalk.bold('\n  By Category:'));
    for (const [cat, count] of Object.entries(stats.by_category)) {
      console.log(`    ${categoryIcon(cat)} ${cat}: ${count}`);
    }
  }

  if (stats.by_agent) {
    console.log(chalk.bold('\n  By Agent:'));
    for (const [agent, count] of Object.entries(stats.by_agent)) {
      console.log(`    ${agent}: ${count}`);
    }
  }
}

// ============================================================================
// Interactive Review Mode
// ============================================================================

async function interactiveReview() {
  console.log(chalk.bold('\n  Gaia Review - Interactive Mode\n'));

  const result = callReviewEngine('list');
  if (result.error) {
    console.error(chalk.red(`  Error: ${result.error}`));
    return;
  }

  const updates = result.updates || [];
  if (updates.length === 0) {
    console.log(chalk.green('  No pending updates to review.'));
    return;
  }

  console.log(`  Found ${chalk.bold(updates.length)} pending update(s):\n`);
  displayTable(updates);
  console.log();

  // Find context path
  const ctxPath = join(CWD, '.claude', 'project-context', 'project-context.json');

  for (const update of updates) {
    const id = (update.update_id || '').substring(0, 12);
    const response = await prompts({
      type: 'select',
      name: 'action',
      message: `${categoryIcon(update.category)} ${update.summary} (${id})`,
      choices: [
        { title: chalk.green('Approve'), value: 'approve' },
        { title: chalk.red('Reject'), value: 'reject' },
        { title: chalk.dim('Skip'), value: 'skip' },
        { title: chalk.dim('Quit'), value: 'quit' },
      ],
    });

    if (!response.action || response.action === 'quit') break;
    if (response.action === 'skip') continue;

    const actionResult = callReviewEngine(response.action, {
      updateId: update.update_id,
      contextPath: response.action === 'approve' ? ctxPath : undefined,
    });

    if (actionResult.error) {
      console.log(chalk.red(`    Error: ${actionResult.error}`));
    } else if (response.action === 'approve') {
      const applied = actionResult.applied ? chalk.green('applied') : chalk.yellow('approved (not applied)');
      console.log(`    ${chalk.green('✓')} Approved and ${applied}`);
    } else {
      console.log(`    ${chalk.red('✗')} Rejected`);
    }
  }
}

// ============================================================================
// CLI
// ============================================================================

const argv = yargs(hideBin(process.argv))
  .usage('Usage: $0 [options]')
  .option('list', { alias: 'l', describe: 'List all pending updates', type: 'boolean' })
  .option('approve', { alias: 'a', describe: 'Approve update by ID', type: 'string' })
  .option('reject', { alias: 'r', describe: 'Reject update by ID', type: 'string' })
  .option('stats', { alias: 's', describe: 'Show statistics', type: 'boolean' })
  .option('json', { describe: 'Output as JSON', type: 'boolean' })
  .option('context-path', { describe: 'Path to project-context.json', type: 'string' })
  .help()
  .alias('help', 'h')
  .parse();

async function main() {
  if (argv.list) {
    const result = callReviewEngine('list');
    if (argv.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      if (result.error) {
        console.error(chalk.red(`Error: ${result.error}`));
        process.exit(1);
      }
      console.log(chalk.bold(`\n  Pending Updates (${result.count || 0}):\n`));
      displayTable(result.updates);
      console.log();
    }
  } else if (argv.approve) {
    const ctxPath = argv.contextPath || join(CWD, '.claude', 'project-context', 'project-context.json');
    const result = callReviewEngine('approve', { updateId: argv.approve, contextPath: ctxPath });
    if (argv.json) {
      console.log(JSON.stringify(result, null, 2));
    } else if (result.error) {
      console.error(chalk.red(`Error: ${result.error}`));
      process.exit(1);
    } else {
      console.log(chalk.green(`✓ Update ${argv.approve} approved and ${result.applied ? 'applied' : 'queued'}`));
    }
  } else if (argv.reject) {
    const result = callReviewEngine('reject', { updateId: argv.reject });
    if (argv.json) {
      console.log(JSON.stringify(result, null, 2));
    } else if (result.error) {
      console.error(chalk.red(`Error: ${result.error}`));
      process.exit(1);
    } else {
      console.log(chalk.red(`✗ Update ${argv.reject} rejected`));
    }
  } else if (argv.stats) {
    const result = callReviewEngine('stats');
    if (argv.json) {
      console.log(JSON.stringify(result, null, 2));
    } else if (result.error) {
      console.error(chalk.red(`Error: ${result.error}`));
      process.exit(1);
    } else {
      displayStats(result.statistics || {});
      console.log();
    }
  } else {
    // Default: interactive mode
    await interactiveReview();
  }
}

main().catch(err => {
  console.error(chalk.red(`Fatal error: ${err.message}`));
  process.exit(1);
});
