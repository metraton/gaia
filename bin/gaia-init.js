#!/usr/bin/env node

/**
 * @jaguilar87/gaia-ops - Installer CLI
 *
 * Interactive installer for Gaia-Ops agent system
 *
 * Usage:
 *   npx @jaguilar87/gaia-ops init                # Interactive mode
 *   npx @jaguilar87/gaia-ops init --non-interactive  # Non-interactive mode
 *   gaia-init                                     # If installed globally
 *
 * CLI Options:
 *   --non-interactive          Skip interactive prompts, use defaults or provided values
 *   --gitops <path>            GitOps directory path (relative or absolute)
 *   --terraform <path>         Terraform directory path (relative or absolute)
 *   --app-services <path>      App services directory path (relative or absolute)
 *   --project-id <id>          GCP Project ID
 *   --region <region>          Primary region (default: us-central1)
 *   --cluster <name>           Cluster name
 *   --skip-claude-install      Skip Claude Code installation
 *   --project-context-repo <url>  Git repository URL for project context
 *
 * Examples:
 *   # With relative paths
 *   npx gaia-init --non-interactive --gitops ./gitops --terraform ./terraform
 *
 *   # With absolute paths (recommended for clarity)
 *   npx gaia-init --non-interactive \
 *     --gitops /home/user/projects/my-app/gitops \
 *     --terraform /home/user/projects/my-app/terraform \
 *     --app-services /home/user/projects/my-app/services \
 *     --project-id my-gcp-project \
 *     --region us-east-1 \
 *     --cluster prod-cluster \
 *     --project-context-repo git@bitbucket.org:org/project-context.git
 *
 * Environment Variables:
 *   CLAUDE_GITOPS_DIR          GitOps directory path
 *   CLAUDE_TERRAFORM_DIR       Terraform directory path
 *   CLAUDE_APP_SERVICES_DIR    App services directory path
 *   CLAUDE_PROJECT_ID          GCP Project ID
 *   CLAUDE_REGION              Primary region
 *   CLAUDE_CLUSTER_NAME        Cluster name
 *   CLAUDE_PROJECT_CONTEXT_REPO Project context git repository URL
 *
 * Features:
 * - Auto-detects project structure (GitOps, Terraform, AppServices)
 * - Installs Claude Code if not present
 * - Generates CLAUDE.md with correct paths
 * - Creates .claude/ symlinks to npm package
 * - Non-invasive: works from any directory
 */

import { fileURLToPath } from 'url';
import { dirname, join, relative, resolve, isAbsolute } from 'path';
import fs from 'fs/promises';
import { existsSync } from 'fs';
import { exec } from 'child_process';
import { promisify } from 'util';
import prompts from 'prompts';
import chalk from 'chalk';
import ora from 'ora';
import yargs from 'yargs';
import { hideBin } from 'yargs/helpers';
import { PACKAGE_ROOT, getTemplatePath } from '@jaguilar87/gaia-ops';

const execAsync = promisify(exec);
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const CWD = process.cwd();

// ============================================================================
// Auto-detection logic
// ============================================================================

/**
 * Detect project structure by scanning for common directories
 */
async function detectProjectStructure() {
  const spinner = ora('Detecting project structure...').start();

  const detected = {
    gitops: null,
    terraform: null,
    appServices: null,
    git: null
  };

  try {
    const entries = await fs.readdir(CWD);

    // GitOps candidates
    const gitopsCandidates = [
      'gitops',
      'non-prod-rnd-gke-gitops',
      'k8s',
      'kubernetes',
      'manifests',
      'deployments'
    ];
    for (const candidate of gitopsCandidates) {
      if (entries.includes(candidate)) {
        detected.gitops = `./${candidate}`;
        break;
      }
    }

    // Terraform candidates
    const terraformCandidates = [
      'terraform',
      'tf',
      'infrastructure',
      'iac',
      'infra'
    ];
    for (const candidate of terraformCandidates) {
      if (entries.includes(candidate)) {
        detected.terraform = `./${candidate}`;
        break;
      }
    }

    // AppServices candidates
    const appServicesCandidates = [
      'app-services',
      'services',
      'apps',
      'applications',
      'src'
    ];
    for (const candidate of appServicesCandidates) {
      if (entries.includes(candidate)) {
        detected.appServices = `./${candidate}`;
        break;
      }
    }

    // Git root
    if (entries.includes('.git')) {
      detected.git = '.';
    }

    spinner.succeed('Project structure detected');
  } catch (error) {
    spinner.fail('Failed to detect project structure');
    throw error;
  }

  return detected;
}

