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
 * - Agent invocations (from episodic-memory/index.json)
 * - Anomaly summary (last 30 days, from anomalies.jsonl)
 * - Activity summary for today
 *
 * Data sources:
 * - .claude/logs/audit-*.jsonl (SSOT for command metrics)
 * - .claude/project-context/episodic-memory/index.json (primary, agent metrics)
 * - .claude/project-context/workflow-episodic-memory/metrics.jsonl (fallback)
 * - .claude/project-context/workflow-episodic-memory/anomalies.jsonl
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

async function readJsonLines(path) {
  if (!existsSync(path)) return [];

  try {
    const content = await fs.readFile(path, 'utf-8');
    return content.split('\n')
      .filter(line => line.trim())
      .map(line => {
        try {
          return JSON.parse(line);
        } catch {
          return null;
        }
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}

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

// metrics-*.jsonl removed: audit logs are the SSOT for command metrics.
// The command_type field is now derived via classifyCommand() from the
// audit log's command field. Set GAIA_WRITE_METRICS=1 to re-enable writing.

/**
 * Read workflow metrics from the episodic memory index (primary source).
 * Falls back to workflow-episodic-memory/metrics.jsonl for backward compatibility.
 * Fields: timestamp, agent, exit_code, output_length, task_id, session_id,
 *         plan_status, output_tokens_approx, prompt
 */
async function readWorkflowMetrics() {
  // Primary: episodic-memory/index.json
  try {
    const indexPath = join(CWD, '.claude', 'project-context', 'episodic-memory', 'index.json');
    if (existsSync(indexPath)) {
      const data = JSON.parse(await fs.readFile(indexPath, 'utf-8'));
      const episodes = (data.episodes || []).filter(e => e.agent);
      if (episodes.length > 0) return episodes;
    }
  } catch { /* fall through to legacy */ }

  // Fallback: workflow-episodic-memory/metrics.jsonl
  try {
    const metricsPath = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'metrics.jsonl');
    if (!existsSync(metricsPath)) return [];

    const content = await fs.readFile(metricsPath, 'utf-8');
    return content.split('\n')
      .filter(l => l.trim())
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(r => r !== null && r.agent);  // exclude empty-agent entries
  } catch {
    return [];
  }
}

/**
 * Read structured run telemetry snapshots from workflow memory.
 * Fields: timestamp, session_id, task_id, agent, tier, plan_status,
 *         context_snapshot, context_updated, context_sections_updated,
 *         context_rejected_sections, default_skills_snapshot
 */
async function readRunSnapshots() {
  const path = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'run-snapshots.jsonl');
  return readJsonLines(path);
}

/**
 * Read persisted runtime skill snapshots from workflow memory.
 * Fields: timestamp, session_id, agent, task_description, model, tools,
 *         skills, skills_count
 */
async function readAgentSkillSnapshots() {
  const path = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'agent-skills.jsonl');
  return readJsonLines(path);
}

/**
 * Read anomaly records from workflow memory.
 * Fields: timestamp, anomalies, metrics
 */
