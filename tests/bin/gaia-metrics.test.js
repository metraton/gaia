import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const SCRIPT_PATH = join(REPO_ROOT, 'bin', 'gaia-metrics.js');

function writeJson(path, value) {
  writeFileSync(path, JSON.stringify(value, null, 2));
}

function writeJsonl(path, rows) {
  writeFileSync(path, `${rows.map(row => JSON.stringify(row)).join('\n')}\n`);
}

function setupProject({ includeTelemetry = true } = {}) {
  const projectRoot = mkdtempSync(join(tmpdir(), 'gaia-metrics-'));
  const claudeDir = join(projectRoot, '.claude');
  const logsDir = join(claudeDir, 'logs');
  const episodicDir = join(claudeDir, 'project-context', 'episodic-memory');
  const workflowDir = join(claudeDir, 'project-context', 'workflow-episodic-memory');
  const agentsDir = join(claudeDir, 'agents');

  mkdirSync(logsDir, { recursive: true });
  mkdirSync(episodicDir, { recursive: true });
  mkdirSync(workflowDir, { recursive: true });
  mkdirSync(agentsDir, { recursive: true });

  const now = new Date();
  const recent = (minutesAgo) => new Date(now.getTime() - minutesAgo * 60_000).toISOString();

  writeJsonl(join(logsDir, 'audit-2026-03-11.jsonl'), [
    {
      timestamp: recent(30),
      tool_name: 'Bash',
      command: 'kubectl get pods -n prod',
      tier: 'T0',
      exit_code: 0,
      session_id: 'sess-001',
    },
    {
      timestamp: recent(10),
      tool_name: 'Bash',
      command: 'git status',
      tier: 'T0',
      exit_code: 0,
      session_id: 'sess-002',
    },
  ]);

  writeJson(join(episodicDir, 'index.json'), {
    episodes: [
      {
        id: 'ep-001',
        timestamp: recent(25),
        agent: 'cloud-troubleshooter',
        task_id: 'task-001',
        exit_code: 0,
        plan_status: 'COMPLETE',
        output_length: 240,
        output_tokens_approx: 60,
        prompt: 'Diagnose rollout drift',
      },
      {
        id: 'ep-002',
        timestamp: recent(15),
        agent: 'developer',
        task_id: 'task-002',
        exit_code: 0,
        plan_status: 'COMPLETE',
        output_length: 360,
        output_tokens_approx: 90,
        prompt: 'Expose analytics surface',
      },
      {
        id: 'ep-003',
        timestamp: recent(5),
        agent: 'cloud-troubleshooter',
        task_id: 'task-003',
        exit_code: 1,
        plan_status: 'BLOCKED',
        output_length: 180,
        output_tokens_approx: 45,
        prompt: 'Re-check anomalies',
      },
    ],
  });

  writeFileSync(
    join(agentsDir, 'cloud-troubleshooter.md'),
    `---
description: Investigates runtime issues.
skills:
  - agent-protocol
  - fast-queries
  - context-updater
---
`
  );

  if (includeTelemetry) {
    writeJsonl(join(workflowDir, 'run-snapshots.jsonl'), [
      {
        timestamp: recent(25),
        session_id: 'sess-001',
        task_id: 'task-001',
        agent_id: 'task-001',
        agent: 'cloud-troubleshooter',
        tier: 'T0',
        plan_status: 'COMPLETE',
        context_snapshot: {
          contract_sections: ['application_services', 'cluster_details'],
          surface_routing: {
            primary_surface: 'live_runtime',
            active_surfaces: ['gitops_desired_state', 'live_runtime'],
            multi_surface: true,
          },
          context_update_scope: {
            readable_sections: ['application_services', 'cluster_details'],
            writable_sections: ['cluster_details'],
          },
        },
        context_updated: true,
        context_sections_updated: ['cluster_details'],
        context_rejected_sections: ['operational_guidelines'],
        default_skills_snapshot: {
          agent: 'cloud-troubleshooter',
          model: 'fast',
          tools: ['Read', 'Bash', 'Task'],
          skills: ['agent-protocol', 'fast-queries', 'context-updater'],
          skills_count: 3,
        },
      },
      {
        timestamp: recent(15),
        session_id: 'sess-002',
        task_id: 'task-002',
        agent_id: 'task-002',
        agent: 'developer',
        tier: 'T0',
        plan_status: 'COMPLETE',
        context_snapshot: {
          contract_sections: ['application_services'],
          surface_routing: {
            primary_surface: 'app_ci_tooling',
            active_surfaces: ['app_ci_tooling'],
            multi_surface: false,
          },
          context_update_scope: {
            readable_sections: ['application_services'],
            writable_sections: ['application_services'],
          },
        },
        context_updated: false,
        context_sections_updated: [],
        context_rejected_sections: [],
        default_skills_snapshot: {
          agent: 'developer',
          model: 'fast',
          tools: ['Read', 'Edit', 'Bash'],
          skills: ['agent-protocol', 'developer-patterns', 'command-execution'],
          skills_count: 3,
        },
      },
    ]);

    writeJsonl(join(workflowDir, 'agent-skills.jsonl'), [
      {
        timestamp: recent(20),
        session_id: 'sess-001',
        agent: 'cloud-troubleshooter',
        task_description: 'Diagnose rollout drift',
        model: 'fast',
        tools: ['Read', 'Bash', 'Task'],
        skills: ['agent-protocol', 'fast-queries', 'context-updater'],
        skills_count: 3,
      },
    ]);

    writeJsonl(join(workflowDir, 'anomalies.jsonl'), [
      {
        timestamp: recent(5),
        anomalies: [
          { type: 'missing_evidence', severity: 'warning' },
          { type: 'scope_escalation', severity: 'warning' },
        ],
        metrics: {
          agent: 'cloud-troubleshooter',
          task_id: 'task-003',
          context_updated: true,
        },
      },
    ]);
  }

  return projectRoot;
}

