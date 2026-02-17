#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Installer CLI v2
 *
 * Scan-Confirm-Install flow: auto-detects everything, asks only gaps.
 *
 * Usage:
 *   npx gaia-init                              # Interactive (scan + confirm)
 *   npx gaia-init --non-interactive             # CI/CD mode (scan + auto-accept)
 *   npx gaia-init --non-interactive --cluster my-cluster  # Override detected values
 *
 * CLI Options:
 *   --non-interactive, -y     Accept detected values without confirmation
 *   --gitops <path>           Override GitOps directory
 *   --terraform <path>        Override Terraform directory
 *   --app-services <path>     Override App Services directory
 *   --project-id <id>         Override cloud project ID (GCP or AWS)
 *   --region <region>         Override primary region
 *   --cluster <name>          Override cluster name
 *   --skip-claude-install     Skip Claude Code installation (not recommended)
 *   --project-context-repo <url>  Git repository with existing project-context.json
 *
 * Environment Variables (override detected values):
 *   CLAUDE_GITOPS_DIR, CLAUDE_TERRAFORM_DIR, CLAUDE_APP_SERVICES_DIR,
 *   CLAUDE_PROJECT_ID, CLAUDE_REGION, CLAUDE_CLUSTER_NAME,
 *   CLAUDE_PROJECT_CONTEXT_REPO
 */

import { fileURLToPath } from 'url';
import { basename, dirname, join, relative, resolve, isAbsolute } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import { exec } from 'child_process';
import { promisify } from 'util';
import prompts from 'prompts';
import chalk from 'chalk';
import ora from 'ora';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const CWD = process.cwd();

// Package root: relative to this bin/ file
const PACKAGE_ROOT = resolve(__dirname, '..');

function getTemplatePath(name) {
  return join(PACKAGE_ROOT, 'templates', name);
}

// ============================================================================
// Phase 1: Silent Scan
// ============================================================================

/**
 * Run all detection in parallel, return unified scan result.
 */
async function runScan() {
  const spinner = ora('Scanning project structure...').start();

  try {
    const [dirs, cloud, gitRemotes, claudeCode] = await Promise.all([
      scanDirectories(),
      scanCloudProvider(),
      scanGitRemotes(),
      scanClaudeCode()
    ]);

    spinner.succeed('Project scanned');

    return { dirs, cloud, gitRemotes, claudeCode };
  } catch (error) {
    spinner.fail('Scan failed');
    throw error;
  }
}

/**
 * Detect directory structure by scanning CWD for known patterns.
 */
async function scanDirectories() {
  const result = { gitops: null, terraform: null, appServices: null };

  let entries;
  try {
    entries = await fs.readdir(CWD);
  } catch {
    return result;
  }

  const candidates = {
    gitops: ['gitops', 'k8s', 'kubernetes', 'manifests', 'deployments'],
    terraform: ['terraform', 'tf', 'infrastructure', 'iac', 'infra'],
    appServices: ['app-services', 'services', 'apps', 'applications', 'src']
  };

  for (const [key, patterns] of Object.entries(candidates)) {
    for (const pattern of patterns) {
      if (entries.includes(pattern)) {
        result[key] = `./${pattern}`;
        break;
      }
    }
  }

  return result;
}

/**
 * Detect cloud provider, project ID, and region from terraform files.
 * Scans terraform/**\/*.tf and *.hcl for provider blocks.
 */