/**
 * Check if Claude Code is installed
 */
async function checkClaudeCodeInstalled() {
  try {
    // Try 'claude' command first (common alias)
    await execAsync('claude --version');
    return true;
  } catch {
    try {
      // Fallback to 'claude-code' command
      await execAsync('claude-code --version');
      return true;
    } catch {
      return false;
    }
  }
}

// ============================================================================
// CLI Argument Parsing
// ============================================================================

/**
 * Parse CLI arguments
 */
function parseCliArguments() {
  return yargs(hideBin(process.argv))
    .usage('Usage: $0 [options]')
    .option('non-interactive', {
      alias: 'y',
      type: 'boolean',
      description: 'Skip interactive prompts, use defaults or provided values',
      default: false
    })
    .option('gitops', {
      type: 'string',
      description: 'GitOps directory path (relative or absolute)'
    })
    .option('terraform', {
      type: 'string',
      description: 'Terraform directory path (relative or absolute)'
    })
    .option('app-services', {
      type: 'string',
      description: 'App services directory path (relative or absolute)'
    })
    .option('project-id', {
      type: 'string',
      description: 'GCP Project ID'
    })
    .option('region', {
      type: 'string',
      description: 'Primary region (default: us-central1)',
      default: 'us-central1'
    })
    .option('cluster', {
      type: 'string',
      description: 'Cluster name'
    })
    .option('skip-claude-install', {
      type: 'boolean',
      description: 'Skip Claude Code installation',
      default: false
    })
    .option('project-context-repo', {
      type: 'string',
      description: 'Git repository URL for project context (e.g., git@bitbucket.org:org/repo.git)'
    })
    .help('h')
    .alias('h', 'help')
    .version('1.0.0')
    .alias('v', 'version')
    .parse();
}

/**
 * Normalize path to be relative to CWD
 * Accepts both absolute and relative paths
 * Returns path relative to CWD for consistency
 */
function normalizePath(userPath) {
  if (!userPath) return null;
  
  // If absolute path, return as-is (user knows what they want)
  if (isAbsolute(userPath)) {
    return userPath;
  }
  
  // If relative path, ensure it's relative to CWD
  return userPath.startsWith('./') ? userPath : `./${userPath}`;
}

/**
 * Get configuration from CLI args, environment variables, and detected values
 */
function getConfiguration(detected, args) {
  // Priority: CLI args > Environment variables > Detected values > Defaults

  const config = {
    gitops: normalizePath(args.gitops || process.env.CLAUDE_GITOPS_DIR || detected.gitops) || './gitops',
    terraform: normalizePath(args.terraform || process.env.CLAUDE_TERRAFORM_DIR || detected.terraform) || './terraform',
    appServices: normalizePath(args.appServices || process.env.CLAUDE_APP_SERVICES_DIR || detected.appServices) || './app-services',
    projectId: args.projectId || process.env.CLAUDE_PROJECT_ID || '',
    region: args.region || process.env.CLAUDE_REGION || 'us-central1',
    clusterName: args.cluster || process.env.CLAUDE_CLUSTER_NAME || '',
    installClaudeCode: !args.skipClaudeInstall,
    projectContextRepo: args.projectContextRepo || process.env.CLAUDE_PROJECT_CONTEXT_REPO || ''
  };

  return config;
}

/**
 * Validate required configuration values
 */
function validateConfiguration(config, nonInteractive) {
  const errors = [];

  if (!config.projectId && nonInteractive) {
    errors.push('--project-id is required in non-interactive mode');
  }

  if (!config.clusterName && nonInteractive) {
    errors.push('--cluster is required in non-interactive mode');
  }

  if (errors.length > 0) {
    console.error(chalk.red('\n❌ Configuration errors:\n'));
    errors.forEach(error => console.error(chalk.red(`  - ${error}`)));
    console.error(chalk.gray('\nProvide values via CLI args or environment variables.\n'));
    process.exit(1);
  }

  return true;
}

// ============================================================================
// Interactive prompts
// ============================================================================

/**
 * Sanitize git repo URL input
 * Handles cases where user pastes "git clone <url>" instead of just "<url>"
 */