async function readAnomalyEntries() {
  const path = join(CWD, '.claude', 'project-context', 'workflow-episodic-memory', 'anomalies.jsonl');
  return readJsonLines(path);
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
 * Derives command_type via classifyCommand() from the audit log command field.
 * Audit logs are the SSOT; metrics-*.jsonl is no longer written.
 */
function calculateCommandTypeBreakdown(auditLogs) {
  const counts = {};
  for (const entry of auditLogs) {
    const type = classifyCommand(entry.command);
    counts[type] = (counts[type] || 0) + 1;
  }

  const total = auditLogs.length;
  const breakdown = Object.entries(counts)
    .map(([type, count]) => ({
      type,
      count,
      percentage: total > 0 ? (count / total * 100) : 0,
    }))
    .sort((a, b) => b.count - a.count);

  return { total, breakdown };
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
 * Agent outcome distribution from plan_status field.
 * Counts COMPLETE, BLOCKED, NEEDS_INPUT, IN_PROGRESS, REVIEW, and others.
 * Returns null if no entries have the plan_status field (older data).
 */
function calculateAgentOutcomes(workflowMetrics) {
  const withStatus = workflowMetrics.filter(r => r.plan_status && r.plan_status !== '');
  if (withStatus.length === 0) return null;

  const counts = {};
  for (const entry of withStatus) {
    const status = entry.plan_status.toUpperCase();
    counts[status] = (counts[status] || 0) + 1;
  }

  const total = withStatus.length;
  const distribution = Object.entries(counts)
    .map(([status, count]) => ({ status, count, percentage: (count / total) * 100 }))
    .sort((a, b) => b.count - a.count);

  return { distribution, total };
}

/**
 * Token usage approximation from output_tokens_approx field.
 * Groups by agent, computes total and average.
 * Returns null if no entries have the field (older data).
 */
function calculateTokenUsage(workflowMetrics) {
  const withTokens = workflowMetrics.filter(r => typeof r.output_tokens_approx === 'number');
  if (withTokens.length === 0) return null;

  const agentMap = {};
  for (const entry of withTokens) {
    const name = entry.agent || 'unknown';
    if (!agentMap[name]) agentMap[name] = { total: 0, count: 0 };
    agentMap[name].total += entry.output_tokens_approx;
    agentMap[name].count++;
  }

  const grandTotal = withTokens.reduce((s, r) => s + r.output_tokens_approx, 0);
  const agents = Object.entries(agentMap)
    .map(([name, { total, count }]) => ({
      name,
      total,
      avg: count > 0 ? Math.round(total / count) : 0,
      count,
    }))
    .sort((a, b) => b.total - a.total);

  return { agents, grandTotal, entryCount: withTokens.length };
}

/**
 * Runtime skill snapshot summary from agent-skills.jsonl and run snapshots.
 */
function calculateRuntimeSkillSummary(skillSnapshots, runSnapshots) {
  const explicitSnapshots = skillSnapshots.filter(entry => entry && entry.agent);
  const runtimeDefaults = runSnapshots
    .filter(entry => entry && entry.agent && entry.default_skills_snapshot)
    .map(entry => ({
      timestamp: entry.timestamp,
      session_id: entry.session_id,
      agent: entry.agent,
      model: entry.default_skills_snapshot.model || '',
      tools: entry.default_skills_snapshot.tools || [],
      skills: entry.default_skills_snapshot.skills || [],
      skills_count: entry.default_skills_snapshot.skills_count || 0,
      source: 'run-default',
    }));

  const latestByAgent = new Map();
  for (const snapshot of [...runtimeDefaults, ...explicitSnapshots]) {
    const agent = snapshot.agent || 'unknown';
    const current = latestByAgent.get(agent);
    if (!current || String(snapshot.timestamp || '') >= String(current.timestamp || '')) {
      latestByAgent.set(agent, {
        agent,
        timestamp: snapshot.timestamp || '',
        model: snapshot.model || '',
        tools: Array.isArray(snapshot.tools) ? snapshot.tools : [],
        skills: Array.isArray(snapshot.skills) ? snapshot.skills : [],
        skillsCount: typeof snapshot.skills_count === 'number'
          ? snapshot.skills_count
          : Array.isArray(snapshot.skills) ? snapshot.skills.length : 0,
        source: snapshot.source || 'explicit',
      });
    }
  }

  const latestProfiles = [...latestByAgent.values()]
    .sort((a, b) => a.agent.localeCompare(b.agent));
  const topSkillsSummary = topCounts(latestProfiles.flatMap(profile => profile.skills), 6);

  return {
    explicitCount: explicitSnapshots.length,
    runDefaultCount: runtimeDefaults.length,
    agentCount: latestProfiles.length,
    latestProfiles,
    topSkills: topSkillsSummary,
  };
}

/**
 * Context snapshot summary from run-snapshots.jsonl.
 */
function calculateContextSnapshotSummary(runSnapshots) {
  const withContext = runSnapshots.filter(entry => {
    const snapshot = entry.context_snapshot || {};
    return Object.keys(snapshot).length > 0;
  });

  if (withContext.length === 0) return null;

  const primarySurfaces = [];
  const contractSections = [];
  const writableSections = [];
  let multiSurfaceCount = 0;

  for (const entry of withContext) {
    const snapshot = entry.context_snapshot || {};
    if (snapshot.surface_routing?.primary_surface) {
      primarySurfaces.push(snapshot.surface_routing.primary_surface);
    }
    if (snapshot.surface_routing?.multi_surface) {
      multiSurfaceCount++;
    }
    contractSections.push(...(snapshot.contract_sections || []));
    writableSections.push(...(snapshot.context_update_scope?.writable_sections || []));
  }

  return {
    total: withContext.length,
    multiSurfaceCount,
    primarySurfaces: topCounts(primarySurfaces, 6),
    contractSections: topCounts(contractSections, 6),
    writableSections: topCounts(writableSections, 6),
  };
}

/**
 * Context update summary from run-snapshots.jsonl.
 */
function calculateContextUpdateSummary(runSnapshots) {
  if (runSnapshots.length === 0) return null;

  const updatedRuns = runSnapshots.filter(entry => entry.context_updated);
  const rejectedRuns = runSnapshots.filter(
    entry => Array.isArray(entry.context_rejected_sections) && entry.context_rejected_sections.length > 0
  );

  return {
    totalRuns: runSnapshots.length,
    updatedRuns: updatedRuns.length,
    rejectedRuns: rejectedRuns.length,
    updatedSections: topCounts(
      updatedRuns.flatMap(entry => entry.context_sections_updated || []),
      6
    ),
    rejectedSections: topCounts(
      runSnapshots.flatMap(entry => entry.context_rejected_sections || []),
      6
    ),
  };
}

/**
 * Anomaly summary from workflow-episodic-memory/anomalies.jsonl.
 * Groups anomalies by type for the last 30 days.
 */
function calculateAnomalySummary(anomalyEntries) {
  const cutoff = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  const entries = anomalyEntries.filter(
    entry => entry && entry.timestamp && entry.timestamp >= cutoff
  );

  if (entries.length === 0) return null;

  const typeCounts = {};
  const agentCounts = {};
  for (const entry of entries) {
    const anomalies = entry.anomalies || [];
    const agent = entry.metrics?.agent || 'unknown';
    for (const anomaly of anomalies) {
      const type = anomaly.type || 'unknown';
      typeCounts[type] = (typeCounts[type] || 0) + 1;
      agentCounts[agent] = (agentCounts[agent] || 0) + 1;
    }
  }

  const total = Object.values(typeCounts).reduce((sum, count) => sum + count, 0);
  const byType = Object.entries(typeCounts)
    .map(([type, count]) => ({
      type,
      count,
      percentage: total > 0 ? (count / total * 100) : 0,
    }))
    .sort((a, b) => b.count - a.count);

  return {
    total,
    sessionCount: entries.length,
    byType,
    byAgent: sortedCounts(agentCounts).slice(0, 5),
  };
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

function getLatestRuntimeProfile(agentName, skillSnapshots, runSnapshots) {
  const explicit = skillSnapshots
    .filter(entry => entry.agent === agentName)
    .map(entry => ({
      timestamp: entry.timestamp || '',
      model: entry.model || '',
      tools: Array.isArray(entry.tools) ? entry.tools : [],
      skills: Array.isArray(entry.skills) ? entry.skills : [],
      skillsCount: typeof entry.skills_count === 'number'
        ? entry.skills_count
        : Array.isArray(entry.skills) ? entry.skills.length : 0,
      source: 'explicit',
    }));

  const defaults = runSnapshots
    .filter(entry => entry.agent === agentName && entry.default_skills_snapshot)
    .map(entry => ({
      timestamp: entry.timestamp || '',
      model: entry.default_skills_snapshot.model || '',
      tools: entry.default_skills_snapshot.tools || [],
      skills: entry.default_skills_snapshot.skills || [],
      skillsCount: typeof entry.default_skills_snapshot.skills_count === 'number'
        ? entry.default_skills_snapshot.skills_count
        : Array.isArray(entry.default_skills_snapshot.skills)
          ? entry.default_skills_snapshot.skills.length
          : 0,
      source: 'run-default',
    }));

  const snapshots = [...defaults, ...explicit].sort(
    (a, b) => String(b.timestamp).localeCompare(String(a.timestamp))
  );

  return {
    latest: snapshots[0] || null,
    explicitCount: explicit.length,
    runDefaultCount: defaults.length,
  };
}

function calculateAgentAnomalySummary(agentName, anomalyEntries) {
  const entries = anomalyEntries.filter(entry => entry.metrics?.agent === agentName);
  if (entries.length === 0) return null;

  const typeCounts = {};
  for (const entry of entries) {
    for (const anomaly of (entry.anomalies || [])) {
      const type = anomaly.type || 'unknown';
      typeCounts[type] = (typeCounts[type] || 0) + 1;
    }
  }

  return {
    total: Object.values(typeCounts).reduce((sum, count) => sum + count, 0),
    sessionCount: entries.length,
    byType: sortedCounts(typeCounts).slice(0, 6),
  };
}

// ─────────────────────────────────────────────────────────────
// DISPLAY FUNCTIONS
// ─────────────────────────────────────────────────────────────

/**
 * Format token count as human-readable (e.g. "6.9k", "1.2M").
 */
function formatTokens(n) {
  if (n === null || n === undefined) return 'n/a';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

function countValues(values) {
  const counts = {};
  for (const value of values) {
    if (!value) continue;
    counts[value] = (counts[value] || 0) + 1;
  }
  return counts;
}

function sortedCounts(counts) {
  return Object.entries(counts)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

function topCounts(values, limit = 5) {
  return sortedCounts(countValues(values)).slice(0, limit);
}

function formatCountSummary(entries, emptyLabel = 'none') {
  if (!entries || entries.length === 0) return emptyLabel;
  return entries.map(({ name, count }) => `${name}(${count})`).join(', ');
}

function formatSkills(skills, limit = 4) {
  if (!Array.isArray(skills) || skills.length === 0) return 'none';
  if (skills.length <= limit) return skills.join(', ');
  return `${skills.slice(0, limit).join(', ')}, +${skills.length - limit} more`;
}

/**
 * Display the main dashboard metrics.
 */
function displayMetrics(
  tiers,
  cmdTypes,
  topCmds,
  agentInvocations,
  errorStats,
  auditTotal,
  agentOutcomes,
  tokenUsage,
  anomalySummary,
  runtimeSkills,
  contextSnapshots,
  contextUpdates
) {
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
  console.log(chalk.bold(`\n🛠  Command Type Breakdown  (derived from ${auditTotal} audit entries)`));

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

  // ── Agent Outcomes ───────────────────────────────────
  if (agentOutcomes) {
    console.log(chalk.bold(`\n📋 Agent Outcomes  (${agentOutcomes.total} sessions with status)`));
    const outcomeColor = { COMPLETE: chalk.green, BLOCKED: chalk.red, NEEDS_INPUT: chalk.yellow, IN_PROGRESS: chalk.cyan, REVIEW: chalk.magenta };
    for (const { status, count, percentage } of agentOutcomes.distribution) {
      const bar = makeBar(percentage, 10);
      const pct = percentage.toFixed(1).padStart(5);
      const color = outcomeColor[status] || chalk.gray;
      console.log(color(`  ${status.padEnd(16)} ${count.toString().padStart(3)}  ${bar.padEnd(10)}  ${pct}%`));
    }
  }

  // ── Token Usage (approx) ─────────────────────────────
  if (tokenUsage) {
    console.log(chalk.bold(`\n🪙 Token Usage (approx)  total: ~${formatTokens(tokenUsage.grandTotal)}`));
    for (const { name, total, avg, count } of tokenUsage.agents) {
      const totalFmt = formatTokens(total).padStart(6);
      const avgFmt = formatTokens(avg).padStart(6);
      console.log(`  ${name.padEnd(24)} ${count.toString().padStart(3)} sessions  total ${totalFmt}  avg ${avgFmt}`);
    }
  }

  // ── Runtime Skill Snapshots ───────────────────────────
  if (runtimeSkills && runtimeSkills.agentCount > 0) {
    console.log(chalk.bold(
      `\n🧠 Runtime Skill Snapshots  (${runtimeSkills.agentCount} agents, ${runtimeSkills.explicitCount} explicit, ${runtimeSkills.runDefaultCount} run defaults)`
    ));
    for (const profile of runtimeSkills.latestProfiles.slice(0, 6)) {
      const model = profile.model || 'default';
      console.log(
        `  ${profile.agent.padEnd(24)} model ${model.padEnd(8)} ` +
        `skills ${String(profile.skillsCount).padStart(2)}  tools ${String(profile.tools.length).padStart(2)}  ` +
        `${formatSkills(profile.skills, 3)}`
      );
    }
    if (runtimeSkills.latestProfiles.length > 6) {
      console.log(chalk.gray(`  ... ${runtimeSkills.latestProfiles.length - 6} more agents with captured snapshots`));
    }
    console.log(`  Common skills: ${formatCountSummary(runtimeSkills.topSkills)}`);
  }

  // ── Context Snapshot Summary ──────────────────────────
  if (contextSnapshots) {
    console.log(chalk.bold(`\n🗺  Context Snapshot Summary  (${contextSnapshots.total} sessions)`));
    console.log(`  Primary surfaces: ${formatCountSummary(contextSnapshots.primarySurfaces)}`);
    console.log(`  Multi-surface:    ${contextSnapshots.multiSurfaceCount}/${contextSnapshots.total} sessions`);
    console.log(`  Contract sections: ${formatCountSummary(contextSnapshots.contractSections)}`);
    if (contextSnapshots.writableSections.length > 0) {
      console.log(`  Writable scope:   ${formatCountSummary(contextSnapshots.writableSections)}`);
    }
  }

  // ── Context Updates ───────────────────────────────────
  if (contextUpdates) {
    console.log(chalk.bold(`\n📝 Context Updates  (${contextUpdates.updatedRuns}/${contextUpdates.totalRuns} sessions updated)`));
    console.log(`  Rejected writes:  ${contextUpdates.rejectedRuns} sessions`);
    console.log(`  Updated sections: ${formatCountSummary(contextUpdates.updatedSections)}`);
    if (contextUpdates.rejectedSections.length > 0) {
      console.log(chalk.yellow(`  Rejected sections: ${formatCountSummary(contextUpdates.rejectedSections)}`));
    }
  }

  // ── Anomaly Summary (last 30 days) ─────────────────
  if (anomalySummary && anomalySummary.total > 0) {
    console.log(chalk.bold(
      `\n⚠️  Anomaly Summary (last 30 days)  ${anomalySummary.total} anomalies across ${anomalySummary.sessionCount} sessions`
    ));
    for (const { type, count, percentage } of anomalySummary.byType) {
      const bar = makeBar(percentage, 10);
      const pct = percentage.toFixed(1).padStart(5);
      const color = type.includes('contract') ? chalk.red : chalk.yellow;
      console.log(color(`  ${type.padEnd(28)} ${count.toString().padStart(3)}  ${bar.padEnd(10)}  ${pct}%`));
    }
    if (anomalySummary.byAgent.length > 0) {
      console.log(`  Agents: ${formatCountSummary(anomalySummary.byAgent)}`);
    }
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
  console.log(
    chalk.gray(
      '💡 Source: .claude/logs/audit-*.jsonl  |  episodic-memory/index.json  |  workflow-episodic-memory/*.jsonl\n'
    )
  );
}

/**
 * Display the agent detail view (--agent <name>).
 */
async function displayAgentDetail(agentName, workflowMetrics, auditLogs, runSnapshots, skillSnapshots, anomalyEntries) {
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

  // ── Runtime Snapshot ─────────────────────────────────
  console.log(chalk.bold('\n🧠 Runtime Snapshot'));
  const runtimeProfile = getLatestRuntimeProfile(agentName, skillSnapshots, runSnapshots);
  if (!runtimeProfile.latest) {
    console.log(chalk.gray('  no runtime skill snapshot data'));
  } else {
    const latest = runtimeProfile.latest;
    console.log(`  Latest model:    ${latest.model || 'default'}`);
    console.log(
      `  Snapshot source: ${latest.source === 'explicit'
        ? 'agent-skills.jsonl'
        : 'run-snapshots default profile'}`
    );
    console.log(`  Snapshots seen:  ${runtimeProfile.explicitCount} explicit, ${runtimeProfile.runDefaultCount} run defaults`);
    console.log(`  Tools:           ${latest.tools.length > 0 ? latest.tools.join(', ') : 'none'}`);
    console.log(`  Skills:          ${formatSkills(latest.skills, 6)}`);
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
    console.log(chalk.gray('  no invocations found in episodic-memory/index.json'));
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

  // ── Context Snapshot Summary ─────────────────────────
  const agentRunSnapshots = runSnapshots.filter(entry => entry.agent === agentName);
  const agentContextSummary = calculateContextSnapshotSummary(agentRunSnapshots);
  const agentContextUpdates = calculateContextUpdateSummary(agentRunSnapshots);
  const agentAnomalies = calculateAgentAnomalySummary(agentName, anomalyEntries);

  console.log(chalk.bold('\n🗺  Context Snapshot Summary'));
  if (!agentContextSummary) {
    console.log(chalk.gray('  no context snapshot data'));
  } else {
    console.log(`  Sessions with context: ${agentContextSummary.total}`);
    console.log(`  Primary surfaces:      ${formatCountSummary(agentContextSummary.primarySurfaces)}`);
    console.log(`  Multi-surface:         ${agentContextSummary.multiSurfaceCount}/${agentContextSummary.total}`);
    console.log(`  Contract sections:     ${formatCountSummary(agentContextSummary.contractSections)}`);
    if (agentContextSummary.writableSections.length > 0) {
      console.log(`  Writable scope:        ${formatCountSummary(agentContextSummary.writableSections)}`);
    }
  }

  console.log(chalk.bold('\n📝 Context Updates + Anomalies'));
  if (!agentContextUpdates && !agentAnomalies) {
    console.log(chalk.gray('  no context update or anomaly data'));
  } else {
    if (agentContextUpdates) {
      console.log(`  Context updated:   ${agentContextUpdates.updatedRuns}/${agentContextUpdates.totalRuns} sessions`);
      console.log(`  Updated sections:  ${formatCountSummary(agentContextUpdates.updatedSections)}`);
      if (agentContextUpdates.rejectedSections.length > 0) {
        console.log(chalk.yellow(`  Rejected sections: ${formatCountSummary(agentContextUpdates.rejectedSections)}`));
      }
    }
    if (agentAnomalies) {
      console.log(`  Anomalies:         ${agentAnomalies.total} across ${agentAnomalies.sessionCount} sessions`);
      console.log(`  Types:             ${formatCountSummary(agentAnomalies.byType)}`);
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
  process.stderr.write('[DEPRECATED] gaia-metrics.js is deprecated. Use: python3 bin/gaia metrics\n[DEPRECATED] Migration guide: see CHANGELOG.md\n');

  // Parse --agent flag
  const agentFlagIdx = process.argv.indexOf('--agent');
  const agentName = agentFlagIdx !== -1 ? process.argv[agentFlagIdx + 1] : null;

  const spinner = ora('Loading metrics...').start();

  try {
    const claudeDir = join(CWD, '.claude');
    if (!existsSync(claudeDir)) {
      spinner.fail('.claude/ directory not found');
      console.log(chalk.yellow('\n⚠️  Gaia-ops not installed in this directory'));
      console.log(chalk.gray('   Run: npx gaia-scan\n'));
      process.exit(1);
    }

    const [auditLogs, workflowMetrics, runSnapshots, skillSnapshots, anomalyEntries] = await Promise.all([
      readAuditLogs(),
      readWorkflowMetrics(),
      readRunSnapshots(),
      readAgentSkillSnapshots(),
      readAnomalyEntries(),
    ]);

    if (
      auditLogs.length === 0 &&
      workflowMetrics.length === 0 &&
      runSnapshots.length === 0 &&
      skillSnapshots.length === 0 &&
      anomalyEntries.length === 0
    ) {
      spinner.info('No log data found');
      console.log(chalk.yellow('\n⚠️  No metrics data available yet'));
      console.log(chalk.gray('   Metrics will be generated as you use the system\n'));
      process.exit(0);
    }

    spinner.succeed(
      `Loaded ${auditLogs.length} audit + ${workflowMetrics.length} workflow + ${runSnapshots.length} telemetry entries`
    );

    if (agentName) {
      // Agent detail view
      await displayAgentDetail(agentName, workflowMetrics, auditLogs, runSnapshots, skillSnapshots, anomalyEntries);
    } else {
      // Full dashboard
      const tiers            = calculateTierUsage(auditLogs);
      const cmdTypes         = calculateCommandTypeBreakdown(auditLogs);
      const topCmds          = calculateTopCommands(auditLogs);
      const agentInvocations = calculateAgentInvocations(workflowMetrics);
      const errorStats       = calculateErrorRate(auditLogs);
      const agentOutcomes    = calculateAgentOutcomes(workflowMetrics);
      const tokenUsage       = calculateTokenUsage(workflowMetrics);
      const anomalySummary   = calculateAnomalySummary(anomalyEntries);
      const runtimeSkills    = calculateRuntimeSkillSummary(skillSnapshots, runSnapshots);
      const contextSnapshots = calculateContextSnapshotSummary(runSnapshots);
      const contextUpdates   = calculateContextUpdateSummary(runSnapshots);

      displayMetrics(
        tiers,
        cmdTypes,
        topCmds,
        agentInvocations,
        errorStats,
        auditLogs.length,
        agentOutcomes,
        tokenUsage,
        anomalySummary,
        runtimeSkills,
        contextSnapshots,
        contextUpdates
      );
    }

  } catch (error) {
    spinner.fail(`Failed to load metrics: ${error.message}`);
    console.error(chalk.red(`\n❌ Error: ${error.stack}\n`));
    process.exit(1);
  }
}

main();
