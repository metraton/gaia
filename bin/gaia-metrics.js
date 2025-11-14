#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Metrics viewer
 *
 * Displays system metrics from logs and configuration
 *
 * Usage:
 *   npx gaia-metrics
 *   gaia-metrics (if installed globally)
 *
 * Metrics shown:
 * - Routing accuracy
 * - Context efficiency
 * - Agent invocations
 * - Tier usage distribution
 * - System health
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

/**
 * Read metrics targets from config
 */
async function loadMetricsTargets() {
  try {
    const metricsPath = join(CWD, '.claude', 'config', 'metrics_targets.json');
    if (!existsSync(metricsPath)) {
      return null;
    }
    const content = await fs.readFile(metricsPath, 'utf-8');
    return JSON.parse(content);
  } catch (error) {
    return null;
  }
}

/**
 * Read logs from .claude/logs/
 */
async function readLogs() {
  try {
    const logsDir = join(CWD, '.claude', 'logs');
    if (!existsSync(logsDir)) {
      return [];
    }

    const files = await fs.readdir(logsDir);
    const jsonlFiles = files.filter(f => f.endsWith('.jsonl'));

    let allLogs = [];
    for (const file of jsonlFiles) {
      try {
        const content = await fs.readFile(join(logsDir, file), 'utf-8');
        const lines = content.split('\n').filter(l => l.trim());
        const logs = lines.map(line => {
          try {
            return JSON.parse(line);
          } catch {
            return null;
          }
        }).filter(l => l !== null);
        allLogs = allLogs.concat(logs);
      } catch (error) {
        // Skip file if can't read
      }
    }

    return allLogs;
  } catch (error) {
    return [];
  }
}

/**
 * Calculate routing accuracy from logs
 */
function calculateRoutingAccuracy(logs) {
  const routingEvents = logs.filter(l => l.event === 'agent_routed' || l.event === 'routing');
  if (routingEvents.length === 0) return null;

  const successful = routingEvents.filter(l => l.success !== false).length;
  const total = routingEvents.length;
  
  return {
    accuracy: (successful / total) * 100,
    total: total,
    successful: successful,
    failed: total - successful
  };
}

/**
 * Calculate agent invocations from logs
 */
function calculateAgentInvocations(logs) {
  const agentEvents = logs.filter(l => l.agent && l.event === 'agent_invoked');
  
  const counts = {};
  for (const event of agentEvents) {
    counts[event.agent] = (counts[event.agent] || 0) + 1;
  }

  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  
  return {
    total: agentEvents.length,
    byAgent: sorted
  };
}

/**
 * Calculate tier usage from logs
 */
function calculateTierUsage(logs) {
  const tierEvents = logs.filter(l => l.tier);
  
  const counts = {};
  for (const event of tierEvents) {
    counts[event.tier] = (counts[event.tier] || 0) + 1;
  }

  const total = tierEvents.length;
  const distribution = Object.entries(counts).map(([tier, count]) => ({
    tier,
    count,
    percentage: total > 0 ? (count / total * 100).toFixed(1) : 0
  })).sort((a, b) => a.tier.localeCompare(b.tier));

  return {
    total,
    distribution
  };
}

/**
 * Calculate context efficiency (if available in logs)
 */
function calculateContextEfficiency(logs) {
  const contextEvents = logs.filter(l => l.event === 'context_generated' && l.tokens);
  
  if (contextEvents.length === 0) return null;

  const totalOriginal = contextEvents.reduce((sum, e) => sum + (e.tokens.original || 0), 0);
  const totalOptimized = contextEvents.reduce((sum, e) => sum + (e.tokens.optimized || 0), 0);

  if (totalOriginal === 0) return null;

  const saved = totalOriginal - totalOptimized;
  const efficiency = (saved / totalOriginal) * 100;

  return {
    efficiency: efficiency,
    tokensSaved: saved,
    originalTokens: totalOriginal,
    optimizedTokens: totalOptimized,
    events: contextEvents.length
  };
}

/**
 * Display metrics in formatted output
 */