async function scanCloudProvider() {
  const result = { provider: null, projectId: null, region: null };

  // Find terraform directory first
  const tfCandidates = ['terraform', 'tf', 'infrastructure', 'iac', 'infra'];
  let tfDir = null;

  for (const candidate of tfCandidates) {
    const path = join(CWD, candidate);
    if (existsSync(path)) {
      tfDir = path;
      break;
    }
  }

  if (!tfDir) return result;

  // Recursively find .tf and .hcl files (max 3 levels deep to avoid huge scans)
  const tfFiles = await findFiles(tfDir, '.tf', 3);
  const hclFiles = await findFiles(tfDir, '.hcl', 3);
  const allFiles = [...tfFiles, ...hclFiles];

  let hasGoogle = false;
  let hasAws = false;

  for (const file of allFiles) {
    try {
      const content = await fs.readFile(file, 'utf-8');

      // Detect providers
      if (/provider\s+"google(?:-beta)?"/.test(content)) hasGoogle = true;
      if (/provider\s+"aws"/.test(content)) hasAws = true;

      // Extract GCP project ID — scoped to provider block context
      if (!result.projectId && hasGoogle) {
        const providerBlock = extractProviderBlock(content, 'google');
        if (providerBlock) {
          const projectMatch = providerBlock.match(/project\s*=\s*"([^"]+)"/);
          if (projectMatch) result.projectId = projectMatch[1];
        }
      }

      // Extract AWS account (from provider block or data.aws_caller_identity)
      if (!result.projectId && hasAws) {
        const providerBlock = extractProviderBlock(content, 'aws');
        if (providerBlock) {
          const accountMatch = providerBlock.match(/account_id\s*=\s*"(\d{12})"/);
          if (accountMatch) result.projectId = accountMatch[1];
        }
      }

      // Extract region — scoped to provider block, supports multi-segment regions (us-gov-west-1)
      if (!result.region) {
        const providerName = hasGoogle ? 'google' : (hasAws ? 'aws' : null);
        const providerBlock = providerName ? extractProviderBlock(content, providerName) : null;
        const source = providerBlock || content;
        const regionMatch = source.match(/region\s*=\s*"([a-z]+-[a-z0-9]+(?:-[a-z0-9]+)*)"/);
        if (regionMatch) result.region = regionMatch[1];
      }
    } catch {
      // Skip unreadable files
    }
  }

  if (hasGoogle && hasAws) result.provider = 'multi-cloud';
  else if (hasGoogle) result.provider = 'gcp';
  else if (hasAws) result.provider = 'aws';

  return result;
}

/**
 * Extract the content of a provider block from HCL/TF content.
 * Handles nested braces to find the correct closing brace.
 */
function extractProviderBlock(content, providerName) {
  const pattern = new RegExp(`provider\\s+"${providerName}(?:-beta)?"\\s*\\{`);
  const match = content.match(pattern);
  if (!match) return null;

  let depth = 1;
  let start = match.index + match[0].length;

  for (let i = start; i < content.length && depth > 0; i++) {
    if (content[i] === '{') depth++;
    else if (content[i] === '}') depth--;
    if (depth === 0) return content.slice(start, i);
  }

  return null;
}

/**
 * Detect git remotes from repositories in subdirectories.
 * Returns list of {path, remote} objects.
 */
async function scanGitRemotes() {
  const remotes = [];

  // Check root
  if (existsSync(join(CWD, '.git'))) {
    const remote = await getGitRemote(CWD);
    if (remote) remotes.push({ path: '.', remote });
  }

  // Check 1 level deep
  let entries;
  try {
    entries = await fs.readdir(CWD, { withFileTypes: true });
  } catch {
    return remotes;
  }

  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.') || entry.name === 'node_modules') continue;

    const subDir = join(CWD, entry.name);
    if (existsSync(join(subDir, '.git'))) {
      const remote = await getGitRemote(subDir);
      if (remote) remotes.push({ path: `./${entry.name}`, remote });
      continue;
    }

    // Check 2 levels deep (e.g., gitops/qxo-monorepo/.git)
    try {
      const subEntries = await fs.readdir(subDir, { withFileTypes: true });
      for (const subEntry of subEntries) {
        if (!subEntry.isDirectory() || subEntry.name.startsWith('.') || subEntry.name === 'node_modules') continue;
        const deepDir = join(subDir, subEntry.name);
        if (existsSync(join(deepDir, '.git'))) {
          const remote = await getGitRemote(deepDir);
          if (remote) remotes.push({ path: `./${entry.name}/${subEntry.name}`, remote });
        }
      }
    } catch {
      // Skip unreadable subdirs
    }
  }

  return remotes;
}

/**
 * Check if Claude Code CLI is installed.
 */
async function scanClaudeCode() {
  try {
    const { stdout } = await execAsync('claude --version 2>/dev/null || claude-code --version 2>/dev/null');
    const version = stdout.trim().split('\n')[0];
    return { installed: true, version };
  } catch {
    return { installed: false, version: null };
  }
}

// ============================================================================
// Phase 2: Display Results + Confirm
// ============================================================================