function runMetrics(projectRoot, args = []) {
  const result = spawnSync('node', [SCRIPT_PATH, ...args], {
    cwd: projectRoot,
    env: {
      ...process.env,
      FORCE_COLOR: '0',
      INIT_CWD: projectRoot,
    },
    encoding: 'utf-8',
  });

  return {
    ...result,
    combinedOutput: `${result.stdout}\n${result.stderr}`,
  };
}

test('dashboard shows runtime skills, context snapshots, and anomaly summaries', () => {
  const projectRoot = setupProject({ includeTelemetry: true });
  const result = runMetrics(projectRoot);

  assert.equal(result.status, 0, result.combinedOutput);
  assert.match(result.combinedOutput, /Runtime Skill Snapshots/);
  assert.match(result.combinedOutput, /Context Snapshot Summary/);
  assert.match(result.combinedOutput, /Context Updates/);
  assert.match(result.combinedOutput, /missing_evidence/);
  assert.match(result.combinedOutput, /scope_escalation/);
  assert.match(result.combinedOutput, /Common skills: .*agent-protocol/);
});

test('agent detail view shows runtime snapshot and context update analytics', () => {
  const projectRoot = setupProject({ includeTelemetry: true });
  const result = runMetrics(projectRoot, ['--agent', 'cloud-troubleshooter']);

  assert.equal(result.status, 0, result.combinedOutput);
  assert.match(result.combinedOutput, /Runtime Snapshot/);
  assert.match(result.combinedOutput, /Snapshot source:\s+agent-skills\.jsonl/);
  assert.match(result.combinedOutput, /Context Snapshot Summary/);
  assert.match(result.combinedOutput, /Context Updates \+ Anomalies/);
  assert.match(result.combinedOutput, /Primary surfaces:\s+live_runtime\(1\)/);
  assert.match(result.combinedOutput, /Rejected sections:\s+operational_guidelines\(1\)/);
});

test('dashboard remains usable when only legacy metrics are present', () => {
  const projectRoot = setupProject({ includeTelemetry: false });
  const result = runMetrics(projectRoot);

  assert.equal(result.status, 0, result.combinedOutput);
  assert.match(result.combinedOutput, /Security Tier Usage/);
  assert.match(result.combinedOutput, /Agent Invocations/);
  assert.doesNotMatch(result.combinedOutput, /Runtime Skill Snapshots/);
});