function displayMetrics(targets, routing, invocations, tiers, context) {
  console.log(chalk.cyan('\n📊 Gaia-Ops System Metrics\n'));
  console.log(chalk.gray('═'.repeat(60)));

  // Routing Accuracy
  if (routing) {
    console.log(chalk.bold('\n🎯 Routing Accuracy'));
    const targetAccuracy = targets?.routing_accuracy ? (targets.routing_accuracy * 100).toFixed(1) : 'N/A';
    const status = routing.accuracy >= (targets?.routing_accuracy * 100 || 90) ? 
      chalk.green('✓ Good') : chalk.yellow('⚠ Below Target');
    
    console.log(chalk.white(`  Current: ${routing.accuracy.toFixed(1)}% ${status}`));
    console.log(chalk.gray(`  Target:  ${targetAccuracy}%`));
    console.log(chalk.gray(`  Total:   ${routing.total} routing decisions`));
    console.log(chalk.gray(`    ✓ Success: ${routing.successful}`));
    console.log(chalk.gray(`    ✗ Failed:  ${routing.failed}`));
  } else {
    console.log(chalk.bold('\n🎯 Routing Accuracy'));
    console.log(chalk.gray('  No routing data available'));
  }

  // Context Efficiency
  if (context) {
    console.log(chalk.bold('\n💾 Context Efficiency'));
    const targetEfficiency = targets?.context_efficiency ? (targets.context_efficiency * 100).toFixed(0) : 'N/A';
    const status = context.efficiency >= (targets?.context_efficiency * 100 || 80) ?
      chalk.green('✓ Good') : chalk.yellow('⚠ Below Target');
    
    console.log(chalk.white(`  Efficiency:  ${context.efficiency.toFixed(1)}% ${status}`));
    console.log(chalk.gray(`  Target:      ${targetEfficiency}%`));
    console.log(chalk.gray(`  Tokens saved: ${context.tokensSaved.toLocaleString()}`));
    console.log(chalk.gray(`  Original:    ${context.originalTokens.toLocaleString()} tokens`));
    console.log(chalk.gray(`  Optimized:   ${context.optimizedTokens.toLocaleString()} tokens`));
  } else {
    console.log(chalk.bold('\n💾 Context Efficiency'));
    console.log(chalk.gray('  No context optimization data available'));
  }

  // Agent Invocations
  if (invocations && invocations.total > 0) {
    console.log(chalk.bold('\n🤖 Agent Invocations'));
    console.log(chalk.white(`  Total: ${invocations.total} invocations`));
    console.log('');
    for (const [agent, count] of invocations.byAgent.slice(0, 6)) {
      const percentage = ((count / invocations.total) * 100).toFixed(1);
      const bar = '█'.repeat(Math.floor(percentage / 5));
      console.log(chalk.gray(`  ${agent.padEnd(25)} ${count.toString().padStart(4)} ${bar} ${percentage}%`));
    }
  } else {
    console.log(chalk.bold('\n🤖 Agent Invocations'));
    console.log(chalk.gray('  No invocation data available'));
  }

  // Tier Usage
  if (tiers && tiers.total > 0) {
    console.log(chalk.bold('\n🔒 Security Tier Usage'));
    console.log(chalk.white(`  Total: ${tiers.total} operations`));
    console.log('');
    for (const { tier, count, percentage } of tiers.distribution) {
      const color = tier === 'T3' ? chalk.red : tier === 'T2' ? chalk.yellow : chalk.green;
      const bar = '█'.repeat(Math.floor(percentage / 5));
      console.log(color(`  ${tier.padEnd(8)} ${count.toString().padStart(4)} ${bar} ${percentage}%`));
    }
  } else {
    console.log(chalk.bold('\n🔒 Security Tier Usage'));
    console.log(chalk.gray('  No tier usage data available'));
  }

  console.log(chalk.gray('\n═'.repeat(60)));
  console.log(chalk.gray('\n💡 Tip: Metrics are calculated from .claude/logs/*.jsonl'));
  console.log(chalk.gray('   Run operations to generate more metrics data\n'));
}

/**
 * Main function
 */
async function main() {
  const spinner = ora('Loading metrics...').start();

  try {
    // Check if .claude/ exists
    const claudeDir = join(CWD, '.claude');
    if (!existsSync(claudeDir)) {
      spinner.fail('.claude/ directory not found');
      console.log(chalk.yellow('\n⚠️  Gaia-ops not installed in this directory'));
      console.log(chalk.gray('   Run: npx gaia-init\n'));
      process.exit(1);
    }

    // Load data
    const targets = await loadMetricsTargets();
    const logs = await readLogs();

    if (logs.length === 0) {
      spinner.info('No log data found');
      console.log(chalk.yellow('\n⚠️  No metrics data available yet'));
      console.log(chalk.gray('   Metrics will be generated as you use the system\n'));
      process.exit(0);
    }

    spinner.succeed(`Loaded ${logs.length} log entries`);

    // Calculate metrics
    const routing = calculateRoutingAccuracy(logs);
    const invocations = calculateAgentInvocations(logs);
    const tiers = calculateTierUsage(logs);
    const context = calculateContextEfficiency(logs);

    // Display
    displayMetrics(targets, routing, invocations, tiers, context);

  } catch (error) {
    spinner.fail(`Failed to load metrics: ${error.message}`);
    console.error(chalk.red(`\n❌ Error: ${error.stack}\n`));
    process.exit(1);
  }
}

main();