/**
 * Build unified config from scan results + CLI overrides + env vars.
 * Priority: CLI args > env vars > scan results > defaults
 */
function buildConfig(scan, args) {
  return {
    gitops: args.gitops || process.env.CLAUDE_GITOPS_DIR || scan.dirs.gitops || './gitops',
    terraform: args.terraform || process.env.CLAUDE_TERRAFORM_DIR || scan.dirs.terraform || './terraform',
    appServices: args.appServices || process.env.CLAUDE_APP_SERVICES_DIR || scan.dirs.appServices || './app-services',
    cloudProvider: scan.cloud.provider || 'gcp',
    projectId: args.projectId || process.env.CLAUDE_PROJECT_ID || scan.cloud.projectId || '',
    region: args.region || process.env.CLAUDE_REGION || scan.cloud.region || '',
    clusterName: args.cluster || process.env.CLAUDE_CLUSTER_NAME || '',
    projectContextRepo: args.projectContextRepo || process.env.CLAUDE_PROJECT_CONTEXT_REPO || '',
    claudeCode: scan.claudeCode,
    gitRemotes: scan.gitRemotes
  };
}

/**
 * Display scan results and ask for confirmation.
 * Returns final config (possibly edited by user).
 */
async function displayAndConfirm(config) {
  // Banner
  console.log(chalk.cyan(`
  ██████╗  █████╗ ██╗ █████╗      ██████╗ ██████╗ ███████╗
 ██╔════╝ ██╔══██╗██║██╔══██╗    ██╔═══██╗██╔══██╗██╔════╝
 ██║  ███╗███████║██║███████║    ██║   ██║██████╔╝███████╗
 ██║   ██║██╔══██║██║██╔══██║    ██║   ██║██╔═══╝ ╚════██║
 ╚██████╔╝██║  ██║██║██║  ██║    ╚██████╔╝██║     ███████║
  ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝     ╚═════╝ ╚═╝     ╚══════╝
  `));

  // Detected config display
  console.log(chalk.cyan.bold('  Detected Configuration\n'));

  // Paths
  console.log(chalk.white('  Paths'));
  printField('GitOps', config.gitops, existsSync(resolve(CWD, config.gitops)));
  printField('Terraform', config.terraform, existsSync(resolve(CWD, config.terraform)));
  printField('App Services', config.appServices, existsSync(resolve(CWD, config.appServices)));

  // Cloud
  console.log(chalk.white('\n  Cloud'));
  printField('Provider', config.cloudProvider?.toUpperCase() || null, !!config.cloudProvider);
  printField('Project ID', config.projectId || null, !!config.projectId);
  printField('Region', config.region || null, !!config.region);
  printField('Cluster', config.clusterName || null, !!config.clusterName);

  // Git remotes (informational)
  if (config.gitRemotes.length > 0) {
    console.log(chalk.white('\n  Git Repositories'));
    for (const { path, remote } of config.gitRemotes.slice(0, 5)) {
      console.log(chalk.gray(`    ${path} → ${remote}`));
    }
    if (config.gitRemotes.length > 5) {
      console.log(chalk.gray(`    ... and ${config.gitRemotes.length - 5} more`));
    }
  }

  // Claude Code
  console.log(chalk.white('\n  Claude Code'));
  if (config.claudeCode.installed) {
    console.log(chalk.green(`    ${config.claudeCode.version}  ✓`));
  } else {
    console.log(chalk.yellow('    Not installed (will be installed automatically)'));
  }

  console.log('');

  // Identify gaps (items that need user input)
  const gaps = [];
  if (!config.projectId) gaps.push('projectId');
  if (!config.region) gaps.push('region');
  if (!config.clusterName) gaps.push('clusterName');

  return { config, gaps };
}

function printField(label, value, found) {
  const padded = label.padEnd(14);
  if (value && found) {
    console.log(chalk.green(`    ${padded} ${value}  ✓`));
  } else if (value) {
    console.log(chalk.yellow(`    ${padded} ${value}  (will be created)`));
  } else {
    console.log(chalk.yellow(`    ${padded} (not detected)  ?`));
  }
}

/**
 * Ask user to confirm or edit the detected configuration.
 * Returns final config.
 */
