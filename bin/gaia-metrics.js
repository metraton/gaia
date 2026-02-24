#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Metrics viewer
 *
 * Displays system metrics using real fields from audit/metrics logs.
 *
 * Usage:
 *   npx gaia-metrics                        # full dashboard
 *   npx gaia-metrics --agent <name>         # agent detail view
 *
 * Metrics shown:
 * - Security tier usage distribution
 * - Command type breakdown (terraform, git, kubernetes, etc.)
 * - Top commands by frequency
 * - Agent invocations (from workflow-episodic/metrics.jsonl)
 * - Activity summary for today
 */

import { join, dirname, resolve } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import chalk from 'chalk';
import ora from 'ora';

/**
 * Find the project root directory by looking for .claude/ directory
 */
function findProjectRoot() {
  if (process.env.INIT_CWD) {
    const claudeDir = join(process.env.INIT_CWD, '.claude');
    if (existsSync(claudeDir)) {
      return process.env.INIT_CWD;
    }
  }

  let currentDir = process.cwd();
  const root = resolve('/');

  while (currentDir !== root) {
    const claudeDir = join(currentDir, '.claude');
    if (existsSync(claudeDir)) {
      return currentDir;
    }
    currentDir = dirname(currentDir);
  }

  return process.env.INIT_CWD || process.cwd();
}

const CWD = findProjectRoot();

// ─────────────────────────────────────────────────────────────
// DATA READERS
// ─────────────────────────────────────────────────────────────

/**
 * Read audit logs from .claude/logs/audit-*.jsonl
 * Fields: timestamp, tool_name, command, tier, exit_code, session_id
 */
async function readAuditLogs() {
  try {
    const logsDir = join(CWD, '.claude', 'logs');
    if (!existsSync(logsDir)) return [];

    const files = await fs.readdir(logsDir);
    const auditFiles = files.filter(f => f.startsWith('audit-') && f.endsWith('.jsonl'));

    let all = [];
    for (const file of auditFiles) {
      try {
        const content = await fs.readFile(join(logsDir, file), 'utf-8');
        const parsed = content.split('\n')
          .filter(l => l.trim())
          .map(l => { try { return JSON.parse(l); } catch { return null; } })
          .filter(Boolean);
        all = all.concat(parsed);
      } catch { /* skip */ }
    }
    return all;
  } catch {
    return [];
  }
}

/**
 * Read metrics logs from .claude/metrics/metrics-*.jsonl
 * Fields: timestamp, tool_name, command_type, tier, success, duration_ms
 */
async function readMetricsLogs() {
  try {
    const metricsDir = join(CWD, '.claude', 'metrics');
    if (!existsSync(metricsDir)) return [];

    const files = await fs.readdir(metricsDir);
    const metricFiles = files.filter(f => f.startsWith('metrics-') && f.endsWith('.jsonl'));

    let all = [];
    for (const file of metricFiles) {
      try {
        const content = await fs.readFile(join(metricsDir, file), 'utf-8');
        const parsed = content.split('\n')
          .filter(l => l.trim())
          .map(l => { try { return JSON.parse(l); } catch { return null; } })
          .filter(Boolean);
        all = all.concat(parsed);
      } catch { /* skip */ }
    }
    return all;
  } catch {
    return [];
  }
}

/**
 * Read workflow episodic metrics from .claude/memory/workflow-episodic/metrics.jsonl
 * Fields: timestamp, agent, exit_code, output_length, task_id, session_id
 * Note: entries with agent === "" are SubagentStop events without a named agent (skip them).
 */
async function readWorkflowMetrics() {
  try {
    const path = join(CWD, '.claude', 'memory', 'workflow-episodic', 'metrics.jsonl');
    if (!existsSync(path)) return [];

    const content = await fs.readFile(path, 'utf-8');
    return content.split('\n')
      .filter(l => l.trim())
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(r => r !== null && r.agent);  // exclude empty-agent entries
  } catch {
    return [];
  }
}