function sanitizeGitUrl(input) {
  if (!input || typeof input !== 'string') return '';

  // Trim whitespace
  let sanitized = input.trim();

  // Remove "git clone" prefix if present
  if (sanitized.toLowerCase().startsWith('git clone ')) {
    sanitized = sanitized.substring('git clone '.length).trim();
  }

  // Remove quotes if present
  sanitized = sanitized.replace(/^["']|["']$/g, '');

  return sanitized;
}

/**
 * Try to clone project context repo early and parse config
 * Returns parsed config or null if clone fails
 */
async function tryCloneProjectContext(repoUrl) {
  if (!repoUrl || repoUrl.trim() === '') {
    return null;
  }

  const sanitizedUrl = sanitizeGitUrl(repoUrl);

  const spinner = ora('Cloning project context repository...').start();

  try {
    const tempDir = join(CWD, '.claude-temp-context');

    // Remove temp dir if exists
    if (existsSync(tempDir)) {
      await fs.rm(tempDir, { recursive: true, force: true });
    }

    // Clone to temp directory
    await execAsync(`git clone ${sanitizedUrl} ${tempDir}`, { timeout: 30000 });

    // Try to read project-context.json
    const contextPath = join(tempDir, 'project-context.json');

    if (existsSync(contextPath)) {
      const contextData = JSON.parse(await fs.readFile(contextPath, 'utf-8'));

      // Clean up temp dir
      await fs.rm(tempDir, { recursive: true, force: true });

      spinner.succeed('Project context loaded successfully');
      console.log(chalk.green('  ✓ Auto-populated configuration from project context\n'));

      return {
        contextData,
        repoUrl: sanitizedUrl
      };
    } else {
      // No project-context.json found
      await fs.rm(tempDir, { recursive: true, force: true });
      spinner.warn('Repository cloned but no project-context.json found');
      return null;
    }
  } catch (error) {
    spinner.fail('Failed to clone project context repository');
    console.log(chalk.yellow(`\n⚠️  Error: ${error.message}`));
    console.log(chalk.gray('  Continuing with manual configuration...\n'));
    return null;
  }
}

/**
 * Present interactive wizard to user
 */
async function runInteractiveWizard(detected) {
  // ASCII Art banner
  console.log(chalk.cyan(`
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   ██████╗  █████╗ ██╗ █████╗      ██████╗ ██████╗ ███████╗   ║
║  ██╔════╝ ██╔══██╗██║██╔══██╗    ██╔═══██╗██╔══██╗██╔════╝   ║
║  ██║  ███╗███████║██║███████║    ██║   ██║██████╔╝███████╗   ║
║  ██║   ██║██╔══██║██║██╔══██║    ██║   ██║██╔═══╝ ╚════██║   ║
║  ╚██████╔╝██║  ██║██║██║  ██║    ╚██████╔╝██║     ███████║   ║
║   ╚═════╝ ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝     ╚═════╝ ╚═╝     ╚══════╝   ║
║                                                               ║
║        Multi-Agent DevOps Orchestration System                ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
  `));

  console.log(chalk.gray('This wizard will set up the Gaia-Ops agent system for your project.\n'));

  // =========================================================================
  // STEP 1: Ask for directory paths FIRST (always required)
  // =========================================================================
  console.log(chalk.yellow('📍 Directory Configuration'));
  console.log(chalk.gray('Where do you keep your code?\n'));

  const pathQuestions = await prompts([
    {
      type: 'text',
      name: 'gitops',
      message: '📦 GitOps directory:',
      initial: detected.gitops || './gitops',
      validate: value => value.trim().length > 0
    },
    {
      type: 'text',
      name: 'terraform',
      message: '🔧 Terraform directory:',
      initial: detected.terraform || './terraform',
      validate: value => value.trim().length > 0
    },
    {
      type: 'text',
      name: 'appServices',
      message: '🚀 App Services directory:',
      initial: detected.appServices || './app-services',
      validate: value => value.trim().length > 0
    }
  ], {
    onCancel: () => {
      console.log(chalk.yellow('\n⚠️  Installation cancelled by user\n'));
      process.exit(0);
    }
  });

  // =========================================================================
  // STEP 2: Ask for project context repo (optional)
  // =========================================================================
  console.log(chalk.yellow('\n🔗 Project Context (Optional)'));
  console.log(chalk.gray('If you have an existing project context repo, provide it here.\n'));
  console.log(chalk.gray('If not, we\'ll ask a few questions to create a basic one.\n'));

  const contextQuestion = await prompts({
    type: 'text',
    name: 'projectContextRepo',
    message: '📦 Project context repo (leave empty if you don\'t have one):',
    initial: ''
  }, {
    onCancel: () => {
      console.log(chalk.yellow('\n⚠️  Installation cancelled by user\n'));
      process.exit(0);
    }
  });

  // Try to clone and parse project context
  let projectContext = null;
  let configFromContext = null;

  if (contextQuestion.projectContextRepo && contextQuestion.projectContextRepo.trim() !== '') {
    projectContext = await tryCloneProjectContext(contextQuestion.projectContextRepo);

    if (projectContext) {
      // Extract config from context
      configFromContext = {
        cloudProvider: projectContext.contextData.sections?.project_details?.cloud_provider || projectContext.contextData.cloud_provider || 'gcp',
        gcpProjectId: projectContext.contextData.sections?.project_details?.project_id || projectContext.contextData.project_id_gcp || '',
        awsAccountId: projectContext.contextData.sections?.project_details?.aws_account || projectContext.contextData.aws_account || '',
        region: projectContext.contextData.sections?.project_details?.region || projectContext.contextData.primary_region || 'us-central1',
        clusterName: projectContext.contextData.sections?.project_details?.cluster_name || projectContext.contextData.infrastructure?.clusters?.primary?.name || ''
      };

      console.log(chalk.green('\n✅ Configuration loaded from project context:'));
      console.log(chalk.gray(`  • Cloud: ${configFromContext.cloudProvider.toUpperCase()}`));
      if (configFromContext.gcpProjectId) console.log(chalk.gray(`  • GCP Project: ${configFromContext.gcpProjectId}`));
      if (configFromContext.awsAccountId) console.log(chalk.gray(`  • AWS Account: ${configFromContext.awsAccountId}`));
      if (configFromContext.region) console.log(chalk.gray(`  • Region: ${configFromContext.region}`));
      if (configFromContext.clusterName) console.log(chalk.gray(`  • Cluster: ${configFromContext.clusterName}`));
      console.log(chalk.gray('\nUsing this configuration. Installation will continue...\n'));
    }
  }

  // =========================================================================
  // STEP 3: If NO project context, ask basic questions to create one
  // =========================================================================
  let additionalConfig = {};

  if (!projectContext) {
    console.log(chalk.yellow('📝 Project Configuration'));
    console.log(chalk.gray('Let\'s gather some basic info to create your project context.\n'));
    console.log(chalk.gray('You can leave fields empty if you don\'t have the info yet.\n'));

    const configQuestions = await prompts([
      {
        type: 'select',
        name: 'cloudProvider',
        message: '☁️  Cloud provider:',
        choices: [
          { title: 'GCP (Google Cloud Platform)', value: 'gcp' },
          { title: 'AWS (Amazon Web Services)', value: 'aws' },
          { title: 'Multi-cloud (AWS + GCP)', value: 'multi-cloud' }
        ],
        initial: 0
      },
      {
        type: (prev) => ['gcp', 'multi-cloud'].includes(prev) ? 'text' : null,
        name: 'gcpProjectId',
        message: '🌐 GCP Project ID (optional):',
        initial: ''
      },
      {
        type: (prev, values) => ['aws', 'multi-cloud'].includes(values.cloudProvider) ? 'text' : null,
        name: 'awsAccountId',
        message: '🌐 AWS Account ID (optional):',
        initial: ''
      },
      {
        type: 'text',
        name: 'region',
        message: '🌍 Primary Region (optional):',
        initial: ''
      },
      {
        type: 'text',
        name: 'clusterName',
        message: '☸️  Cluster Name (optional):',
        initial: ''
      }
    ], {
      onCancel: () => {
        console.log(chalk.yellow('\n⚠️  Installation cancelled by user\n'));
        process.exit(0);
      }
    });

    additionalConfig = configQuestions;
  }

  // =========================================================================
  // STEP 4: Ask if should install Claude Code
  // =========================================================================
  const claudeCodeQuestion = await prompts({
    type: 'confirm',
    name: 'installClaudeCode',
    message: '📥 Install Claude Code if not present?',
    initial: true
  }, {
    onCancel: () => {
      console.log(chalk.yellow('\n⚠️  Installation cancelled by user\n'));
      process.exit(0);
    }
  });

  // =========================================================================
  // Build final configuration
  // =========================================================================
  const finalConfig = {
    // Paths (always from user input)
    gitops: pathQuestions.gitops,
    terraform: pathQuestions.terraform,
    appServices: pathQuestions.appServices,

    // Config (from context OR from user input)
    ...(projectContext ? configFromContext : additionalConfig),

    // Claude Code installation preference
    installClaudeCode: claudeCodeQuestion.installClaudeCode,

    // Context repo info
    projectContextRepo: contextQuestion.projectContextRepo || '',
    projectContextAlreadyCloned: !!projectContext
  };

  // Set defaults for empty values
  if (!finalConfig.cloudProvider) finalConfig.cloudProvider = 'gcp';
  if (!finalConfig.gcpProjectId) finalConfig.gcpProjectId = '';
  if (!finalConfig.awsAccountId) finalConfig.awsAccountId = '';
  if (!finalConfig.region) finalConfig.region = 'us-central1';
  if (!finalConfig.clusterName) finalConfig.clusterName = '';

  return finalConfig;
}

// ============================================================================
// Installation steps
// ============================================================================

/**
 * Install Claude Code CLI
 */
async function installClaudeCode() {
  const spinner = ora('Installing Claude Code...').start();

  try {
    await execAsync('npm install -g @anthropic-ai/claude-code');
    spinner.succeed('Claude Code installed successfully');
  } catch (error) {
    spinner.fail('Failed to install Claude Code');
    console.error(chalk.red('\nError:'), error.message);
    console.log(chalk.yellow('\nPlease install Claude Code manually:'));
    console.log(chalk.gray('  npm install -g @anthropic-ai/claude-code\n'));
    throw error;
  }
}

/**
 * Create .claude/ directory structure with symlinks
 */
async function createClaudeDirectory() {
  const spinner = ora('Creating .claude/ directory...').start();

  try {
    const claudeDir = join(CWD, '.claude');

    // Create base directory
    await fs.mkdir(claudeDir, { recursive: true });

    // Calculate relative path from .claude/ to node_modules/@jaguilar87/gaia-ops
    const packagePath = join(CWD, 'node_modules', '@jaguilar87', 'gaia-ops');
    const relativePath = relative(claudeDir, packagePath);

    // Create symlinks to npm package
    const symlinks = [
      { target: join(relativePath, 'agents'), link: join(claudeDir, 'agents') },
      { target: join(relativePath, 'tools'), link: join(claudeDir, 'tools') },
      { target: join(relativePath, 'hooks'), link: join(claudeDir, 'hooks') },
      { target: join(relativePath, 'commands'), link: join(claudeDir, 'commands') },
      { target: join(relativePath, 'templates'), link: join(claudeDir, 'templates') },
      { target: join(relativePath, 'config'), link: join(claudeDir, 'config') },
      { target: join(relativePath, 'speckit'), link: join(claudeDir, 'speckit') },
      { target: join(relativePath, 'CHANGELOG.md'), link: join(claudeDir, 'CHANGELOG.md') }
    ];

    for (const { target, link } of symlinks) {
      // Remove existing symlink if present
      if (existsSync(link)) {
        await fs.unlink(link);
      }
      await fs.symlink(target, link);
    }

    // Create project-specific directories (NOT symlinked)
    await fs.mkdir(join(claudeDir, 'logs'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'tests'), { recursive: true });
    await fs.mkdir(join(claudeDir, 'project-context'), { recursive: true });

    spinner.succeed('.claude/ directory created with symlinks');
  } catch (error) {
    spinner.fail('Failed to create .claude/ directory');
    throw error;
  }
}

/**
 * Generate settings.json from template
 */
async function generateSettingsJson() {
  const spinner = ora('Generating .claude/settings.json...').start();

  try {
    const settingsPath = join(CWD, '.claude', 'settings.json');
    const templatePath = getTemplatePath('settings.template.json');

    // Read template
    const template = await fs.readFile(templatePath, 'utf-8');

    // Write settings.json (no placeholders to replace)
    await fs.writeFile(settingsPath, template, 'utf-8');

    spinner.succeed('.claude/settings.json generated');
  } catch (error) {
    spinner.fail('Failed to generate settings.json');
    throw error;
  }
}

/**
 * Generate CLAUDE.md from template
 */
async function generateClaudeMd(config) {
  const spinner = ora('Generating CLAUDE.md...').start();

  try {
    // Read template
    const templatePath = getTemplatePath('CLAUDE.template.md');
    let template = await fs.readFile(templatePath, 'utf-8');

    // Build project configuration section based on cloud provider
    let projectConfig = '';

    if (config.cloudProvider === 'gcp') {
      projectConfig = `- **Cloud Provider:** GCP
- **GCP Project ID:** ${config.gcpProjectId}
- **Region:** ${config.region}
- **Cluster:** ${config.clusterName}`;
    } else if (config.cloudProvider === 'aws') {
      projectConfig = `- **Cloud Provider:** AWS
- **AWS Account ID:** ${config.awsAccountId}
- **Region:** ${config.region}
- **Cluster:** ${config.clusterName}`;
    } else if (config.cloudProvider === 'multi-cloud') {
      projectConfig = `- **Cloud Provider:** Multi-cloud (AWS + GCP)`;
      if (config.gcpProjectId) {
        projectConfig += `\n- **GCP Project ID:** ${config.gcpProjectId}`;
      }
      if (config.awsAccountId) {
        projectConfig += `\n- **AWS Account ID:** ${config.awsAccountId}`;
      }
      projectConfig += `\n- **Primary Region:** ${config.region}
- **Cluster:** ${config.clusterName}`;
    }

    // Replace placeholders
    template = template.replace(/{{TIMESTAMP}}/g, new Date().toISOString().split('T')[0]);
    template = template.replace(/{{PROJECT_CONFIG}}/g, projectConfig);
    template = template.replace(/{{GITOPS_PATH}}/g, config.gitops);
    template = template.replace(/{{TERRAFORM_PATH}}/g, config.terraform);
    template = template.replace(/{{APP_SERVICES_PATH}}/g, config.appServices);

    // Write to current directory
    const claudeMdPath = join(CWD, 'CLAUDE.md');
    await fs.writeFile(claudeMdPath, template, 'utf-8');

    spinner.succeed('CLAUDE.md generated');
  } catch (error) {
    spinner.fail('Failed to generate CLAUDE.md');
    throw error;
  }
}

/**
 * Generate AGENTS.md symlink
 * NOTE: AGENTS.md is already available at .claude/config/AGENTS.md via the config/ symlink
 * No need to create a separate symlink in the project root
 */
async function generateAgentsMd() {
  // AGENTS.md is accessible via .claude/config/AGENTS.md (symlinked directory)
  // No action needed - keeping function for backward compatibility
  return Promise.resolve();
}

/**
 * Validate and setup project paths (gitops, terraform, app-services)
 * Creates directories automatically if they don't exist
 * Accepts both absolute and relative paths
 */
async function validateAndSetupProjectPaths(config) {
  console.log(chalk.cyan('\n📁 Setting up project directories...\n'));

  const paths = {
    gitops: { path: config.gitops, name: 'GitOps' },
    terraform: { path: config.terraform, name: 'Terraform' },
    appServices: { path: config.appServices, name: 'App Services' }
  };

  for (const [, { path: userPath, name }] of Object.entries(paths)) {
    // Handle both absolute and relative paths
    const absPath = isAbsolute(userPath) ? userPath : resolve(CWD, userPath);

    // Check if path exists
    if (existsSync(absPath)) {
      console.log(chalk.gray(`  ✓ ${name}: ${userPath} (exists)`));
      continue;
    }

    // Create directory automatically
    await fs.mkdir(absPath, { recursive: true });
    console.log(chalk.green(`  ✓ ${name}: ${userPath} (created)`));
  }

  console.log('');
}

/**
 * Generate project-context.json
 */
async function generateProjectContext(config) {
  const spinner = ora('Generating project-context.json...').start();

  try {
    // Build metadata section based on cloud provider
    const metadata = {
      version: '1.0',
      last_updated: new Date().toISOString(),
      project_root: '.',  // Reference point: where CLAUDE.md is located
      created_by: 'gaia-init',
      cloud_provider: config.cloudProvider,
      environment: 'non-prod',
      primary_region: config.region
    };

    // Add cloud-specific metadata
    if (config.cloudProvider === 'gcp') {
      metadata.project_id = config.gcpProjectId;
    } else if (config.cloudProvider === 'aws') {
      metadata.aws_account = config.awsAccountId;
    } else if (config.cloudProvider === 'multi-cloud') {
      if (config.gcpProjectId) metadata.project_id = config.gcpProjectId;
      if (config.awsAccountId) metadata.aws_account = config.awsAccountId;
    }

    // Build project_details section
    const projectDetails = {
      region: config.region,
      environment: 'non-prod',
      cluster_name: config.clusterName,
      cloud_provider: config.cloudProvider
    };

    // Add provider-specific fields (matching contract expectations)
    if (config.gcpProjectId) {
      projectDetails.project_id = config.gcpProjectId;  // GCP contract expects project_id
    }
    if (config.awsAccountId) {
      projectDetails.account_id = config.awsAccountId;  // AWS contract expects account_id
    }

    // Build provider_credentials section
    const providerCredentials = {};
    if (config.gcpProjectId) {
      providerCredentials.gcp = {
        project: config.gcpProjectId,
        region: config.region
      };
    }
    if (config.awsAccountId) {
      providerCredentials.aws = {
        account_id: config.awsAccountId,
        region: config.region
      };
    }

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
            platform: 'flux'
          },
          flux_details: {
            namespaces: []
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

    const projectContextPath = join(CWD, '.claude', 'project-context', 'project-context.json');
    await fs.writeFile(projectContextPath, JSON.stringify(projectContext, null, 2), 'utf-8');

    spinner.succeed('project-context/project-context.json generated');
  } catch (error) {
    spinner.fail('Failed to generate project-context.json');
    throw error;
  }
}

/**
 * Install @jaguilar87/gaia-ops as dependency
 */
async function installClaudeAgentsPackage() {
  const spinner = ora('Installing @jaguilar87/gaia-ops package...').start();

  try {
    // Check if package.json exists
    const packageJsonPath = join(CWD, 'package.json');
    if (!existsSync(packageJsonPath)) {
      // Create minimal package.json
      const packageJson = {
        name: 'my-project',
        version: '1.0.0',
        private: true,
        dependencies: {}
      };
      await fs.writeFile(packageJsonPath, JSON.stringify(packageJson, null, 2), 'utf-8');
    }

    // Install @jaguilar87/gaia-ops
    // NOTE: In production, this would install from npm registry
    // For now, we'll use local path
    await execAsync('npm install @jaguilar87/gaia-ops');

    spinner.succeed('@jaguilar87/gaia-ops package installed');
  } catch (error) {
    spinner.fail('Failed to install @jaguilar87/gaia-ops');
    throw error;
  }
}

/**
 * Clone project context repository (optional)
 * If already cloned during wizard, skip clone and just set it up in final location
 */
async function cloneProjectContextRepo(repoUrl, alreadyCloned = false) {
  if (!repoUrl || repoUrl.trim() === '') {
    console.log(chalk.gray('\n✓ Skipping project context repo clone (not provided)\n'));
    return;
  }

  const sanitizedUrl = sanitizeGitUrl(repoUrl);

  if (alreadyCloned) {
    // Context was already cloned during wizard, just re-clone to final location
    const spinner = ora('Setting up project context...').start();

    try {
      const projectContextDir = join(CWD, '.claude', 'project-context');

      // Remove the generated project-context.json as it will be replaced by the cloned repo
      const generatedFile = join(projectContextDir, 'project-context.json');
      if (existsSync(generatedFile)) {
        await fs.unlink(generatedFile);
      }

      // Clone fresh to final location (we already validated it works)
      const tempDir = `${projectContextDir}-temp`;
      await execAsync(`git clone ${sanitizedUrl} ${tempDir}`, { timeout: 30000 });

      // Move contents from temp to project-context
      const files = await fs.readdir(tempDir);
      for (const file of files) {
        const src = join(tempDir, file);
        const dest = join(projectContextDir, file);
        await fs.rename(src, dest);
      }

      // Remove temp directory
      await fs.rm(tempDir, { recursive: true, force: true });

      spinner.succeed('Project context repository configured');
      console.log(chalk.green(`  → Location: .claude/project-context/\n`));
    } catch (error) {
      spinner.fail('Failed to setup project context repository');
      console.log(chalk.yellow(`\n⚠️  You can clone it manually with:`));
      console.log(chalk.gray(`  cd .claude`));
      console.log(chalk.gray(`  rm -rf project-context`));
      console.log(chalk.gray(`  git clone ${sanitizedUrl} project-context\n`));
      // Don't throw - allow installation to continue
    }
    return;
  }

  // Not cloned yet during wizard, try to clone now
  const spinner = ora('Cloning project context repository...').start();

  try {
    const projectContextDir = join(CWD, '.claude', 'project-context');

    // Remove the generated project-context.json as it will be replaced by the cloned repo
    const generatedFile = join(projectContextDir, 'project-context.json');
    if (existsSync(generatedFile)) {
      await fs.unlink(generatedFile);
    }

    // Clone repo
    const tempDir = `${projectContextDir}-temp`;
    await execAsync(`git clone ${sanitizedUrl} ${tempDir}`, { timeout: 30000 });

    // Move contents from temp to project-context
    const files = await fs.readdir(tempDir);
    for (const file of files) {
      const src = join(tempDir, file);
      const dest = join(projectContextDir, file);
      await fs.rename(src, dest);
    }

    // Remove temp directory
    await fs.rm(tempDir, { recursive: true, force: true });

    spinner.succeed('Project context repository cloned');
    console.log(chalk.green(`  → Cloned from: ${sanitizedUrl}`));
    console.log(chalk.gray(`  → Location: .claude/project-context/\n`));
  } catch (error) {
    spinner.fail('Failed to clone project context repository');
    console.log(chalk.yellow(`\n⚠️  Error: ${error.message}`));
    console.log(chalk.gray('\n  Common issues:'));
    console.log(chalk.gray('  • Check SSH keys are configured: ssh -T git@bitbucket.org'));
    console.log(chalk.gray('  • Verify repository URL is correct'));
    console.log(chalk.gray('  • Ensure you have access to the repository\n'));
    console.log(chalk.yellow(`  You can clone it manually later with:`));
    console.log(chalk.gray(`    cd .claude`));
    console.log(chalk.gray(`    rm -rf project-context`));
    console.log(chalk.gray(`    git clone ${sanitizedUrl} project-context\n`));
    // Don't throw - allow installation to continue
  }
}

// ============================================================================
// Main installer flow
// ============================================================================

async function main() {
  try {
    // Parse CLI arguments
    const args = parseCliArguments();

    // Clear console unless in non-interactive mode
    if (!args.nonInteractive) {
      console.clear();
    }

    // Step 1: Check Claude Code installation
    const claudeCodeInstalled = await checkClaudeCodeInstalled();
    if (!claudeCodeInstalled) {
      console.log(chalk.yellow('⚠️  Claude Code is not installed\n'));
    } else {
      console.log(chalk.green('✅ Claude Code is already installed\n'));
    }

    // Step 2: Auto-detect project structure
    const detected = await detectProjectStructure();

    // Step 3: Get configuration (CLI args, env vars, detected, or interactive)
    let config;
    if (args.nonInteractive) {
      // Non-interactive mode: use CLI args, env vars, and detected values
      config = getConfiguration(detected, args);
      validateConfiguration(config, true);

      // Display configuration being used
      console.log(chalk.cyan('\n📋 Configuration:\n'));
      console.log(chalk.gray(`  GitOps:       ${config.gitops}`));
      console.log(chalk.gray(`  Terraform:    ${config.terraform}`));
      console.log(chalk.gray(`  App Services: ${config.appServices}`));
      console.log(chalk.gray(`  Project ID:   ${config.projectId}`));
      console.log(chalk.gray(`  Region:       ${config.region}`));
      console.log(chalk.gray(`  Cluster:      ${config.clusterName}\n`));
    } else {
      // Interactive mode: run wizard with pre-filled detected values
      config = await runInteractiveWizard(detected);
    }

    // Step 4: Install Claude Code only if requested AND not already installed
    if (config.installClaudeCode) {
      if (claudeCodeInstalled) {
        console.log(chalk.green('✓ Claude Code already installed, skipping reinstall\n'));
      } else {
        await installClaudeCode();
      }
    } else {
      console.log(chalk.gray('✓ Claude Code installation skipped\n'));
    }

    // Step 5: Install @jaguilar87/gaia-ops package
    await installClaudeAgentsPackage();

    // Step 5.5: Validate and setup project paths (gitops, terraform, app-services)
    await validateAndSetupProjectPaths(config);

    // Step 6: Create .claude/ directory with symlinks
    await createClaudeDirectory();

    // Step 6.5: Generate settings.json
    await generateSettingsJson();

    // Step 7: Generate CLAUDE.md
    await generateClaudeMd(config);

    // Step 8: Generate AGENTS.md symlink
    await generateAgentsMd();

    // Step 9: Generate project-context.json
    await generateProjectContext(config);

    // Step 10: Clone project context repository (optional)
    if (config.projectContextRepo) {
      await cloneProjectContextRepo(config.projectContextRepo, config.projectContextAlreadyCloned);
    }

    // Success message
    console.log(chalk.green.bold('\n✅ Installation complete!\n'));
    console.log(chalk.gray('Next steps:'));
    console.log(chalk.gray('  1. Review CLAUDE.md and adjust paths if needed'));
    if (!config.projectContextRepo) {
      console.log(chalk.gray('  2. Update .claude/project-context/project-context.json with your services'));
      console.log(chalk.gray('     OR clone your project context repo:'));
      console.log(chalk.gray('     cd .claude && git clone <your-repo> project-context'));
    } else {
      console.log(chalk.gray('  2. Your project context has been cloned to .claude/project-context/'));
    }
    console.log(chalk.gray('  3. Start Claude Code: claude-code\n'));
    console.log(chalk.cyan('📚 Documentation: .claude/config/\n'));

  } catch (error) {
    console.error(chalk.red('\n❌ Installation failed\n'));
    console.error(error);
    process.exit(1);
  }
}

main();