async function confirmOrEdit(config, gaps) {
  // If there are gaps, ask for them first
  if (gaps.length > 0) {
    console.log(chalk.yellow(`  ${gaps.length} item(s) need your input:\n`));

    const gapQuestions = [];

    if (gaps.includes('projectId')) {
      gapQuestions.push({
        type: 'text',
        name: 'projectId',
        message: config.cloudProvider === 'aws' ? '  AWS Account ID:' : '  Cloud Project ID:',
        initial: '',
        validate: v => v.trim().length > 0 || 'Required for agent operations'
      });
    }

    if (gaps.includes('region')) {
      gapQuestions.push({
        type: 'text',
        name: 'region',
        message: '  Primary Region:',
        initial: config.cloudProvider === 'gcp' ? 'us-central1' : 'us-east-1'
      });
    }

    if (gaps.includes('clusterName')) {
      gapQuestions.push({
        type: 'text',
        name: 'clusterName',
        message: '  Cluster Name (empty to skip):',
        initial: ''
      });
    }

    const gapAnswers = await prompts(gapQuestions, {
      onCancel: () => { console.log(chalk.yellow('\n  Installation cancelled.\n')); process.exit(0); }
    });

    // Merge gap answers into config
    if (gapAnswers.projectId) config.projectId = gapAnswers.projectId;
    if (gapAnswers.region) config.region = gapAnswers.region;
    if (gapAnswers.clusterName !== undefined) config.clusterName = gapAnswers.clusterName;
  }

  // Confirmation
  const { action } = await prompts({
    type: 'select',
    name: 'action',
    message: 'Proceed with this configuration?',
    choices: [
      { title: 'Accept and install', value: 'accept' },
      { title: 'Edit configuration', value: 'edit' },
      { title: 'Cancel', value: 'cancel' }
    ],
    initial: 0
  }, {
    onCancel: () => { console.log(chalk.yellow('\n  Installation cancelled.\n')); process.exit(0); }
  });

  if (action === 'cancel') {
    console.log(chalk.yellow('\n  Installation cancelled.\n'));
    process.exit(0);
  }

  if (action === 'edit') {
    return await editConfig(config);
  }

  return config;
}

/**
 * Let user edit all config fields.
 */
async function editConfig(config) {
  console.log(chalk.gray('\n  Edit any field (press Enter to keep current value):\n'));

  const answers = await prompts([
    { type: 'text', name: 'gitops', message: 'GitOps directory:', initial: config.gitops },
    { type: 'text', name: 'terraform', message: 'Terraform directory:', initial: config.terraform },
    { type: 'text', name: 'appServices', message: 'App Services directory:', initial: config.appServices },
    {
      type: 'select', name: 'cloudProvider', message: 'Cloud provider:',
      choices: [
        { title: 'GCP', value: 'gcp' },
        { title: 'AWS', value: 'aws' },
        { title: 'Multi-cloud', value: 'multi-cloud' }
      ],
      initial: config.cloudProvider === 'aws' ? 1 : config.cloudProvider === 'multi-cloud' ? 2 : 0
    },
    { type: 'text', name: 'projectId', message: 'Project/Account ID:', initial: config.projectId },
    { type: 'text', name: 'region', message: 'Primary region:', initial: config.region },
    { type: 'text', name: 'clusterName', message: 'Cluster name:', initial: config.clusterName },
    { type: 'text', name: 'projectContextRepo', message: 'Project context repo (empty to skip):', initial: config.projectContextRepo }
  ], {
    onCancel: () => { console.log(chalk.yellow('\n  Installation cancelled.\n')); process.exit(0); }
  });

  return {
    ...config,
    ...answers
  };
}

// ============================================================================
// Phase 3 & 4: Install
// ============================================================================

/**
 * Install Claude Code if not present (mandatory).
 */
async function ensureClaudeCode(claudeCodeStatus, skipInstall = false) {
  if (claudeCodeStatus.installed) return;

  if (skipInstall) {
    console.log(chalk.yellow('\n  ⚠️  Claude Code not installed (--skip-claude-install used)'));
    console.log(chalk.gray('  Install manually: npm install -g @anthropic-ai/claude-code\n'));
    return;
  }

  const spinner = ora('Installing Claude Code (mandatory)...').start();

  try {
    await execAsync('npm install -g @anthropic-ai/claude-code', { timeout: 120000 });
    spinner.succeed('Claude Code installed');
  } catch (error) {
    spinner.fail('Failed to install Claude Code');
    console.log(chalk.red(`\n  Error: ${error.message}`));
    console.log(chalk.yellow('  Install manually: npm install -g @anthropic-ai/claude-code\n'));
    // Don't throw - continue installation
  }
}