/**
 * Read and parse agent definition from .claude/agents/<name>.md
 * Extracts description and skills list from YAML frontmatter.
 * Returns { description, skills } or null if not found.
 */
async function readAgentDefinition(agentName) {
  const agentPath = join(CWD, '.claude', 'agents', `${agentName}.md`);
  if (!existsSync(agentPath)) return null;

  try {
    const content = await fs.readFile(agentPath, 'utf-8');
    if (!content.startsWith('---')) return null;

    const endIdx = content.indexOf('---', 3);
    if (endIdx === -1) return null;

    const frontmatter = content.slice(3, endIdx);
    let description = '';
    const skills = [];
    let inSkills = false;

    for (const line of frontmatter.split('\n')) {
      const trimmed = line.trim();
      if (trimmed.startsWith('description:')) {
        description = trimmed.replace(/^description:\s*/, '').replace(/^['"]|['"]$/g, '');
        inSkills = false;
      } else if (trimmed === 'skills:') {
        inSkills = true;
      } else if (inSkills && trimmed.startsWith('- ')) {
        skills.push(trimmed.slice(2).trim());
      } else if (inSkills && trimmed && !trimmed.startsWith('-')) {
        inSkills = false;
      }
    }

    return { description, skills };
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────
// UTILITY FUNCTIONS
// ─────────────────────────────────────────────────────────────

/**
 * Classify a raw command string into a technology category.
 * Used as fallback when metrics log command_type is unavailable.
 */
function classifyCommand(command) {
  if (!command) return 'general';
  const cmd = command.trim().toLowerCase();

  if (cmd.startsWith('terragrunt') || cmd.startsWith('terraform')) return 'terraform';
  if (cmd.startsWith('kubectl')) return 'kubernetes';
  if (cmd.startsWith('helm') || cmd.startsWith('flux')) return 'gitops';
  if (cmd.startsWith('git') || cmd.startsWith('glab')) return 'git';
  if (cmd.startsWith('gcloud') || cmd.startsWith('gsutil')) return 'gcp';
  if (cmd.startsWith('aws')) return 'aws';
  if (cmd.startsWith('docker')) return 'docker';
  if (cmd.startsWith('npm') || cmd.startsWith('node') || cmd.startsWith('python') || cmd.startsWith('pip')) return 'dev';
  return 'general';
}

/**
 * Extract a short human-readable label from a full command string.
 * Strips wrappers like `timeout 30s`, env vars, and path prefixes.
 * Takes the tool name + first subcommand, truncated to 32 chars.
 */
function extractCommandLabel(command) {
  if (!command) return '(unknown)';
  let cmd = command.trim();

  // Strip leading env var assignments (FOO=bar cmd ...)
  cmd = cmd.replace(/^(?:[A-Z_][A-Z0-9_]*=\S+\s+)+/, '');

  // Strip timeout wrapper: "timeout 30s <real cmd>"
  cmd = cmd.replace(/^timeout\s+\S+\s+/, '');

  // Strip leading cd/pushd navigation: "cd /some/path && <real cmd>"
  const cdMatch = cmd.match(/^(?:cd|pushd)\s+\S+\s*(?:&&|;)\s*(.*)/);
  if (cdMatch) cmd = cdMatch[1].trim();

  // Strip shell redirections and anything after pipe/semicolon/&&
  cmd = cmd.split(/\s*(?:[|;&]|&&|\|\|)\s*/)[0].trim();
  // Strip trailing redirections like 2>&1 or > /tmp/file
  cmd = cmd.replace(/\s*\d*>.*$/, '').trim();

  const tokens = cmd.split(/\s+/);
  // Take tool (token 0) + first non-flag, non-path, non-quote argument
  const parts = [tokens[0]];
  for (let i = 1; i < tokens.length && parts.length < 3; i++) {
    const t = tokens[i];
    if (!t.startsWith('-') && !t.startsWith('/') && !t.startsWith('"') && !t.startsWith("'")) {
      parts.push(t);
    }
  }
  return parts.join(' ').slice(0, 32);
}

/**
 * Format output_length as human-readable string (e.g. "23.3k chars").
 */
function formatChars(n) {
  if (n === null || n === undefined) return 'n/a';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return `${n}`;
}

/**
 * Build a fixed-width bar string using block characters.
 */
function makeBar(percentage, maxWidth = 14) {
  const filled = Math.max(0, Math.round((percentage / 100) * maxWidth));
  return '█'.repeat(filled);
}

// ─────────────────────────────────────────────────────────────
// METRIC CALCULATORS
// ─────────────────────────────────────────────────────────────

/**
 * Security tier usage distribution.
 * Also extracts today's activity stats and peak hour.
 */
function calculateTierUsage(auditLogs) {
  const tierEntries = auditLogs.filter(l => l.tier);

  const counts = {};
  for (const entry of tierEntries) {
    const t = entry.tier || 'unknown';
    counts[t] = (counts[t] || 0) + 1;
  }

  const total = tierEntries.length;
  const distribution = Object.entries(counts)
    .map(([tier, count]) => ({
      tier,
      count,
      percentage: total > 0 ? (count / total * 100) : 0,
    }))
    .sort((a, b) => a.tier.localeCompare(b.tier));

  // Today's stats
  const today = new Date().toISOString().slice(0, 10);
  const todayEntries = auditLogs.filter(l => l.timestamp && l.timestamp.startsWith(today));
  const todayT3 = todayEntries.filter(l => l.tier === 'T3').length;

  // Peak hour from today's entries
  const hourCounts = {};
  for (const entry of todayEntries) {
    if (entry.timestamp) {
      const hour = entry.timestamp.slice(11, 13);
      hourCounts[hour] = (hourCounts[hour] || 0) + 1;
    }
  }
  let peakHour = null;
  let peakCount = 0;
  for (const [hour, count] of Object.entries(hourCounts)) {
    if (count > peakCount) { peakCount = count; peakHour = hour; }
  }

  return { total, distribution, todayCount: todayEntries.length, todayT3, peakHour, peakCount };
}

/**
 * Command type breakdown.
 * Prefers the command_type field from metrics logs; falls back to
 * classifyCommand() applied to the audit log command field.
 */
function calculateCommandTypeBreakdown(auditLogs, metricsLogs) {
  const fromMetrics = metricsLogs.length > 0;
  const source = fromMetrics ? metricsLogs : auditLogs;

  const counts = {};
  for (const entry of source) {
    const type = fromMetrics
      ? (entry.command_type || 'general')
      : classifyCommand(entry.command);
    counts[type] = (counts[type] || 0) + 1;
  }

  const total = source.length;
  const breakdown = Object.entries(counts)
    .map(([type, count]) => ({
      type,
      count,
      percentage: total > 0 ? (count / total * 100) : 0,
    }))
    .sort((a, b) => b.count - a.count);

  return { total, breakdown, fromMetrics };
}

/**
 * Top 10 commands by frequency from audit logs.
 * Labels are the first 2-3 non-flag tokens of the command string.
 * Tracks the highest tier seen per label and whether any T3 was used.
 */
function calculateTopCommands(auditLogs) {
  const tierOrder = { T3: 3, T2: 2, T1: 1, T0: 0, unknown: -1 };
  const labelMap = {};

  for (const entry of auditLogs) {
    if (!entry.command) continue;
    const label = extractCommandLabel(entry.command);
    const tier = entry.tier || 'unknown';

    if (!labelMap[label]) {
      labelMap[label] = { count: 0, tier, t3count: 0 };
    }
    labelMap[label].count++;
    if (tier === 'T3') labelMap[label].t3count++;
    if ((tierOrder[tier] ?? -1) > (tierOrder[labelMap[label].tier] ?? -1)) {
      labelMap[label].tier = tier;
    }
  }

  return Object.entries(labelMap)
    .map(([label, { count, tier, t3count }]) => ({ label, count, tier, t3count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
}

/**
 * Error rate analysis from audit logs.
 * Detects whether the known hook API bug (exit_code always 0) is present.
 */
function calculateErrorRate(auditLogs) {
  const withExitCode = auditLogs.filter(l => 'exit_code' in l);
  const errors = withExitCode.filter(l => l.exit_code !== 0);
  const allZero = withExitCode.length > 0 && errors.length === 0;

  return {
    total: withExitCode.length,
    errors: errors.length,
    errorRate: withExitCode.length > 0 ? (errors.length / withExitCode.length * 100) : 0,
    limitedByApi: allZero,
  };
}

/**
 * Agent invocations summary from workflow episodic metrics.
 * Groups by agent name, computes count, avg output_length, success rate.
 * Also returns todayCount for the header.
 */
function calculateAgentInvocations(workflowMetrics) {
  const today = new Date().toISOString().slice(0, 10);
  const todayCount = workflowMetrics.filter(
    r => r.timestamp && r.timestamp.startsWith(today)
  ).length;

  const agentMap = {};
  for (const entry of workflowMetrics) {
    const name = entry.agent;
    if (!agentMap[name]) {
      agentMap[name] = { count: 0, totalOutput: 0, successes: 0 };
    }
    agentMap[name].count++;
    agentMap[name].totalOutput += (entry.output_length || 0);
    if (entry.exit_code === 0) agentMap[name].successes++;
  }

  const total = workflowMetrics.length;
  const agents = Object.entries(agentMap)
    .map(([name, { count, totalOutput, successes }]) => ({
      name,
      count,
      avgOutput: count > 0 ? Math.round(totalOutput / count) : 0,
      successRate: count > 0 ? (successes / count * 100) : 0,
      percentage: total > 0 ? (count / total * 100) : 0,
    }))
    .sort((a, b) => b.count - a.count);

  return { agents, total, todayCount };
}

/**
 * Extract audit log entries that fall within the time window of a given
 * agent session. Uses the session timestamp as the end boundary, and
 * the previous named-agent session as the start boundary (approximation).
 *
 * This is a best-effort time-window correlation, documented as approximate.
 */
function correlateAuditLogsToSession(auditLogs, sessionEnd, sessionStart) {
  const endTs = new Date(sessionEnd).getTime();
  const startTs = sessionStart ? new Date(sessionStart).getTime() : endTs - 10 * 60 * 1000; // 10 min fallback

  return auditLogs.filter(entry => {
    if (!entry.timestamp) return false;
    const ts = new Date(entry.timestamp).getTime();
    return ts >= startTs && ts <= endTs;
  });
}

// ─────────────────────────────────────────────────────────────
// DISPLAY FUNCTIONS
// ─────────────────────────────────────────────────────────────

/**
 * Display the main dashboard metrics.
 */
function displayMetrics(tiers, cmdTypes, topCmds, agentInvocations, errorStats, auditTotal) {
  const SEP = chalk.gray('═'.repeat(52));

  console.log(chalk.cyan('\n📊 Gaia-Ops System Metrics'));
  console.log(SEP);

  // ── Security Tier Usage ──────────────────────────────
  console.log(chalk.bold(`\n🔒 Security Tier Usage  (${tiers.total} operations)`));

  if (tiers.total === 0) {
    console.log(chalk.gray('  no tier data'));
  } else {
    const tierLabel = { T0: 'read-only', T1: 'validation', T2: 'simulation', T3: 'realization' };
    for (const { tier, count, percentage } of tiers.distribution) {
      const color = tier === 'T3' ? chalk.red
        : tier === 'T2' ? chalk.yellow
        : chalk.green;
      const bar = makeBar(percentage, 14);
      const pct = percentage.toFixed(1).padStart(5);
      const label = tierLabel[tier] || tier;
      const suffix = tier === 'T3' ? chalk.red('  ⚠️  realization') : `  ${label}`;
      console.log(color(
        `  ${tier.padEnd(4)} ${count.toString().padStart(4)}  ${bar.padEnd(14)}  ${pct}%${suffix}`
      ));
    }
  }

  // ── Command Type Breakdown ───────────────────────────
  const srcNote = cmdTypes.fromMetrics
    ? `from ${cmdTypes.total} metrics entries`
    : `derived from ${auditTotal} audit entries`;
  console.log(chalk.bold(`\n🛠  Command Type Breakdown  (${srcNote})`));

  if (cmdTypes.breakdown.length === 0) {
    console.log(chalk.gray('  no command data'));
  } else {
    for (const { type, count, percentage } of cmdTypes.breakdown) {
      const bar = makeBar(percentage, 10);
      const pct = percentage.toFixed(1).padStart(5);
      console.log(`  ${type.padEnd(12)} ${count.toString().padStart(4)}  ${bar.padEnd(10)}  ${pct}%`);
    }
  }

  // ── Top Commands ─────────────────────────────────────
  console.log(chalk.bold('\n🔝 Top Commands'));

  if (topCmds.length === 0) {
    console.log(chalk.gray('  no command data'));
  } else {
    for (const { label, count, tier, t3count } of topCmds) {
      const tierColor = tier === 'T3' ? chalk.red
        : tier === 'T2' ? chalk.yellow
        : chalk.gray;
      const warn = t3count > 0 ? chalk.red('  ⚠️') : '';
      console.log(
        `  ${label.padEnd(30)} ${count.toString().padStart(4)}  ${tierColor(tier)}${warn}`
      );
    }
  }

  // ── Agent Invocations ────────────────────────────────
  const agentHeader = agentInvocations.todayCount > 0
    ? `(${agentInvocations.todayCount} sessions today)`
    : `(${agentInvocations.total} total)`;
  console.log(chalk.bold(`\n🤖 Agent Invocations  ${agentHeader}`));

  if (agentInvocations.agents.length === 0) {
    console.log(chalk.gray('  no invocation data'));
  } else {
    for (const { name, count, avgOutput, successRate, percentage } of agentInvocations.agents) {
      const bar = makeBar(percentage, 8);
      const avg = `avg ${formatChars(avgOutput).padStart(6)} chars`;
      const ok = successRate === 100
        ? chalk.green('100% ok')
        : chalk.yellow(`${successRate.toFixed(0)}% ok`);
      console.log(
        `  ${name.padEnd(24)} ${count.toString().padStart(3)}  ${bar.padEnd(8)}  ${avg}  ${ok}`
      );
    }
    console.log(chalk.gray(`  💡 tip: npx gaia-metrics --agent <name>  for detail view`));
  }

  // ── Activity Today ───────────────────────────────────
  console.log(chalk.bold('\n⚡ Activity Today'));
  console.log(`  Total calls:   ${tiers.todayCount}`);

  if (tiers.todayT3 > 0) {
    console.log(chalk.red(`  T3 operations: ${tiers.todayT3}  ⚠️`));
  } else {
    console.log(chalk.green(`  T3 operations: ${tiers.todayT3}`));
  }

  if (tiers.peakHour !== null) {
    console.log(`  Peak hour:     ${tiers.peakHour}:00-${tiers.peakHour}:59  (${tiers.peakCount} calls)`);
  } else {
    console.log(chalk.gray('  Peak hour:     no data for today'));
  }

  if (errorStats.limitedByApi) {
    console.log(chalk.gray('  Error rate:    n/a  (hook API limitation — exit_code always 0)'));
  } else if (errorStats.total === 0) {
    console.log(chalk.gray('  Error rate:    no exit_code data'));
  } else {
    const errColor = errorStats.errors > 0 ? chalk.red : chalk.green;
    console.log(
      `  Error rate:    ${errColor(`${errorStats.errors}/${errorStats.total} (${errorStats.errorRate.toFixed(1)}%)`)}`
    );
  }

  console.log('\n' + SEP);
  console.log(chalk.gray('💡 Source: .claude/logs/audit-*.jsonl  |  .claude/metrics/metrics-*.jsonl\n'));
}

/**
 * Display the agent detail view (--agent <name>).
 */
async function displayAgentDetail(agentName, workflowMetrics, auditLogs) {
  const SEP = chalk.gray('═'.repeat(52));

  console.log(chalk.cyan(`\n🤖 Agent: ${agentName}`));
  console.log(SEP);

  // ── Profile ──────────────────────────────────────────
  console.log(chalk.bold('\n📋 Profile'));
  const agentDef = await readAgentDefinition(agentName);

  if (!agentDef) {
    console.log(chalk.yellow('  Agent definition not found in .claude/agents/'));
  } else {
    if (agentDef.description) {
      console.log(`  Description: ${agentDef.description}`);
    }
    if (agentDef.skills.length > 0) {
      // Wrap skills at ~60 chars
      const skillLine = agentDef.skills.join(', ');
      if (skillLine.length <= 56) {
        console.log(`  Skills:      ${skillLine}`);
      } else {
        const chunks = [];
        let current = [];
        let len = 0;
        for (const s of agentDef.skills) {
          if (len + s.length + 2 > 56 && current.length > 0) {
            chunks.push(current.join(', '));
            current = [s];
            len = s.length;
          } else {
            current.push(s);
            len += s.length + 2;
          }
        }
        if (current.length) chunks.push(current.join(', '));
        console.log(`  Skills:      ${chunks[0]}`);
        for (let i = 1; i < chunks.length; i++) {
          console.log(`               ${chunks[i]}`);
        }
      }
    }
  }

  // ── Invocation History ───────────────────────────────
  const agentSessions = workflowMetrics
    .filter(r => r.agent === agentName)
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp));

  const successCount = agentSessions.filter(r => r.exit_code === 0).length;
  const totalOutput = agentSessions.reduce((s, r) => s + (r.output_length || 0), 0);
  const avgOutput = agentSessions.length > 0 ? Math.round(totalOutput / agentSessions.length) : 0;

  console.log(chalk.bold(`\n📊 Invocation History  (last 7 days)`));

  if (agentSessions.length === 0) {
    console.log(chalk.gray('  no invocations found in workflow-episodic/metrics.jsonl'));
  } else {
    console.log(
      `  Total: ${agentSessions.length} invocations  |  ` +
      `Success: ${successCount}/${agentSessions.length}  |  ` +
      `Avg output: ${formatChars(avgOutput)} chars`
    );
    console.log('');

    for (const session of agentSessions) {
      const dt = session.timestamp.slice(0, 16).replace('T', ' ');
      const ok = session.exit_code === 0
        ? chalk.green('✓')
        : chalk.red('✗');
      const chars = (session.output_length || 0).toLocaleString();
      const taskShort = session.task_id ? session.task_id.slice(0, 8) : 'n/a';
      console.log(
        `  ${dt}  ${ok}  ${chars.padStart(7)} chars  task: ${taskShort}`
      );
    }
  }

  // ── Top Commands (correlated from audit log) ─────────
  console.log(chalk.bold('\n🔝 Top Commands  (sampled from audit log, approximate time windows)'));

  if (agentSessions.length === 0 || auditLogs.length === 0) {
    console.log(chalk.gray('  no data to correlate'));
  } else {
    // Build session windows: for each named-agent stop, find the previous named-agent stop
    // to define the start of the window. Falls back to 10 minutes before the stop.
    const namedStops = workflowMetrics
      .filter(r => r.agent)
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp));

    const agentStopIdxs = agentSessions.map(s =>
      namedStops.findIndex(r => r.task_id === s.task_id)
    );

    const tierOrder = { T3: 3, T2: 2, T1: 1, T0: 0, unknown: -1 };
    const labelMap = {};

    for (let i = 0; i < agentSessions.length; i++) {
      const session = agentSessions[i];
      const stopIdx = agentStopIdxs[i];
      const prevStop = stopIdx > 0 ? namedStops[stopIdx - 1] : null;
      const windowStart = prevStop ? prevStop.timestamp : null;

      const windowCmds = correlateAuditLogsToSession(
        auditLogs,
        session.timestamp,
        windowStart
      );

      for (const entry of windowCmds) {
        if (!entry.command) continue;
        const label = extractCommandLabel(entry.command);
        const tier = entry.tier || 'unknown';
        if (!labelMap[label]) labelMap[label] = { count: 0, tier, t3count: 0 };
        labelMap[label].count++;
        if (tier === 'T3') labelMap[label].t3count++;
        if ((tierOrder[tier] ?? -1) > (tierOrder[labelMap[label].tier] ?? -1)) {
          labelMap[label].tier = tier;
        }
      }
    }

    const topCmds = Object.entries(labelMap)
      .map(([label, { count, tier, t3count }]) => ({ label, count, tier, t3count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);

    if (topCmds.length === 0) {
      console.log(chalk.gray('  no overlapping commands found in audit window'));
    } else {
      for (const { label, count, tier, t3count } of topCmds) {
        const tierColor = tier === 'T3' ? chalk.red
          : tier === 'T2' ? chalk.yellow
          : chalk.gray;
        const warn = t3count > 0 ? chalk.red('  ⚠️') : '';
        console.log(
          `  ${tierColor(tier.padEnd(3))}  ${label.padEnd(28)} ${count.toString().padStart(4)}${warn}`
        );
      }
    }

    console.log(chalk.gray('\n  Note: command windows are approximated from SubagentStop timestamps'));
  }

  console.log('\n' + SEP + '\n');
}

// ─────────────────────────────────────────────────────────────
// MAIN
// ─────────────────────────────────────────────────────────────

async function main() {
  // Parse --agent flag
  const agentFlagIdx = process.argv.indexOf('--agent');
  const agentName = agentFlagIdx !== -1 ? process.argv[agentFlagIdx + 1] : null;

  const spinner = ora('Loading metrics...').start();

  try {
    const claudeDir = join(CWD, '.claude');
    if (!existsSync(claudeDir)) {
      spinner.fail('.claude/ directory not found');
      console.log(chalk.yellow('\n⚠️  Gaia-ops not installed in this directory'));
      console.log(chalk.gray('   Run: npx gaia-init\n'));
      process.exit(1);
    }

    const [auditLogs, metricsLogs, workflowMetrics] = await Promise.all([
      readAuditLogs(),
      readMetricsLogs(),
      readWorkflowMetrics(),
    ]);

    if (auditLogs.length === 0 && metricsLogs.length === 0 && workflowMetrics.length === 0) {
      spinner.info('No log data found');
      console.log(chalk.yellow('\n⚠️  No metrics data available yet'));
      console.log(chalk.gray('   Metrics will be generated as you use the system\n'));
      process.exit(0);
    }

    spinner.succeed(
      `Loaded ${auditLogs.length} audit + ${metricsLogs.length} metrics + ${workflowMetrics.length} workflow entries`
    );

    if (agentName) {
      // Agent detail view
      await displayAgentDetail(agentName, workflowMetrics, auditLogs);
    } else {
      // Full dashboard
      const tiers           = calculateTierUsage(auditLogs);
      const cmdTypes        = calculateCommandTypeBreakdown(auditLogs, metricsLogs);
      const topCmds         = calculateTopCommands(auditLogs);
      const agentInvocations = calculateAgentInvocations(workflowMetrics);
      const errorStats      = calculateErrorRate(auditLogs);

      displayMetrics(tiers, cmdTypes, topCmds, agentInvocations, errorStats, auditLogs.length);
    }

  } catch (error) {
    spinner.fail(`Failed to load metrics: ${error.message}`);
    console.error(chalk.red(`\n❌ Error: ${error.stack}\n`));
    process.exit(1);
  }
}

main();
