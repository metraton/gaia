#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Cleanup script
 *
 * Purpose:
 * - Remove CLAUDE.md
 * - Remove settings.json
 * - Remove all symlinks (agents, tools, hooks, commands, config, templates, CHANGELOG.md)
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
import { existsSync, lstatSync, statSync, readdirSync } from 'fs';
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
 * Remove legacy CLAUDE.md if it exists (identity now injected by hook).
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
      join(claudeDir, 'CHANGELOG.md'),
      join(claudeDir, 'README.en.md'),
      join(claudeDir, 'README.md')
    ];
    
    // Also remove AGENTS.md at project root
    const agentsMdPath = join(CWD, 'AGENTS.md');

    let removed = 0;
    
    // Remove known symlinks in .claude/
    for (const symlinkPath of symlinks) {
      try {
        // Use lstat to check if path exists as a symlink (works for broken symlinks too)
        const stats = lstatSync(symlinkPath);
        if (stats.isSymbolicLink() || stats.isFile()) {
          await fs.unlink(symlinkPath);
          removed++;
        }
      } catch (error) {
        // Path doesn't exist or other error, skip
      }
    }
    
    // Remove AGENTS.md at project root (it's a symlink)
    try {
      const stats = lstatSync(agentsMdPath);
      if (stats.isSymbolicLink() || stats.isFile()) {
        await fs.unlink(agentsMdPath);
        removed++;
      }
    } catch (error) {
      // Path doesn't exist, skip
    }
    
    // Scan for ANY other broken symlinks in .claude/ directory
    try {
      const entries = await fs.readdir(claudeDir);
      for (const entry of entries) {
        const fullPath = join(claudeDir, entry);
        try {
          const stats = lstatSync(fullPath);
          // Check if it's a symlink
          if (stats.isSymbolicLink()) {
            // Try to access the target to see if it's broken
            try {
              await fs.access(fullPath);
              // Symlink is valid, skip
            } catch {
              // Symlink is broken, remove it
              await fs.unlink(fullPath);
              removed++;
            }
          }
        } catch {
          // Skip if can't check
        }
      }
    } catch (error) {
      // Can't read directory, skip scan
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
 * Data retention policy configuration
 * Defines max age in days for each data category
 */
const RETENTION_POLICY = {
  auditLogs:          { pattern: 'audit-*.jsonl',    dir: '.claude/logs',                                              maxDays: 30,  label: 'Audit logs' },
  hookLogs:           { pattern: 'hooks-*.log',      dir: '.claude/logs',                                              maxDays: 14,  label: 'Hook logs' },
  monthlyMetrics:     { pattern: 'metrics-*.jsonl',  dir: '.claude/metrics',                                           maxDays: 90,  label: 'Monthly metrics' },
  responseContract:   { type: 'dirs',                dir: '.claude/session/active/response-contract',                   maxDays: 7,   label: 'Response contract sessions' },
  episodicEpisodes:   { pattern: '*.json',           dir: '.claude/project-context/episodic-memory/episodes',           maxDays: 90,  label: 'Episodic memory episodes' },
  workflowMetrics:    { type: 'truncate-jsonl',      file: '.claude/project-context/workflow-episodic-memory/metrics.jsonl',    maxDays: 90,  label: 'Workflow metrics' },
  anomalies:          { type: 'truncate-jsonl',      file: '.claude/project-context/workflow-episodic-memory/anomalies.jsonl',  maxDays: 90,  label: 'Anomalies' },
  legacyLogs:         { type: 'legacy',              dir: '.claude/logs',   patterns: ['pre_tool_use_v2-*.log', 'post_tool_use_v2-*.log', 'subagent_stop-*.log'], label: 'Legacy logs' },
  anomalyFlag:        { type: 'flag-ttl',            file: '.claude/project-context/workflow-episodic-memory/signals/needs_analysis.flag', maxHours: 1, label: 'Anomaly signal flag' },
};

/**
 * Check if a filename matches a glob-like pattern (supports * wildcard)
 */
function matchesPattern(filename, pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${escaped}$`).test(filename);
}

/**
 * Delete files matching a pattern that are older than maxDays
 */
async function pruneOldFiles(dirPath, pattern, maxDays, label) {
  const fullDir = join(CWD, dirPath);
  if (!existsSync(fullDir)) return 0;

  const cutoff = Date.now() - maxDays * 24 * 60 * 60 * 1000;
  let removed = 0;

  try {
    const entries = readdirSync(fullDir);
    for (const entry of entries) {
      if (!matchesPattern(entry, pattern)) continue;
      const fullPath = join(fullDir, entry);
      try {
        const stats = statSync(fullPath);
        if (stats.isFile() && stats.mtimeMs < cutoff) {
          await fs.unlink(fullPath);
          console.log(chalk.gray(`  Pruned: ${dirPath}/${entry} (${label})`));
          removed++;
        }
      } catch { /* skip unreadable files */ }
    }
  } catch { /* directory not readable */ }

  return removed;
}

/**
 * Delete directories older than maxDays (by modification time)
 */
async function pruneOldDirs(dirPath, maxDays, label) {
  const fullDir = join(CWD, dirPath);
  if (!existsSync(fullDir)) return 0;

  const cutoff = Date.now() - maxDays * 24 * 60 * 60 * 1000;
  let removed = 0;

  try {
    const entries = readdirSync(fullDir);
    for (const entry of entries) {
      const fullPath = join(fullDir, entry);
      try {
        const stats = statSync(fullPath);
        if (stats.isDirectory() && stats.mtimeMs < cutoff) {
          await fs.rm(fullPath, { recursive: true, force: true });
          console.log(chalk.gray(`  Pruned dir: ${dirPath}/${entry} (${label})`));
          removed++;
        }
      } catch { /* skip */ }
    }
  } catch { /* directory not readable */ }

  return removed;
}

/**
 * Truncate a JSONL file, keeping only entries newer than maxDays
 * Entries must have a "timestamp" field (ISO 8601)
 */
async function truncateJsonl(filePath, maxDays, label) {
  const fullPath = join(CWD, filePath);
  if (!existsSync(fullPath)) return 0;

  const cutoff = Date.now() - maxDays * 24 * 60 * 60 * 1000;
  let removedCount = 0;

  try {
    const content = await fs.readFile(fullPath, 'utf-8');
    const lines = content.trim().split('\n').filter(l => l.trim());
    const kept = [];

    for (const line of lines) {
      try {
        const entry = JSON.parse(line);
        const ts = entry.timestamp ? new Date(entry.timestamp).getTime() : Date.now();
        if (ts >= cutoff) {
          kept.push(line);
        } else {
          removedCount++;
        }
      } catch {
        // Unparseable line - keep it to be safe
        kept.push(line);
      }
    }

    if (removedCount > 0) {
      await fs.writeFile(fullPath, kept.join('\n') + (kept.length ? '\n' : ''));
      console.log(chalk.gray(`  Truncated: ${filePath} - removed ${removedCount} old entries (${label})`));
    }
  } catch { /* file not readable */ }

  return removedCount;
}

/**
 * Delete legacy log files that should no longer exist
 */
async function pruneLegacyLogs(dirPath, patterns, label) {
  const fullDir = join(CWD, dirPath);
  if (!existsSync(fullDir)) return 0;

  let removed = 0;

  try {
    const entries = readdirSync(fullDir);
    for (const entry of entries) {
      const matches = patterns.some(p => matchesPattern(entry, p));
      if (!matches) continue;
      const fullPath = join(fullDir, entry);
      try {
        const stats = statSync(fullPath);
        if (stats.isFile()) {
          await fs.unlink(fullPath);
          console.log(chalk.gray(`  Deleted legacy: ${dirPath}/${entry} (${label})`));
          removed++;
        }
      } catch { /* skip */ }
    }
  } catch { /* directory not readable */ }

  return removed;
}

/**
 * Delete a flag file if it is older than maxHours
 */
async function pruneFlagByTtl(filePath, maxHours, label) {
  const fullPath = join(CWD, filePath);
  if (!existsSync(fullPath)) return 0;

  const cutoff = Date.now() - maxHours * 60 * 60 * 1000;

  try {
    const stats = statSync(fullPath);
    if (stats.mtimeMs < cutoff) {
      await fs.unlink(fullPath);
      console.log(chalk.gray(`  Expired flag: ${filePath} (${label})`));
      return 1;
    }

    // Also check created_at timestamp inside the JSON
    try {
      const content = await fs.readFile(fullPath, 'utf-8');
      const data = JSON.parse(content);
      const createdAt = data.created_at || data.timestamp;
      if (createdAt) {
        const createdMs = new Date(createdAt).getTime();
        if (createdMs < cutoff) {
          await fs.unlink(fullPath);
          console.log(chalk.gray(`  Expired flag (by created_at): ${filePath} (${label})`));
          return 1;
        }
      }
    } catch { /* not valid JSON, use mtime check above */ }
  } catch { /* file not accessible */ }

  return 0;
}

/**
 * Rebuild episodic memory index after pruning episodes
 */
async function rebuildEpisodicIndex() {
  const episodesDir = join(CWD, '.claude/project-context/episodic-memory/episodes');
  const indexPath = join(CWD, '.claude/project-context/episodic-memory/index.json');

  if (!existsSync(episodesDir)) return;

  try {
    const entries = readdirSync(episodesDir).filter(e => e.endsWith('.json')).sort();
    const index = [];

    for (const entry of entries) {
      try {
        const content = await fs.readFile(join(episodesDir, entry), 'utf-8');
        const episode = JSON.parse(content);
        index.push({
          id: episode.id || entry.replace('.json', ''),
          timestamp: episode.timestamp || '',
          prompt_summary: (episode.prompt || '').slice(0, 100),
          outcome: episode.outcome || 'unknown',
          tags: episode.tags || [],
        });
      } catch { /* skip unparseable */ }
    }

    await fs.writeFile(indexPath, JSON.stringify({ episodes: index, rebuilt_at: new Date().toISOString() }, null, 2));
    console.log(chalk.gray(`  Rebuilt episodic index: ${index.length} episodes`));
  } catch (error) {
    console.log(chalk.yellow(`  Warning: could not rebuild episodic index: ${error.message}`));
  }
}

/**
 * Apply data retention policy - prune old data across all categories
 */
async function applyRetentionPolicy() {
  const spinner = ora('Applying data retention policy...').start();

  let totalPruned = 0;

  try {
    for (const [_key, policy] of Object.entries(RETENTION_POLICY)) {
      if (policy.type === 'dirs') {
        totalPruned += await pruneOldDirs(policy.dir, policy.maxDays, policy.label);
      } else if (policy.type === 'truncate-jsonl') {
        totalPruned += await truncateJsonl(policy.file, policy.maxDays, policy.label);
      } else if (policy.type === 'legacy') {
        totalPruned += await pruneLegacyLogs(policy.dir, policy.patterns, policy.label);
      } else if (policy.type === 'flag-ttl') {
        totalPruned += await pruneFlagByTtl(policy.file, policy.maxHours, policy.label);
      } else if (policy.pattern) {
        totalPruned += await pruneOldFiles(policy.dir, policy.pattern, policy.maxDays, policy.label);
      }
    }

    // Rebuild episodic index if any episodes were pruned
    if (totalPruned > 0) {
      await rebuildEpisodicIndex();
    }

    if (totalPruned > 0) {
      spinner.succeed(`Data retention applied: ${totalPruned} item(s) pruned`);
    } else {
      spinner.info('Data retention: nothing to prune');
    }

    return totalPruned > 0;
  } catch (error) {
    spinner.fail(`Data retention failed: ${error.message}`);
    return false;
  }
}

/**
 * Main function
 */
async function main() {
  process.stderr.write('[DEPRECATED] gaia-cleanup.js is deprecated. Use: python3 bin/gaia cleanup\n[DEPRECATED] Migration guide: see CHANGELOG.md\n');

  const args = process.argv.slice(2);
  const pruneOnly = args.includes('--prune') || args.includes('--retain');

  if (pruneOnly) {
    console.log(chalk.cyan('\n🧹 @jaguilar87/gaia-ops data retention\n'));
    console.log(chalk.gray('Retention policy:'));
    console.log(chalk.gray('  Audit logs:           30 days'));
    console.log(chalk.gray('  Hook logs:            14 days'));
    console.log(chalk.gray('  Monthly metrics:      90 days'));
    console.log(chalk.gray('  Response contracts:    7 days'));
    console.log(chalk.gray('  Episodic episodes:   90 days'));
    console.log(chalk.gray('  Workflow metrics:     90 days'));
    console.log(chalk.gray('  Anomalies:           90 days'));
    console.log(chalk.gray('  Legacy logs:         all removed'));
    console.log(chalk.gray('  Anomaly flag:         1 hour TTL\n'));

    try {
      const pruned = await applyRetentionPolicy();
      if (pruned) {
        console.log(chalk.green('\n✅ Data retention completed\n'));
      } else {
        console.log(chalk.gray('\n✓ All data within retention limits\n'));
      }
    } catch (error) {
      console.error(chalk.red(`\n❌ Data retention failed: ${error.message}\n`));
      process.exit(1);
    }
    return;
  }

  console.log(chalk.cyan('\n🧹 @jaguilar87/gaia-ops cleanup\n'));

  try {
    const claudeRemoved = await removeClaudeMd();
    const settingsRemoved = await removeSettingsJson();
    const symlinksRemoved = await removeSymlinks();

    // Always apply data retention as part of cleanup
    const retentionApplied = await applyRetentionPolicy();

    if (claudeRemoved || settingsRemoved || symlinksRemoved || retentionApplied) {
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