/**
 * Ensure @jaguilar87/gaia-ops is installed as npm dependency.
 */
async function ensureGaiaOpsPackage() {
  const spinner = ora('Setting up @jaguilar87/gaia-ops...').start();

  try {
    const packageJsonPath = join(CWD, 'package.json');

    // Check if already installed in node_modules
    const pkgPath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops', 'package.json');
    if (existsSync(pkgPath)) {
      spinner.succeed('@jaguilar87/gaia-ops already installed');
      return;
    }

    // Create package.json if missing
    if (!existsSync(packageJsonPath)) {
      await fs.writeFile(packageJsonPath, JSON.stringify({
        name: 'my-project',
        version: '1.0.0',
        private: true,
        dependencies: {}
      }, null, 2), 'utf-8');
    }

    await execAsync('npm install @jaguilar87/gaia-ops', { timeout: 120000 });
    spinner.succeed('@jaguilar87/gaia-ops installed');
  } catch (error) {
    spinner.fail(`Failed to install package: ${error.message}`);
    throw error;
  }
}

/**
 * Create .claude/ directory with symlinks to npm package.
 */
async function createClaudeDirectory() {
  const spinner = ora('Creating .claude/ directory...').start();

  try {
    const claudeDir = join(CWD, '.claude');
    await fs.mkdir(claudeDir, { recursive: true });

    const packagePath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops');
    const relativePath = relative(claudeDir, packagePath);

    const symlinks = [
      'agents', 'tools', 'hooks', 'commands',
      'templates', 'config', 'speckit', 'skills'
    ];

    for (const name of symlinks) {
      const link = join(claudeDir, name);
      if (existsSync(link)) await fs.unlink(link);
      await fs.symlink(join(relativePath, name), link);
    }

    // CHANGELOG.md symlink
    const changelogLink = join(claudeDir, 'CHANGELOG.md');
    if (existsSync(changelogLink)) await fs.unlink(changelogLink);
    await fs.symlink(join(relativePath, 'CHANGELOG.md'), changelogLink);

    // Project-specific directories (NOT symlinked)
    await fs.mkdir(join(claudeDir, 'logs'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'tests'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'project-context'), { recursive: true });

    spinner.succeed('.claude/ directory created');
  } catch (error) {
    spinner.fail('Failed to create .claude/');
    throw error;
  }
}

/**
 * Copy static CLAUDE.md from template (no placeholders to replace).
 */
async function copyClaudeMd() {
  const spinner = ora('Generating CLAUDE.md...').start();

  try {
    const templatePath = getTemplatePath('CLAUDE.template.md');
    const destPath = join(CWD, 'CLAUDE.md');
    await fs.copyFile(templatePath, destPath);
    spinner.succeed('CLAUDE.md generated (static)');
  } catch (error) {
    spinner.fail('Failed to generate CLAUDE.md');
    throw error;
  }
}

/**
 * Copy settings.json from template.
 */
async function copySettingsJson() {
  const spinner = ora('Generating settings.json...').start();

  try {
    const templatePath = getTemplatePath('settings.template.json');
    const destPath = join(CWD, '.claude', 'settings.json');
    await fs.copyFile(templatePath, destPath);
    spinner.succeed('settings.json generated');
  } catch (error) {
    spinner.fail('Failed to generate settings.json');
    throw error;
  }
}

/**
 * Generate project-context.json from detected + user config.
 */
