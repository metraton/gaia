#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - System Status CLI
 *
 * Quick snapshot of the current gaia-ops system state.
 *
 * Usage:
 *   npx gaia-status
 */

import { join, dirname, resolve } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import chalk from 'chalk';

function findProjectRoot() {
  if (process.env.INIT_CWD) {
    if (existsSync(join(process.env.INIT_CWD, '.claude'))) return process.env.INIT_CWD;
  }
  let currentDir = process.cwd();
  const root = resolve('/');
  while (currentDir !== root) {
    if (existsSync(join(currentDir, '.claude'))) return currentDir;
    currentDir = dirname(currentDir);
  }
  return process.env.INIT_CWD || process.cwd();
}

const CWD = findProjectRoot();

// ─────────────────────────────────────────────────────────────
// DATA READERS
// ─────────────────────────────────────────────────────────────

/**
 * Read the last non-empty line of a file.
 */
async function readLastLine(filePath) {
  try {
    const content = await fs.readFile(filePath, 'utf-8');
    const lines = content.split('\n').filter(l => l.trim());
    return lines.length > 0 ? lines[lines.length - 1] : null;
  } catch {
    return null;
  }
}

/**
 * Get the last agent session from workflow metrics.
 */
async function getLastAgent() {
  const metricsPath = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'metrics.jsonl');
  if (!existsSync(metricsPath)) return null;
  const line = await readLastLine(metricsPath);
  if (!line) return null;
  try {
    return JSON.parse(line);
  } catch {
    return null;
  }
}

/**
 * Get pending context update count from index.
 */
async function getPendingCount() {
  const indexPath = join(CWD, '.claude', 'project-context', 'pending-updates', 'pending-index.json');
  if (!existsSync(indexPath)) return 0;
  try {
    const data = JSON.parse(await fs.readFile(indexPath, 'utf-8'));
    return data.pending_count || 0;
  } catch {
    return 0;
  }
}

/**
 * Count active signal files in workflow-episodic-memory/signals/.
 */
async function getAnomalyCount() {
  const signalsDir = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'signals');
  if (!existsSync(signalsDir)) return 0;
  try {
    const files = await fs.readdir(signalsDir);
    return files.filter(f => f.endsWith('.flag')).length;
  } catch {
    return 0;
  }
}

/**
 * Get project-context.json last_updated timestamp.
 */
async function getContextLastUpdated() {
  const contextPath = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (!existsSync(contextPath)) return null;
  try {
    const data = JSON.parse(await fs.readFile(contextPath, 'utf-8'));
    return data.metadata?.last_updated || null;
  } catch {
    return null;
  }
}

/**
 * Count episodes from the episodic memory index.
 */
async function getEpisodeCount() {
  const indexPath = join(CWD, '.claude', 'project-context', 'episodic-memory', 'index.json');
  if (!existsSync(indexPath)) return null;
  try {
    const data = JSON.parse(await fs.readFile(indexPath, 'utf-8'));
    return (data.episodes || []).length;
  } catch {
    return null;
  }
}

/**
 * Count workflow metrics entries.
 */
async function getMetricsCount() {
  const metricsPath = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'metrics.jsonl');
  if (!existsSync(metricsPath)) return null;
  try {
    const content = await fs.readFile(metricsPath, 'utf-8');
    return content.split('\n').filter(l => l.trim()).length;
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────
// FORMAT HELPERS
// ─────────────────────────────────────────────────────────────

/**
 * Format an ISO timestamp as a short local time string (HH:MM today, or MM-DD HH:MM otherwise).
 */
function formatTime(iso) {
  if (!iso) return 'unknown';
  try {
    const d = new Date(iso);
    const today = new Date().toISOString().slice(0, 10);
    const dayStr = d.toISOString().slice(0, 10);
    if (dayStr === today) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }
    return `${dayStr.slice(5)} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}`;
  } catch {
    return iso.slice(0, 16).replace('T', ' ');
  }
}

// ─────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────

async function main() {
  const claudeDir = join(CWD, '.claude');
  if (!existsSync(claudeDir)) {
    console.log(chalk.yellow('\n  gaia-ops not installed in this directory'));
    console.log(chalk.gray('  Run: npx gaia-init\n'));
    process.exit(1);
  }

  const [
    lastAgent,
    pendingCount,
    anomalyCount,
    contextUpdated,
    episodeCount,
    metricsCount,
  ] = await Promise.all([
    getLastAgent(),
    getPendingCount(),
    getAnomalyCount(),
    getContextLastUpdated(),
    getEpisodeCount(),
    getMetricsCount(),
  ]);

  const governancePath = join(CWD, '.claude', 'project-context', 'speckit-project-specs', 'governance.md');
  const governanceOk = existsSync(governancePath);

  const SEP = chalk.gray('─'.repeat(50));

  console.log(chalk.cyan('\n  Gaia System Status'));
  console.log('  ' + SEP);

  // Last agent
  if (lastAgent) {
    const time = formatTime(lastAgent.timestamp);
    const status = lastAgent.plan_status || (lastAgent.exit_code === 0 ? 'ok' : 'failed');
    const statusColor = status === 'COMPLETE' || status === 'ok' ? chalk.green
      : status === 'BLOCKED' ? chalk.red
      : chalk.yellow;
    const agentName = lastAgent.agent || '(unknown)';
    console.log(
      `  Last agent:   ${chalk.bold(agentName.padEnd(22))} ${chalk.gray(time)} — ${statusColor(status)}`
    );
  } else {
    console.log(`  Last agent:   ${chalk.gray('no agent sessions recorded yet')}`);
  }

  // Pending updates
  const pendingColor = pendingCount > 0 ? chalk.yellow : chalk.green;
  const pendingNote = pendingCount > 0 ? chalk.gray('  run: npx gaia-review') : '';
  console.log(`  Pending:      ${pendingColor(`${pendingCount} context update${pendingCount !== 1 ? 's' : ''} to review`)}${pendingNote}`);

  // Anomalies
  const anomalyColor = anomalyCount > 0 ? chalk.red : chalk.green;
  const anomalyNote = anomalyCount > 0 ? chalk.gray('  check workflow-episodic-memory/signals/') : '';
  console.log(`  Anomalies:    ${anomalyColor(`${anomalyCount} active signal${anomalyCount !== 1 ? 's' : ''}`)}${anomalyNote}`);

  // Context
  if (contextUpdated) {
    console.log(`  Context:      project-context.json — ${chalk.gray('updated ' + formatTime(contextUpdated))}`);
  } else {
    console.log(`  Context:      ${chalk.yellow('project-context.json missing — run gaia-init')}`);
  }

  // Governance
  const govStatus = governanceOk ? chalk.green('OK') : chalk.yellow('MISSING — run /speckit.init');
  console.log(`  Governance:   speckit-project-specs/governance.md — ${govStatus}`);

  // Memory
  const epStr = episodeCount !== null ? `${episodeCount} episodes` : 'no episodic-memory';
  const metStr = metricsCount !== null ? `${metricsCount} metrics entries` : 'no metrics';
  console.log(`  Memory:       ${chalk.gray(epStr + '  |  ' + metStr)}`);

  console.log('  ' + SEP + '\n');
}

main().catch(err => {
  console.error(chalk.red(`\n  Error: ${err.message}\n`));
  process.exit(1);
});
