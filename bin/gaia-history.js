#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Agent Session History CLI
 *
 * Shows recent agent sessions with task descriptions, outcomes, and token usage.
 *
 * Usage:
 *   npx gaia-history                    # last 20 sessions
 *   npx gaia-history --today / -t       # today only
 *   npx gaia-history --blocked / -b     # BLOCKED or NEEDS_INPUT sessions only
 *   npx gaia-history --agent <name> / -a <name>  # filter by agent
 *   npx gaia-history --limit <n> / -n <n>        # show N sessions (default 20)
 */

import { join, dirname, resolve } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import chalk from 'chalk';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';

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
 * Read all workflow metrics entries.
 */
async function readWorkflowMetrics() {
  const path = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'metrics.jsonl');
  if (!existsSync(path)) return [];
  try {
    const content = await fs.readFile(path, 'utf-8');
    return content.split('\n')
      .filter(l => l.trim())
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(r => r !== null && r.agent); // skip blank-agent entries
  } catch {
    return [];
  }
}

/**
 * Build a task description lookup from episodic memory index (optional enrichment).
 * Returns a map of task_id -> title for quick lookup.
 * This is best-effort: entries may not always match.
 */
async function buildTaskIndex() {
  const indexPath = join(CWD, '.claude', 'project-context', 'episodic-memory', 'index.json');
  if (!existsSync(indexPath)) return {};
  try {
    const data = JSON.parse(await fs.readFile(indexPath, 'utf-8'));
    const map = {};
    // The episodic index doesn't store task_id, so we can't cross-reference directly.
    // Future: correlate by timestamp proximity. For now return empty map.
    return map;
  } catch {
    return {};
  }
}

// ─────────────────────────────────────────────────────────────
// FORMAT HELPERS
// ─────────────────────────────────────────────────────────────

const GENERIC_PROMPT_RE = /^SubagentStop for /i;

/**
 * Format time as HH:MM (always, since history spans at most a few days).
 */
function formatTime(iso) {
  if (!iso) return '?';
  try {
    const d = new Date(iso);
    const today = new Date().toISOString().slice(0, 10);
    const dayStr = d.toISOString().slice(0, 10);
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    if (dayStr === today) return timeStr;
    return `${dayStr.slice(5)} ${timeStr}`;
  } catch {
    return iso.slice(11, 16);
  }
}

/**
 * Truncate a task description to fit the table column.
 */
function truncate(str, maxLen) {
  if (!str) return '';
  str = str.replace(/\n/g, ' ').replace(/\s+/g, ' ').trim();
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + '…';
}

/**
 * Format approximate token count.
 */
function formatTokens(n) {
  if (n === null || n === undefined) return '   n/a';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`.padStart(6);
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`.padStart(6);
  return `${n}`.padStart(6);
}

/**
 * Color a plan_status string for display.
 */
function colorStatus(status) {
  if (!status) return chalk.gray('  n/a  ');
  const s = status.toUpperCase();
  if (s === 'COMPLETE') return chalk.green(s.padEnd(8));
  if (s === 'BLOCKED') return chalk.red(s.padEnd(8));
  if (s === 'NEEDS_INPUT') return chalk.yellow(s.padEnd(8));
  if (s === 'INVESTIGATING' || s === 'PLANNING') return chalk.cyan(s.padEnd(8));
  return chalk.gray(s.padEnd(8));
}

// ─────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────