async function generateProjectContext(config) {
  const spinner = ora('Generating project-context.json...').start();

  try {
    const metadata = {
      version: '1.0',
      last_updated: new Date().toISOString(),
      project_name: config.projectName || basename(CWD),
      project_root: '.',
      created_by: 'gaia-init',
      cloud_provider: config.cloudProvider,
      environment: 'non-prod',
      primary_region: config.region
    };

    // Cloud-specific metadata
    if (['gcp', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      metadata.project_id = config.projectId;
    }
    if (['aws', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      metadata.aws_account = config.projectId;
    }

    const projectDetails = {
      region: config.region,
      environment: 'non-prod',
      cluster_name: config.clusterName,
      cloud_provider: config.cloudProvider
    };

    if (config.cloudProvider === 'gcp' || config.cloudProvider === 'multi-cloud') {
      projectDetails.project_id = config.projectId;
    }
    if (config.cloudProvider === 'aws' || config.cloudProvider === 'multi-cloud') {
      projectDetails.account_id = config.projectId;
    }

    // Build provider credentials
    const providerCredentials = {};
    if (['gcp', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      providerCredentials.gcp = { project: config.projectId, region: config.region };
    }
    if (['aws', 'multi-cloud'].includes(config.cloudProvider) && config.projectId) {
      providerCredentials.aws = { account_id: config.projectId, region: config.region };
    }

    // Detect gitops platform from files
    const gitopsPlatform = await detectGitopsPlatform(config.gitops);

    const projectContext = {
      metadata,
      paths: {
        gitops: config.gitops,
        terraform: config.terraform,
        app_services: config.appServices
      },
      sections: {
        project_details: projectDetails,
        terraform_infrastructure: {
          layout: {
            base_path: config.terraform,
            module_structure: 'terragrunt'
          },
          provider_credentials: providerCredentials
        },
        gitops_configuration: {
          repository: {
            path: config.gitops,
            platform: gitopsPlatform
          }
        },
        application_services: {
          base_path: config.appServices,
          services: []
        },
        operational_guidelines: {
          commit_standards: {
            format: 'conventional_commits',
            validation_required: true,
            config_path: '.claude/config/git_standards.json'
          }
        }
      }
    };

    const destPath = join(CWD, '.claude', 'project-context', 'project-context.json');
    await fs.writeFile(destPath, JSON.stringify(projectContext, null, 2), 'utf-8');

    spinner.succeed('project-context.json generated');
  } catch (error) {
    spinner.fail('Failed to generate project-context.json');
    throw error;
  }
}

/**
 * Ensure project directories exist (create if missing).
 */
async function ensureProjectDirs(config) {
  const dirs = [
    { path: config.gitops, name: 'GitOps' },
    { path: config.terraform, name: 'Terraform' },
    { path: config.appServices, name: 'App Services' }
  ];

  for (const { path: dirPath, name } of dirs) {
    const absPath = isAbsolute(dirPath) ? dirPath : resolve(CWD, dirPath);
    if (!existsSync(absPath)) {
      await fs.mkdir(absPath, { recursive: true });
      console.log(chalk.gray(`  Created ${name}: ${dirPath}`));
    }
  }
}

/**
 * Clone project context repo if provided.
 */
async function cloneProjectContextRepo(repoUrl) {
  if (!repoUrl || repoUrl.trim() === '') return;

  const sanitized = sanitizeGitUrl(repoUrl);
  const spinner = ora('Cloning project context repository...').start();

  try {
    const contextDir = join(CWD, '.claude', 'project-context');
    const tempDir = `${contextDir}-temp`;

    if (existsSync(tempDir)) await fs.rm(tempDir, { recursive: true, force: true });

    await execAsync(`git clone ${sanitized} ${tempDir}`, { timeout: 30000 });

    // Move contents to project-context/
    const files = await fs.readdir(tempDir);
    for (const file of files) {
      await fs.rename(join(tempDir, file), join(contextDir, file));
    }
    await fs.rm(tempDir, { recursive: true, force: true });

    spinner.succeed('Project context cloned');
  } catch (error) {
    spinner.fail(`Failed to clone: ${error.message}`);
    console.log(chalk.gray(`  Clone manually: cd .claude && git clone ${sanitized} project-context\n`));
  }
}

// ============================================================================
// Verification (inline gaia-doctor)
// ============================================================================

/**
 * Run post-install verification checks.
 */
async function runVerification() {
  console.log(chalk.cyan('\n  Verifying installation...\n'));

  const checks = [
    { name: 'Symlinks', fn: checkSymlinks },
    { name: 'CLAUDE.md', fn: checkClaudeMd },
    { name: 'settings.json', fn: checkSettingsJson },
    { name: 'project-context', fn: checkProjectContext },
    { name: 'Python', fn: checkPython },
    { name: 'Hooks', fn: checkHooks }
  ];

  let allPassed = true;

  for (const { name, fn } of checks) {
    try {
      const result = await fn();
      if (result.ok) {
        console.log(chalk.green(`    ✓ ${name.padEnd(18)} ${result.detail || ''}`));
      } else {
        console.log(chalk.yellow(`    ⚠ ${name.padEnd(18)} ${result.detail}`));
        if (result.fix) console.log(chalk.gray(`      Fix: ${result.fix}`));
        allPassed = false;
      }
    } catch {
      console.log(chalk.red(`    ✗ ${name.padEnd(18)} Check failed`));
      allPassed = false;
    }
  }

  console.log('');
  return allPassed;
}

async function checkSymlinks() {
  const names = ['agents', 'tools', 'hooks', 'commands', 'templates', 'config', 'speckit', 'CHANGELOG.md'];
  let valid = 0;
  for (const name of names) {
    if (existsSync(join(CWD, '.claude', name))) valid++;
  }
  return { ok: valid === names.length, detail: `${valid}/${names.length} valid` };
}

async function checkClaudeMd() {
  const path = join(CWD, 'CLAUDE.md');
  if (!existsSync(path)) return { ok: false, detail: 'Missing', fix: 'Run gaia-init' };
  const content = await fs.readFile(path, 'utf-8');
  if (content.includes('{{')) return { ok: false, detail: 'Contains raw placeholders', fix: 'Run gaia-init to regenerate' };
  return { ok: true, detail: 'Valid' };
}

async function checkSettingsJson() {
  const path = join(CWD, '.claude', 'settings.json');
  if (!existsSync(path)) return { ok: false, detail: 'Missing', fix: 'Run gaia-init' };
  try {
    JSON.parse(await fs.readFile(path, 'utf-8'));
    return { ok: true, detail: 'Valid JSON' };
  } catch {
    return { ok: false, detail: 'Invalid JSON', fix: 'Delete and run gaia-init' };
  }
}

async function checkProjectContext() {
  const path = join(CWD, '.claude', 'project-context', 'project-context.json');
  if (!existsSync(path)) return { ok: false, detail: 'Missing', fix: 'Run gaia-init or /speckit.init' };
  try {
    const data = JSON.parse(await fs.readFile(path, 'utf-8'));
    const sections = Object.keys(data.sections || {}).length;
    return { ok: sections >= 3, detail: `${sections} sections` };
  } catch {
    return { ok: false, detail: 'Invalid JSON', fix: 'Regenerate with /speckit.init' };
  }
}

async function checkPython() {
  try {
    const { stdout } = await execAsync('python3 --version');
    const version = stdout.trim();
    return { ok: true, detail: version };
  } catch {
    return { ok: false, detail: 'Not found', fix: 'Install Python 3.9+' };
  }
}

async function checkHooks() {
  const hookPath = join(CWD, '.claude', 'hooks', 'pre_tool_use.py');
  if (!existsSync(hookPath)) return { ok: false, detail: 'pre_tool_use.py missing' };
  return { ok: true, detail: 'pre_tool_use.py found' };
}

// ============================================================================
// Utility functions
// ============================================================================

async function getGitRemote(dir) {
  try {
    const { stdout } = await execAsync('git remote get-url origin', { cwd: dir, timeout: 5000 });
    return stdout.trim();
  } catch {
    return null;
  }
}

async function findFiles(dir, extension, maxDepth, currentDepth = 0) {
  if (currentDepth >= maxDepth) return [];

  const results = [];
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
      const fullPath = join(dir, entry.name);
      if (entry.isDirectory()) {
        const nested = await findFiles(fullPath, extension, maxDepth, currentDepth + 1);
        results.push(...nested);
      } else if (entry.name.endsWith(extension)) {
        results.push(fullPath);
      }
    }
  } catch {
    // Skip unreadable directories
  }
  return results;
}

async function detectGitopsPlatform(gitopsPath) {
  const absPath = isAbsolute(gitopsPath) ? gitopsPath : resolve(CWD, gitopsPath);
  if (!existsSync(absPath)) return 'flux';

  try {
    const files = await findFiles(absPath, '.yaml', 3);
    for (const file of files) {
      try {
        const content = await fs.readFile(file, 'utf-8');
        if (content.includes('kind: Kustomization') && content.includes('toolkit.fluxcd.io')) return 'flux';
        if (content.includes('kind: Application') && content.includes('argoproj.io')) return 'argocd';
      } catch {
        continue;
      }
    }
  } catch {
    // Default
  }
  return 'flux';
}

function sanitizeGitUrl(input) {
  if (!input || typeof input !== 'string') return '';
  let sanitized = input.trim();
  if (sanitized.toLowerCase().startsWith('git clone ')) {
    sanitized = sanitized.substring('git clone '.length).trim();
  }
  return sanitized.replace(/^["']|["']$/g, '');
}

// ============================================================================
// CLI Argument Parsing
// ============================================================================

function parseCliArguments() {
  return yargs(hideBin(process.argv))
    .usage('Usage: $0 [options]')
    .option('non-interactive', { alias: 'y', type: 'boolean', description: 'Accept detected values', default: false })
    .option('gitops', { type: 'string', description: 'GitOps directory path' })
    .option('terraform', { type: 'string', description: 'Terraform directory path' })
    .option('app-services', { type: 'string', description: 'App services directory path' })
    .option('project-id', { type: 'string', description: 'Cloud project/account ID' })
    .option('region', { type: 'string', description: 'Primary region' })
    .option('cluster', { type: 'string', description: 'Cluster name' })
    .option('skip-claude-install', { type: 'boolean', description: 'Skip Claude Code install', default: false })
    .option('project-context-repo', { type: 'string', description: 'Git repo with project context' })
    .help('h').alias('h', 'help')
    .version('2.0.0').alias('v', 'version')
    .parse();
}

// ============================================================================
// Main
// ============================================================================

async function main() {
  try {
    const args = parseCliArguments();

    // Phase 1: Scan
    const scan = await runScan();

    // Build config from scan + overrides
    let config = buildConfig(scan, args);

    if (args.nonInteractive) {
      // Non-interactive: show what was detected and proceed
      console.log(chalk.cyan('\n  Configuration (auto-detected + overrides):\n'));
      console.log(chalk.gray(`    GitOps:       ${config.gitops}`));
      console.log(chalk.gray(`    Terraform:    ${config.terraform}`));
      console.log(chalk.gray(`    App Services: ${config.appServices}`));
      console.log(chalk.gray(`    Cloud:        ${config.cloudProvider?.toUpperCase()}`));
      console.log(chalk.gray(`    Project ID:   ${config.projectId || '(none)'}`));
      console.log(chalk.gray(`    Region:       ${config.region || '(none)'}`));
      console.log(chalk.gray(`    Cluster:      ${config.clusterName || '(none)'}\n`));
    } else {
      // Phase 2: Display + Confirm
      const { gaps } = await displayAndConfirm(config);

      // Phase 3: Fill gaps + get confirmation
      config = await confirmOrEdit(config, gaps);
    }

    // Phase 4: Install
    console.log(chalk.cyan('\n  Installing...\n'));

    // 4.1 Claude Code (mandatory)
    await ensureClaudeCode(config.claudeCode, args.skipClaudeInstall);

    // 4.2 npm package
    await ensureGaiaOpsPackage();

    // 4.3 Project directories
    await ensureProjectDirs(config);

    // 4.4 .claude/ directory with symlinks
    await createClaudeDirectory();

    // 4.5 Static CLAUDE.md
    await copyClaudeMd();

    // 4.6 Settings.json
    await copySettingsJson();

    // 4.7 Project context
    await generateProjectContext(config);

    // 4.8 Clone context repo (optional)
    if (config.projectContextRepo) {
      await cloneProjectContextRepo(config.projectContextRepo);
    }

    // Verification
    const healthy = await runVerification();

    // Success
    console.log(chalk.green.bold('  ✓ Installation complete!\n'));

    if (!healthy) {
      console.log(chalk.yellow('  Some checks have warnings. Run `npx gaia-doctor` for details.\n'));
    }

    console.log(chalk.gray('  Next steps:'));
    console.log(chalk.gray('    1. Start Claude Code: claude'));
    console.log(chalk.gray('    2. Enrich context:    /speckit.init'));
    console.log(chalk.gray('    3. Verify health:     npx gaia-doctor\n'));

  } catch (error) {
    console.error(chalk.red(`\n  ✗ Installation failed: ${error.message}\n`));
    process.exit(1);
  }
}

main();