async function main() {
  const args = yargs(hideBin(process.argv))
    .option('today', { alias: 't', type: 'boolean', default: false, description: 'Show today only' })
    .option('blocked', { alias: 'b', type: 'boolean', default: false, description: 'Show BLOCKED/NEEDS_INPUT only' })
    .option('agent', { alias: 'a', type: 'string', description: 'Filter by agent name' })
    .option('limit', { alias: 'n', type: 'number', default: 20, description: 'Max sessions to show' })
    .help('h').alias('h', 'help')
    .version(false)
    .parse();

  const claudeDir = join(CWD, '.claude');
  if (!existsSync(claudeDir)) {
    console.log(chalk.yellow('\n  gaia-ops not installed in this directory'));
    console.log(chalk.gray('  Run: npx gaia-init\n'));
    process.exit(1);
  }

  let entries = await readWorkflowMetrics();

  if (entries.length === 0) {
    console.log(chalk.yellow('\n  No agent session history found yet'));
    console.log(chalk.gray('  History is recorded after each agent completes\n'));
    process.exit(0);
  }

  // Apply filters
  if (args.today) {
    const today = new Date().toISOString().slice(0, 10);
    entries = entries.filter(r => r.timestamp && r.timestamp.startsWith(today));
  }

  if (args.blocked) {
    entries = entries.filter(r => {
      const s = (r.plan_status || '').toUpperCase();
      return s === 'BLOCKED' || s === 'NEEDS_INPUT';
    });
  }

  if (args.agent) {
    const needle = args.agent.toLowerCase();
    entries = entries.filter(r => r.agent && r.agent.toLowerCase().includes(needle));
  }

  // Sort newest-first, then apply limit
  entries = entries
    .slice()
    .sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''))
    .slice(0, args.limit);

  if (entries.length === 0) {
    console.log(chalk.yellow('\n  No sessions match the current filters\n'));
    process.exit(0);
  }

  // Column widths
  const TIME_W  = 11;
  const AGENT_W = 22;
  const TASK_W  = 38;
  const STATUS_W = 12;
  const SEP = chalk.gray('─'.repeat(TIME_W + AGENT_W + TASK_W + STATUS_W + 16));

  console.log(chalk.cyan('\n  Agent Session History'));
  console.log('  ' + SEP);
  console.log(
    chalk.gray(
      `  ${'Time'.padEnd(TIME_W)} ${'Agent'.padEnd(AGENT_W)} ${'Task'.padEnd(TASK_W)} ${'Status'.padEnd(STATUS_W)} ~Tokens`
    )
  );
  console.log('  ' + SEP);

  let totalTokens = 0;
  const agentSet = new Set();

  for (const entry of entries) {
    const time  = formatTime(entry.timestamp).padEnd(TIME_W);
    const agent = (entry.agent || 'unknown').padEnd(AGENT_W);

    // Build task description: use prompt if it's not a generic "SubagentStop for ..." fallback
    const rawPrompt = entry.prompt || '';
    const task = GENERIC_PROMPT_RE.test(rawPrompt) || !rawPrompt
      ? chalk.gray('(no description)'.padEnd(TASK_W))
      : truncate(rawPrompt, TASK_W).padEnd(TASK_W);

    const status = colorStatus(entry.plan_status);
    const tokens = formatTokens(entry.output_tokens_approx);
    const tokensStr = chalk.gray(tokens);

    console.log(`  ${chalk.gray(time)} ${chalk.bold(agent)} ${task} ${status} ${tokensStr}`);

    if (typeof entry.output_tokens_approx === 'number') totalTokens += entry.output_tokens_approx;
    agentSet.add(entry.agent || 'unknown');
  }

  console.log('  ' + SEP);

  // Footer summary
  const totalStr = totalTokens > 0 ? `~${totalTokens >= 1000 ? (totalTokens / 1000).toFixed(1) + 'k' : totalTokens} tokens approx` : '';
  console.log(
    chalk.gray(
      `  Total: ${entries.length} sessions | ${agentSet.size} agent${agentSet.size !== 1 ? 's' : ''}`
      + (totalStr ? ` | ${totalStr}` : '')
    )
  );
  console.log('');

  // Tips
  if (!args.today && !args.blocked && !args.agent) {
    console.log(chalk.gray('  Flags: --today | --blocked | --agent <name> | --limit <n>\n'));
  }
}

main().catch(err => {
  console.error(chalk.red(`\n  Error: ${err.message}\n`));
  process.exit(1);
});
